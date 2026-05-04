"""LLM-powered document-to-HTML conversion.

Takes LiteParse spatial output and uses an LLM to produce accessible,
WCAG 2.2 AA compliant HTML fragments suitable for Canvas wiki pages.

Pipeline: LiteParse → structured text → LLM → HTML → CanvasHTMLValidator
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from lti_app.core.ai.prompt_library import (
    DOCUMENT_TO_HTML_SYSTEM_PROMPT,
    get_document_to_html_prompt,
)
from lti_app.core.ai.vision_client import VisionClient, get_vision_client
from lti_app.core.documents.liteparse_adapter import (
    PageLayout,
    SpatialParseResult,
    TextItem,
)

_logger = structlog.get_logger(__name__)

# Max words per LLM request to stay within context limits
_MAX_WORDS_PER_CHUNK = 3000

# Per-document wall-clock budget for LLM conversion. Once exceeded, the
# remaining chunks fall back to plain-text wrapping so a single 300-page PDF
# can't grind silently for 30+ minutes inside AutoRemedy phase 4. Mirrors the
# pattern from CLU-58's alt-text budget.
#
# 10 minutes covers ~120 chunks at the post-CLU-67 latency of ~5-10s/chunk.
# For a 315-page textbook PDF, that's roughly the first 40% of pages getting
# full LLM-structured HTML; remaining pages get plain-text fallback. Bumped
# from 5 → 10 min after seeing real Art103 data show typical chunks at
# 3-7 sec each (2026-04-07).
_DOC_BUDGET_SECONDS = 600.0

# Fallback page size when no clear heading structure is found.
# Used when a PDF has many source pages but no detectable h2 headings —
# we still need to split it because the LLM output for the whole document
# would exceed Canvas page size limits (CLU-67).
_CHAPTER_FALLBACK_PAGE_SIZE = 20

# Cap on chapter count from heading detection. If detection produces more
# chapters than this, treat it as noise (e.g. headings on every page) and
# fall back to fixed splitting.
_CHAPTER_MAX_DETECTED = 30


@dataclass
class Chapter:
    """One logical chapter extracted from a multi-page document.

    Used by DocumentToHTMLService.split_into_chapters() and consumed by
    ConversionService for multi-page Canvas output (CLU-67).
    """

    title: str
    pages: list[PageLayout] = field(default_factory=list)


def _strip_empty_src_imgs(html: str) -> str:
    """Replace ``<img>`` tags with empty/missing src with caption text.

    The LLM converter often produces ``<img alt="…" src="">`` tags for
    images that LiteParse couldn't extract — see CLU-67 quality bug
    on Art103 textbook chapters. Each empty-src img is replaced with
    ``<p><em>[Image: {alt}]</em></p>`` so the descriptive text becomes
    visible content for students. Imgs with no alt at all are removed
    entirely (no useful information to keep).

    Imports BeautifulSoup locally to avoid pulling it into the module
    import graph for callers that only use the public conversion API.
    """
    if "<img" not in html:
        return html

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for img in list(soup.find_all("img")):
        src = (img.get("src") or "").strip()
        if src:
            continue  # Real src — leave it alone
        alt = (img.get("alt") or "").strip()
        if alt:
            # Replace with a visible caption paragraph
            new_p = soup.new_tag("p")
            em = soup.new_tag("em")
            em.string = f"[Image: {alt}]"
            new_p.append(em)
            img.replace_with(new_p)
        else:
            img.decompose()
        changed = True

    if not changed:
        return html
    return str(soup)


# Lowercase letters that count as "vowel-like" for word-shape detection.
# Includes 'y' since it functions as a vowel in many English words.
_VOWELS = set("aeiouy")


def _looks_like_garbage_heading_text(text: str) -> bool:
    """Return True if heading text is OCR noise that should be dropped.

    PDF→LLM conversion sometimes promotes corrupted OCR text to heading
    tags (Art103 PRINTMAKINGch6-1 produced ``<h5>l</h5>``,
    ``<h6>171</h6>``, ``<h4>UdrtnigFra nlss</h4>``, ``<h5>-</h5>``).
    Garbage criteria:

    1. < 4 characters: too short to be a meaningful heading.
    2. All-numeric (page numbers from the source PDF).
    3. No alphabetic characters at all.
    4. Any whitespace-split token of length ≥ 4 has zero vowels and is
       not an all-caps acronym (the "nlss" case in "UdrtnigFra nlss" —
       OCR noise has consonant-only sub-words while real English words
       almost always contain at least one vowel).
    """
    stripped = text.strip()
    if not stripped:
        return True

    # Rule 1: too short
    if len(stripped) < 4:
        return True

    # Rule 2: all digits (with optional separators)
    if re.match(r"^[\d\s,.\-:/]+$", stripped):
        return True

    # Rule 3: no letters at all
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return True

    # Rule 4: zero-vowel tokens. Real English words always contain at
    # least one vowel (we count y as a vowel). The exception is
    # all-uppercase acronyms ("BBC", "FBI"), which we skip. Garbage
    # OCR text often has tokens like "nlss", "drtn", "Fra" — long
    # enough to be suspicious but with no vowels.
    for token in stripped.split():
        token_letters = [c for c in token if c.isalpha()]
        if len(token_letters) < 4:
            continue  # too short to judge
        if token.isupper():
            continue  # acronym — leave it alone
        vowel_count = sum(1 for c in token_letters if c.lower() in _VOWELS)
        if vowel_count == 0:
            return True

    return False


def _strip_garbage_headings(html: str) -> str:
    """Drop heading tags whose text content is OCR garbage.

    Uses ``_looks_like_garbage_heading_text`` for the policy. Headings
    that wrap an image with alt text (and no other content) are
    preserved — the image is the heading's content.
    """
    if "<h" not in html:
        return html

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for heading in list(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])):
        # If it wraps an image with alt, the image is the content
        imgs_with_alt = [
            img for img in heading.find_all("img")
            if (img.get("alt") or "").strip()
        ]
        if imgs_with_alt:
            continue
        text = heading.get_text(strip=True)
        if _looks_like_garbage_heading_text(text):
            heading.decompose()
            changed = True

    if not changed:
        return html
    return str(soup)


class DocumentToHTMLService:
    """Convert LiteParse spatial output to accessible HTML via LLM."""

    def __init__(self, client: VisionClient | None = None):
        self._client = client

    def _get_client(self) -> VisionClient:
        if self._client is None:
            self._client = get_vision_client()
        return self._client

    async def convert(
        self,
        result: SpatialParseResult,
        title: str = "",
        cancel_check: "Callable[[], None] | None" = None,
    ) -> str:
        """Convert a SpatialParseResult to an accessible HTML fragment.

        Processes page-by-page using a document-wide heading map. Falls
        back to plain-text wrapping if the LLM fails OR the per-document
        budget runs out.

        CLU-68: pass `cancel_check` to short-circuit the inner LLM
        chunk loop when the user clicks Cancel. The callback is invoked
        at the start of each chunk iteration; if it raises, the
        exception propagates out cleanly. The standard caller is
        `AutoRemedyService._check_cancel` which raises
        `AutoRemedyCancelled` (a `BaseException` subclass).
        """
        if not result.pages:
            return self._plain_text_fallback(result.text, title)

        heading_map = self.build_heading_map(result)
        return await self._convert_pages(
            pages=result.pages,
            heading_map=heading_map,
            title=title,
            fallback_text=result.text,
            cancel_check=cancel_check,
        )

    async def convert_chapter(
        self,
        chapter: Chapter,
        heading_map: dict[float, int],
        cancel_check: "Callable[[], None] | None" = None,
    ) -> str:
        """Convert a single Chapter (subset of pages) into HTML.

        Public entry point for ConversionService's multi-page flow
        (CLU-67). The heading_map should be pre-built from the FULL
        document so font→heading inference is consistent across chapters.
        Each chapter conversion gets its own per-document LLM budget.

        CLU-68: see `convert()` for cancel_check semantics. Each chapter
        gets its own cancel check passes.
        """
        return await self._convert_pages(
            pages=chapter.pages,
            heading_map=heading_map,
            title=chapter.title,
            fallback_text="\n".join(p.text for p in chapter.pages),
            cancel_check=cancel_check,
        )

    async def _convert_pages(
        self,
        pages: list[PageLayout],
        heading_map: dict[float, int],
        title: str,
        fallback_text: str = "",
        cancel_check: "Callable[[], None] | None" = None,
    ) -> str:
        """Convert a sequence of pages using a pre-built heading map.

        Used by ``convert()`` for whole-document conversion and by
        ``convert_chapter()`` for per-chapter conversion in CLU-67.
        """
        # Pre-compute total chunk count so the per-chunk progress log is
        # actually meaningful (otherwise "chunk 47" doesn't tell you whether
        # the doc is at 10% or 95%).
        all_page_chunks: list[tuple[int, list[str]]] = []
        total_chunks = 0
        for i, page in enumerate(pages):
            structured = self._build_structured_text(page, heading_map)
            if not structured.strip():
                continue
            chunks = self._chunk_text(structured)
            all_page_chunks.append((i, chunks))
            total_chunks += len(chunks)

        if total_chunks == 0:
            return self._plain_text_fallback(fallback_text, title)

        _logger.info(
            "llm_doc_convert_start",
            title=title[:60],
            pages=len(all_page_chunks),
            chunks=total_chunks,
            budget_seconds=_DOC_BUDGET_SECONDS,
        )

        deadline = time.monotonic() + _DOC_BUDGET_SECONDS
        budget_exhausted = False
        chunk_idx = 0
        page_htmls: list[str] = []
        total_pages = len(pages)

        for i, chunks in all_page_chunks:
            page_parts: list[str] = []
            for chunk in chunks:
                chunk_idx += 1

                # CLU-68: cooperative cancel check at the start of each
                # chunk. If the orchestrator (AutoRemedyService) signals
                # cancellation, the callback raises and propagates out
                # cleanly through the budget loop and the gather. Without
                # this, the user's Cancel click takes 2-8 minutes to
                # propagate because the LLM chunk loop has no other
                # cooperative break points.
                if cancel_check is not None:
                    cancel_check()

                # Per-doc budget check (CLU follow-up to CLU-67/68 — silent
                # multi-hour grinds on huge PDFs were indistinguishable from
                # genuine hangs). After the budget runs out, fall back to
                # plain text for every remaining chunk.
                if time.monotonic() >= deadline:
                    if not budget_exhausted:
                        _logger.warning(
                            "llm_doc_convert_budget_exhausted",
                            title=title[:60],
                            done=chunk_idx - 1,
                            total=total_chunks,
                        )
                        budget_exhausted = True
                    page_parts.append(self._plain_text_fallback(chunk, ""))
                    continue

                heading_desc = self._heading_map_description(heading_map)
                prompt = get_document_to_html_prompt(
                    structured_text=chunk,
                    heading_map=heading_desc,
                    page_num=i,
                    total_pages=total_pages,
                )

                chunk_start = time.monotonic()
                try:
                    html = await self._call_llm(prompt)
                    html = self._clean_llm_output(html)
                    elapsed = time.monotonic() - chunk_start
                    _logger.info(
                        "llm_doc_convert_chunk",
                        title=title[:60],
                        chunk=chunk_idx,
                        total=total_chunks,
                        page=i + 1,
                        seconds=round(elapsed, 1),
                    )
                    if html:
                        page_parts.append(html)
                    else:
                        page_parts.append(self._plain_text_fallback(chunk, ""))
                except Exception as e:
                    elapsed = time.monotonic() - chunk_start
                    _logger.warning(
                        "llm_doc_convert_chunk_failed",
                        title=title[:60],
                        chunk=chunk_idx,
                        total=total_chunks,
                        page=i + 1,
                        seconds=round(elapsed, 1),
                        error=str(e),
                    )
                    page_parts.append(self._plain_text_fallback(chunk, ""))

            page_htmls.append("\n".join(page_parts))

        _logger.info(
            "llm_doc_convert_complete",
            title=title[:60],
            chunks=total_chunks,
            budget_exhausted=budget_exhausted,
        )

        if not page_htmls:
            return self._plain_text_fallback(fallback_text, title)

        # Join pages — use <hr> separator for multi-page docs
        if len(page_htmls) > 1:
            html = "\n<hr>\n".join(page_htmls)
        else:
            html = page_htmls[0]

        return html

    # ------------------------------------------------------------------
    # Font analysis → heading map
    # ------------------------------------------------------------------

    def build_heading_map(
        self, result: SpatialParseResult
    ) -> dict[float, int]:
        """Map font sizes to heading levels based on frequency distribution.

        The most common font size is body text (<p>). Larger sizes get
        heading levels h2-h6, with the largest mapped to h2.

        Returns a dict of {font_size: heading_level} where 0 means body.
        """
        size_counter: Counter[float] = Counter()
        for page in result.pages:
            for item in page.text_items:
                if item.font_size and item.text.strip():
                    # Round to nearest 0.5 to group similar sizes
                    rounded = round(item.font_size * 2) / 2
                    size_counter[rounded] += len(item.text)

        if not size_counter:
            return {}

        # Most common size (by total character count) is body text
        body_size = size_counter.most_common(1)[0][0]

        # Sizes larger than body are headings, sorted descending
        heading_sizes = sorted(
            [s for s in size_counter if s > body_size],
            reverse=True,
        )

        heading_map: dict[float, int] = {body_size: 0}  # 0 = body
        for i, size in enumerate(heading_sizes[:5]):  # h2-h6
            heading_map[size] = i + 2  # h2, h3, h4, h5, h6

        return heading_map

    # ------------------------------------------------------------------
    # Chapter splitting (CLU-67)
    # ------------------------------------------------------------------

    def split_into_chapters(
        self, result: SpatialParseResult
    ) -> list[Chapter]:
        """Split a spatial parse result into chapters for multi-page output.

        Strategy:
        1. Build the heading map. The smallest level (h2) is the chapter
           boundary signal.
        2. Walk pages; whenever a page contains an h2-sized text item near
           the top, treat it as a new chapter boundary.
        3. If detection yields 0-1 chapters OR > _CHAPTER_MAX_DETECTED
           chapters, fall back to fixed-size splitting at
           _CHAPTER_FALLBACK_PAGE_SIZE pages each.
        4. If the first chapter doesn't start on page 1, group the
           leading pages into a synthetic "Front Matter" chapter.

        Returns an empty list when ``result.pages`` is empty.
        """
        pages = result.pages
        if not pages:
            return []
        if len(pages) == 1:
            return [Chapter(title=self._first_chapter_title(pages[0]), pages=list(pages))]

        heading_map = self.build_heading_map(result)
        h2_size = self._h2_font_size(heading_map)

        if h2_size is None:
            return self._fixed_split(pages)

        # Walk pages, identify chapter starts
        boundaries: list[tuple[int, str]] = []  # (page_idx, title)
        for idx, page in enumerate(pages):
            heading_text = self._extract_h2_heading(page, h2_size)
            if heading_text:
                boundaries.append((idx, heading_text))

        if len(boundaries) < 2:
            return self._fixed_split(pages)

        if len(boundaries) > _CHAPTER_MAX_DETECTED:
            return self._fixed_split(pages)

        chapters: list[Chapter] = []
        # Front matter: pages before the first detected boundary
        if boundaries[0][0] > 0:
            chapters.append(
                Chapter(
                    title="Front Matter",
                    pages=list(pages[: boundaries[0][0]]),
                )
            )

        for i, (start_idx, title) in enumerate(boundaries):
            end_idx = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(pages)
            chapters.append(
                Chapter(title=title, pages=list(pages[start_idx:end_idx]))
            )

        return chapters

    @staticmethod
    def _h2_font_size(heading_map: dict[float, int]) -> float | None:
        """Return the font size that maps to h2, or None if no headings."""
        for size, level in heading_map.items():
            if level == 2:
                return size
        return None

    @staticmethod
    def _extract_h2_heading(page: PageLayout, h2_size: float) -> str:
        """Return the text of an h2-sized item on this page, or empty.

        Looks for any text item whose font size is within 0.5pt of the h2
        size. Returns the topmost one in reading order, truncated to a
        sensible chapter title length.
        """
        candidates = [
            item for item in page.text_items
            if item.font_size and abs(item.font_size - h2_size) <= 0.5
            and item.text.strip()
        ]
        if not candidates:
            return ""
        topmost = min(candidates, key=lambda i: i.y)
        title = topmost.text.strip()
        if len(title) > 80:
            title = title[:77] + "..."
        return title

    def _fixed_split(self, pages: list[PageLayout]) -> list[Chapter]:
        """Split pages into fixed-size chapters when heading detection fails."""
        chapters: list[Chapter] = []
        size = _CHAPTER_FALLBACK_PAGE_SIZE
        for start in range(0, len(pages), size):
            slice_ = pages[start : start + size]
            first = slice_[0].page_num
            last = slice_[-1].page_num
            title = f"Pages {first}-{last}"
            chapters.append(Chapter(title=title, pages=list(slice_)))
        return chapters

    @staticmethod
    def _first_chapter_title(page: PageLayout) -> str:
        """Pick a sensible title for a single-page document."""
        for item in page.text_items:
            if item.text.strip():
                return item.text.strip()[:80]
        return "Document"

    def _heading_map_description(self, heading_map: dict[float, int]) -> str:
        """Describe the heading map for the LLM prompt."""
        if not heading_map:
            return "No font size data available. Infer headings from context."

        lines = []
        for size, level in sorted(heading_map.items(), reverse=True):
            if level == 0:
                lines.append(f"- Font size ~{size}pt → body text (<p>)")
            else:
                lines.append(f"- Font size ~{size}pt → <h{level}>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Structured text building
    # ------------------------------------------------------------------

    def _build_structured_text(
        self,
        page: PageLayout,
        heading_map: dict[float, int],
    ) -> str:
        """Build structured text from page layout with role annotations.

        Sorts text items by reading order (top-to-bottom, left-to-right)
        and annotates each with its inferred role based on font size.
        """
        if not page.text_items:
            return page.text

        # Sort by y position (top to bottom), then x (left to right)
        sorted_items = sorted(
            page.text_items,
            key=lambda t: (round(t.y / 5) * 5, t.x),
        )

        lines: list[str] = []
        for item in sorted_items:
            text = item.text.strip()
            if not text:
                continue

            role = self._infer_role(item, heading_map)
            if role:
                lines.append(f"[{role}] {text}")
            else:
                lines.append(text)

        return "\n".join(lines)

    def _infer_role(
        self,
        item: TextItem,
        heading_map: dict[float, int],
    ) -> str:
        """Infer the semantic role of a text item from its font size."""
        if not item.font_size or not heading_map:
            return ""

        rounded = round(item.font_size * 2) / 2

        # Check exact match first
        if rounded in heading_map:
            level = heading_map[rounded]
            if level == 0:
                return ""  # Body text, no annotation needed
            return f"H{level}"

        # Check closest match within 1pt
        for size, level in heading_map.items():
            if abs(rounded - size) <= 1.0 and level > 0:
                return f"H{level}"

        return ""

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into chunks of roughly _MAX_WORDS_PER_CHUNK words."""
        words = text.split()
        if len(words) <= _MAX_WORDS_PER_CHUNK:
            return [text]

        chunks: list[str] = []
        lines = text.split("\n")
        current: list[str] = []
        current_words = 0

        for line in lines:
            line_words = len(line.split())
            if current_words + line_words > _MAX_WORDS_PER_CHUNK and current:
                chunks.append("\n".join(current))
                current = []
                current_words = 0
            current.append(line)
            current_words += line_words

        if current:
            chunks.append("\n".join(current))

        return chunks

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str:
        """Call the vision client with the document-to-HTML prompt."""
        client = self._get_client()
        model = client.get_primary_model()

        messages = [
            {"role": "system", "content": DOCUMENT_TO_HTML_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        return await client.chat(
            model=model,
            messages=messages,
            run_id="doc_to_html",
            timeout=120.0,
        )

    # ------------------------------------------------------------------
    # Output cleaning
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_llm_output(raw: str) -> str:
        """Strip markdown code fences and broken-img artifacts.

        - Removes the LLM's typical ```html``` wrapping.
        - Strips ``<img>`` tags whose ``src`` is missing, empty, or
          whitespace-only. The LLM converter often gets text labels for
          images out of LiteParse but no actual binary, then dutifully
          generates ``<img alt="..." src="">`` tags. Those break visually
          AND trigger PopeTech's "Redundant alternative text" alert when
          multiple appear on the same page (CLU-67 quality regression
          discovered on Art103). Replaced with a visible caption
          paragraph so the descriptive text reaches students.
        """
        text = raw.strip()

        # Remove ```html ... ``` wrapping
        text = re.sub(r"^```(?:html)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

        # Remove leading/trailing backticks
        text = text.strip("`").strip()

        text = _strip_empty_src_imgs(text)
        text = _strip_garbage_headings(text)

        return text

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_text_fallback(text: str, title: str) -> str:
        """Wrap plain text in <p> tags as a degraded fallback."""
        if not text.strip():
            return ""

        paragraphs = [
            line.strip()
            for line in text.split("\n")
            if line.strip()
        ]

        parts: list[str] = []
        if title:
            parts.append(f"<h2>{title}</h2>")

        for para in paragraphs:
            parts.append(f"<p>{para}</p>")

        return "\n".join(parts)

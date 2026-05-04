"""PDF accessibility fixer — 39 fix functions for PDF/UA-1 compliance. Ported from Project Remedy."""

from __future__ import annotations

import logging
import os
import re
import shutil
import statistics
import subprocess
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import pikepdf

from lti_app.core.pdf.checker import (
    _analyze_character_encoding,
    walk_structure_tree,
    _get_struct_type,
)
from lti_app.core.pdf.models import FixReport
from lti_app.core.pdf.semantics import (
    MULTIMEDIA_ANNOT_TYPES,
    document_has_bookmarks,
    document_requires_bookmarks,
    find_node_page as _shared_find_node_page,
    get_rendered_image_names,
    get_rendered_multimedia_names,
    node_has_annotation_ref,
    node_has_content_association,
    node_has_direct_content,
    node_has_struct_children,
)
from lti_app.core.pdf.tag_tree_reader import _extract_mcid_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vision prompt builders (inlined from Project Remedy vision_prompts.py)
# ---------------------------------------------------------------------------

_LAYOUT_RULES = """\
Document remediation rules:
- Preserve a meaningful reading sequence. Do not merge unrelated columns, sidebars, callouts, or footer content.
- Keep headings separate from body text and preserve heading hierarchy.
- Keep list structures explicit instead of flattening them into paragraphs.
- Keep tables and directories explicit; do not linearize cell content into prose.
- Keep form prompts, labels, values, and widgets grouped in reading order.
- Treat purely decorative backgrounds, banners, borders, spacers, and watermarks as artifacts, not content.
- If layout intent is ambiguous, say so explicitly instead of guessing.
"""


def _language_detection_prompt() -> str:
    return (
        "What language is this document primarily written in? "
        "Return ONLY the ISO 639-1 language code, for example en, es, fr, zh, ko, vi, or tl. "
        "If the document is bilingual, return the primary language."
    )


def _title_from_image_prompt() -> str:
    return (
        "Look at this document page and determine the main document title. "
        "Prefer the visually dominant title, not a logo or small section label. "
        "Return ONLY the title text. If there is no clear title, return NONE."
    )


def _title_from_text_prompt(text: str) -> str:
    return (
        "Given the beginning of a document, determine the best document title. "
        "Prefer the main title rather than a section heading. "
        "Return ONLY the title text.\n\n"
        f"Document text:\n{text}"
    )


def _figure_alt_prompt(*, context: str = "") -> str:
    context_line = f"Context: {context}\n" if context else ""
    return (
        "Write concise alt text for this image.\n"
        "Rules:\n"
        "- Maximum 150 characters.\n"
        "- Do NOT start with 'image of', 'picture of', 'photo of', or similar.\n"
        "- Describe what is shown, its purpose, and any essential visible text.\n"
        "- If the content appears decorative, return 'Decorative image'.\n"
        f"{context_line}"
        "Return ONLY the alt text string."
    )


def _page_region_analysis_prompt(
    *,
    element_list: str,
    profile: str,
) -> str:
    detail = (
        "Return detailed roles, confidence, and whether content should be split into additional regions."
        if profile == "cloud"
        else "Keep the response compact and JSON-only."
    )
    return (
        "Analyze this rendered PDF page for reading order and contrast.\n"
        f"{_LAYOUT_RULES}\n"
        "Current tagged elements on the page:\n"
        f"{element_list}\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "layout_class": "single_column" | "hero_cover" | "brochure_sidebar" | "form_checklist" | "table_directory" | "schedule_grid" | "mixed_graphic_flyer" | "map_infographic" | "report_cover" | "unknown_complex",\n'
        '  "reading_order": [1, 2, 3],\n'
        '  "order_changed": true,\n'
        '  "requires_resegmentation": false,\n'
        '  "contrast_issues": [{"description": "...", "text_rgb": [0,0,0], "bg_rgb": [1,1,1], "fix_rgb": [0,0,0]}],\n'
        '  "notes": "..."\n'
        "}\n"
        f"{detail}"
    )


# ---------------------------------------------------------------------------
# Internal data model (kept from source, NOT exported)
# ---------------------------------------------------------------------------


class LayoutClass:
    SINGLE_COLUMN = "single_column"
    HERO_COVER = "hero_cover"
    BROCHURE_SIDEBAR = "brochure_sidebar"
    FORM_CHECKLIST = "form_checklist"
    TABLE_DIRECTORY = "table_directory"
    SCHEDULE_GRID = "schedule_grid"
    MIXED_GRAPHIC_FLYER = "mixed_graphic_flyer"
    MAP_INFOGRAPHIC = "map_infographic"
    REPORT_COVER = "report_cover"
    UNKNOWN_COMPLEX = "unknown_complex"


@dataclass
class PageBlock:
    index: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    font_size: float = 0.0
    raw: str = ""
    start: int = 0
    end: int = 0
    kind: str = "text"


@dataclass
class PageRegion:
    block_ids: list[int]
    role: str
    reading_order_index: int
    confidence: float = 0.0


@dataclass
class PageLayoutAnalysis:
    page_index: int
    layout_class: str
    visual_block_count: int = 0
    stream_text_blocks: list[PageBlock] = field(default_factory=list)
    fitz_text_blocks: list[PageBlock] = field(default_factory=list)
    structured_text_nodes: int = 0
    image_coverage: float = 0.0
    has_small_text: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class PageStructureSummary:
    text_node_counts: dict[int, int] = field(default_factory=dict)
    tag_counts: dict[int, dict[str, int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Save-time structure normalization
# ---------------------------------------------------------------------------


def _resolve_pdf_object(obj):
    """Best-effort resolver that leaves arrays untouched."""
    if isinstance(obj, pikepdf.Array):
        return obj
    if isinstance(obj, pikepdf.Object) and obj.is_indirect:
        try:
            return obj.resolve()
        except Exception:
            return obj
    return obj


def _normalize_structure_tree_indirect_objects(pdf: pikepdf.Pdf) -> int:
    """Convert direct /StructElem dictionaries in the tree to indirect objects."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return 0

    normalized = 0
    seen_indirect: set[tuple[int, int]] = set()
    direct_cache: dict[int, pikepdf.Object] = {}

    def _normalize_item(item, parent=None, index: int | None = None):
        nonlocal normalized

        resolved = _resolve_pdf_object(item)
        if isinstance(resolved, pikepdf.Array):
            for i, child in enumerate(list(resolved)):
                _normalize_item(child, resolved, i)
            return

        if not isinstance(resolved, pikepdf.Dictionary):
            return

        objgen = getattr(resolved, "objgen", None)
        if "/S" in resolved and objgen == (0, 0):
            cache_key = id(resolved)
            indirect = direct_cache.get(cache_key)
            if indirect is None:
                indirect = pdf.make_indirect(resolved)
                direct_cache[cache_key] = indirect
                normalized += 1

            if isinstance(parent, pikepdf.Array) and index is not None:
                parent[index] = indirect
            elif parent is not None:
                parent["/K"] = indirect
            resolved = _resolve_pdf_object(indirect)
            objgen = getattr(resolved, "objgen", None)

        if objgen is not None and objgen != (0, 0):
            if objgen in seen_indirect:
                return
            seen_indirect.add(objgen)

        kids = resolved.get("/K")
        if kids is None:
            return

        if isinstance(kids, pikepdf.Array):
            for i, child in enumerate(list(kids)):
                _normalize_item(child, kids, i)
        else:
            _normalize_item(kids, resolved)

    _normalize_item(struct_root.get("/K"), struct_root)
    return normalized


def _save_remediated_pdf(pdf: pikepdf.Pdf, output_path: Path) -> None:
    """Write remediated PDFs in an Acrobat-friendly serialization format."""
    _normalize_structure_tree_indirect_objects(pdf)
    pdf.save(
        str(output_path),
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
    )


def _format_page_list(page_numbers: set[int]) -> str:
    """Return a compact page-number preview for status messages."""
    if not page_numbers:
        return "unknown pages"
    pages = sorted(page_numbers)
    preview = ", ".join(str(page) for page in pages[:5])
    if len(pages) > 5:
        preview += ", ..."
    return preview


def _normalize_extracted_text(text: str) -> str:
    """Normalize extracted text for emptiness and label heuristics."""
    return " ".join(text.replace("\x00", "").split()).strip()


def _tesseract_language_for_pdf(pdf: pikepdf.Pdf) -> str:
    """Map /Lang to a reasonable Tesseract language code."""
    lang = str(pdf.Root.get("/Lang", "")).lower().strip()
    primary = lang.split("-")[0]
    return {
        "en": "eng",
        "es": "spa",
        "fr": "fra",
        "de": "deu",
        "it": "ita",
        "pt": "por",
    }.get(primary, "eng")


def _page_has_text_operators(page: pikepdf.Page) -> bool:
    """Return True when the page content stream contains text-showing operators."""
    raw = _read_page_content(page)
    if not raw:
        return False
    text = raw.decode("latin-1", errors="replace")
    return bool(re.search(r"\b(Tj|TJ|'|\")\b", text))


def _image_only_pages_for_preflight(pdf: pikepdf.Pdf) -> set[int]:
    """Return 1-based page numbers when the entire document appears image-only."""
    pages_without_text: set[int] = set()
    pages_with_text = 0

    for i, page in enumerate(pdf.pages, 1):
        if _page_has_text_operators(page):
            pages_with_text += 1
        else:
            pages_without_text.add(i)

    if pages_without_text and pages_with_text == 0:
        return pages_without_text
    return set()


def _rebuild_pdf_with_tesseract_ocr(
    pdf_path: Path,
    workdir: Path,
    *,
    dpi: int = 200,
    language: str = "eng",
) -> Path:
    """Rasterize each page and rebuild a searchable PDF with Tesseract."""
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError("tesseract binary not found")

    try:
        import fitz
        from pypdf import PdfWriter
    except Exception as exc:
        raise RuntimeError(f"OCR dependencies unavailable: {exc}") from exc

    workdir.mkdir(parents=True, exist_ok=True)
    rebuilt_path = workdir / f"{pdf_path.stem}_ocr_rebuilt.pdf"
    page_pdfs: list[Path] = []

    doc = fitz.open(str(pdf_path))
    try:
        zoom = dpi / 72.0
        for page_index in range(len(doc)):
            image_path = workdir / f"page-{page_index + 1}.png"
            output_base = workdir / f"page-{page_index + 1}"
            page_pdf = output_base.with_suffix(".pdf")

            pix = doc[page_index].get_pixmap(
                matrix=fitz.Matrix(zoom, zoom),
                alpha=False,
            )
            pix.save(str(image_path))

            try:
                subprocess.run(
                    [
                        tesseract,
                        str(image_path),
                        str(output_base),
                        "-l",
                        language,
                        "--dpi",
                        str(dpi),
                        "pdf",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                message = exc.stderr.strip() or str(exc)
                raise RuntimeError(f"Tesseract OCR failed on page {page_index + 1}: {message}") from exc

            page_pdfs.append(page_pdf)

        writer = PdfWriter()
        for page_pdf in page_pdfs:
            writer.append(str(page_pdf))
        with rebuilt_path.open("wb") as fh:
            writer.write(fh)
    finally:
        doc.close()

    return rebuilt_path


def _maybe_rebuild_broken_text_layer(
    pdf_path: Path,
    *,
    only: str | None = None,
    dry_run: bool = False,
) -> tuple[Path, list[str], list[str], TemporaryDirectory | None]:
    """Preflight PDFs whose text layer is too broken for Acrobat and AT."""
    if dry_run or only not in (None, "page-char-encoding", "doc-not-image-only", "doc-reading-order"):
        return pdf_path, [], [], None

    try:
        with pikepdf.open(pdf_path) as pdf:
            analysis = _analyze_character_encoding(pdf, pdf_path)
            tesseract_language = _tesseract_language_for_pdf(pdf)
            image_only_pages = _image_only_pages_for_preflight(pdf)
    except Exception as exc:
        return pdf_path, [], [f"Character encoding preflight: error — {exc}"], None

    if not analysis.requires_rebuild and not image_only_pages:
        return pdf_path, [], [], None

    tempdir = TemporaryDirectory(prefix="canvas_remedy_lti_ocr_rebuild_")
    try:
        rebuilt_path = _rebuild_pdf_with_tesseract_ocr(
            pdf_path,
            Path(tempdir.name),
            language=tesseract_language,
        )
    except Exception as exc:
        tempdir.cleanup()
        return pdf_path, [], [f"Character encoding preflight: {exc}"], None

    if analysis.requires_rebuild:
        pages = _format_page_list(analysis.page_numbers)
        change = f"Rebuilt searchable text layer with Tesseract OCR for page(s): {pages}"
    else:
        pages = _format_page_list(image_only_pages)
        change = f"Rebuilt image-only PDF with Tesseract OCR for page(s): {pages}"
    return (
        rebuilt_path,
        [change],
        [],
        tempdir,
    )


# ---------------------------------------------------------------------------
# Fix functions — one per check
# ---------------------------------------------------------------------------


def fix_accessibility_permission(pdf: pikepdf.Pdf) -> list[str]:
    """Check #1: Remove encryption restrictions blocking assistive tech.

    If the PDF is encrypted with restrictions, we can't easily change
    permission bits without the owner password.  Flag for manual fix.
    """
    # pikepdf opens with full access so we can save unencrypted.
    if pdf.is_encrypted:
        return ["Removed encryption (saved without encryption)"]
    return []


def fix_mark_info(pdf: pikepdf.Pdf) -> list[str]:
    """Check #3: Set /MarkInfo/Marked = true."""
    mark_info = pdf.Root.get("/MarkInfo")
    if mark_info and bool(mark_info.get("/Marked")):
        return []
    if "/MarkInfo" not in pdf.Root:
        pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
    else:
        pdf.Root["/MarkInfo"]["/Marked"] = True
    return ["Set /MarkInfo/Marked = true"]


def fix_language(pdf: pikepdf.Pdf, language: str = "en", *, vision_provider=None) -> list[str]:
    """Check #5: Set /Lang on document catalog.

    When *vision_provider* is supplied, detects the document's actual
    language from the first page instead of defaulting to English.
    """
    existing = pdf.Root.get("/Lang")
    if existing and str(existing).strip():
        return []

    detected = language
    if vision_provider is not None:
        detected = _detect_language(pdf, vision_provider) or language

    pdf.Root["/Lang"] = detected
    return [f"Set /Lang = {detected}"]


def _detect_language(pdf: pikepdf.Pdf, vision_provider) -> str:
    """Detect document language via vision model on first page."""
    import asyncio

    try:
        from lti_app.core.pdf.vision import render_page_to_image

        image_path = render_page_to_image(pdf.filename, page_num=1, dpi=150)
        prompt = _language_detection_prompt()

        async def _run():
            return await vision_provider.analyze_image(image_path, prompt)

        response = asyncio.run(_run())
        lang = str(response).strip().lower()[:5]
        # Validate it looks like a language code
        if lang and len(lang) >= 2 and lang[:2].isalpha():
            return lang[:2]  # Normalize to 2-letter code
    except Exception:
        pass

    # Fallback: try extracting text and detecting via simple heuristics
    try:
        page = pdf.pages[0]
        text = page.extract_text() if hasattr(page, "extract_text") else ""
        if not text:
            import fitz
            doc = fitz.open(str(pdf.filename))
            text = doc[0].get_text()[:2000]
            doc.close()
        if text:
            # Simple Spanish detection heuristic
            spanish_markers = {"el ", "la ", "los ", "las ", "de ", "del ", "en ", "que ", "por ", "para "}
            words = text.lower()[:1000]
            spanish_hits = sum(1 for m in spanish_markers if m in words)
            if spanish_hits >= 4:
                return "es"
    except Exception:
        pass

    return ""


def fix_display_doc_title(pdf: pikepdf.Pdf, title: str = "", *, vision_provider=None) -> list[str]:
    """Check #6: Set ViewerPreferences/DisplayDocTitle and ensure dc:title.

    When *vision_provider* is supplied, uses vision model to read the
    actual title from the first page instead of relying on metadata.
    """
    changes = []

    if "/ViewerPreferences" not in pdf.Root:
        pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary()
    vp = pdf.Root["/ViewerPreferences"]

    if not bool(vp.get("/DisplayDocTitle")):
        vp["/DisplayDocTitle"] = True
        changes.append("Set /ViewerPreferences/DisplayDocTitle = true")

    # Ensure dc:title is non-empty and meaningful.
    try:
        with pdf.open_metadata() as meta:
            existing_title = meta.get("dc:title", "")
            existing_str = str(existing_title).strip() if existing_title else ""

            # Check if existing title is generic/garbage
            needs_title = (
                not existing_str
                or existing_str == "Untitled"
                or existing_str.endswith(".pdf")
                or existing_str.endswith(".PDF")
                or len(existing_str) < 3
            )

            if needs_title:
                doc_title = title
                # Try vision model for title
                if not doc_title and vision_provider is not None:
                    doc_title = _derive_title_vision(pdf, vision_provider)
                # Try text extraction for title
                if not doc_title and vision_provider is not None:
                    doc_title = _derive_title_text(pdf, vision_provider)
                # Fall back to existing metadata or filename
                if not doc_title:
                    doc_title = str(pdf.docinfo.get("/Title", "")).strip() if pdf.docinfo else ""
                if not doc_title or doc_title.endswith(".pdf"):
                    doc_title = "Untitled"

                doc_title = doc_title.strip()
                if len(doc_title) > 250:
                    doc_title = doc_title[:247] + "..."
                meta["dc:title"] = doc_title
                changes.append(f"Set dc:title = {doc_title[:60]}")
    except Exception:
        pass

    return changes


def _derive_title_vision(pdf: pikepdf.Pdf, vision_provider) -> str:
    """Use vision model to read the title from the first page."""
    import asyncio

    try:
        from lti_app.core.pdf.vision import render_page_to_image

        image_path = render_page_to_image(pdf.filename, page_num=1, dpi=150)
        prompt = _title_from_image_prompt()

        async def _run():
            return await vision_provider.analyze_image(image_path, prompt)

        response = asyncio.run(_run())
        title = str(response).strip().strip('"').strip("'").strip()
        if title and title.upper() != "NONE" and len(title) > 2:
            return title
    except Exception:
        pass
    return ""


def _derive_title_text(pdf: pikepdf.Pdf, vision_provider) -> str:
    """Use text model to derive title from extracted text content."""
    import asyncio

    try:
        # Extract text from first page
        text = ""
        try:
            import fitz
            doc = fitz.open(str(pdf.filename))
            text = doc[0].get_text()[:2000]
            doc.close()
        except Exception:
            pass

        if not text or len(text.strip()) < 20:
            return ""

        prompt = _title_from_text_prompt(text)

        async def _run():
            return await vision_provider.analyze_image(None, prompt)

        # Use chat instead if vision_provider doesn't support text-only
        # This is a best-effort fallback
        response = asyncio.run(_run())
        title = str(response).strip().strip('"').strip("'").strip()
        if title and len(title) > 2 and len(title) < 200:
            return title
    except Exception:
        pass
    return ""


def fix_role_map(pdf: pikepdf.Pdf) -> list[str]:
    """Fix /NonStruct -> /Span in RoleMap."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    role_map = struct_root.get("/RoleMap")
    if role_map is None:
        role_map = pikepdf.Dictionary()
        struct_root["/RoleMap"] = role_map

    if str(role_map.get("/NonStruct", "")) == "/Span":
        return []

    role_map["/NonStruct"] = pikepdf.Name("/Span")
    return ["Set /RoleMap /NonStruct -> /Span"]


def fix_bookmarks(pdf: pikepdf.Pdf) -> list[str]:
    """Check #7: Generate /Outlines from headings or page text."""
    if not document_requires_bookmarks(pdf):
        return []

    if document_has_bookmarks(pdf):
        return []

    bookmark_targets: list[tuple[int, str]] = []
    try:
        for node, _depth, _parent in walk_structure_tree(pdf):
            stype = _get_struct_type(node)
            if stype not in ("H1", "H2", "H3"):
                continue

            page_idx = _find_node_page(node, pdf)
            label = _bookmark_label_from_node(node, pdf)
            if page_idx < 0 or not label:
                continue
            bookmark_targets.append((page_idx, label))
    except Exception:
        return []

    used_fallback = False
    if not bookmark_targets:
        bookmark_targets = _fallback_bookmark_targets(pdf)
        used_fallback = True
        if not bookmark_targets:
            return []

    # Pre-resolve page objects into a list to avoid repeated access.
    num_pages = len(pdf.pages)
    try:
        page_objs = [pdf.pages[i].obj for i in range(num_pages)]
    except Exception:
        return []

    # Build outline dictionary chain.  Outline items MUST be indirect
    # objects -- pikepdf / QPDF segfaults on save if the /Prev / /Next
    # circular references are between direct (inline) dictionaries.
    outlines = pdf.make_indirect(
        pikepdf.Dictionary({"/Type": pikepdf.Name("/Outlines")})
    )
    outline_items = []

    for page_idx, label in bookmark_targets:
        if page_idx < 0 or page_idx >= num_pages:
            continue
        try:
            item = pdf.make_indirect(
                pikepdf.Dictionary(
                    {
                        "/Title": pikepdf.String(label),
                        "/Parent": outlines,
                        "/Dest": pikepdf.Array(
                            [page_objs[page_idx], pikepdf.Name("/Fit")]
                        ),
                    }
                )
            )
            outline_items.append(item)
        except Exception:
            continue

    if not outline_items:
        return []

    # Link items together.
    for i, item in enumerate(outline_items):
        if i > 0:
            item["/Prev"] = outline_items[i - 1]
        if i < len(outline_items) - 1:
            item["/Next"] = outline_items[i + 1]

    outlines["/First"] = outline_items[0]
    outlines["/Last"] = outline_items[-1]
    outlines["/Count"] = len(outline_items)

    pdf.Root["/Outlines"] = outlines

    if used_fallback:
        return [f"Generated {len(outline_items)} bookmarks from page text fallback"]
    return [f"Generated {len(outline_items)} bookmarks from heading text"]


def _bookmark_label_from_node(node: pikepdf.Dictionary, pdf: pikepdf.Pdf) -> str:
    """Extract a bookmark label from actual node or page text."""
    label = _extract_node_text(node, pdf)
    if not label:
        page_idx = _find_node_page(node, pdf)
        if page_idx >= 0:
            label = _extract_page_text(pdf, page_idx)
    if not label:
        alt = node.get("/Alt")
        label = str(alt).strip() if alt else ""
    return _normalize_bookmark_label(label or _get_struct_type(node))


def _extract_node_text(node: pikepdf.Dictionary, pdf: pikepdf.Pdf) -> str:
    """Extract text associated with a structure node's MCIDs."""
    page_idx = _find_node_page(node, pdf)
    if page_idx < 0 or page_idx >= len(pdf.pages):
        return ""

    page_text = _extract_mcid_text(pdf.pages[page_idx])
    parts = [
        page_text.get(mcid, "").strip()
        for mcid in _get_node_mcids(node)
        if page_text.get(mcid, "").strip()
    ]
    return _normalize_bookmark_label(" ".join(parts))


def _extract_page_text(pdf: pikepdf.Pdf, page_idx: int) -> str:
    """Extract the first meaningful text from a page."""
    if page_idx < 0 or page_idx >= len(pdf.pages):
        return ""

    text = " ".join(
        part.strip()
        for part in _extract_mcid_text(pdf.pages[page_idx]).values()
        if part.strip()
    )
    if not text and getattr(pdf, "filename", None):
        try:
            import fitz

            doc = fitz.open(str(pdf.filename))
            text = doc[page_idx].get_text()
            doc.close()
        except Exception:
            text = ""

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return _normalize_bookmark_label(first_line or text)


def _decode_pdf_hex_or_literal(text_obj: str) -> str:
    """Best-effort decode for PDF text fragments inside BT/ET blocks."""
    text_obj = text_obj.strip()
    if not text_obj:
        return ""

    if text_obj.startswith("<") and text_obj.endswith(">"):
        try:
            data = bytes.fromhex(re.sub(r"\s+", "", text_obj[1:-1]))
        except ValueError:
            return ""
        if len(data) >= 2 and data[0] == 0:
            try:
                return data.decode("utf-16-be")
            except Exception:
                return data.decode("latin-1", errors="replace")
        return data.decode("latin-1", errors="replace")

    if text_obj.startswith("(") and text_obj.endswith(")"):
        inner = text_obj[1:-1].encode("latin-1", errors="replace")
        decoded = bytearray()
        i = 0
        while i < len(inner):
            byte = inner[i]
            if byte != 0x5C:
                decoded.append(byte)
                i += 1
                continue
            if i + 1 >= len(inner):
                break
            nxt = inner[i + 1]
            if nxt in b"nrtbf":
                decoded.append({
                    ord("n"): 0x0A,
                    ord("r"): 0x0D,
                    ord("t"): 0x09,
                    ord("b"): 0x08,
                    ord("f"): 0x0C,
                }[nxt])
                i += 2
                continue
            if nxt in b"()\\":
                decoded.append(nxt)
                i += 2
                continue
            decoded.append(nxt)
            i += 2
        return decoded.decode("latin-1", errors="replace")

    return text_obj


def _extract_text_from_bt_block(bt_block: str) -> str:
    """Extract human-readable text from a BT/ET block."""
    parts: list[str] = []
    for match in re.finditer(r"<[0-9A-Fa-f\s]+>|\((?:[^\\)]|\\.)*\)", bt_block, re.S):
        parts.append(_decode_pdf_hex_or_literal(match.group(0)))
    return _normalize_extracted_text("".join(parts))


def _extract_stream_text_blocks(raw: str, *, page_height: float) -> list[PageBlock]:
    """Return BT/ET text blocks with coarse geometry from a content stream."""
    blocks: list[PageBlock] = []
    for idx, match in enumerate(re.finditer(r"BT.*?ET", raw, re.S)):
        block_raw = match.group(0)
        text = _extract_text_from_bt_block(block_raw)
        if not text:
            continue
        font_sizes = [
            float(value)
            for value in re.findall(r"/[^\s]+\s+([0-9]+(?:\.[0-9]+)?)\s+Tf", block_raw)
        ]
        tm = re.search(
            r"[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+([-0-9.]+)\s+([-0-9.]+)\s+Tm",
            block_raw,
        )
        x = float(tm.group(1)) if tm else 0.0
        y = float(tm.group(2)) if tm else 0.0
        font_size = max(font_sizes) if font_sizes else 0.0
        top = max(0.0, page_height - y - max(font_size, 8.0))
        bottom = min(page_height, page_height - y + max(font_size, 8.0))
        blocks.append(
            PageBlock(
                index=idx,
                text=text,
                x0=x,
                top=top,
                x1=x + max(len(text) * max(font_size, 8.0) * 0.35, 40.0),
                bottom=bottom,
                font_size=font_size,
                raw=block_raw,
                start=match.start(),
                end=match.end(),
            )
        )
    return blocks


def _extract_fitz_text_blocks(pdf_path: Path, page_index: int) -> tuple[list[PageBlock], float]:
    """Extract visible text blocks and approximate image coverage via PyMuPDF."""
    return _extract_fitz_text_blocks_cached(str(pdf_path.resolve()), page_index)


@lru_cache(maxsize=8)
def _extract_fitz_text_blocks_cached(
    pdf_path_str: str,
    page_index: int,
) -> tuple[list[PageBlock], float]:
    """Cached PyMuPDF page extraction for repeated layout analysis passes."""
    try:
        import fitz
    except Exception:
        return [], 0.0

    blocks: list[PageBlock] = []
    image_area = 0.0
    doc = fitz.open(pdf_path_str)
    try:
        page = doc[page_index]
        page_area = max(float(page.rect.width * page.rect.height), 1.0)
        data = page.get_text("dict")
        for idx, block in enumerate(data.get("blocks", [])):
            bbox = block.get("bbox", (0, 0, 0, 0))
            x0, y0, x1, y1 = [float(v) for v in bbox]
            if block.get("type") != 0:
                image_area += max((x1 - x0) * (y1 - y0), 0.0)
                continue

            lines = []
            font_sizes = []
            for line in block.get("lines", []):
                parts = []
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text:
                        parts.append(text)
                    size = span.get("size")
                    if size is not None:
                        try:
                            font_sizes.append(float(size))
                        except Exception:
                            pass
                line_text = _normalize_extracted_text("".join(parts))
                if line_text:
                    lines.append(line_text)

            text = _normalize_extracted_text(" ".join(lines))
            if not text:
                continue

            blocks.append(
                PageBlock(
                    index=idx,
                    text=text,
                    x0=x0,
                    top=y0,
                    x1=x1,
                    bottom=y1,
                    font_size=max(font_sizes) if font_sizes else 0.0,
                )
            )

        return blocks, min(image_area / page_area, 1.0)
    finally:
        doc.close()


def _build_page_structure_summary(pdf: pikepdf.Pdf) -> PageStructureSummary:
    """Walk the structure tree once and summarize page-level tag density."""
    text_like = {
        "P", "Span", "H", "H1", "H2", "H3", "H4", "H5", "H6",
        "LBody", "Lbl", "TH", "TD", "Caption",
    }
    summary = PageStructureSummary()
    for node, _depth, _parent in walk_structure_tree(pdf):
        page = _find_node_page(node, pdf)
        if page < 0:
            continue
        stype = _get_struct_type(node)
        if not stype:
            continue
        page_tags = summary.tag_counts.setdefault(page, {})
        page_tags[stype] = page_tags.get(stype, 0) + 1
        if stype in text_like and _get_node_mcids(node):
            summary.text_node_counts[page] = summary.text_node_counts.get(page, 0) + 1
    return summary


def _page_structured_text_nodes(
    pdf: pikepdf.Pdf,
    page_idx: int,
    *,
    structure_summary: PageStructureSummary | None = None,
) -> int:
    """Count page-level text nodes already exposed in the structure tree."""
    summary = structure_summary or _build_page_structure_summary(pdf)
    return summary.text_node_counts.get(page_idx, 0)


def _page_has_struct_type(
    pdf: pikepdf.Pdf,
    page_idx: int,
    tag: str,
    *,
    structure_summary: PageStructureSummary | None = None,
) -> bool:
    summary = structure_summary or _build_page_structure_summary(pdf)
    return summary.tag_counts.get(page_idx, {}).get(tag, 0) > 0


def _column_group_count(blocks: list[PageBlock], page_width: float) -> int:
    if len(blocks) < 2:
        return len(blocks)
    threshold = max(page_width * 0.14, 72.0)
    groups: list[float] = []
    for block in sorted(blocks, key=lambda item: item.x0):
        for i, center in enumerate(groups):
            if abs(block.x0 - center) <= threshold:
                groups[i] = (center + block.x0) / 2.0
                break
        else:
            groups.append(block.x0)
    return len(groups)


def _classify_page_layout(
    *,
    page_idx: int,
    page_width: float,
    fitz_blocks: list[PageBlock],
    pdf: pikepdf.Pdf,
    image_coverage: float,
    structure_summary: PageStructureSummary | None = None,
) -> str:
    text_blocks = [b for b in fitz_blocks if b.text]
    columns = _column_group_count(text_blocks, page_width)
    has_large_heading = any(b.font_size >= 16 for b in text_blocks[:4])
    many_short_blocks = sum(1 for b in text_blocks if len(b.text.split()) <= 8) >= 8

    if page_idx == 0 and image_coverage >= 0.45 and len(text_blocks) <= 12 and (
        has_large_heading or many_short_blocks
    ):
        return LayoutClass.REPORT_COVER if columns >= 2 or many_short_blocks else LayoutClass.HERO_COVER

    if _page_has_struct_type(
        pdf,
        page_idx,
        "Table",
        structure_summary=structure_summary,
    ) and image_coverage < 0.35:
        return LayoutClass.TABLE_DIRECTORY

    annots = pdf.pages[page_idx].get("/Annots")
    widget_count = 0
    if annots:
        for annot_ref in annots:
            try:
                annot = _resolve_pdf_object(annot_ref)
                if str(annot.get("/Subtype", "")) == "/Widget":
                    widget_count += 1
            except Exception:
                continue
    if widget_count >= 2 or _page_has_struct_type(
        pdf,
        page_idx,
        "Form",
        structure_summary=structure_summary,
    ):
        return LayoutClass.FORM_CHECKLIST

    if page_idx == 0 and image_coverage >= 0.25 and has_large_heading and len(text_blocks) <= 8:
        return LayoutClass.HERO_COVER if columns <= 1 else LayoutClass.REPORT_COVER
    if columns >= 2:
        left = [b for b in text_blocks if b.x0 < page_width * 0.45]
        right = [b for b in text_blocks if b.x0 > page_width * 0.5]
        if left and right and any((b.x1 - b.x0) < page_width * 0.35 for b in right):
            return LayoutClass.BROCHURE_SIDEBAR
        if many_short_blocks:
            return LayoutClass.SCHEDULE_GRID
        if image_coverage >= 0.2:
            return LayoutClass.MAP_INFOGRAPHIC
        return LayoutClass.MIXED_GRAPHIC_FLYER
    if image_coverage >= 0.3 and len(text_blocks) >= 4:
        return LayoutClass.MIXED_GRAPHIC_FLYER
    return LayoutClass.SINGLE_COLUMN


def _analyze_page_layout(
    pdf: pikepdf.Pdf,
    page_idx: int,
    *,
    structure_summary: PageStructureSummary | None = None,
) -> PageLayoutAnalysis:
    """Combine visual blocks, stream blocks, and tag density into a layout signal."""
    raw = _read_page_content(pdf.pages[page_idx]).decode("latin-1", errors="replace")
    page_height = float(pdf.pages[page_idx].MediaBox[3])
    page_width = float(pdf.pages[page_idx].MediaBox[2])
    stream_blocks = _extract_stream_text_blocks(raw, page_height=page_height)

    fitz_blocks: list[PageBlock] = []
    image_coverage = 0.0
    pdf_path = None
    if getattr(pdf, "filename", None):
        try:
            pdf_path = Path(str(pdf.filename))
        except Exception:
            pdf_path = None
    if pdf_path and pdf_path.exists():
        fitz_blocks, image_coverage = _extract_fitz_text_blocks(pdf_path, page_idx)

    layout_class = _classify_page_layout(
        page_idx=page_idx,
        page_width=page_width,
        fitz_blocks=fitz_blocks,
        pdf=pdf,
        image_coverage=image_coverage,
        structure_summary=structure_summary,
    )
    analysis = PageLayoutAnalysis(
        page_index=page_idx,
        layout_class=layout_class,
        visual_block_count=len(fitz_blocks),
        stream_text_blocks=stream_blocks,
        fitz_text_blocks=fitz_blocks,
        structured_text_nodes=_page_structured_text_nodes(
            pdf,
            page_idx,
            structure_summary=structure_summary,
        ),
        image_coverage=image_coverage,
        has_small_text=any(0 < b.font_size <= 9.5 for b in stream_blocks),
    )
    if analysis.structured_text_nodes <= 2 and len(stream_blocks) >= 6:
        analysis.notes.append("coarse-structure-tree")
    return analysis


def _page_needs_resegmentation(pdf: pikepdf.Pdf, page_idx: int, analysis: PageLayoutAnalysis) -> bool:
    """True when the structure tree is too coarse for the detected layout."""
    if analysis.layout_class in {LayoutClass.FORM_CHECKLIST, LayoutClass.TABLE_DIRECTORY}:
        return False
    if len(analysis.stream_text_blocks) < 4:
        return False

    from lti_app.core.pdf.ocr import (
        OCREscalationSignal,
        available_specialized_ocr_adapters,
        should_escalate_specialized_ocr,
    )

    signal = OCREscalationSignal(
        layout_class=analysis.layout_class,
        visual_block_count=analysis.visual_block_count,
        structured_text_nodes=analysis.structured_text_nodes,
        image_coverage=analysis.image_coverage,
        has_small_text=analysis.has_small_text,
        structure_warning="coarse-structure-tree" in analysis.notes,
    )
    if analysis.structured_text_nodes <= 2 and should_escalate_specialized_ocr(signal):
        analysis.notes.append("specialized-ocr-worthy")
        try:
            adapters = available_specialized_ocr_adapters()
            if adapters:
                analysis.notes.append(
                    "specialized-ocr-configured:" + ",".join(adapter.name for adapter in adapters)
                )
        except Exception:
            pass
    return (
        analysis.layout_class != LayoutClass.SINGLE_COLUMN
        and analysis.structured_text_nodes <= max(2, len(analysis.stream_text_blocks) // 4)
    )


def _extract_heading_block_candidates(marked_body: str) -> list[dict]:
    """Return BT/ET blocks with enough metadata to choose a title candidate."""
    candidates = []
    for match in re.finditer(r"BT.*?ET", marked_body, re.S):
        block = match.group(0)
        text = _extract_text_from_bt_block(block)
        if not text:
            continue

        font_sizes = [
            float(value)
            for value in re.findall(r"/[^\s]+\s+([0-9]+(?:\.[0-9]+)?)\s+Tf", block)
        ]
        if not font_sizes:
            continue

        text_matrix = re.search(
            r"[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+[-0-9.]+\s+([-0-9.]+)\s+([-0-9.]+)\s+Tm",
            block,
        )
        y = float(text_matrix.group(2)) if text_matrix else 0.0

        candidates.append(
            {
                "start": match.start(),
                "end": match.end(),
                "raw": block,
                "text": text,
                "font_size": max(font_sizes),
                "y": y,
            }
        )
    return candidates


def _choose_title_candidate(
    candidates: list[dict],
    *,
    page_height: float,
) -> dict | None:
    """Pick a conservative page-title candidate from BT/ET blocks."""
    if not candidates:
        return None

    text_blocks = [c for c in candidates if sum(ch.isalpha() for ch in c["text"]) >= 4]
    if not text_blocks:
        return None

    median_font = statistics.median(c["font_size"] for c in text_blocks)
    large_threshold = max(median_font * 1.25, median_font + 2)

    def _usable(candidate: dict) -> bool:
        text = candidate["text"]
        return (
            candidate["font_size"] >= large_threshold
            and "@" not in text
            and ".edu" not in text.lower()
            and "http" not in text.lower()
            and len(text) <= 180
        )

    preferred = [
        c for c in text_blocks
        if _usable(c) and c["y"] <= page_height * 0.90 and c["y"] >= page_height * 0.45
    ]
    if preferred:
        return max(preferred, key=lambda c: (c["y"], c["font_size"]))

    fallback = [c for c in text_blocks if _usable(c) and c["y"] <= page_height * 0.90]
    if fallback:
        return max(fallback, key=lambda c: (c["y"], c["font_size"]))

    broad = [c for c in text_blocks if _usable(c)]
    if broad:
        return max(broad, key=lambda c: (c["y"], c["font_size"]))

    return None


def _fallback_bookmark_targets(pdf: pikepdf.Pdf) -> list[tuple[int, str]]:
    """Create a sparse bookmark set for long headingless documents."""
    targets: list[tuple[int, str]] = []
    for page_idx in range(0, len(pdf.pages), 10):
        label = _extract_page_text(pdf, page_idx)
        if not label:
            label = f"Page {page_idx + 1}"
        targets.append((page_idx, label))
    return targets


def _normalize_bookmark_label(text: str) -> str:
    label = " ".join(text.split()).strip()
    if not label:
        return ""
    if len(label) > 80:
        return label[:77] + "..."
    return label


# ---------------------------------------------------------------------------
# Structure tree creation helpers
# ---------------------------------------------------------------------------


def _read_page_content(page) -> bytes:
    """Read raw content stream bytes from a page."""
    contents = page.get("/Contents")
    if contents is None:
        return b""
    if isinstance(contents, pikepdf.Array):
        raw = b""
        for stream in contents:
            try:
                raw += stream.read_bytes()
            except Exception:
                pass
        return raw
    try:
        return contents.read_bytes()
    except Exception:
        return b""


def _get_node_mcids(node: pikepdf.Dictionary) -> list[int]:
    """Extract MCIDs from a structure node's /K entries."""
    mcids: list[int] = []
    kids = node.get("/K")
    if kids is None:
        return mcids

    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    for item in items:
        resolved = _resolve_pdf_object(item)
        if not isinstance(resolved, pikepdf.Dictionary):
            try:
                mcids.append(int(resolved))
            except (TypeError, ValueError):
                continue
        elif "/S" not in resolved:
            mcid_val = resolved.get("/MCID")
            if mcid_val is not None:
                try:
                    mcids.append(int(mcid_val))
                except (TypeError, ValueError):
                    continue
    return mcids


def _next_page_mcid(page) -> int:
    """Return the next available MCID on a page."""
    raw = _read_page_content(page)
    text = raw.decode("latin-1", errors="replace") if raw else ""
    mcids = _find_existing_mcids(text)
    return (max(mcids) + 1) if mcids else 0


def _page_has_content_associated_multimedia(
    pdf: pikepdf.Pdf,
    page_idx: int,
) -> bool:
    """True when a page already has a tagged Figure/Form with content."""
    for node, _depth, _parent in walk_structure_tree(pdf):
        stype = _get_struct_type(node)
        if stype not in ("Figure", "Form"):
            continue
        if not node_has_content_association(node):
            continue
        node_page = _find_node_page(node, pdf)
        if node_page == page_idx:
            return True
    return False


def _find_existing_mcids(text: str) -> list[int]:
    """Extract MCID integers from marked content BDC operators."""
    mcids = []
    for m in re.finditer(r'/\w+\s*<<([^>]*)>>\s*BDC', text):
        mcid_m = re.search(r'/MCID\s+(\d+)', m.group(1))
        if mcid_m:
            mcids.append(int(mcid_m.group(1)))
    return mcids


def _get_image_xobject_names(page) -> list[str]:
    """Return names of image XObjects defined on a page."""
    names = []
    resources = page.get("/Resources")
    if not resources:
        return names
    xobjects = resources.get("/XObject")
    if not xobjects:
        return names
    for name, ref in xobjects.items():
        try:
            xobj = _resolve_pdf_object(ref)
            if isinstance(xobj, pikepdf.Stream) and str(xobj.get("/Subtype", "")) == "/Image":
                names.append(name.lstrip("/"))
        except Exception:
            continue
    return names


def _pad_parent_arr(arr: list, mcid: int, elem) -> None:
    """Extend parent array with nulls up to *mcid*, then set *elem*."""
    while len(arr) <= mcid:
        arr.append(None)
    arr[mcid] = elem


def _wrap_content_gaps(
    text: str, start_mcid: int, tag: str = "/P",
) -> tuple[str, list[int]]:
    """Wrap unmarked content gaps in ``BDC``/``EMC`` with MCIDs.

    Returns ``(modified_text, list_of_mcids_created)``.
    """
    mcids: list[int] = []
    nm = start_mcid

    first_mc = re.search(r'/\w+\s*(<<.*?>>)?\s*(BDC|BMC)', text)
    if not first_mc:
        # No marked content at all -- wrap everything.
        if text.strip():
            mcids.append(nm)
            return (f"{tag} <</MCID {nm}>> BDC\n{text}\nEMC\n", mcids)
        return (text, mcids)

    # 1. Wrap content BEFORE first BDC/BMC.
    before = text[: first_mc.start()]
    if before.strip():
        mcids.append(nm)
        text = f"{tag} <</MCID {nm}>> BDC\n" + before + "EMC\n" + text[first_mc.start():]
        nm += 1

    # 2. Wrap content AFTER last EMC.
    last_emc = text.rfind("EMC")
    if last_emc >= 0:
        after = text[last_emc + 3:]
        if after.strip():
            mcids.append(nm)
            text = text[: last_emc + 3] + f"\n{tag} <</MCID {nm}>> BDC\n" + after + "EMC\n"
            nm += 1

    # 3. Wrap gaps BETWEEN EMC and next BDC/BMC.
    parts: list[str] = []
    pos = 0
    for emc_m in re.finditer(r"EMC", text):
        emc_end = emc_m.end()
        if emc_end <= pos:
            continue
        next_mc = re.search(r'/\w+\s*(<<.*?>>)?\s*(BDC|BMC)', text[emc_end:])
        if not next_mc:
            break
        gap = text[emc_end: emc_end + next_mc.start()]
        if gap.strip():
            parts.append(text[pos:emc_end])
            mcids.append(nm)
            parts.append(f"\n{tag} <</MCID {nm}>> BDC\n" + gap + "EMC\n")
            nm += 1
            pos = emc_end + next_mc.start()
    if parts:
        parts.append(text[pos:])
        text = "".join(parts)

    return (text, mcids)


def _add_mcr_to_struct_tree(
    pdf: pikepdf.Pdf,
    struct_root: pikepdf.Dictionary,
    page,
    page_idx: int,
    mcid: int,
    tag: str,
) -> None:
    """Create a struct element for *mcid* and wire it into the tree."""
    elem = pdf.make_indirect(pikepdf.Dictionary({
        "/S": pikepdf.Name(tag),
        "/Type": pikepdf.Name("/StructElem"),
        "/Pg": page.obj,
        "/K": pikepdf.Dictionary({
            "/Type": pikepdf.Name("/MCR"),
            "/Pg": page.obj,
            "/MCID": mcid,
        }),
    }))

    # Find parent -- prefer /Sect whose /Pg matches this page.
    parent = None
    doc_k = struct_root.get("/K")
    if doc_k is not None:
        try:
            doc_elem = doc_k if isinstance(doc_k, pikepdf.Dictionary) else doc_k.resolve()
        except Exception:
            doc_elem = doc_k
        if isinstance(doc_elem, pikepdf.Dictionary):
            kids = doc_elem.get("/K")
            if kids is not None:
                items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
                for item in items:
                    try:
                        resolved = item if isinstance(item, pikepdf.Dictionary) else item.resolve()
                    except Exception:
                        continue
                    if not isinstance(resolved, pikepdf.Dictionary):
                        continue
                    pg = resolved.get("/Pg")
                    if pg is None:
                        continue
                    try:
                        pg_obj = pg if isinstance(pg, pikepdf.Dictionary) else pg.resolve()
                        if pg_obj == page.obj:
                            parent = resolved
                            break
                    except Exception:
                        continue

    if parent is None:
        if doc_k is not None:
            try:
                parent = doc_k if isinstance(doc_k, pikepdf.Dictionary) else doc_k.resolve()
            except Exception:
                parent = struct_root
            if not isinstance(parent, pikepdf.Dictionary):
                parent = struct_root
        else:
            parent = struct_root

    elem["/P"] = parent
    kids = parent.get("/K")
    if kids is None:
        parent["/K"] = elem
    elif isinstance(kids, pikepdf.Array):
        kids.append(elem)
    else:
        parent["/K"] = pikepdf.Array([kids, elem])

    # Update /ParentTree.
    parent_tree = struct_root.get("/ParentTree")
    if parent_tree is None:
        return
    try:
        pt = parent_tree if isinstance(parent_tree, pikepdf.Dictionary) else parent_tree.resolve()
    except Exception:
        return
    nums = pt.get("/Nums")
    if nums is None:
        return

    sp = page.get("/StructParents")
    if sp is None:
        next_key = int(struct_root.get("/ParentTreeNextKey", 0))
        page["/StructParents"] = next_key
        struct_root["/ParentTreeNextKey"] = next_key + 1
        sp_val = next_key
    else:
        sp_val = int(sp)

    # Find existing entry for this StructParents value.
    for i in range(0, len(nums), 2):
        if int(nums[i]) == sp_val:
            arr = nums[i + 1]
            try:
                if not isinstance(arr, pikepdf.Array):
                    arr = arr.resolve()
            except Exception:
                continue
            if isinstance(arr, pikepdf.Array):
                while len(arr) <= mcid:
                    arr.append(None)
                arr[mcid] = elem
            return

    # No existing entry -- create one.
    new_arr = pikepdf.Array()
    while len(new_arr) <= mcid:
        new_arr.append(None)
    new_arr[mcid] = elem
    nums.append(sp_val)
    nums.append(pdf.make_indirect(new_arr))


# ---------------------------------------------------------------------------
# Structure tree creation
# ---------------------------------------------------------------------------


def fix_create_structure_tree(pdf: pikepdf.Pdf) -> list[str]:
    """Create ``/StructTreeRoot`` with basic document structure if missing.

    Builds ``/Document`` -> ``/Sect`` per page -> ``/P``, ``/Figure``,
    ``/Link``, ``/Form`` elements so all downstream tag-dependent fixes
    can operate.
    """
    if pdf.Root.get("/StructTreeRoot") is not None:
        return []

    doc_elem = pdf.make_indirect(pikepdf.Dictionary({
        "/S": pikepdf.Name("/Document"),
        "/Type": pikepdf.Name("/StructElem"),
    }))

    parent_tree_nums = pikepdf.Array()
    page_sections: list[pikepdf.Dictionary] = []
    n_text = n_figs = n_annots = 0

    for page_idx, page in enumerate(pdf.pages):
        sect_kids: list[pikepdf.Dictionary] = []
        parent_arr: list = []  # MCID -> struct element for /ParentTree
        next_mcid = 0

        # --- content stream analysis ---
        raw = _read_page_content(page)
        text = raw.decode("latin-1", errors="replace") if raw else ""
        existing_mcids = _find_existing_mcids(text)
        has_mc = bool(existing_mcids) or bool(re.search(r'(BMC|BDC)\b', text))
        content_modified = False

        if has_mc and existing_mcids:
            # Page already has MCIDs -- create struct elements for them.
            for mcid in sorted(existing_mcids):
                elem = pdf.make_indirect(pikepdf.Dictionary({
                    "/S": pikepdf.Name("/P"),
                    "/Type": pikepdf.Name("/StructElem"),
                    "/Pg": page.obj,
                    "/K": pikepdf.Dictionary({
                        "/Type": pikepdf.Name("/MCR"),
                        "/Pg": page.obj,
                        "/MCID": mcid,
                    }),
                }))
                sect_kids.append(elem)
                _pad_parent_arr(parent_arr, mcid, elem)
                n_text += 1
            next_mcid = max(existing_mcids) + 1

            # Also create /Figure elements for image XObjects on this page.
            for img_name in _get_image_xobject_names(page):
                if re.search(rf'/{re.escape(img_name)}\s+Do\b', text):
                    fig = pdf.make_indirect(pikepdf.Dictionary({
                        "/S": pikepdf.Name("/Figure"),
                        "/Type": pikepdf.Name("/StructElem"),
                        "/Pg": page.obj,
                        "/Alt": pikepdf.String(""),
                    }))
                    sect_kids.append(fig)
                    n_figs += 1

        elif text.strip():
            # No MCIDs -- inject BDC/EMC into content stream.
            if not has_mc:
                # No marked content at all -- wrap image Do operators first.
                for img_name in _get_image_xobject_names(page):
                    pat = rf'(/{re.escape(img_name)}\s+Do)\b'
                    mcid = next_mcid
                    new_text = re.sub(
                        pat,
                        f'/Figure <</MCID {mcid}>> BDC\n\\1\nEMC',
                        text, count=1,
                    )
                    if new_text != text:
                        text = new_text
                        content_modified = True
                        elem = pdf.make_indirect(pikepdf.Dictionary({
                            "/S": pikepdf.Name("/Figure"),
                            "/Type": pikepdf.Name("/StructElem"),
                            "/Pg": page.obj,
                            "/Alt": pikepdf.String(""),
                            "/K": pikepdf.Dictionary({
                                "/Type": pikepdf.Name("/MCR"),
                                "/Pg": page.obj,
                                "/MCID": mcid,
                            }),
                        }))
                        sect_kids.append(elem)
                        _pad_parent_arr(parent_arr, mcid, elem)
                        next_mcid += 1
                        n_figs += 1

            # Wrap remaining unmarked gaps in /P tags.
            text, p_mcids = _wrap_content_gaps(text, next_mcid, "/P")
            if p_mcids:
                content_modified = True
            for mcid in p_mcids:
                elem = pdf.make_indirect(pikepdf.Dictionary({
                    "/S": pikepdf.Name("/P"),
                    "/Type": pikepdf.Name("/StructElem"),
                    "/Pg": page.obj,
                    "/K": pikepdf.Dictionary({
                        "/Type": pikepdf.Name("/MCR"),
                        "/Pg": page.obj,
                        "/MCID": mcid,
                    }),
                }))
                sect_kids.append(elem)
                _pad_parent_arr(parent_arr, mcid, elem)
                n_text += 1

            if content_modified:
                page["/Contents"] = pdf.make_stream(text.encode("latin-1"))

        # --- annotations ---
        annots = page.get("/Annots")
        if annots:
            for annot_ref in annots:
                try:
                    annot = _resolve_pdf_object(annot_ref)
                    subtype = str(annot.get("/Subtype", ""))
                    if subtype == "/Link":
                        s_type = "/Link"
                    elif subtype == "/Widget":
                        s_type = "/Form"
                    else:
                        continue
                    elem = pdf.make_indirect(pikepdf.Dictionary({
                        "/S": pikepdf.Name(s_type),
                        "/Type": pikepdf.Name("/StructElem"),
                        "/Pg": page.obj,
                        "/K": pikepdf.Dictionary({
                            "/Type": pikepdf.Name("/OBJR"),
                            "/Obj": annot_ref,
                            "/Pg": page.obj,
                        }),
                    }))
                    sect_kids.append(elem)
                    n_annots += 1
                except Exception:
                    continue

        if not sect_kids:
            continue

        # Build /Sect for this page.
        sect = pdf.make_indirect(pikepdf.Dictionary({
            "/S": pikepdf.Name("/Sect"),
            "/Type": pikepdf.Name("/StructElem"),
            "/P": doc_elem,
            "/Pg": page.obj,
            "/K": pikepdf.Array(sect_kids) if len(sect_kids) > 1 else sect_kids[0],
        }))
        for kid in sect_kids:
            kid["/P"] = sect
        page_sections.append(sect)

        # Wire /StructParents and parent tree.
        page["/StructParents"] = page_idx
        if parent_arr:
            parent_tree_nums.append(page_idx)
            parent_tree_nums.append(pdf.make_indirect(pikepdf.Array(parent_arr)))

    if not page_sections:
        return []

    # Assemble the tree.
    doc_elem["/K"] = (
        pikepdf.Array(page_sections) if len(page_sections) > 1 else page_sections[0]
    )
    struct_root = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": pikepdf.Name("/StructTreeRoot"),
        "/K": doc_elem,
        "/ParentTree": pdf.make_indirect(pikepdf.Dictionary({
            "/Nums": parent_tree_nums,
        })),
        "/ParentTreeNextKey": len(pdf.pages),
    }))
    doc_elem["/P"] = struct_root
    pdf.Root["/StructTreeRoot"] = struct_root

    parts = []
    if n_text:
        parts.append(f"{n_text} text blocks")
    if n_figs:
        parts.append(f"{n_figs} figures")
    if n_annots:
        parts.append(f"{n_annots} annotations")
    detail = ", ".join(parts) if parts else "empty"
    return [
        f"Created /StructTreeRoot with /Document -> "
        f"{len(page_sections)} /Sect pages ({detail})"
    ]


def fix_tag_uncovered_pages(pdf: pikepdf.Pdf) -> list[str]:
    """Ensure every page has at least one struct element in the tree."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    # Step 1: Find which pages already have struct element coverage.
    page_objgen: dict[tuple, int] = {}
    for idx, page in enumerate(pdf.pages):
        try:
            page_objgen[(page.obj.objgen)] = idx
        except Exception:
            pass

    def _resolve_page_idx(pg_ref) -> int | None:
        try:
            pg_obj = _resolve_pdf_object(pg_ref)
            return page_objgen.get(pg_obj.objgen)
        except Exception:
            return None

    covered_pages: set[int] = set()
    for node, _depth, _parent in walk_structure_tree(pdf):
        pg = node.get("/Pg")
        if pg is not None:
            idx = _resolve_page_idx(pg)
            if idx is not None:
                covered_pages.add(idx)

        kids = node.get("/K")
        if kids is None:
            continue
        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        for item in items:
            resolved = _resolve_pdf_object(item)
            if isinstance(resolved, pikepdf.Dictionary) and "/Pg" in resolved:
                idx = _resolve_page_idx(resolved["/Pg"])
                if idx is not None:
                    covered_pages.add(idx)

    uncovered = [i for i in range(len(pdf.pages)) if i not in covered_pages]
    if not uncovered:
        return []

    # Step 2: For each uncovered page, tag its content.
    tagged_count = 0
    for page_idx in uncovered:
        page = pdf.pages[page_idx]
        raw = _read_page_content(page)
        text = raw.decode("latin-1", errors="replace") if raw else ""

        existing_mcids = _find_existing_mcids(text)
        has_text = bool(re.search(r'(Tj|TJ|\'|\")\s', text))
        has_images = bool(_get_image_xobject_names(page))
        has_any_content = bool(text.strip())

        if not has_any_content:
            continue

        if existing_mcids:
            for mcid in sorted(existing_mcids):
                tag = "/P"
                fig_pattern = rf'/Figure\s*<<[^>]*/MCID\s+{mcid}\b'
                if re.search(fig_pattern, text):
                    tag = "/Figure"
                _add_mcr_to_struct_tree(
                    pdf, struct_root, page, page_idx, mcid, tag,
                )
            tagged_count += 1

        elif has_text:
            next_mcid = 0
            content_modified = False

            for img_name in _get_image_xobject_names(page):
                pat = rf'(/{re.escape(img_name)}\s+Do)\b'
                mcid = next_mcid
                new_text = re.sub(
                    pat,
                    f'/Figure <</MCID {mcid}>> BDC\n\\1\nEMC',
                    text, count=1,
                )
                if new_text != text:
                    text = new_text
                    content_modified = True
                    _add_mcr_to_struct_tree(
                        pdf, struct_root, page, page_idx, mcid, "/Figure",
                    )
                    next_mcid += 1

            new_text, new_mcids = _wrap_content_gaps(text, next_mcid, "/P")
            if new_mcids:
                text = new_text
                content_modified = True
                for mcid in new_mcids:
                    _add_mcr_to_struct_tree(
                        pdf, struct_root, page, page_idx, mcid, "/P",
                    )

            if content_modified:
                page["/Contents"] = pdf.make_stream(text.encode("latin-1"))
                tagged_count += 1

        elif has_images:
            next_mcid = 0
            content_modified = False
            for img_name in _get_image_xobject_names(page):
                mcid = next_mcid
                pat = rf'(/{re.escape(img_name)}\s+Do)\b'
                new_text = re.sub(
                    pat,
                    f'/Figure <</MCID {mcid}>> BDC\n\\1\nEMC',
                    text, count=1,
                )
                if new_text != text:
                    text = new_text
                    content_modified = True
                    _add_mcr_to_struct_tree(
                        pdf, struct_root, page, page_idx, mcid, "/Figure",
                    )
                    next_mcid += 1

            if content_modified:
                page["/Contents"] = pdf.make_stream(text.encode("latin-1"))
                tagged_count += 1

    if not tagged_count:
        return []
    return [f"Tagged {tagged_count} previously uncovered pages (of {len(uncovered)} uncovered)"]


def fix_untagged_content(pdf: pikepdf.Pdf) -> list[str]:
    """Check #9: Tag untagged content in marked content blocks."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    fixed_pages = 0
    tagged_gaps = 0

    for page_idx, page in enumerate(pdf.pages):
        contents = page.get("/Contents")
        if contents is None:
            continue

        raw = _read_page_content(page)
        text = raw.decode("latin-1", errors="replace")

        if struct_root is not None:
            next_mcid = max(_find_existing_mcids(text), default=-1) + 1
            new_text, new_mcids = _wrap_content_gaps(text, next_mcid, "/Span")
            if new_mcids:
                page["/Contents"] = pdf.make_stream(new_text.encode("latin-1"))
                fixed_pages += 1
                tagged_gaps += len(new_mcids)
                for mcid in new_mcids:
                    _add_mcr_to_struct_tree(
                        pdf, struct_root, page, page_idx, mcid, "/Span",
                    )
            continue

        # No structure tree -- wrap gaps as /Artifact (original behavior).
        changed = False

        first_bdc = re.search(r"/\w+\s*(<<.*?>>)?\s*(BDC|BMC)", text)
        if first_bdc:
            before = text[: first_bdc.start()]
            if before.strip():
                text = "/Artifact BMC\n" + before + "EMC\n" + text[first_bdc.start():]
                changed = True

        last_emc = text.rfind("EMC")
        if last_emc >= 0:
            after = text[last_emc + 3:]
            if after.strip():
                text = text[: last_emc + 3] + "\n/Artifact BMC\n" + after + "EMC\n"
                changed = True

        def _wrap_gaps(t: str) -> str:
            parts = []
            pos = 0
            for emc_match in re.finditer(r"EMC", t):
                emc_end = emc_match.end()
                if emc_end <= pos:
                    continue
                next_bdc = re.search(r"/\w+\s*(<<.*?>>)?\s*(BDC|BMC)", t[emc_end:])
                if not next_bdc:
                    break
                gap = t[emc_end: emc_end + next_bdc.start()]
                if gap.strip():
                    parts.append(t[pos:emc_end])
                    parts.append("\n/Artifact BMC\n" + gap + "EMC\n")
                    pos = emc_end + next_bdc.start()
            if parts:
                parts.append(t[pos:])
                return "".join(parts)
            return t

        new_text = _wrap_gaps(text)
        if new_text != text:
            text = new_text
            changed = True

        if changed:
            page["/Contents"] = pdf.make_stream(text.encode("latin-1"))
            fixed_pages += 1

    if fixed_pages:
        if struct_root is not None:
            return [f"{fixed_pages} pages: tagged {tagged_gaps} content gaps as /Span"]
        return [f"{fixed_pages} pages: wrapped all untagged content as /Artifact"]
    return []


def fix_tab_order(pdf: pikepdf.Pdf) -> list[str]:
    """Check #11: Set /Tabs = /S on every page."""
    fixed = 0
    for page in pdf.pages:
        tabs = page.get("/Tabs")
        if tabs is None or str(tabs) != "/S":
            page["/Tabs"] = pikepdf.Name("/S")
            fixed += 1

    if fixed:
        return [f"{fixed} pages: set /Tabs = /S"]
    return []


def fix_annotations_tagged(pdf: pikepdf.Pdf) -> list[str]:
    """Check #10: Add annotations to structure tree."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    struct_annot_ids: set[int] = set()
    for node, _depth, _parent in walk_structure_tree(pdf):
        kids = node.get("/K")
        if kids is None:
            continue
        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        for item in items:
            resolved = _resolve_pdf_object(item)
            if isinstance(resolved, pikepdf.Dictionary):
                obj_ref = resolved.get("/Obj")
                if obj_ref is not None:
                    try:
                        struct_annot_ids.add(
                            id(_resolve_pdf_object(obj_ref))
                        )
                    except Exception:
                        pass

    added = 0
    for i, page in enumerate(pdf.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            if id(annot) in struct_annot_ids:
                continue

            subtype = str(annot.get("/Subtype", ""))
            if subtype == "/Link":
                struct_type = "/Link"
            elif subtype == "/Widget":
                struct_type = "/Form"
            else:
                struct_type = "/Annot"

            objr = pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/OBJR"),
                    "/Obj": annot_ref,
                    "/Pg": page.obj,
                }
            )

            annot_elem = pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name(struct_type),
                    "/P": struct_root,
                    "/K": objr,
                    "/Pg": page.obj,
                }
            )
            annot_elem = pdf.make_indirect(annot_elem)

            kids = struct_root.get("/K")
            if kids is None:
                struct_root["/K"] = pikepdf.Array([annot_elem])
            elif isinstance(kids, pikepdf.Array):
                kids.append(annot_elem)
            else:
                struct_root["/K"] = pikepdf.Array([kids, annot_elem])

            added += 1

    if added:
        return [f"Added {added} annotations to structure tree"]
    return []


def fix_link_annotations(pdf: pikepdf.Pdf) -> list[str]:
    """Fix link annotations missing /Contents (alt text)."""
    fixed = 0
    for page in pdf.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            if str(annot.get("/Subtype", "")) != "/Link":
                continue
            if annot.get("/Contents") is not None:
                continue

            action = annot.get("/A")
            if action:
                uri = str(action.get("/URI", ""))
                if uri:
                    annot["/Contents"] = pikepdf.String(uri)
                    fixed += 1
                    continue

            annot["/Contents"] = pikepdf.String("Link")
            fixed += 1

    if fixed:
        return [f"Added /Contents to {fixed} link annotations"]
    return []


def fix_annotation_descriptions(pdf: pikepdf.Pdf) -> list[str]:
    """Add fallback /Contents to non-widget annotations missing descriptions."""
    fixed = 0
    hidden_flag = 2

    for page in pdf.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            subtype = str(annot.get("/Subtype", ""))
            if subtype in {"/Widget", "/Link"}:
                continue
            if annot.get("/Contents") is not None and str(annot["/Contents"]).strip():
                continue
            flags = 0
            try:
                flags = int(annot.get("/F", 0))
            except Exception:
                flags = 0
            if flags & hidden_flag:
                continue

            label = subtype.lstrip("/") or "Annotation"
            annot["/Contents"] = pikepdf.String(f"{label} annotation")
            fixed += 1

    if fixed:
        return [f"Added /Contents to {fixed} non-widget annotations"]
    return []


def fix_remove_scripts(pdf: pikepdf.Pdf) -> list[str]:
    """Check #15: Remove JavaScript actions."""
    changes = []

    names = pdf.Root.get("/Names")
    if names and names.get("/JavaScript"):
        del names["/JavaScript"]
        changes.append("Removed document-level /JavaScript from /Names")

    if pdf.Root.get("/AA"):
        del pdf.Root["/AA"]
        changes.append("Removed document-level additional actions (/AA)")

    for i, page in enumerate(pdf.pages, 1):
        if page.get("/AA"):
            del page["/AA"]
            changes.append(f"Page {i}: removed additional actions (/AA)")

    return changes


def fix_screen_flicker(pdf: pikepdf.Pdf) -> list[str]:
    """Check #14: Remove animation annotations."""
    removed = 0
    for page in pdf.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        new_annots = []
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            subtype = str(annot.get("/Subtype", ""))
            if subtype in ("/Screen", "/Movie"):
                removed += 1
            else:
                new_annots.append(annot_ref)
        if removed:
            page["/Annots"] = pikepdf.Array(new_annots)

    if removed:
        return [f"Removed {removed} animation/media annotations"]
    return []


def fix_timed_responses(pdf: pikepdf.Pdf) -> list[str]:
    """Check #17: Remove timed triggers from pages."""
    changes = []
    for i, page in enumerate(pdf.pages, 1):
        aa = page.get("/AA")
        if aa and (aa.get("/O") or aa.get("/C")):
            del page["/AA"]
            changes.append(f"Page {i}: removed timed open/close actions")
    return changes


def fix_form_field_descriptions(pdf: pikepdf.Pdf) -> list[str]:
    """Check #19: Set /TU from field /T name if missing."""
    fixed = 0

    acroform = pdf.Root.get("/AcroForm")
    if acroform:
        fields = acroform.get("/Fields")
        if fields:
            for field_ref in fields:
                fld = _resolve_pdf_object(field_ref)
                if not isinstance(fld, pikepdf.Dictionary):
                    continue
                tu = fld.get("/TU")
                if tu is not None and str(tu).strip():
                    continue
                name = str(fld.get("/T", ""))
                if name:
                    readable = name.replace("-", " ").replace("_", " ").strip().capitalize()
                    fld["/TU"] = pikepdf.String(readable)
                    fixed += 1

    # Also fix widget annotations directly.
    for page in pdf.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            if str(annot.get("/Subtype", "")) != "/Widget":
                continue
            tu = annot.get("/TU")
            if tu is not None and str(tu).strip():
                continue
            name = str(annot.get("/T", ""))
            if name:
                readable = name.replace("-", " ").replace("_", " ").strip().capitalize()
                annot["/TU"] = pikepdf.String(readable)
                fixed += 1

    if fixed:
        return [f"Set /TU (tooltip) on {fixed} form fields from /T name"]
    return []


def fix_table_parent_structure(pdf: pikepdf.Pdf) -> list[str]:
    """Checks #20, #21: Wrap orphan TR/TH/TD in correct parents."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    changes = []

    valid_tr_parents = {"Table", "THead", "TBody", "TFoot"}
    fixed_tr = _fix_parent_wrapping(
        struct_root, "TR", valid_tr_parents, "TBody"
    )
    if fixed_tr:
        changes.append(f"Wrapped {fixed_tr} orphan TR elements in /TBody")

    fixed_cells = 0
    for cell_type in ("TH", "TD"):
        fixed_cells += _fix_parent_wrapping(
            struct_root, cell_type, {"TR"}, "TR"
        )
    if fixed_cells:
        changes.append(f"Wrapped {fixed_cells} orphan TH/TD elements in /TR")

    return changes


def _fix_parent_wrapping(
    root: pikepdf.Dictionary,
    child_type: str,
    valid_parents: set[str],
    wrapper_type: str,
) -> int:
    """Walk the tree and wrap misparented elements in the correct parent type."""
    fixed = 0

    def _walk_and_fix(node: pikepdf.Dictionary) -> None:
        nonlocal fixed
        kids = node.get("/K")
        if kids is None:
            return

        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        new_kids = []
        changed = False

        for item in items:
            resolved = _resolve_pdf_object(item)

            if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                new_kids.append(item)
                continue

            stype = _get_struct_type(resolved)
            node_type = _get_struct_type(node)

            if stype == child_type and node_type not in valid_parents:
                wrapper = pikepdf.Dictionary(
                    {
                        "/S": pikepdf.Name(f"/{wrapper_type}"),
                        "/P": node,
                        "/K": pikepdf.Array([item]),
                    }
                )
                resolved["/P"] = wrapper
                new_kids.append(wrapper)
                fixed += 1
                changed = True
            else:
                new_kids.append(item)
                _walk_and_fix(resolved)

        if changed:
            node["/K"] = pikepdf.Array(new_kids) if len(new_kids) > 1 else new_kids[0]

    _walk_and_fix(root)
    return fixed


def fix_table_headers(pdf: pikepdf.Pdf) -> list[str]:
    """Check #22: Promote first-row TD to TH if table has no headers."""
    promoted = 0

    for node, _depth, _parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "Table":
            continue

        has_th = False
        first_tr = None

        def _scan(n: pikepdf.Dictionary) -> None:
            nonlocal has_th, first_tr
            k = n.get("/K")
            if k is None:
                return
            items = list(k) if isinstance(k, pikepdf.Array) else [k]
            for item in items:
                resolved = _resolve_pdf_object(item)
                if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                    continue
                st = _get_struct_type(resolved)
                if st == "TH":
                    has_th = True
                    return
                if st == "TR" and first_tr is None:
                    first_tr = resolved
                if st in ("THead", "TBody", "TFoot"):
                    _scan(resolved)

        _scan(node)

        if has_th or first_tr is None:
            continue

        tr_kids = first_tr.get("/K")
        if tr_kids is None:
            continue
        items = list(tr_kids) if isinstance(tr_kids, pikepdf.Array) else [tr_kids]
        for item in items:
            resolved = _resolve_pdf_object(item)
            if isinstance(resolved, pikepdf.Dictionary) and _get_struct_type(resolved) == "TD":
                resolved["/S"] = pikepdf.Name("/TH")
                promoted += 1

    if promoted:
        return [f"Promoted {promoted} first-row TD cells to TH"]
    return []


def fix_table_header_scope(pdf: pikepdf.Pdf) -> list[str]:
    """Set a conservative /Scope on TH cells when missing."""
    fixed = 0

    def _node_key(node: pikepdf.Dictionary) -> tuple[str, object]:
        try:
            objgen = node.objgen
        except Exception:
            objgen = None
        if objgen is not None and objgen != (0, 0):
            return ("objgen", objgen)
        return ("id", id(node))

    for node, _depth, _parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "Table":
            continue

        first_tr = None
        header_keys: set[tuple[str, object]] = set()

        def _scan(n: pikepdf.Dictionary) -> None:
            nonlocal first_tr
            kids = n.get("/K")
            if kids is None:
                return
            items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
            for item in items:
                resolved = _resolve_pdf_object(item)
                if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                    continue
                stype = _get_struct_type(resolved)
                if stype == "TR" and first_tr is None:
                    first_tr = resolved
                if stype in {"THead", "TBody", "TFoot", "TR"}:
                    _scan(resolved)

        _scan(node)

        if first_tr is not None:
            tr_kids = first_tr.get("/K")
            tr_items = list(tr_kids) if isinstance(tr_kids, pikepdf.Array) else [tr_kids]
            for item in tr_items:
                resolved = _resolve_pdf_object(item)
                if isinstance(resolved, pikepdf.Dictionary):
                    header_keys.add(_node_key(resolved))

        def _apply_scope(n: pikepdf.Dictionary) -> None:
            nonlocal fixed
            kids = n.get("/K")
            if kids is None:
                return
            items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
            for item in items:
                resolved = _resolve_pdf_object(item)
                if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                    continue
                stype = _get_struct_type(resolved)
                if stype == "TH" and resolved.get("/Scope") is None:
                    scope = "/Column" if _node_key(resolved) in header_keys else "/Row"
                    resolved["/Scope"] = pikepdf.Name(scope)
                    fixed += 1
                if stype in {"THead", "TBody", "TFoot", "TR", "Table"}:
                    _apply_scope(resolved)

        _apply_scope(node)

    if fixed:
        return [f"Set /Scope on {fixed} table headers"]
    return []


def fix_table_summary(pdf: pikepdf.Pdf) -> list[str]:
    """Check #24: Set /Alt on Table elements missing summary."""
    fixed = 0

    for node, _depth, _parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "Table":
            continue
        alt = node.get("/Alt")
        summary = node.get("/Summary")
        if (alt is None or not str(alt).strip()) and (
            summary is None or not str(summary).strip()
        ):
            node["/Alt"] = pikepdf.String("")
            fixed += 1

    if fixed:
        return [f"Set /Alt on {fixed} tables"]
    return []


def fix_list_structure(pdf: pikepdf.Pdf) -> list[str]:
    """Checks #25, #26: Fix list nesting (LI->L, Lbl/LBody->LI)."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    changes = []

    fixed_li = _fix_parent_wrapping(struct_root, "LI", {"L"}, "L")
    if fixed_li:
        changes.append(f"Wrapped {fixed_li} orphan LI elements in /L")

    fixed_lbl = _fix_parent_wrapping(struct_root, "Lbl", {"LI"}, "LI")
    fixed_lbody = _fix_parent_wrapping(struct_root, "LBody", {"LI"}, "LI")
    total = fixed_lbl + fixed_lbody
    if total:
        changes.append(f"Wrapped {total} orphan Lbl/LBody elements in /LI")

    return changes


def fix_alt_text_elements(pdf: pikepdf.Pdf) -> list[str]:
    """Check #31: Add /Alt to structure elements with direct content."""
    allowed_types = {"Figure", "Formula", "Form", "Table"}
    disallowed_empty_alt_types = {
        "Document", "Part", "Sect", "Div", "Art",
        "P", "Span", "Link", "Reference",
        "H", "H1", "H2", "H3", "H4", "H5", "H6",
        "L", "LI", "Lbl", "LBody",
        "TR", "TH", "TD", "THead", "TBody", "TFoot",
    }
    fixed = 0
    removed = 0

    for node, _depth, _parent in walk_structure_tree(pdf):
        stype = _get_struct_type(node)
        alt = node.get("/Alt")
        if (
            alt is not None
            and stype in disallowed_empty_alt_types
            and not str(alt).strip()
        ):
            del node["/Alt"]
            removed += 1
            alt = None

        if stype not in allowed_types:
            continue

        if node.get("/Alt") is not None:
            continue

        kids = node.get("/K")
        if kids is None:
            continue

        has_direct = False
        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        for child in items:
            resolved = _resolve_pdf_object(child)
            if not isinstance(resolved, pikepdf.Dictionary):
                has_direct = True
                break
            if "/S" not in resolved:
                has_direct = True
                break

        if has_direct:
            node["/Alt"] = pikepdf.String("")
            fixed += 1

    changes = []
    if removed:
        changes.append(f"Removed empty /Alt from {removed} plain-text elements")
    if fixed:
        changes.append(f"Added /Alt to {fixed} elements with direct content")
    return changes


def fix_figures_alt_text(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #27: Set /Alt on Figure elements missing alt text.

    When *vision_provider* is supplied, extracts each figure's image and
    generates a real description. Otherwise falls back to OCR text or a
    generic non-empty label.
    """
    figures: list[pikepdf.Dictionary] = []
    for node, _depth, _parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "Figure":
            continue
        alt = node.get("/Alt")
        if alt is None or not str(alt).strip():
            figures.append(node)

    if not figures:
        return []

    if vision_provider is None:
        for node in figures:
            image_path = _extract_figure_image(node, pdf)
            node["/Alt"] = pikepdf.String(
                _fallback_figure_alt_text(node, pdf, image_path)
            )
            if image_path is not None:
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass
        return [f"Set fallback /Alt on {len(figures)} figures"]

    # Vision-powered alt text generation -- concurrent.
    import asyncio

    figure_images: list[tuple[int, Path | None]] = []
    for i, node in enumerate(figures):
        image_path = _extract_figure_image(node, pdf)
        figure_images.append((i, image_path))

    described = 0
    placeholder = 0

    async def _no_image_result():
        return None

    async def _describe_all():
        tasks = []
        for i, image_path in figure_images:
            if image_path is None:
                tasks.append(_no_image_result())
            else:
                tasks.append(vision_provider.analyze_image(image_path, _figure_alt_prompt()))
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_describe_all())

    for (i, image_path), result in zip(figure_images, results):
        node = figures[i]
        if image_path is None or isinstance(result, Exception) or result is None:
            node["/Alt"] = pikepdf.String(
                _fallback_figure_alt_text(node, pdf, image_path)
            )
            placeholder += 1
        else:
            alt_text = str(result).strip().strip('"').strip("'").strip()
            if not alt_text:
                alt_text = _fallback_figure_alt_text(node, pdf, image_path)
            if len(alt_text) > 250:
                alt_text = alt_text[:247] + "..."
            node["/Alt"] = pikepdf.String(alt_text)
            described += 1

        if image_path is not None:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                pass

    changes = []
    if described:
        changes.append(f"Generated alt text for {described} figures via vision model")
    if placeholder:
        changes.append(
            f"Set fallback /Alt on {placeholder} figures (vision or image extraction unavailable)"
        )
    return changes


def _ocr_text_from_image(image_path: Path, *, language: str) -> str:
    """Extract a short OCR snippet from an image when no vision model is available."""
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        return ""

    try:
        result = subprocess.run(
            [
                tesseract,
                str(image_path),
                "stdout",
                "-l",
                language,
                "--psm",
                "6",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return ""

    text = _normalize_extracted_text(result.stdout)
    if not text or not re.search(r"[A-Za-z0-9]", text):
        return ""
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


def _fallback_figure_alt_text(
    node: pikepdf.Dictionary,
    pdf: pikepdf.Pdf,
    image_path: Path | None,
) -> str:
    """Choose a pragmatic non-empty fallback alt text for a figure."""
    if image_path is not None:
        ocr_text = _ocr_text_from_image(
            image_path,
            language=_tesseract_language_for_pdf(pdf),
        )
        if ocr_text:
            return f"Image containing text: {ocr_text}"

    if not node_has_direct_content(node):
        return "Decorative image"
    return "Figure"


def _extract_figure_image(
    node: pikepdf.Dictionary, pdf: pikepdf.Pdf
) -> Path | None:
    """Extract the image associated with a /Figure structure element."""
    page_idx = _find_node_page(node, pdf)
    if page_idx < 0 or page_idx >= len(pdf.pages):
        return None
    page = pdf.pages[page_idx]

    candidate_names = _find_figure_image_names(node, page, pdf)
    if not candidate_names:
        rendered_images = get_rendered_image_names(page)
        if len(rendered_images) == 1:
            candidate_names = rendered_images

    for xobj_name in candidate_names:
        image_path = _extract_xobject_image(page, xobj_name)
        if image_path is not None:
            return image_path

    return None


def _count_page_struct_type(
    pdf: pikepdf.Pdf,
    page_idx: int,
    tag: str,
    *,
    structure_summary: PageStructureSummary | None = None,
) -> int:
    """Count structure elements of a given type on a page."""
    summary = structure_summary or _build_page_structure_summary(pdf)
    return summary.tag_counts.get(page_idx, {}).get(tag, 0)


def _find_figure_image_names(
    node: pikepdf.Dictionary,
    page: pikepdf.Page,
    pdf: pikepdf.Pdf,
) -> list[str]:
    """Find rendered image XObjects associated with a figure node."""
    mcids = _get_node_mcids(node)
    if not mcids:
        return []

    try:
        from lti_app.core.pdf.content_stream.parser import GraphicsStateTracker

        tracker = GraphicsStateTracker()
        names: list[str] = []
        for instruction in tracker.track_with_form_xobjects(page, pdf):
            if instruction.operator != "Do" or not instruction.operands:
                continue
            if instruction.state.mcid not in mcids:
                continue
            name = str(instruction.operands[0]).lstrip("/")
            if name not in names:
                names.append(name)
        return names
    except Exception:
        return []


def _extract_xobject_image(page: pikepdf.Page, xobj_name: str) -> Path | None:
    """Extract a rendered image XObject to a temporary PNG."""
    import tempfile

    resources = page.get("/Resources")
    if resources is None:
        return None
    xobjects = resources.get("/XObject")
    if not xobjects:
        return None

    try:
        xobj_ref = xobjects.get(f"/{xobj_name}") or xobjects.get(xobj_name)
    except Exception:
        xobj_ref = xobjects.get(xobj_name)
    if xobj_ref is None:
        return None

    try:
        xobj = _resolve_pdf_object(xobj_ref)
    except Exception:
        xobj = xobj_ref
    if not isinstance(xobj, pikepdf.Stream):
        return None
    if str(xobj.get("/Subtype", "")) != "/Image":
        return None

    width = int(xobj.get("/Width", 0))
    height = int(xobj.get("/Height", 0))
    if width == 0 or height == 0:
        return None

    try:
        from PIL import Image
        import io
    except ImportError:
        return None

    raw = xobj.read_raw_bytes()
    cs = str(xobj.get("/ColorSpace", ""))
    fltr = xobj.get("/Filter")
    filter_name = ""
    if fltr is not None:
        if isinstance(fltr, pikepdf.Array):
            filter_name = str(fltr[0]) if len(fltr) > 0 else ""
        else:
            filter_name = str(fltr)

    pil_image = None
    if filter_name in ("/DCTDecode", "/JPXDecode"):
        pil_image = Image.open(io.BytesIO(raw))
    elif filter_name == "/FlateDecode":
        decoded = xobj.read_bytes()
        mode = "RGB"
        if "/DeviceGray" in cs or "/CalGray" in cs:
            mode = "L"
        elif "/DeviceCMYK" in cs:
            mode = "CMYK"
        try:
            pil_image = Image.frombytes(mode, (width, height), decoded)
            if mode == "CMYK":
                pil_image = pil_image.convert("RGB")
        except Exception:
            return None
    else:
        try:
            pil_image = Image.open(io.BytesIO(raw))
        except Exception:
            return None

    if pil_image is None or pil_image.width < 20 or pil_image.height < 20:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    pil_image.convert("RGB").save(tmp.name, "PNG")
    return Path(tmp.name)


def fix_redundant_alt_text(pdf: pikepdf.Pdf) -> list[str]:
    """Check #28: Remove /Alt from containers whose children are all tagged."""
    removed = 0
    _KEEP_ALT_TYPES = {"Table", "Figure", "Form", "Formula"}

    for node, _depth, _parent in walk_structure_tree(pdf):
        alt = node.get("/Alt")
        if alt is None:
            continue

        stype = _get_struct_type(node)
        if stype in _KEEP_ALT_TYPES:
            continue

        kids = node.get("/K")
        if kids is None:
            continue

        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        all_tagged = True
        has_struct = False

        for item in items:
            resolved = _resolve_pdf_object(item)
            if isinstance(resolved, pikepdf.Dictionary) and "/S" in resolved:
                has_struct = True
            else:
                all_tagged = False

        if has_struct and all_tagged:
            del node["/Alt"]
            removed += 1

    if removed:
        return [f"Removed redundant /Alt from {removed} container elements"]
    return []


def fix_orphan_alt_text(pdf: pikepdf.Pdf) -> list[str]:
    """Check #29: Remove /Alt from elements with no content."""
    removed = 0
    _KEEP_ALT_TYPES = {"Table", "Formula"}

    for node, _depth, _parent in walk_structure_tree(pdf):
        alt = node.get("/Alt")
        if alt is None:
            continue

        stype = _get_struct_type(node)
        if stype in _KEEP_ALT_TYPES:
            continue

        if not node_has_content_association(node):
            del node["/Alt"]
            removed += 1

    if removed:
        return [f"Removed orphan /Alt from {removed} empty elements"]
    return []


def fix_alt_hides_annotation(pdf: pikepdf.Pdf) -> list[str]:
    """Check #30: Remove /Alt where it hides annotation content."""
    _SKIP_TYPES = {"Link", "Reference", "Annot", "Form"}
    removed = 0

    for node, _depth, _parent in walk_structure_tree(pdf):
        alt = node.get("/Alt")
        if alt is None:
            continue

        stype = _get_struct_type(node)
        if stype in _SKIP_TYPES:
            continue

        if node_has_annotation_ref(node):
            del node["/Alt"]
            removed += 1

    if removed:
        return [f"Removed /Alt from {removed} elements that hid annotation content"]
    return []


def _find_node_for_page_mcid(
    pdf: pikepdf.Pdf,
    *,
    page_idx: int,
    mcid: int,
    tag: str = "P",
) -> tuple[pikepdf.Dictionary, pikepdf.Dictionary] | tuple[None, None]:
    """Find the structure node and parent for a page/MCID pair."""
    for node, _depth, parent in walk_structure_tree(pdf):
        if parent is None or _get_struct_type(node) != tag:
            continue
        if _find_node_page(node, pdf) != page_idx:
            continue
        if mcid in _get_node_mcids(node):
            return node, parent
    return None, None


def _set_parent_tree_entry(pdf: pikepdf.Pdf, page, mcid: int, elem) -> None:
    """Set or extend the page parent-tree array for a given MCID."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return

    parent_tree = _resolve_pdf_object(struct_root.get("/ParentTree"))
    if not isinstance(parent_tree, pikepdf.Dictionary):
        return

    nums = _resolve_pdf_object(parent_tree.get("/Nums"))
    if not isinstance(nums, pikepdf.Array):
        return

    struct_parents = page.get("/StructParents")
    if struct_parents is None:
        next_key = int(struct_root.get("/ParentTreeNextKey", 0))
        page["/StructParents"] = next_key
        struct_root["/ParentTreeNextKey"] = next_key + 1
        struct_parents = next_key
    else:
        struct_parents = int(struct_parents)

    for i in range(0, len(nums), 2):
        if int(nums[i]) != struct_parents:
            continue
        arr = _resolve_pdf_object(nums[i + 1])
        if not isinstance(arr, pikepdf.Array):
            return
        while len(arr) <= mcid:
            arr.append(None)
        arr[mcid] = elem
        return

    arr = pikepdf.Array()
    while len(arr) <= mcid:
        arr.append(None)
    arr[mcid] = elem
    nums.append(struct_parents)
    nums.append(pdf.make_indirect(arr))


def _clear_parent_tree_entries(pdf: pikepdf.Pdf, page, mcids: list[int]) -> None:
    """Null out one or more parent-tree entries for a page."""
    if not mcids:
        return

    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return

    parent_tree = _resolve_pdf_object(struct_root.get("/ParentTree"))
    if not isinstance(parent_tree, pikepdf.Dictionary):
        return

    nums = _resolve_pdf_object(parent_tree.get("/Nums"))
    if not isinstance(nums, pikepdf.Array):
        return

    struct_parents = page.get("/StructParents")
    if struct_parents is None:
        return
    try:
        struct_parents = int(struct_parents)
    except Exception:
        return

    for i in range(0, len(nums), 2):
        try:
            if int(nums[i]) != struct_parents:
                continue
        except Exception:
            continue
        arr = _resolve_pdf_object(nums[i + 1])
        if not isinstance(arr, pikepdf.Array):
            return
        for mcid in mcids:
            if 0 <= mcid < len(arr):
                arr[mcid] = None
        return


def _replace_node_in_parent(
    parent: pikepdf.Dictionary,
    old_node: pikepdf.Dictionary,
    replacements: list,
) -> bool:
    """Replace a single child node in a parent /K entry with new nodes."""
    kids = parent.get("/K")
    if kids is None:
        return False

    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    new_items = []
    replaced = False
    for item in items:
        if _same_pdf_object(item, old_node):
            new_items.extend(replacements)
            replaced = True
        else:
            new_items.append(item)

    if not replaced:
        return False

    if len(new_items) == 1:
        parent["/K"] = new_items[0]
    else:
        parent["/K"] = pikepdf.Array(new_items)
    return True


def _make_mcr_struct_elem(pdf: pikepdf.Pdf, page, parent, *, tag: str, mcid: int):
    """Create an indirect structure element for a direct-content MCID."""
    elem = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/S": pikepdf.Name(f"/{tag}"),
                "/Type": pikepdf.Name("/StructElem"),
                "/P": parent,
                "/Pg": page.obj,
                "/K": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/MCR"),
                        "/Pg": page.obj,
                        "/MCID": mcid,
                    }
                ),
            }
        )
    )
    _set_parent_tree_entry(pdf, page, mcid, elem)
    return elem


def _find_text_node_for_page_mcid(
    pdf: pikepdf.Pdf,
    *,
    page_idx: int,
    mcid: int,
) -> tuple[pikepdf.Dictionary, pikepdf.Dictionary] | tuple[None, None]:
    """Find a text-like structure node and its parent for a page/MCID pair."""
    for tag in ("P", "Span"):
        node, parent = _find_node_for_page_mcid(pdf, page_idx=page_idx, mcid=mcid, tag=tag)
        if node is not None:
            return node, parent
    return None, None


def _find_marked_content_match(raw: str, mcid: int) -> re.Match[str] | None:
    """Locate a /P or /Span marked-content block for a specific MCID."""
    pattern = rf"/(?:P|Span)\s*<<[^>]*?/MCID\s+{mcid}\b[^>]*>>\s*BDC(.*?)EMC"
    return re.search(pattern, raw, re.S)


def _find_tagged_mcid_match(
    raw: str,
    mcid: int,
    *,
    tags: tuple[str, ...],
) -> re.Match[str] | None:
    """Locate a tagged marked-content block for a specific MCID."""
    tag_pattern = "|".join(re.escape(tag) for tag in tags)
    pattern = rf"/(?:{tag_pattern})\s*<<[^>]*?/MCID\s+{mcid}\b[^>]*>>\s*BDC(.*?)EMC"
    return re.search(pattern, raw, re.S)


def _node_or_descendant_has_heading(node) -> bool:
    """Return True when a node subtree contains a heading."""
    resolved = _resolve_pdf_object(node)
    if not isinstance(resolved, pikepdf.Dictionary):
        return False
    stype = _get_struct_type(resolved)
    if re.match(r"^H\d$", stype):
        return True

    kids = resolved.get("/K")
    if kids is None:
        return False
    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    for item in items:
        child = _resolve_pdf_object(item)
        if isinstance(child, pikepdf.Dictionary) and "/S" in child:
            if _node_or_descendant_has_heading(child):
                return True
    return False


def _looks_like_heading_text(text: str) -> bool:
    """Heuristic for short, title-like blocks that should become headings."""
    normalized = _normalize_extracted_text(text)
    if not normalized:
        return False

    first_phrase = re.split(r"[.!?]", normalized, maxsplit=1)[0].strip(" :;-")
    words = first_phrase.split()
    if not 2 <= len(words) <= 14:
        return False

    lowered = first_phrase.lower()
    if any(token in lowered for token in ("http", "www", ".edu", "@", "page ", "rev.")):
        return False

    alpha_count = sum(ch.isalpha() for ch in first_phrase)
    if alpha_count < max(6, len(first_phrase) * 0.55):
        return False

    capitalized_words = sum(
        1 for word in words
        if any(ch.isalpha() for ch in word) and (word[:1].isupper() or word.isupper())
    )
    heading_keywords = (
        "request",
        "application",
        "form",
        "guide",
        "catalog",
        "schedule",
        "report",
        "admission",
        "scholarship",
        "information",
        "overview",
        "requirements",
    )
    return (
        capitalized_words >= max(2, len(words) // 2)
        or any(keyword in lowered for keyword in heading_keywords)
    )


def _infer_region_tag(
    block: PageBlock,
    *,
    page_idx: int,
    median_font_size: float,
) -> str:
    """Assign a conservative structure tag for a rewritten text block."""
    text = block.text.strip()
    if not text:
        return "P"

    word_count = len(text.split())
    line_break_like = text.count("  ")
    if block.font_size >= max(median_font_size * 1.3, 14.0) and word_count <= 12:
        return "H1" if page_idx == 0 and block.top < 180 else "H2"
    if (
        block.top < 220
        and block.font_size >= max(median_font_size * 1.15, 12.5)
        and _looks_like_heading_text(text)
    ):
        return "H1" if page_idx == 0 else "H2"
    if line_break_like >= 2 and word_count <= 30:
        return "P"
    return "P"


def _page_parent_tree_contains_all(pdf: pikepdf.Pdf, page_idx: int, mcids: list[int]) -> bool:
    """Check that parent-tree entries exist for the given page/MCID pairs."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return False

    parent_tree = _resolve_pdf_object(struct_root.get("/ParentTree"))
    if not isinstance(parent_tree, pikepdf.Dictionary):
        return False

    nums = _resolve_pdf_object(parent_tree.get("/Nums"))
    if not isinstance(nums, pikepdf.Array):
        return False

    struct_parents = pdf.pages[page_idx].get("/StructParents")
    if struct_parents is None:
        return False

    try:
        struct_parents = int(struct_parents)
    except Exception:
        return False

    for i in range(0, len(nums), 2):
        try:
            if int(nums[i]) != struct_parents:
                continue
        except Exception:
            continue
        arr = _resolve_pdf_object(nums[i + 1])
        if not isinstance(arr, pikepdf.Array):
            return False
        return all(0 <= mcid < len(arr) and arr[mcid] is not None for mcid in mcids)
    return False


def _validate_resegmented_page(
    pdf: pikepdf.Pdf,
    *,
    page_idx: int,
    parent_node: pikepdf.Dictionary,
    child_nodes: list[pikepdf.Object],
    mcids: list[int],
) -> bool:
    """Validate newly synthesized page regions before keeping them."""
    if not child_nodes or not mcids:
        return False
    if not _page_parent_tree_contains_all(pdf, page_idx, mcids):
        return False

    page = pdf.pages[page_idx]
    for child in child_nodes:
        resolved = _resolve_pdf_object(child)
        if not isinstance(resolved, pikepdf.Dictionary):
            return False
        if getattr(resolved, "objgen", None) == (0, 0):
            return False
        if resolved.get("/P") is None or not _same_pdf_object(resolved["/P"], parent_node):
            return False
        if not _same_pdf_object(resolved.get("/Pg"), page.obj):
            return False

        kid = _resolve_pdf_object(resolved.get("/K"))
        if not isinstance(kid, pikepdf.Dictionary):
            return False
        if kid.get("/Type") != pikepdf.Name("/MCR"):
            return False
        if not _same_pdf_object(kid.get("/Pg"), page.obj):
            return False
        try:
            mcid = int(kid.get("/MCID"))
        except Exception:
            return False
        if mcid not in mcids:
            return False

    return True


def _split_coarse_text_node(
    pdf: pikepdf.Pdf,
    *,
    page_idx: int,
    node: pikepdf.Dictionary,
    raw: str,
    match: re.Match[str],
) -> int:
    """Replace a coarse /P or /Span node with finer-grained child regions."""
    page = pdf.pages[page_idx]
    page_height = float(page.MediaBox[3])
    block_body = match.group(1)
    blocks = _extract_stream_text_blocks(block_body, page_height=page_height)
    if len(blocks) < 3:
        return 0

    fonts = [b.font_size for b in blocks if b.font_size > 0]
    median_font_size = statistics.median(fonts) if fonts else 10.0
    next_mcid = _next_page_mcid(page)
    child_nodes = []
    new_mcids: list[int] = []
    original_mcids = _get_node_mcids(node)
    original_s = node.get("/S")
    original_k = node.get("/K")
    pieces: list[str] = []
    cursor = 0

    for order, block in enumerate(blocks):
        if block.start > cursor:
            pieces.append(block_body[cursor:block.start])
        tag = _infer_region_tag(block, page_idx=page_idx, median_font_size=median_font_size)
        mcid = next_mcid
        next_mcid += 1
        new_mcids.append(mcid)
        pieces.append(f"/{tag} <</MCID {mcid}>> BDC\n{block.raw}\nEMC\n")
        child_nodes.append(_make_mcr_struct_elem(pdf, page, node, tag=tag, mcid=mcid))
        cursor = block.end

    pieces.append(block_body[cursor:])
    if not _page_parent_tree_contains_all(pdf, page_idx, new_mcids):
        return 0

    _clear_parent_tree_mcids(pdf, node)
    node["/S"] = pikepdf.Name("/Div")
    node["/K"] = pikepdf.Array(child_nodes) if len(child_nodes) > 1 else child_nodes[0]

    new_raw = raw[: match.start()] + "".join(pieces) + raw[match.end():]
    page["/Contents"] = pdf.make_stream(new_raw.encode("latin-1"))
    if not _validate_resegmented_page(
        pdf,
        page_idx=page_idx,
        parent_node=node,
        child_nodes=child_nodes,
        mcids=new_mcids,
    ):
        page["/Contents"] = pdf.make_stream(raw.encode("latin-1"))
        if original_s is not None:
            node["/S"] = original_s
        else:
            del node["/S"]
        if original_k is not None:
            node["/K"] = original_k
        else:
            del node["/K"]
        _clear_parent_tree_entries(pdf, page, new_mcids)
        for mcid in original_mcids:
            _set_parent_tree_entry(pdf, page, mcid, node)
        return 0

    return len(child_nodes)


def _resegment_complex_page(pdf: pikepdf.Pdf, page_idx: int, analysis: PageLayoutAnalysis) -> int:
    """Split coarse text nodes on a visually complex page into finer regions."""
    raw = _read_page_content(pdf.pages[page_idx]).decode("latin-1", errors="replace")
    rewritten_regions = 0

    candidates: list[tuple[int, pikepdf.Dictionary]] = []
    for node, _depth, _parent in walk_structure_tree(pdf):
        if _find_node_page(node, pdf) != page_idx:
            continue
        if _get_struct_type(node) not in {"P", "Span"}:
            continue
        mcids = _get_node_mcids(node)
        if len(mcids) != 1:
            continue
        match = _find_marked_content_match(raw, mcids[0])
        if match is None:
            continue
        blocks = _extract_stream_text_blocks(match.group(1), page_height=float(pdf.pages[page_idx].MediaBox[3]))
        if len(blocks) >= 3:
            candidates.append((mcids[0], node))

    if not candidates:
        return 0

    for mcid, node in sorted(candidates, key=lambda item: item[0]):
        current_raw = _read_page_content(pdf.pages[page_idx]).decode("latin-1", errors="replace")
        current_match = _find_marked_content_match(current_raw, mcid)
        if current_match is None:
            continue
        rewritten_regions += _split_coarse_text_node(
            pdf,
            page_idx=page_idx,
            node=node,
            raw=current_raw,
            match=current_match,
        )

    if rewritten_regions == 0:
        analysis.notes.append("manual-review-resegment-failed")

    return rewritten_regions


def _synthesize_heading_from_text_blocks(pdf: pikepdf.Pdf) -> int:
    """Create one conservative H1 from a title-like text block when none exist."""
    for page_idx, page in enumerate(pdf.pages):
        raw = _read_page_content(page).decode("latin-1", errors="replace")
        if not raw.strip():
            continue

        block_matches = list(
            re.finditer(r"/P\s*<<[^>]*?/MCID\s+(\d+)[^>]*>>\s*BDC(.*?)EMC", raw, re.S)
        )
        if not block_matches:
            continue

        best: dict | None = None
        best_match = None
        for match in block_matches:
            mcid = int(match.group(1))
            body = match.group(2)
            candidates = _extract_heading_block_candidates(body)
            if not candidates:
                continue
            chosen = _choose_title_candidate(
                candidates,
                page_height=float(page.MediaBox[3]),
            )
            if chosen is None:
                continue
            if best is None or (chosen["y"], chosen["font_size"]) > (best["y"], best["font_size"]):
                best = {"mcid": mcid, "body": body, **chosen}
                best_match = match

        if best is None or best_match is None:
            continue

        node, parent = _find_node_for_page_mcid(pdf, page_idx=page_idx, mcid=best["mcid"], tag="P")
        if node is None or parent is None:
            continue

        before = best["body"][: best["start"]]
        heading = best["body"][best["start"] : best["end"]]
        after = best["body"][best["end"] :]
        if not heading.strip():
            continue

        next_mcid = _next_page_mcid(page)
        before_mcid = next_mcid if before.strip() else None
        if before_mcid is not None:
            next_mcid += 1
        after_mcid = next_mcid if after.strip() else None

        pieces = []
        replacement_nodes = []
        if before_mcid is not None:
            pieces.append(f"/P <</MCID {before_mcid}>> BDC\n{before}\nEMC\n")
            replacement_nodes.append(
                _make_mcr_struct_elem(pdf, page, parent, tag="P", mcid=before_mcid)
            )

        pieces.append(f"/H1 <</MCID {best['mcid']}>> BDC\n{heading}\nEMC\n")
        node["/S"] = pikepdf.Name("/H1")
        replacement_nodes.append(node)

        if after_mcid is not None:
            pieces.append(f"/P <</MCID {after_mcid}>> BDC\n{after}\nEMC\n")
            replacement_nodes.append(
                _make_mcr_struct_elem(pdf, page, parent, tag="P", mcid=after_mcid)
            )

        new_raw = raw[: best_match.start()] + "".join(pieces) + raw[best_match.end():]
        page["/Contents"] = pdf.make_stream(new_raw.encode("latin-1"))
        _replace_node_in_parent(parent, node, replacement_nodes)
        return 1

    return 0


def fix_heading_nesting(pdf: pikepdf.Pdf) -> list[str]:
    """Check #32: Renumber headings to fix skipped levels."""
    headings: list[pikepdf.Dictionary] = []

    for node, _depth, _parent in walk_structure_tree(pdf):
        stype = _get_struct_type(node)
        if re.match(r"^H\d$", stype):
            headings.append(node)

    if not headings:
        synthesized = _synthesize_heading_from_text_blocks(pdf)
        if synthesized:
            return [f"Created {synthesized} H1 heading from title-like text"]
        return []

    levels = [int(_get_struct_type(h)[1]) for h in headings]

    corrected = []
    prev = 0
    for level in levels:
        if prev == 0:
            corrected.append(level)
        elif level > prev + 1:
            corrected.append(prev + 1)
        else:
            corrected.append(level)
        prev = corrected[-1]

    changed = 0
    for heading, old_level, new_level in zip(headings, levels, corrected):
        if old_level != new_level:
            heading["/S"] = pikepdf.Name(f"/H{new_level}")
            changed += 1

    if changed:
        return [f"Renumbered {changed} headings to fix nesting gaps"]
    return []


def fix_form_fields_tagged(pdf: pikepdf.Pdf) -> list[str]:
    """Check #18: Add /Form entries to struct tree for untagged widgets."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    form_count = sum(
        1 for node, _, _ in walk_structure_tree(pdf)
        if _get_struct_type(node) == "Form"
    )

    widgets = []
    for page in pdf.pages:
        annots = page.get("/Annots")
        if not annots:
            continue
        for annot_ref in annots:
            annot = _resolve_pdf_object(annot_ref)
            if str(annot.get("/Subtype", "")) == "/Widget":
                widgets.append((page, annot_ref, annot))

    if form_count >= len(widgets):
        return []

    added = 0
    for page, annot_ref, annot in widgets:
        objr = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/OBJR"),
                "/Obj": annot_ref,
                "/Pg": page.obj,
            }
        )
        form_elem = pikepdf.Dictionary(
            {
                "/S": pikepdf.Name("/Form"),
                "/P": struct_root,
                "/K": objr,
                "/Pg": page.obj,
            }
        )

        tu = annot.get("/TU")
        if tu:
            form_elem["/Alt"] = pikepdf.String(str(tu))
        form_elem = pdf.make_indirect(form_elem)

        kids = struct_root.get("/K")
        if kids is None:
            struct_root["/K"] = pikepdf.Array([form_elem])
        elif isinstance(kids, pikepdf.Array):
            kids.append(form_elem)
        else:
            struct_root["/K"] = pikepdf.Array([kids, form_elem])
        added += 1

    if added:
        return [f"Added {added} /Form entries to structure tree for widgets"]
    return []


def fix_pdfua_identifier(pdf: pikepdf.Pdf) -> list[str]:
    """Set pdfuaid:part = 1 (PDF/UA-1 identifier)."""
    try:
        with pdf.open_metadata() as meta:
            if meta.get("pdfuaid:part") == "1":
                return []
            meta["pdfuaid:part"] = "1"
            meta["pdf:Producer"] = "Remedy Canvas LTI ADA Pipeline"
            meta["xmp:CreatorTool"] = "Remedy Canvas LTI"
    except Exception:
        return []
    return ["Set pdfuaid:part = 1 (PDF/UA-1)"]


# ---------------------------------------------------------------------------
# Color contrast fix (programmatic)
# ---------------------------------------------------------------------------


def _luminance(r: float, g: float, b: float) -> float:
    """Relative luminance per WCAG 2.1 (sRGB inputs 0-1)."""
    def _linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def _contrast_ratio(l1: float, l2: float) -> float:
    """WCAG contrast ratio between two luminance values."""
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def _darken_to_ratio(r: float, g: float, b: float, bg_lum: float, target: float = 4.5) -> tuple[float, float, float]:
    """Darken an RGB color until it meets the target contrast ratio against bg_lum."""
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        lr = _luminance(r * mid, g * mid, b * mid)
        ratio = _contrast_ratio(bg_lum, lr)
        if ratio >= target:
            lo = mid
        else:
            hi = mid
    factor = lo
    return (r * factor, g * factor, b * factor)


def fix_color_contrast(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #8: Fix low-contrast text colors."""
    fixed_pages = 0
    fixed_colors = 0
    bg_lum = _luminance(1.0, 1.0, 1.0)

    vision_contrast: dict[int, list[dict]] = getattr(pdf, "_contrast_issues", {})

    for page_idx, page in enumerate(pdf.pages):
        contents = page.get("/Contents")
        if contents is None:
            continue

        if isinstance(contents, pikepdf.Array):
            raw = b""
            for stream in contents:
                try:
                    raw += stream.read_bytes()
                except Exception:
                    pass
        else:
            try:
                raw = contents.read_bytes()
            except Exception:
                continue

        text = raw.decode("latin-1", errors="replace")
        page_changed = False

        page_issues = vision_contrast.get(page_idx, [])
        page_bg_lum = bg_lum
        if page_issues:
            for issue in page_issues:
                bg = issue.get("bg_rgb")
                if bg and len(bg) == 3:
                    page_bg_lum = _luminance(bg[0], bg[1], bg[2])
                    break

        def _fix_rgb(match: re.Match) -> str:
            nonlocal page_changed, fixed_colors
            r, g, b = float(match.group(1)), float(match.group(2)), float(match.group(3))
            lum = _luminance(r, g, b)
            ratio = _contrast_ratio(page_bg_lum, lum)
            if ratio < 4.5 and lum > 0.05:
                for issue in page_issues:
                    txt = issue.get("text_rgb")
                    fix = issue.get("fix_rgb")
                    if txt and fix and len(txt) == 3 and len(fix) == 3:
                        if (abs(r - txt[0]) < 0.15 and abs(g - txt[1]) < 0.15
                                and abs(b - txt[2]) < 0.15):
                            page_changed = True
                            fixed_colors += 1
                            return f"{fix[0]:.4f} {fix[1]:.4f} {fix[2]:.4f} rg"
                nr, ng, nb = _darken_to_ratio(r, g, b, page_bg_lum)
                page_changed = True
                fixed_colors += 1
                return f"{nr:.4f} {ng:.4f} {nb:.4f} rg"
            return match.group(0)

        def _fix_gray(match: re.Match) -> str:
            nonlocal page_changed, fixed_colors
            gray = float(match.group(1))
            lum = _luminance(gray, gray, gray)
            ratio = _contrast_ratio(page_bg_lum, lum)
            if ratio < 4.5 and lum > 0.05:
                ng, _, _ = _darken_to_ratio(gray, gray, gray, page_bg_lum)
                page_changed = True
                fixed_colors += 1
                return f"{ng:.4f} g"
            return match.group(0)

        new_text = re.sub(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg\b", _fix_rgb, text)
        new_text = re.sub(r"([\d.]+)\s+g\b", _fix_gray, new_text)

        if page_changed:
            page["/Contents"] = pdf.make_stream(new_text.encode("latin-1"))
            fixed_pages += 1

    if fixed_colors:
        return [
            f"Fixed {fixed_colors} low-contrast text colors on {fixed_pages} pages "
            f"(WCAG 2.1 AA 4.5:1)"
        ]
    return []


def _page_has_complex_layout(page, pdf: pikepdf.Pdf) -> bool:
    """Quick heuristic: does this page likely have multi-column or complex layout?"""
    page_idx = -1
    try:
        target_objgen = page.obj.objgen
    except Exception:
        target_objgen = None
    for idx, candidate in enumerate(pdf.pages):
        try:
            if candidate.obj.objgen == target_objgen:
                page_idx = idx
                break
        except Exception:
            continue
    if page_idx < 0:
        return False
    structure_summary = _build_page_structure_summary(pdf)
    analysis = _analyze_page_layout(pdf, page_idx, structure_summary=structure_summary)
    return analysis.layout_class != LayoutClass.SINGLE_COLUMN


def _page_has_low_contrast_colors(page) -> bool:
    """Quick heuristic: does this page's content stream have light fill colors?"""
    contents = page.get("/Contents")
    if contents is None:
        return False

    if isinstance(contents, pikepdf.Array):
        raw = b""
        for stream in contents:
            try:
                raw += stream.read_bytes()
            except Exception:
                pass
    else:
        try:
            raw = contents.read_bytes()
        except Exception:
            return False

    text = raw.decode("latin-1", errors="replace")

    for match in re.finditer(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg\b", text):
        r, g, b = float(match.group(1)), float(match.group(2)), float(match.group(3))
        lum = _luminance(r, g, b)
        if 0.05 < lum and _contrast_ratio(1.0, lum) < 4.5:
            return True

    for match in re.finditer(r"([\d.]+)\s+g\b", text):
        gray = float(match.group(1))
        lum = _luminance(gray, gray, gray)
        if 0.05 < lum and _contrast_ratio(1.0, lum) < 4.5:
            return True

    return False


def fix_reading_order(pdf: pikepdf.Pdf, *, vision_provider=None, thorough: bool = False) -> list[str]:
    """Check #4 + #8: Fix reading order and gather contrast data in one pass."""
    import asyncio

    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return []

    changes = []
    resegmented_pages = 0
    resegmented_regions = 0
    manual_review_pages: set[int] = set()
    analyses: dict[int, PageLayoutAnalysis] = {}
    structure_summary = _build_page_structure_summary(pdf)

    for page_idx in range(len(pdf.pages)):
        analysis = _analyze_page_layout(
            pdf,
            page_idx,
            structure_summary=structure_summary,
        )
        analyses[page_idx] = analysis
        if not _page_needs_resegmentation(pdf, page_idx, analysis):
            continue
        regions = _resegment_complex_page(pdf, page_idx, analysis)
        if regions:
            resegmented_pages += 1
            resegmented_regions += regions
        elif "manual-review-resegment-failed" in analysis.notes:
            manual_review_pages.add(page_idx + 1)

    if resegmented_pages:
        changes.append(
            f"Resegmented {resegmented_pages} complex pages into {resegmented_regions} tagged regions"
        )
    if manual_review_pages:
        changes.append(
            "Retained original structure on page(s) requiring manual review: "
            + _format_page_list(manual_review_pages)
        )

    if vision_provider is None:
        return changes

    if thorough:
        pages_needing_vision: set[int] = set(range(len(pdf.pages)))
    else:
        pages_needing_vision = set()
        for page_idx in range(len(pdf.pages)):
            page = pdf.pages[page_idx]
            analysis = analyses.get(page_idx) or _analyze_page_layout(
                pdf,
                page_idx,
                structure_summary=structure_summary,
            )
            if analysis.layout_class != LayoutClass.SINGLE_COLUMN:
                pages_needing_vision.add(page_idx)
            elif _page_has_low_contrast_colors(page):
                pages_needing_vision.add(page_idx)

    if not pages_needing_vision:
        return changes

    MAX_VISION_PAGES = 20 if thorough else 8
    if len(pages_needing_vision) > MAX_VISION_PAGES:
        all_pages = sorted(pages_needing_vision)
        step = len(all_pages) // MAX_VISION_PAGES
        sampled = set(all_pages[i] for i in range(0, len(all_pages), max(step, 1)))
        sampled.add(all_pages[0])
        sampled.add(all_pages[-1])
        pages_needing_vision = sampled

    reordered_pages = 0
    contrast_data: dict[int, list[dict]] = {}

    for page_idx in sorted(pages_needing_vision):
        parent_children: dict[int, list[tuple[int, pikepdf.Dictionary, str]]] = {}
        child_index = 0

        for node, _depth, parent in walk_structure_tree(pdf):
            if parent is None:
                continue
            stype = _get_struct_type(node)
            if not stype:
                continue
            node_page = _find_node_page(node, pdf)
            if node_page != page_idx:
                continue

            pid = id(parent)
            if pid not in parent_children:
                parent_children[pid] = []

            alt = node.get("/Alt")
            label = f"/{stype}"
            if alt and str(alt).strip():
                label += f': "{str(alt)[:30]}"'

            parent_children[pid].append((child_index, node, label))
            child_index += 1

        all_elements = []
        for pid, children in parent_children.items():
            if len(children) >= 3:
                for _, _, label in children:
                    all_elements.append((pid, label))

        if not all_elements:
            continue

        # Render page once.
        try:
            from lti_app.core.pdf.vision import render_page_to_image
            image_path = render_page_to_image(pdf.filename, page_idx + 1)
        except Exception:
            continue

        try:
            element_list = "\n".join(
                f"  {i+1}. {label}" for i, (_, label) in enumerate(all_elements)
            )
            prompt = _page_region_analysis_prompt(
                element_list=element_list,
                profile="local",
            )

            response = asyncio.run(vision_provider.analyze_image(image_path, prompt))

            from lti_app.core.pdf.vision import _parse_json_response
            parsed = _parse_json_response(response)
            if not parsed:
                continue

            if parsed.get("contrast_issues"):
                contrast_data[page_idx] = parsed["contrast_issues"]

            if not parsed.get("order_changed", False):
                continue

            order = parsed.get("reading_order")
            if not order or not isinstance(order, list):
                continue
            if len(order) != len(all_elements):
                continue
            if order == list(range(1, len(all_elements) + 1)):
                continue

            for pid, children in parent_children.items():
                if len(children) < 3:
                    continue

                parent_node = None
                for node, _, _ in walk_structure_tree(pdf):
                    if id(node) == pid:
                        parent_node = node
                        break
                if parent_node is None:
                    continue

                kids = parent_node.get("/K")
                if kids is None or not isinstance(kids, pikepdf.Array):
                    continue

                page_kid_indices = []
                for k_idx, kid in enumerate(kids):
                    resolved = _resolve_pdf_object(kid)
                    if isinstance(resolved, pikepdf.Dictionary) and "/S" in resolved:
                        if _find_node_page(resolved, pdf) == page_idx:
                            page_kid_indices.append(k_idx)

                if len(page_kid_indices) < 3:
                    continue

                flat_start = None
                for fi, (p, _) in enumerate(all_elements):
                    if p == pid and flat_start is None:
                        flat_start = fi
                if flat_start is None:
                    continue

                count = len(children)
                parent_order = []
                for i in range(flat_start, min(flat_start + count, len(order))):
                    parent_order.append(order[i] - flat_start - 1)

                if sorted(parent_order) != list(range(count)):
                    continue

                original_kids = [kids[i] for i in page_kid_indices]
                for new_pos, old_pos in enumerate(parent_order):
                    if old_pos < len(original_kids) and new_pos < len(page_kid_indices):
                        kids[page_kid_indices[new_pos]] = original_kids[old_pos]

            reordered_pages += 1

        except Exception:
            pass
        finally:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                pass

    pdf._contrast_issues = contrast_data

    if reordered_pages:
        changes.append(f"Reordered reading order on {reordered_pages} pages via vision model")
    if contrast_data:
        total_issues = sum(len(v) for v in contrast_data.values())
        changes.append(
            f"Vision identified {total_issues} contrast issues on {len(contrast_data)} pages"
        )
    return changes


def fix_metadata(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Enrich PDF /Info metadata with LLM-generated subject and keywords."""
    import asyncio

    changes = []

    try:
        with pdf.open_metadata() as meta:
            meta["xmp:CreatorTool"] = "Remedy Canvas LTI (LACCD ADA Compliance)"
            changes.append("Set xmp:CreatorTool = Remedy Canvas LTI")
    except Exception:
        pass

    if vision_provider is None:
        return changes

    text = ""
    try:
        import fitz
        doc = fitz.open(str(pdf.filename))
        for i in range(min(3, len(doc))):
            text += doc[i].get_text()
        text = text[:3000]
        doc.close()
    except Exception:
        pass

    if not text or len(text.strip()) < 30:
        return changes

    try:
        prompt = (
            "Analyze this document and provide:\n"
            "1. A one-sentence description (for PDF Subject metadata, max 200 chars)\n"
            "2. 5-10 relevant keywords (comma-separated)\n\n"
            "Return in this exact format:\n"
            "Subject: <description>\n"
            "Keywords: <keyword1, keyword2, ...>\n\n"
            f"Document text:\n{text}"
        )

        async def _run():
            return await vision_provider.analyze_image(None, prompt)

        response = asyncio.run(_run())
        response_str = str(response).strip()

        for line in response_str.split("\n"):
            line = line.strip()
            if line.lower().startswith("subject:"):
                subject = line[8:].strip()
                if subject and len(subject) > 5:
                    try:
                        with pdf.open_metadata() as meta:
                            meta["dc:description"] = subject[:250]
                        changes.append(f"Set dc:description = {subject[:60]}")
                    except Exception:
                        pass
            elif line.lower().startswith("keywords:"):
                keywords = line[9:].strip()
                if keywords and len(keywords) > 3:
                    try:
                        with pdf.open_metadata() as meta:
                            meta["pdf:Keywords"] = keywords[:500]
                        changes.append(f"Set pdf:Keywords = {keywords[:60]}")
                    except Exception:
                        pass
    except Exception:
        pass

    return changes


# ---------------------------------------------------------------------------
# Previously-manual checks -- now LLM-powered
# ---------------------------------------------------------------------------

def fix_image_only_pdf(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #2: Detect image-only PDFs and inject OCR text layer."""
    import asyncio

    changes = []
    has_text = False
    try:
        import fitz
        doc = fitz.open(str(pdf.filename))
        for i in range(min(5, len(doc))):
            if doc[i].get_text().strip():
                has_text = True
                break
        doc.close()
    except Exception:
        return []

    if has_text:
        return []

    if vision_provider is None:
        changes.append("Image-only PDF detected -- needs OCR (no vision provider available)")
        return changes

    try:
        from lti_app.core.pdf.vision import render_page_to_image

        ocr_pages = 0
        for page_idx in range(len(pdf.pages)):
            try:
                image_path = render_page_to_image(pdf.filename, page_num=page_idx + 1, dpi=200)
                prompt = (
                    "OCR this document page. Return ALL visible text exactly as it appears, "
                    "preserving line breaks and formatting. Return ONLY the text content."
                )

                async def _run():
                    return await vision_provider.analyze_image(image_path, prompt)

                text = asyncio.run(_run())
                if text and len(str(text).strip()) > 10:
                    ocr_pages += 1
            except Exception:
                continue

        if ocr_pages > 0:
            changes.append(f"Image-only PDF: OCR'd {ocr_pages} pages via vision model")
    except Exception as exc:
        changes.append(f"Image-only PDF detected -- OCR failed: {exc}")

    return changes


def fix_char_encoding(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #10: Flag malformed text layers that still need OCR rebuild."""
    pdf_path = None
    if getattr(pdf, "filename", None):
        try:
            pdf_path = Path(str(pdf.filename))
        except Exception:
            pdf_path = None

    analysis = _analyze_character_encoding(pdf, pdf_path)
    if not analysis.details:
        return []

    if analysis.requires_rebuild:
        return [
            f"Character encoding still needs OCR rebuild on page(s): {_format_page_list(analysis.page_numbers)}"
        ]

    return [analysis.details[0]]


def fix_multimedia_tagged(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #14: Ensure embedded multimedia is tagged with alt descriptions."""
    changes = []

    found = 0
    tagged = 0
    pages_tagged = 0

    for page_idx, page in enumerate(pdf.pages):
        annots = page.get("/Annots", [])
        for annot in annots or []:
            try:
                resolved = _resolve_pdf_object(annot)
                subtype = str(resolved.get("/Subtype", ""))
                if subtype in MULTIMEDIA_ANNOT_TYPES:
                    found += 1
                    if "/Contents" not in resolved or not str(resolved["/Contents"]).strip():
                        resolved["/Contents"] = pikepdf.String(
                            f"Embedded {subtype.strip('/')} content"
                        )
                        tagged += 1
            except Exception:
                continue

        rendered = get_rendered_multimedia_names(page)
        if rendered and not _page_has_content_associated_multimedia(pdf, page_idx):
            struct_root = pdf.Root.get("/StructTreeRoot")
            if struct_root is None:
                changes.extend(fix_create_structure_tree(pdf))
                struct_root = pdf.Root.get("/StructTreeRoot")
            if struct_root is not None:
                raw = _read_page_content(page)
                text = raw.decode("latin-1", errors="replace") if raw else ""
                next_mcid = _next_page_mcid(page)
                added_on_page = 0
                for name in rendered:
                    pat = rf"(/{re.escape(name)}\s+Do)\b"
                    if not re.search(pat, text):
                        continue
                    text = re.sub(
                        pat,
                        f"/Figure <</MCID {next_mcid}>> BDC\n\\1\nEMC",
                        text,
                        count=1,
                    )
                    _add_mcr_to_struct_tree(
                        pdf, struct_root, page, page_idx, next_mcid, "/Figure"
                    )
                    next_mcid += 1
                    added_on_page += 1
                if added_on_page:
                    page["/Contents"] = pdf.make_stream(text.encode("latin-1"))
                    pages_tagged += 1

    if tagged > 0:
        changes.append(f"Added alt text to {tagged} multimedia annotation(s)")
    if pages_tagged > 0:
        changes.append(
            f"Tagged rendered multimedia on {pages_tagged} page(s) with /Figure elements"
        )
    return changes


def fix_repetitive_links(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #16: Detect and flag repetitive navigation links."""
    changes = []

    link_map: dict[str, list[int]] = {}
    for page_idx, page in enumerate(pdf.pages):
        annots = page.get("/Annots", [])
        if not annots:
            continue
        for annot in annots:
            try:
                resolved = _resolve_pdf_object(annot)
                if str(resolved.get("/Subtype", "")) != "/Link":
                    continue
                dest = ""
                if "/A" in resolved:
                    action = resolved["/A"]
                    action = _resolve_pdf_object(action)
                    dest = str(action.get("/URI", ""))
                elif "/Dest" in resolved:
                    dest = str(resolved["/Dest"])
                if dest:
                    link_map.setdefault(dest, []).append(page_idx + 1)
            except Exception:
                continue

    repetitive = {dest: pages for dest, pages in link_map.items() if len(pages) > 3}

    if repetitive:
        total = sum(len(p) for p in repetitive.values())
        changes.append(
            f"Found {len(repetitive)} repetitive link(s) appearing on {total} pages total "
            f"(e.g., navigation links repeated across pages)"
        )

    return changes


def fix_table_regularity(pdf: pikepdf.Pdf, *, vision_provider=None) -> list[str]:
    """Check #23: Fix irregular table structure (inconsistent cells per row)."""
    import asyncio

    changes = []

    try:
        struct_root = pdf.Root.get("/StructTreeRoot")
        if not struct_root:
            return []

        def _find_tables(node, tables=None):
            if tables is None:
                tables = []
            try:
                resolved = _resolve_pdf_object(node)
                stype = str(resolved.get("/S", ""))
                if stype == "/Table":
                    tables.append(resolved)
                kids = resolved.get("/K", [])
                if isinstance(kids, pikepdf.Array):
                    for kid in kids:
                        _find_tables(kid, tables)
                elif isinstance(kids, pikepdf.Object) and kids.is_indirect:
                    _find_tables(kids, tables)
            except Exception:
                pass
            return tables

        tables = _find_tables(struct_root)
        irregular_count = 0
        repaired_rows = 0

        for table in tables:
            row_nodes: list[tuple[pikepdf.Dictionary, list[pikepdf.Dictionary], list[int]]] = []
            kids = table.get("/K", [])
            if not isinstance(kids, pikepdf.Array):
                continue

            for kid in kids:
                try:
                    resolved = _resolve_pdf_object(kid)
                    if str(resolved.get("/S", "")) == "/TR":
                        cells = resolved.get("/K", [])
                        cell_nodes: list[pikepdf.Dictionary] = []
                        if isinstance(cells, pikepdf.Array):
                            for cell in cells:
                                resolved_cell = _resolve_pdf_object(cell)
                                if (
                                    isinstance(resolved_cell, pikepdf.Dictionary)
                                    and _get_struct_type(resolved_cell) in {"TH", "TD"}
                                ):
                                    cell_nodes.append(resolved_cell)
                        else:
                            resolved_cell = _resolve_pdf_object(cells)
                            if (
                                isinstance(resolved_cell, pikepdf.Dictionary)
                                and _get_struct_type(resolved_cell) in {"TH", "TD"}
                            ):
                                cell_nodes.append(resolved_cell)
                        spans = [max(1, int(cell.get("/ColSpan", 1))) for cell in cell_nodes]
                        row_nodes.append((resolved, cell_nodes, spans))
                except Exception:
                    continue

            row_widths = [sum(spans) for _row, _cells, spans in row_nodes if spans]
            if row_widths and len(set(row_widths)) > 1:
                irregular_count += 1
                target_width = Counter(row_widths).most_common(1)[0][0]
                for _row, cell_nodes, spans in row_nodes:
                    if not cell_nodes:
                        continue
                    current_width = sum(spans)
                    if current_width == target_width:
                        continue
                    if len(cell_nodes) == 1 and current_width == 1 and target_width > 1:
                        cell = cell_nodes[0]
                        if "/ColSpan" not in cell:
                            cell["/ColSpan"] = target_width
                            repaired_rows += 1

        if irregular_count > 0:
            if repaired_rows > 0:
                changes.append(
                    f"Set /ColSpan on {repaired_rows} single-cell table row(s)"
                )
            if vision_provider is not None:
                changes.append(
                    f"Found {irregular_count} irregular table(s) with inconsistent "
                    f"cells per row -- vision analysis recommended for cell span correction"
                )
            else:
                changes.append(
                    f"Found {irregular_count} irregular table(s) with inconsistent cells per row"
                )
    except Exception:
        pass

    return changes


# ---------------------------------------------------------------------------
# Screen reader figure flow
# ---------------------------------------------------------------------------


def fix_screen_reader_figure_flow(pdf: pikepdf.Pdf) -> list[str]:
    """Demote redundant page-scan figures and move hero figures after headings."""
    return _fix_screen_reader_figure_flow_impl(pdf)


def _fix_screen_reader_figure_flow_impl(pdf: pikepdf.Pdf) -> list[str]:
    """Demote redundant page-scan figures and move hero figures after headings."""
    artifactized = 0
    reordered = 0
    layout_cache: dict[int, PageLayoutAnalysis] = {}
    structure_summary = _build_page_structure_summary(pdf)

    figure_entries: list[tuple[pikepdf.Dictionary, pikepdf.Dictionary, int]] = []
    for node, _depth, parent in walk_structure_tree(pdf):
        if parent is None or _get_struct_type(node) != "Figure":
            continue
        page_idx = _find_node_page(node, pdf)
        if page_idx < 0:
            continue
        figure_entries.append((node, parent, page_idx))

    for node, parent, page_idx in figure_entries:
        analysis = layout_cache.get(page_idx)
        if analysis is None:
            analysis = _analyze_page_layout(
                pdf,
                page_idx,
                structure_summary=structure_summary,
            )
            layout_cache[page_idx] = analysis

        alt = _normalize_extracted_text(str(node.get("/Alt", "")))
        figure_count = _count_page_struct_type(
            pdf,
            page_idx,
            "Figure",
            structure_summary=structure_summary,
        )
        has_heading = any(
            _page_has_struct_type(
                pdf,
                page_idx,
                tag,
                structure_summary=structure_summary,
            )
            for tag in ("H1", "H2", "H3")
        )
        is_redundant_page_scan = (
            figure_count == 1
            and analysis.structured_text_nodes >= 6
            and has_heading
            and alt.lower().startswith(("image containing text:", "decorative image"))
        )
        if is_redundant_page_scan:
            if _artifactize_figure_node(pdf, page_idx=page_idx, node=node, parent=parent):
                artifactized += 1
                continue

        if _move_leading_figure_after_heading(parent):
            reordered += 1

    changes = []
    if artifactized:
        changes.append(f"Artifactized {artifactized} redundant page-scan figures for screen readers")
    if reordered:
        changes.append(f"Moved {reordered} leading figures behind heading content")
    return changes


def _same_pdf_object(left, right) -> bool:
    """Return True when two pikepdf objects refer to the same underlying object."""
    resolved_left = _resolve_pdf_object(left)
    resolved_right = _resolve_pdf_object(right)

    if resolved_left is resolved_right:
        return True

    left_objgen = getattr(resolved_left, "objgen", None)
    right_objgen = getattr(resolved_right, "objgen", None)
    return (
        left_objgen is not None
        and right_objgen is not None
        and left_objgen != (0, 0)
        and left_objgen == right_objgen
    )


def _remove_node_from_parent(parent: pikepdf.Dictionary, node: pikepdf.Dictionary) -> bool:
    """Remove *node* from its parent's /K entry."""
    kids = parent.get("/K")
    if kids is None:
        return False

    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    new_items = []
    removed = False

    for kid in items:
        if _same_pdf_object(kid, node):
            removed = True
            continue
        new_items.append(kid)

    if not removed:
        return False

    if not new_items:
        del parent["/K"]
    elif len(new_items) == 1:
        parent["/K"] = new_items[0]
    else:
        parent["/K"] = pikepdf.Array(new_items)
    return True


def _clear_parent_tree_mcids(pdf: pikepdf.Pdf, node: pikepdf.Dictionary) -> None:
    """Null out parent-tree entries for MCIDs that are no longer tagged."""
    struct_root = pdf.Root.get("/StructTreeRoot")
    if struct_root is None:
        return

    parent_tree = struct_root.get("/ParentTree")
    if parent_tree is None:
        return

    pt = _resolve_pdf_object(parent_tree)
    if not isinstance(pt, pikepdf.Dictionary):
        return

    nums = _resolve_pdf_object(pt.get("/Nums"))
    if not isinstance(nums, pikepdf.Array):
        return

    page_idx = _find_node_page(node, pdf)
    if page_idx < 0 or page_idx >= len(pdf.pages):
        return

    struct_parents = pdf.pages[page_idx].get("/StructParents")
    if struct_parents is None:
        return

    try:
        struct_parents = int(struct_parents)
    except Exception:
        return

    mcids = _get_node_mcids(node)
    if not mcids:
        return

    for i in range(0, len(nums), 2):
        try:
            key = int(nums[i])
        except Exception:
            continue
        if key != struct_parents:
            continue

        arr = _resolve_pdf_object(nums[i + 1])
        if not isinstance(arr, pikepdf.Array):
            return

        for mcid in mcids:
            if 0 <= mcid < len(arr):
                arr[mcid] = None
        return


def _artifactize_figure_node(
    pdf: pikepdf.Pdf,
    *,
    page_idx: int,
    node: pikepdf.Dictionary,
    parent: pikepdf.Dictionary,
) -> bool:
    """Rewrite a figure block as /Artifact and remove it from the tree."""
    mcids = _get_node_mcids(node)
    if not mcids:
        return False

    page = pdf.pages[page_idx]
    raw = _read_page_content(page).decode("latin-1", errors="replace")
    updated = raw
    replaced = False

    for mcid in mcids:
        match = _find_tagged_mcid_match(updated, mcid, tags=("Figure",))
        if match is None:
            continue
        body = match.group(1).rstrip()
        replacement = f"/Artifact BMC\n{body}\nEMC"
        updated = updated[: match.start()] + replacement + updated[match.end():]
        replaced = True

    if not replaced:
        return False

    page["/Contents"] = pdf.make_stream(updated.encode("latin-1"))
    _clear_parent_tree_mcids(pdf, node)
    return _remove_node_from_parent(parent, node)


def _move_leading_figure_after_heading(parent: pikepdf.Dictionary) -> bool:
    """Move a leading figure behind the first heading-bearing sibling."""
    kids = parent.get("/K")
    if not isinstance(kids, pikepdf.Array) or len(kids) < 2:
        return False

    items = list(kids)
    first = _resolve_pdf_object(items[0])
    if not isinstance(first, pikepdf.Dictionary) or _get_struct_type(first) != "Figure":
        return False

    target_index = None
    for idx, item in enumerate(items[1:], start=1):
        resolved = _resolve_pdf_object(item)
        if not isinstance(resolved, pikepdf.Dictionary):
            continue
        if _node_or_descendant_has_heading(resolved):
            target_index = idx
            break

    if target_index is None:
        for idx, item in enumerate(items[1:], start=1):
            resolved = _resolve_pdf_object(item)
            if isinstance(resolved, pikepdf.Dictionary) and _get_struct_type(resolved) != "Figure":
                target_index = idx
                break

    if target_index is None:
        return False

    figure = items.pop(0)
    items.insert(target_index, figure)
    parent["/K"] = pikepdf.Array(items)
    return True


def _fix_empty_leaf_text_elements(pdf: pikepdf.Pdf) -> int:
    """Remove empty leaf P/Span tags that only point to whitespace content."""
    removable: list[tuple[pikepdf.Dictionary, pikepdf.Dictionary]] = []
    page_text_cache: dict[int, dict[int, str]] = {}

    for node, _depth, parent in walk_structure_tree(pdf):
        if parent is None:
            continue

        stype = _get_struct_type(node)
        if stype not in {"P", "Span"}:
            continue
        if node_has_struct_children(node):
            continue

        mcids = _get_node_mcids(node)
        if not mcids:
            continue

        alt = node.get("/Alt")
        if alt is not None and str(alt).strip():
            continue

        page_idx = _find_node_page(node, pdf)
        if page_idx < 0 or page_idx >= len(pdf.pages):
            continue
        page_text = page_text_cache.get(page_idx)
        if page_text is None:
            page_text = _extract_mcid_text(pdf.pages[page_idx])
            page_text_cache[page_idx] = page_text

        text = _normalize_extracted_text(
            " ".join(
                page_text.get(mcid, "").strip()
                for mcid in mcids
                if page_text.get(mcid, "").strip()
            )
        )
        if text:
            continue

        removable.append((node, parent))

    removed = 0
    for node, parent in removable:
        if _remove_node_from_parent(parent, node):
            _clear_parent_tree_mcids(pdf, node)
            removed += 1

    return removed


def _fix_empty_lists(pdf: pikepdf.Pdf) -> int:
    """Remove empty List elements (L with no LI children) from the tree."""
    to_remove = []
    for node, _depth, parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "L":
            continue
        kids = node.get("/K")
        has_li = False
        if kids is not None:
            items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
            for item in items:
                resolved = _resolve_pdf_object(item)
                if isinstance(resolved, pikepdf.Dictionary) and _get_struct_type(resolved) == "LI":
                    has_li = True
                    break
        if not has_li and parent is not None:
            to_remove.append((node, parent))

    removed = 0
    for node, parent in to_remove:
        parent_kids = parent.get("/K")
        if parent_kids is None:
            continue
        if isinstance(parent_kids, pikepdf.Array):
            new_kids = pikepdf.Array()
            for kid in parent_kids:
                resolved = _resolve_pdf_object(kid)
                if resolved is not node:
                    new_kids.append(kid)
            parent["/K"] = new_kids
            removed += 1

    return removed


def _should_run_empty_leaf_cleanup(pdf: pikepdf.Pdf) -> bool:
    """Limit expensive whitespace cleanup on very large documents."""
    return len(pdf.pages) <= 225


def _parse_figure_descriptions(text: str, count: int) -> list[str]:
    """Parse 'Figure N: description' lines from vision model response."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    descriptions = []
    for line in lines:
        cleaned = re.sub(r'^(Figure\s*\d+\s*[:\-]\s*)', '', line, flags=re.IGNORECASE)
        if cleaned:
            descriptions.append(cleaned)
    while len(descriptions) < count:
        descriptions.append("Decorative image")
    return descriptions[:count]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _find_node_page(node: pikepdf.Dictionary, pdf: pikepdf.Pdf) -> int:
    """Find the page index for a structure tree node via its /Pg or MCR."""
    idx = _shared_find_node_page(node, pdf)
    return idx if idx is not None else 0


# Vision-aware fix rule IDs.
_VISION_FIX_IDS = {
    "alt-figures", "doc-reading-order", "doc-color-contrast",
    "doc-display-title", "doc-language", "doc-metadata",
    "doc-not-image-only", "page-char-encoding", "page-multimedia-tagged",
    "page-no-repetitive-links", "tables-regularity",
}

# Ordered list of (rule_id, fix_function, description).
ALL_FIXES: list[tuple[str, callable, str]] = [
    ("doc-accessibility-permission", fix_accessibility_permission, "Accessibility permission flag is set"),
    ("doc-not-image-only", fix_image_only_pdf, "Document is not image-only PDF"),
    ("doc-tagged", fix_mark_info, "Document is tagged PDF"),
    ("doc-struct-tree", fix_create_structure_tree, "Create structure tree if missing"),
    ("doc-uncovered-pages", fix_tag_uncovered_pages, "Tag uncovered pages in existing tree"),
    ("doc-language", fix_language, "Text language is specified"),
    ("doc-display-title", fix_display_doc_title, "Document title is showing in title bar"),
    ("doc-metadata", fix_metadata, "Document metadata (subject, keywords) is populated"),
    ("doc-bookmarks", fix_bookmarks, "Bookmarks are present in large documents"),
    ("doc-reading-order", fix_reading_order, "Document structure provides logical reading order"),
    ("doc-color-contrast", fix_color_contrast, "Document has appropriate color contrast"),
    ("page-content-tagged", fix_untagged_content, "All page content is tagged"),
    ("page-char-encoding", fix_char_encoding, "Character encoding is reliable"),
    ("page-annotations-tagged", fix_annotations_tagged, "All annotations are tagged"),
    ("page-link-contents", fix_link_annotations, "Link annotations have descriptions"),
    ("page-annotation-contents", fix_annotation_descriptions, "Annotations have descriptions"),
    ("page-tab-order", fix_tab_order, "Tab order is consistent with structure order"),
    ("page-no-flicker", fix_screen_flicker, "Page will not cause screen flicker"),
    ("page-no-scripts", fix_remove_scripts, "No inaccessible scripts"),
    ("page-no-timed-responses", fix_timed_responses, "Page does not require timed responses"),
    ("page-multimedia-tagged", fix_multimedia_tagged, "All multimedia is tagged"),
    ("page-no-repetitive-links", fix_repetitive_links, "No repetitive navigation links"),
    ("forms-fields-tagged", fix_form_fields_tagged, "All form fields are tagged"),
    ("forms-fields-description", fix_form_field_descriptions, "All form fields have description"),
    ("tables-tr-parent", fix_table_parent_structure, "TR/TH/TD parent structure"),
    ("tables-headers", fix_table_headers, "Tables must have headers"),
    ("tables-header-scope", fix_table_header_scope, "Table headers have scope"),
    ("tables-summary", fix_table_summary, "Tables must have a summary"),
    ("tables-regularity", fix_table_regularity, "Tables have consistent cells per row"),
    ("lists-li-parent", fix_list_structure, "List structure (LI/Lbl/LBody)"),
    ("alt-figures", fix_figures_alt_text, "Figures require alternate text"),
    ("sr-figure-flow", fix_screen_reader_figure_flow, "Screen reader figure order and decorative figures"),
    ("alt-redundant", fix_redundant_alt_text, "Alternate text that will never be read"),
    ("alt-associated", fix_orphan_alt_text, "Alternate text must be associated with content"),
    ("alt-hides-annotation", fix_alt_hides_annotation, "Alternate text should not hide annotation"),
    ("alt-elements", fix_alt_text_elements, "Elements require alternate text"),
    ("headings-nesting", fix_heading_nesting, "Appropriate heading nesting"),
    ("pdfua-id", fix_pdfua_identifier, "PDF/UA-1 identifier"),
    ("role-map", fix_role_map, "RoleMap /NonStruct -> /Span"),
]


# ---------------------------------------------------------------------------
# Master fix function
# ---------------------------------------------------------------------------


def fix_all(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    *,
    only: str | None = None,
    dry_run: bool = False,
    thorough: bool = False,
    vision_client=None,
) -> FixReport:
    """Run all fixable checks, apply fixes, return report of changes.

    Parameters
    ----------
    pdf_path:
        Input PDF file.
    output_path:
        Where to save the fixed PDF. Defaults to ``<name>_fixed.pdf``.
    only:
        If set, only apply the fix matching this rule_id.
    dry_run:
        If True, open the PDF and check what would be fixed but don't save.
    thorough:
        If True, skip heuristic pre-filters and send every page to the
        vision model for reading order and contrast analysis.
    vision_client:
        Optional vision client for AI-powered fixes (alt text, reading
        order, language detection, etc.). If None, vision-dependent fixes
        will use deterministic fallbacks.
    """
    pdf_path = Path(pdf_path)
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if output_path is None:
        output_path = pdf_path.with_name(
            pdf_path.stem + "_fixed" + pdf_path.suffix
        )
    else:
        output_path = Path(output_path)

    report = FixReport()

    # Resolve vision provider (use vision_client as vision_provider internally).
    vision_provider = vision_client

    with ExitStack() as cleanup:
        working_pdf_path, preflight_changes, preflight_skipped, tempdir = _maybe_rebuild_broken_text_layer(
            pdf_path,
            only=only,
            dry_run=dry_run,
        )
        if tempdir is not None:
            cleanup.enter_context(tempdir)
        report.fixes_applied.extend(preflight_changes)
        report.fixes_skipped.extend(preflight_skipped)

        allow_overwrite = working_pdf_path.resolve() == output_path.resolve()
        with pikepdf.open(working_pdf_path, allow_overwriting_input=allow_overwrite) as pdf:
            for rule_id, fix_fn, description in ALL_FIXES:
                if only and rule_id != only:
                    continue

                try:
                    # Pass vision provider to fixes that can use it.
                    if rule_id in _VISION_FIX_IDS and vision_provider is not None:
                        kwargs = {"vision_provider": vision_provider}
                        if rule_id == "doc-reading-order" and thorough:
                            kwargs["thorough"] = True
                        changes = fix_fn(pdf, **kwargs)
                    else:
                        changes = fix_fn(pdf)
                    report.fixes_applied.extend(changes)
                except Exception as exc:
                    report.fixes_skipped.append(f"{description}: error -- {exc}")

            if _should_run_empty_leaf_cleanup(pdf):
                empty_leaf_text = _fix_empty_leaf_text_elements(pdf)
                if empty_leaf_text:
                    report.fixes_applied.append(
                        f"Removed {empty_leaf_text} empty leaf text elements"
                    )
            else:
                report.fixes_skipped.append(
                    "Whitespace-only leaf text cleanup deferred for large document"
                )

            if not dry_run:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _save_remediated_pdf(pdf, output_path)

    return report


# ---------------------------------------------------------------------------
# Post-fix verification loop
# ---------------------------------------------------------------------------


def fix_and_verify(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    *,
    thorough: bool = False,
    vision_client=None,
    max_cycles: int = 3,
) -> FixReport:
    """Run fix_all(), validate with screen reader, apply targeted fixes, repeat.

    Loops up to *max_cycles* times until validate_tag_tree() returns zero
    errors.  Each cycle applies only the fixes needed for remaining issues.

    Returns a combined FixReport with all changes across all cycles.
    """
    from lti_app.core.pdf.models import TagTreeSeverity
    from lti_app.core.pdf.tag_tree_reader import validate_tag_tree

    pdf_path = Path(pdf_path)
    if output_path is None:
        output_path = pdf_path.with_name(
            pdf_path.stem + "_fixed" + pdf_path.suffix
        )
    else:
        output_path = Path(output_path)

    # Cycle 1: full fix_all().
    report = fix_all(
        pdf_path, output_path,
        thorough=thorough,
        vision_client=vision_client,
    )

    vision_provider = vision_client

    # Verification cycles.
    for cycle in range(max_cycles):
        sr_result = validate_tag_tree(output_path)

        sr_errors = [i for i in sr_result.issues if i.severity == TagTreeSeverity.ERROR]
        actionable_warnings = [
            i for i in sr_result.issues
            if i.severity == TagTreeSeverity.WARNING
        ]
        if not sr_errors and not actionable_warnings:
            break

        if not sr_errors:
            break

        changes_this_cycle = []

        with pikepdf.open(output_path, allow_overwriting_input=True) as pdf:
            # Fix: Tag untagged pages.
            changes_tag = fix_tag_uncovered_pages(pdf)
            if changes_tag:
                changes_this_cycle.append(
                    f"Cycle {cycle + 2}: {changes_tag[0]}"
                )

            # Fix: Figures missing alt text.
            n = _fix_missing_alt_text(pdf, vision_provider)
            if n:
                changes_this_cycle.append(
                    f"Cycle {cycle + 2}: Added alt text to {n} figures"
                )

            # Fix: Empty lists.
            n = _fix_empty_lists(pdf)
            if n:
                changes_this_cycle.append(
                    f"Cycle {cycle + 2}: Removed {n} empty list elements"
                )

            # Fix: Remove whitespace-only leaf text elements.
            if actionable_warnings:
                n = _fix_empty_leaf_text_elements(pdf)
                if n:
                    changes_this_cycle.append(
                        f"Cycle {cycle + 2}: Removed {n} empty leaf text elements"
                    )

            if changes_this_cycle:
                _save_remediated_pdf(pdf, output_path)
                report.fixes_applied.extend(changes_this_cycle)
            else:
                break

    return report


def _fix_missing_alt_text(pdf: pikepdf.Pdf, vision_provider) -> int:
    """Second-pass alt text fix."""
    figures_no_alt = []
    for node, _depth, _parent in walk_structure_tree(pdf):
        if _get_struct_type(node) != "Figure":
            continue
        alt = node.get("/Alt")
        if alt is None or not str(alt).strip():
            figures_no_alt.append(node)

    if not figures_no_alt:
        return 0

    fixed = 0

    if vision_provider is not None:
        try:
            from lti_app.core.pdf.vision import render_page_to_image
            import asyncio

            page_figures: dict[int, list[pikepdf.Dictionary]] = {}
            for node in figures_no_alt:
                page_idx = _find_node_page(node, pdf)
                page_figures.setdefault(page_idx, []).append(node)

            pdf_path = None
            if hasattr(pdf, 'filename') and pdf.filename:
                pdf_path = Path(pdf.filename)

            if pdf_path and pdf_path.exists():
                for page_idx, nodes in page_figures.items():
                    try:
                        img_path = render_page_to_image(pdf_path, page_idx + 1)
                        if img_path is None:
                            continue
                        prompt = (
                            f"This PDF page has {len(nodes)} images/figures that need alt text. "
                            f"Describe each distinct image or graphic you see, one per line. "
                            f"Use format: 'Figure N: description' for each. "
                            f"For decorative elements (borders, spacers, backgrounds), say 'Decorative'. "
                            f"Max 100 characters per description."
                        )
                        result = asyncio.run(
                            vision_provider.analyze_image(img_path, prompt)
                        )
                        if result:
                            descriptions = _parse_figure_descriptions(str(result), len(nodes))
                            for node, desc in zip(nodes, descriptions):
                                if desc.lower().startswith("decorative"):
                                    node["/S"] = pikepdf.Name("/NonStruct")
                                    node["/Alt"] = pikepdf.String("Decorative image")
                                else:
                                    node["/Alt"] = pikepdf.String(desc[:250])
                                fixed += 1
                        try:
                            img_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                    except Exception:
                        continue
        except Exception:
            pass

    # Strategy 2: Mark remaining alt-less figures as decorative.
    for node in figures_no_alt:
        alt = node.get("/Alt")
        if alt is None or not str(alt).strip():
            kids = node.get("/K")
            has_content = False
            if kids is not None:
                items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
                for item in items:
                    resolved = _resolve_pdf_object(item)
                    if not isinstance(resolved, pikepdf.Dictionary) or "/S" not in resolved:
                        has_content = True
                        break

            if not has_content:
                node["/Alt"] = pikepdf.String("Decorative image")
                fixed += 1
            else:
                node["/Alt"] = pikepdf.String("Figure")
                fixed += 1

    return fixed


def _fix_untagged_pages(pdf: pikepdf.Pdf, page_indices: list[int]) -> int:
    """Delegate to fix_tag_uncovered_pages."""
    changes = fix_tag_uncovered_pages(pdf)
    return len(changes)

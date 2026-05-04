"""Vision-model analysis for PDF accessibility checks.

Renders PDF pages to images and sends them to a vision model for
spatial analysis that can't be done with structure-tree inspection
alone -- reading order validation and color contrast estimation.

Adapted from Project Remedy's ``pdf_vision.py`` to use the unified
:class:`~lti_app.core.ai.vision_client.VisionClient` interface built
in Phase 4 instead of provider-specific implementations.

Usage::

    from lti_app.core.ai.vision_client import get_vision_client

    client = get_vision_client()
    analyzer = VisionAnalyzer(vision_client=client)
    results = await analyzer.analyze_reading_order(Path("doc.pdf"), pages=[1, 2, 3])
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import pikepdf
from pydantic import BaseModel, Field

from lti_app.core.pdf.checker import walk_structure_tree, _get_struct_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models (Pydantic)
# ---------------------------------------------------------------------------


class ReadingOrderIssue(BaseModel):
    """A single reading-order problem identified by the vision model."""

    page: int
    description: str
    severity: str = "warning"  # "error" | "warning" | "info"
    suggestion: str = ""


class ContrastIssue(BaseModel):
    """A color contrast problem identified by the vision model."""

    page: int
    description: str
    location: str = ""


class VisionCheckResult(BaseModel):
    """Result of vision-based analysis for one or more pages."""

    reading_order_issues: list[ReadingOrderIssue] = Field(default_factory=list)
    contrast_issues: list[ContrastIssue] = Field(default_factory=list)
    raw_responses: dict[int, str] = Field(default_factory=dict)

    @property
    def reading_order_passed(self) -> bool:
        return not any(i.severity == "error" for i in self.reading_order_issues)

    @property
    def contrast_passed(self) -> bool:
        return len(self.contrast_issues) == 0


# ---------------------------------------------------------------------------
# Prompt builders (inlined from Project Remedy vision_prompts.py)
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


def _build_reading_order_prompt(*, structure_order: str, layout_hint: str = "") -> str:
    hint = f"Layout hint from local analysis: {layout_hint}\n" if layout_hint else ""
    return (
        "You are a PDF accessibility expert. Compare the structure-tree order below against the visual page layout.\n"
        f"{hint}"
        f"Structure tree order:\n{structure_order}\n\n"
        f"{_LAYOUT_RULES}\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "page_layout": "single_column" | "hero_cover" | "brochure_sidebar" | "form_checklist" | "table_directory" | "schedule_grid" | "mixed_graphic_flyer" | "map_infographic" | "report_cover" | "unknown_complex",\n'
        '  "issues": [{"severity": "error" | "warning", "description": "...", "suggestion": "..."}],\n'
        '  "summary": "..."\n'
        "}\n"
        "If the reading order is acceptable, return an empty issues array."
    )


def _build_contrast_detection_prompt(level: str = "AA") -> str:
    normal = "4.5:1" if level == "AA" else "7.0:1"
    large = "3.0:1" if level == "AA" else "4.5:1"
    return (
        f"Analyze this PDF page image for color contrast issues under WCAG {level}.\n"
        f"{_LAYOUT_RULES}\n"
        "Examine text, image-of-text, form affordances, icons, lines, fills, and borders.\n"
        "Return ONLY valid JSON matching the provided schema.\n"
        f"Thresholds: normal text {normal}, large text {large}, non-text graphics 3.0:1."
    )


# ---------------------------------------------------------------------------
# PDF page renderer
# ---------------------------------------------------------------------------


def render_page_to_image(pdf_path: Path, page_num: int, dpi: int = 150) -> Path:
    """Render a single PDF page to a PNG image.

    Tries pdf2image (poppler) first, then falls back to PyMuPDF (fitz).

    Returns the path to the temporary PNG file.
    """
    try:
        from pdf2image import convert_from_path

        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=page_num,
            last_page=page_num,
            fmt="png",
        )
        if images:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            images[0].save(tmp.name, "PNG")
            return Path(tmp.name)
    except ImportError:
        pass

    # Fallback: use PyMuPDF (fitz) if available.
    try:
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(str(pdf_path))
        page = doc[page_num - 1]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pix.save(tmp.name)
        doc.close()
        return Path(tmp.name)
    except ImportError:
        pass

    raise RuntimeError(
        "No PDF renderer available. Install pdf2image (poppler) or PyMuPDF: "
        "pip install pdf2image  OR  pip install pymupdf"
    )


# ---------------------------------------------------------------------------
# Structure order extractor
# ---------------------------------------------------------------------------


def _get_page_structure_order(pdf_path: Path, page_num: int) -> str:
    """Extract the structure tree reading order for a specific page.

    Returns a numbered list of structure elements on that page.
    """
    lines: list[str] = []

    with pikepdf.open(pdf_path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            return "(invalid page number)"

        target_page = pdf.pages[page_num - 1]
        order = 0

        for node, depth, _parent in walk_structure_tree(pdf):
            # Check if this node is on the target page.
            pg = node.get("/Pg")
            if pg is None:
                # Check MCR children for page ref.
                kids = node.get("/K")
                if kids is None:
                    continue
                items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
                on_page = False
                for item in items:
                    resolved = item
                    if hasattr(item, "resolve"):
                        try:
                            resolved = item.resolve()
                        except Exception:
                            continue
                    if isinstance(resolved, pikepdf.Dictionary):
                        item_pg = resolved.get("/Pg")
                        if item_pg is not None:
                            try:
                                page_obj = item_pg.resolve() if hasattr(item_pg, "resolve") else item_pg
                                if page_obj == target_page.obj:
                                    on_page = True
                                    break
                            except Exception:
                                pass
                if not on_page:
                    continue
            else:
                try:
                    resolved_pg = pg.resolve() if hasattr(pg, "resolve") else pg
                    if resolved_pg != target_page.obj:
                        continue
                except Exception:
                    continue

            stype = _get_struct_type(node)
            if not stype:
                continue

            order += 1
            alt = node.get("/Alt")
            indent = "  " * min(depth, 4)
            line = f"{order:3d}. {indent}/{stype}"
            if alt and str(alt).strip():
                line += f'  (alt: "{str(alt)[:40]}")'
            lines.append(line)

    return "\n".join(lines) if lines else "(no structure elements found on this page)"


# ---------------------------------------------------------------------------
# Helper: build vision messages for VisionClient.chat()
# ---------------------------------------------------------------------------


def _build_vision_messages(image_path: Path, prompt: str) -> list[dict[str, Any]]:
    """Build OpenAI-compatible chat messages with an embedded image.

    The resulting messages list can be passed directly to
    ``VisionClient.chat(messages=...)``.
    """
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode()
    suffix = image_path.suffix.lstrip(".").lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "image/png"
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }
    ]


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------


def _parse_json_response(text: str) -> dict[str, Any] | None:
    """Extract JSON from a vision model response that may contain markdown fences."""
    # Try direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences.
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Main analyzer class
# ---------------------------------------------------------------------------


class VisionAnalyzer:
    """Analyze PDF accessibility using a vision model.

    Accepts the unified :class:`~lti_app.core.ai.vision_client.VisionClient`
    built in Phase 4, which handles provider selection, retries, and
    rate-limiting internally.

    Parameters
    ----------
    vision_client:
        A ``VisionClient`` instance (e.g. from ``get_vision_client()``).
        If ``None``, vision-based analysis methods will raise when called.
    """

    def __init__(self, vision_client: Any | None = None) -> None:
        self._client = vision_client

    def _ensure_client(self) -> None:
        """Raise if no vision client was provided."""
        if self._client is None:
            raise RuntimeError(
                "VisionAnalyzer requires a VisionClient. "
                "Pass one via VisionAnalyzer(vision_client=get_vision_client())."
            )

    async def _analyze_image(self, image_path: Path, prompt: str) -> str:
        """Send an image + prompt to the vision model via VisionClient.chat()."""
        self._ensure_client()
        messages = _build_vision_messages(image_path, prompt)
        model = self._client.get_primary_model()
        return await self._client.chat(
            model=model,
            messages=messages,
            timeout=120.0,
        )

    async def analyze_reading_order(
        self,
        pdf_path: Path,
        pages: list[int] | None = None,
        dpi: int = 150,
    ) -> VisionCheckResult:
        """Analyze reading order on specified pages (or all pages).

        Renders each page to an image, builds the structure-tree order for
        that page, and asks the vision model to compare.
        """
        self._ensure_client()
        result = VisionCheckResult()

        with pikepdf.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

        if pages is None:
            pages = list(range(1, total_pages + 1))

        for page_num in pages:
            try:
                image_path = render_page_to_image(pdf_path, page_num, dpi=dpi)
            except RuntimeError as e:
                result.reading_order_issues.append(
                    ReadingOrderIssue(
                        page=page_num,
                        description=f"Could not render page: {e}",
                        severity="warning",
                    )
                )
                continue

            try:
                structure_order = _get_page_structure_order(pdf_path, page_num)
                prompt = _build_reading_order_prompt(structure_order=structure_order)

                response = await self._analyze_image(image_path, prompt)
                result.raw_responses[page_num] = response

                # Parse JSON from response.
                parsed = _parse_json_response(response)
                if parsed and "issues" in parsed:
                    for issue in parsed["issues"]:
                        result.reading_order_issues.append(
                            ReadingOrderIssue(
                                page=page_num,
                                description=issue.get("description", ""),
                                severity=issue.get("severity", "warning"),
                                suggestion=issue.get("suggestion", ""),
                            )
                        )

            except Exception as e:
                logger.warning("Vision analysis failed for page %d: %s", page_num, e)
                result.reading_order_issues.append(
                    ReadingOrderIssue(
                        page=page_num,
                        description=f"Vision analysis error: {e}",
                        severity="warning",
                    )
                )
            finally:
                # Clean up temp image.
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass

        return result

    async def analyze_contrast(
        self,
        pdf_path: Path,
        pages: list[int] | None = None,
        dpi: int = 150,
    ) -> VisionCheckResult:
        """Analyze color contrast on specified pages."""
        self._ensure_client()
        result = VisionCheckResult()

        with pikepdf.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

        if pages is None:
            pages = list(range(1, total_pages + 1))

        for page_num in pages:
            try:
                image_path = render_page_to_image(pdf_path, page_num, dpi=dpi)
            except RuntimeError as e:
                result.contrast_issues.append(
                    ContrastIssue(
                        page=page_num,
                        description=f"Could not render page: {e}",
                    )
                )
                continue

            try:
                prompt = _build_contrast_detection_prompt("AA")
                response = await self._analyze_image(image_path, prompt)
                result.raw_responses[page_num] = response

                parsed = _parse_json_response(response)
                if parsed and "issues" in parsed:
                    for issue in parsed["issues"]:
                        result.contrast_issues.append(
                            ContrastIssue(
                                page=page_num,
                                description=issue.get("description", ""),
                                location=issue.get("location", ""),
                            )
                        )

            except Exception as e:
                logger.warning("Contrast analysis failed for page %d: %s", page_num, e)
                result.contrast_issues.append(
                    ContrastIssue(
                        page=page_num,
                        description=f"Vision analysis error: {e}",
                    )
                )
            finally:
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass

        return result

    async def analyze_all(
        self,
        pdf_path: Path,
        pages: list[int] | None = None,
        dpi: int = 150,
    ) -> VisionCheckResult:
        """Run both reading order and contrast analysis."""
        ro_result, contrast_result = await asyncio.gather(
            self.analyze_reading_order(pdf_path, pages, dpi),
            self.analyze_contrast(pdf_path, pages, dpi),
        )
        # Merge results.
        merged = VisionCheckResult(
            reading_order_issues=ro_result.reading_order_issues,
            contrast_issues=contrast_result.contrast_issues,
            raw_responses={**ro_result.raw_responses, **contrast_result.raw_responses},
        )
        return merged

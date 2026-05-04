"""Accessibility utility endpoints — contrast checker, equation OCR."""

import base64

import structlog
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from lti_app.core.pdf.contrast.color_utils import (
    contrast_ratio,
    hex_to_rgb,
    is_large_text,
    wcag_threshold,
)

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/accessibility", tags=["accessibility"])


# ---------------------------------------------------------------------------
# Contrast checker
# ---------------------------------------------------------------------------


class ContrastCheckRequest(BaseModel):
    foreground: str  # hex color e.g. "#333333"
    background: str  # hex color e.g. "#FFFFFF"
    font_size: float = 16.0
    is_bold: bool = False


class ContrastCheckResult(BaseModel):
    ratio: float
    aa_pass: bool
    aaa_pass: bool
    large_text: bool
    aa_threshold: float
    aaa_threshold: float
    suggestion: str = ""


@router.post("/contrast-check")
async def check_contrast(body: ContrastCheckRequest) -> ContrastCheckResult:
    """Check WCAG contrast ratio between two colors."""
    fg = hex_to_rgb(body.foreground)
    bg = hex_to_rgb(body.background)
    ratio = contrast_ratio(fg, bg)
    large = is_large_text(body.font_size, body.is_bold)
    aa_thresh = wcag_threshold("AA", large)
    aaa_thresh = wcag_threshold("AAA", large)

    suggestion = ""
    if ratio < aa_thresh:
        suggestion = (
            f"Contrast ratio {ratio:.2f}:1 fails WCAG AA "
            f"(needs {aa_thresh}:1). Use darker text or lighter background."
        )

    return ContrastCheckResult(
        ratio=round(ratio, 2),
        aa_pass=ratio >= aa_thresh,
        aaa_pass=ratio >= aaa_thresh,
        large_text=large,
        aa_threshold=aa_thresh,
        aaa_threshold=aaa_thresh,
        suggestion=suggestion,
    )


# ---------------------------------------------------------------------------
# Equation OCR
# ---------------------------------------------------------------------------


class EquationOCRResult(BaseModel):
    latex: str
    mathml: str
    error: str = ""


@router.post("/equation-ocr")
async def equation_ocr(image: UploadFile = File(...)) -> EquationOCRResult:
    """Recognize handwritten equation from an uploaded image -> LaTeX + MathML."""
    from lti_app.core.ai.equation_ocr import recognize_equation

    image_bytes = await image.read()
    if not image_bytes:
        return EquationOCRResult(latex="", mathml="", error="Empty image upload")

    try:
        result = await recognize_equation(image_bytes)
        return EquationOCRResult(**result)
    except Exception as exc:
        _logger.error("equation_ocr_error", error=str(exc))
        return EquationOCRResult(latex="", mathml="", error=str(exc))

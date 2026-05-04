"""Color contrast analysis and remediation — WCAG 1.4.3, 1.4.6, 1.4.11."""

from __future__ import annotations

from lti_app.core.pdf.contrast.models import (
    ContrastAnalysis,
    ContrastIssue,
    ContrastIssueType,
    PageContrastResult,
)
from lti_app.core.pdf.contrast.color_utils import (
    cmyk_to_rgb,
    contrast_ratio,
    gray_to_rgb,
    hex_to_rgb,
    int_color_to_rgb,
    is_large_text,
    nearest_passing_color,
    relative_luminance,
    rgb_to_hex,
    wcag_threshold,
    NON_TEXT_THRESHOLD,
)
from lti_app.core.pdf.contrast.detector import ContrastDetector
from lti_app.core.pdf.contrast.image_enhancer import ImageContrastEnhancer
from lti_app.core.pdf.contrast.remediator import ContrastRemediator

__all__ = [
    # Models
    "ContrastIssue",
    "ContrastAnalysis",
    "PageContrastResult",
    "ContrastIssueType",
    # Color utils
    "relative_luminance",
    "contrast_ratio",
    "is_large_text",
    "wcag_threshold",
    "hex_to_rgb",
    "rgb_to_hex",
    "cmyk_to_rgb",
    "gray_to_rgb",
    "int_color_to_rgb",
    "nearest_passing_color",
    "NON_TEXT_THRESHOLD",
    # Detector, enhancer, remediator
    "ContrastDetector",
    "ImageContrastEnhancer",
    "ContrastRemediator",
]

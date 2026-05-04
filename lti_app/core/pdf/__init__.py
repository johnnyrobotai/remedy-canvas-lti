"""PDF accessibility checking package."""

from lti_app.core.pdf.models import (
    CheckReport,
    CheckResult,
    CheckStatus,
    FixReport,
    TagTreeSeverity,
    ScreenReaderIssue,
    TagTreeReport,
)
from lti_app.core.pdf.checker import PDFAccessibilityChecker

__all__ = [
    "CheckStatus",
    "CheckResult",
    "CheckReport",
    "PDFAccessibilityChecker",
    "FixReport",
    "TagTreeSeverity",
    "ScreenReaderIssue",
    "TagTreeReport",
]

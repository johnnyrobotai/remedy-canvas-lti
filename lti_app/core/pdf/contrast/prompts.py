"""Vision AI prompts for contrast detection and validation."""

from __future__ import annotations


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


def contrast_detection_prompt(level: str = "AA") -> str:
    """Build the prompt for AI-driven contrast issue detection."""
    normal = "4.5:1" if level == "AA" else "7.0:1"
    large = "3.0:1" if level == "AA" else "4.5:1"
    return (
        f"Analyze this PDF page image for color contrast issues under WCAG {level}.\n"
        f"{_LAYOUT_RULES}\n"
        "Examine text, image-of-text, form affordances, icons, lines, fills, and borders.\n"
        "Return ONLY valid JSON matching the provided schema.\n"
        f"Thresholds: normal text {normal}, large text {large}, non-text graphics 3.0:1."
    )


def contrast_validation_prompt(level: str, issue_descriptions: str) -> str:
    """Build the prompt for AI-driven fix validation."""
    return (
        f"Verify whether the following contrast issues on this PDF page now pass WCAG {level}.\n"
        f"{issue_descriptions}\n\n"
        "Return ONLY valid JSON matching the provided schema. "
        "Judge text, image-of-text, and non-text graphics against the correct threshold for each case."
    )

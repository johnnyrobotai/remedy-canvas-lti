"""Shared heuristics for deciding when course images need alt-text generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bs4 import Tag

GENERIC_ALT_TEXTS = {
    "image",
    "photo",
    "picture",
    "pic",
    "img",
    "graphic",
    "icon",
    "logo",
    "banner",
    "screenshot",
    "screen shot",
    "untitled",
    "null",
    "undefined",
    "placeholder",
    "test",
    "temp",
    "temporary",
    "related image",
}

GENERIC_ALT_PREFIXES = (
    "image result for",
    "image of",
    "an image of",
    "photo of",
    "a photo of",
    "picture of",
    "a picture of",
    "graphic of",
    "diagram of",
    "a diagram of",
    "illustration of",
    "chart of",
    "graph of",
    "figure of",
)

IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
)

REFERENCE_CODE_RE = re.compile(r"^[a-z]?\d+(?:[.\-_]\d+)*(?:[a-z]+)?$", re.I)
# Simple char class avoids catastrophic backtracking on long alt text.
# Previous pattern `^[a-z]+\d+(?:[.\-_]?[a-z0-9]+)*$` would exponentially
# backtrack on inputs like compacted sentence text ("abeautifulsunset...")
# and hang for seconds-to-minutes per regex call.
COMPACT_REFERENCE_RE = re.compile(r"^[a-z]+\d+[a-z0-9._\-]*$", re.I)

# Guard: reference codes are short by definition (e.g., "fig1.2a", "table3-b").
# Refusing longer inputs both matches reality and removes any pathological
# regex-evaluation risk.
_MAX_REFERENCE_CODE_LEN = 40
MAX_ALT_TEXT_CHARS = 100
ALT_TEXT_TRUNCATION_SOFT_LIMIT = 70


@dataclass(frozen=True)
class AltTextAssessment:
    """Shared assessment for an image's alt-text state."""

    needs_generation: bool
    reason: str | None = None
    skip_reason: str | None = None


def assess_image_tag(tag: Tag) -> AltTextAssessment:
    """Assess whether an image should be sent for alt-text generation."""
    skip_reason = get_equation_image_skip_reason(
        src=tag.get("src", ""),
        classes=tag.get("class"),
        data_equation_content=tag.get("data-equation-content"),
    )
    if skip_reason:
        return AltTextAssessment(
            needs_generation=False,
            reason=skip_reason,
            skip_reason=skip_reason,
        )

    reason = get_inadequate_alt_reason(tag.get("alt"))
    return AltTextAssessment(
        needs_generation=reason is not None,
        reason=reason,
    )


def get_equation_image_skip_reason(
    *,
    src: str = "",
    classes: Any = None,
    data_equation_content: str | None = None,
) -> str | None:
    """Return a skip reason for equation/math images that should not be regenerated."""
    normalized_classes = _normalize_classes(classes)
    normalized_src = (src or "").lower()

    if "equation_image" in normalized_classes:
        return "equation_image"
    if "/equation_images/" in normalized_src:
        return "equation_image"
    if data_equation_content:
        return "equation_image"
    return None


def get_inadequate_alt_reason(alt_text: str | None) -> str | None:
    """Return a reason when alt text is missing or too weak to preserve."""
    alt = (alt_text or "").strip()
    if not alt:
        return "missing_alt"

    alt_lower = alt.lower()
    if alt_lower in GENERIC_ALT_TEXTS:
        return "generic_placeholder"
    if any(alt_lower.startswith(prefix) for prefix in GENERIC_ALT_PREFIXES):
        return "generic_placeholder"
    if _is_filename_like(alt_lower):
        return "filename"
    if _is_reference_code(alt):
        return "reference_code"
    if len(alt) > MAX_ALT_TEXT_CHARS:
        return "too_long"
    return None


def describe_inadequate_alt(reason: str, alt_text: str) -> str:
    """Create a user-facing issue description for an inadequate alt string."""
    if reason == "generic_placeholder":
        return f'Alt text "{alt_text}" is not descriptive'
    if reason == "filename":
        return f'Alt text appears to be a filename: "{alt_text}"'
    if reason == "reference_code":
        return f'Alt text "{alt_text}" looks like an internal reference code'
    if reason == "too_long":
        return (
            f'Alt text must stay under 100 characters '
            f'({len(alt_text)} chars)'
        )
    return f'Alt text "{alt_text}" needs improvement'


def truncate_alt_text(alt_text: str | None) -> str:
    """Trim alt text to the shared under-120-character limit."""
    normalized = " ".join((alt_text or "").split()).strip()
    if len(normalized) <= MAX_ALT_TEXT_CHARS:
        return normalized

    truncated = normalized[:MAX_ALT_TEXT_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > ALT_TEXT_TRUNCATION_SOFT_LIMIT:
        return truncated[:last_space].rstrip()
    return truncated.rstrip()


def _normalize_classes(classes: Any) -> set[str]:
    if not classes:
        return set()
    if isinstance(classes, str):
        return {part.strip().lower() for part in classes.split() if part.strip()}
    return {str(part).strip().lower() for part in classes if str(part).strip()}


def _is_filename_like(text: str) -> bool:
    return any(text.endswith(ext) for ext in IMAGE_EXTENSIONS)


def _is_reference_code(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) > _MAX_REFERENCE_CODE_LEN:
        return False
    if not any(char.isdigit() for char in compact):
        return False
    return bool(
        REFERENCE_CODE_RE.fullmatch(compact)
        or COMPACT_REFERENCE_RE.fullmatch(compact)
    )

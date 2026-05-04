"""File eligibility classifier for selective remediation (CLU-85).

Single source of truth for "should this file be converted by phase 4 of
AutoRemedy?" Used by:

- GET /api/courses/{id}/autoremedy/conversion-candidates to render the
  Files review UI with the correct default-selected / grayed-out state
- autoremedy_service._run_convert_and_replace_phase to enforce the same
  eligibility at runtime

By sharing one classifier between the two sites, the UI cannot lie about
what the backend would do. The rules match the existing CLU-69 skip
mechanism in conversion_service.py but split it into hard vs soft
exclusions (CLU-85 spec, Codex review 2026-04-08 finding 7).
"""
from dataclasses import dataclass

# CLU-69 filename keywords — soft exclusion (user can override).
# Matches case-insensitively as a substring against the filename.
_SOFT_SKIP_KEYWORDS = frozenset({
    "textbook",
    "catalog",
    "schedule",
    "calendar",
    "manual",
    "handbook",
    "reference",
    "readings",
})

# Legacy binary formats — hard exclusion, conversion pipeline rejects
# these at the LibreOffice/LiteParse CLI step anyway.
_LEGACY_FORMATS = frozenset({".ppt", ".pps"})

# PDFs with more than this many source pages are hard-excluded — CLU-69
# real-world trigger was a 315-page Art Appreciation textbook that took
# 49 minutes to chapter-convert into 16 unread Canvas pages.
_MAX_PDF_PAGES = 50


@dataclass
class FileEligibility:
    """Classification result for a convertible file.

    ``phase4_eligible`` is True for any file that phase 4 would normally
    consider — failed PDFs plus convertible Office docs. It does NOT
    imply the file will actually be converted (``default_selected`` or
    user-override decides that). It's used by the frontend to decide
    whether the row should even appear in the Files view.

    ``default_selected`` is the initial checkbox state. False for hard
    and soft exclusions; True for ordinary files.

    ``hard_excluded`` means the user cannot re-check the item in the UI
    (checkbox disabled, grayed out). Only True for cases the backend
    would refuse to run regardless of user preference.

    ``exclude_reason`` is a short human-readable string shown next to
    grayed-out or soft-excluded rows. Null for ordinary files.
    """
    phase4_eligible: bool
    default_selected: bool
    hard_excluded: bool
    exclude_reason: str | None


def classify_file_for_conversion(
    filename: str,
    content_type: str,
    size_bytes: int,
    page_count: int | None,
) -> FileEligibility:
    """Classify a file for the selective-remediation UI.

    Rule order (first match wins for hard exclusions):

    1. Legacy format (``.ppt``, ``.pps``) → hard exclude.
    2. PDF with > 50 source pages → hard exclude.
    3. Filename contains a CLU-69 keyword → soft exclude
       (``default_selected=False``, ``hard_excluded=False``). The user
       can re-check it in the UI for this run.
    4. Otherwise → default-selected, no reason.

    ``content_type`` is accepted for future use (e.g. mime-type based
    hard exclusions) but is not currently consulted by any rule. It is
    part of the signature so callers don't need to be rewritten when
    a rule is added later.
    """
    lower_name = filename.lower()

    # Rule 1: legacy format — hard exclude but still visible in the Files view.
    # Phase 4 does route .ppt/.pps through the conversion pipeline (LibreOffice
    # rejects them at the CLI step), so phase4_eligible is True. hard_excluded
    # is True so the UI grays the row out and the user cannot re-check it.
    for ext in _LEGACY_FORMATS:
        if lower_name.endswith(ext):
            return FileEligibility(
                phase4_eligible=True,
                default_selected=False,
                hard_excluded=True,
                exclude_reason=f"Legacy {ext} format not supported — re-save as .pptx",
            )

    # Rule 2: PDF over page threshold
    if lower_name.endswith(".pdf") and page_count is not None and page_count > _MAX_PDF_PAGES:
        return FileEligibility(
            phase4_eligible=True,
            default_selected=False,
            hard_excluded=True,
            exclude_reason=f"PDF has {page_count} pages (limit is {_MAX_PDF_PAGES})",
        )

    # Rule 3: filename keyword match (soft, user can override)
    for keyword in _SOFT_SKIP_KEYWORDS:
        if keyword in lower_name:
            return FileEligibility(
                phase4_eligible=True,
                default_selected=False,
                hard_excluded=False,
                exclude_reason=f"Filename contains '{keyword}' — likely a reference document",
            )

    # Default: eligible and selected
    return FileEligibility(
        phase4_eligible=True,
        default_selected=True,
        hard_excluded=False,
        exclude_reason=None,
    )

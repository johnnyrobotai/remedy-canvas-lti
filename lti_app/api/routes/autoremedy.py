"""AutoRemedy API endpoints."""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    AutoRemedyRepository,
    ConversionRepository,
    ExclusionRepository,
    FileAuditRepository,
    PDFFixRepository,
    RemediationRepository,
    ScanRepository,
    get_autoremedy_repository,
    get_conversion_repository,
    get_exclusion_repository,
    get_file_audit_repository,
    get_pdf_fix_repository,
    get_remediation_repository,
    get_scan_repository,
)
from lti_app.models import ConversionCandidate, SelectiveAutoRemedyRequest
from lti_app.services.autoremedy_service import AutoRemedyService
from lti_app.services.conversion_service import CONVERTIBLE_TYPES
from lti_app.services.file_eligibility import classify_file_for_conversion

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["autoremedy"])


def _get_autoremedy_service(
    autoremedy_repo: AutoRemedyRepository = Depends(get_autoremedy_repository),
    scan_repo: ScanRepository = Depends(get_scan_repository),
    remediation_repo: RemediationRepository = Depends(get_remediation_repository),
    file_audit_repo: FileAuditRepository = Depends(get_file_audit_repository),
    pdf_fix_repo: PDFFixRepository = Depends(get_pdf_fix_repository),
    conversion_repo: ConversionRepository = Depends(get_conversion_repository),
) -> AutoRemedyService:
    return AutoRemedyService(autoremedy_repo, scan_repo, remediation_repo, file_audit_repo, pdf_fix_repo, conversion_repo)


def _ensure_resource_course(resource, course_id: int) -> None:
    if str(getattr(resource, "course_id", "")) != str(course_id):
        raise HTTPException(status_code=404, detail="Resource not found")


@router.post("/{course_id}/autoremedy")
async def start_autoremedy(
    course_id: int,
    session: InstructorSession,
    body: SelectiveAutoRemedyRequest | None = None,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
    scan_repo: ScanRepository = Depends(get_scan_repository),
    file_audit_repo: FileAuditRepository = Depends(get_file_audit_repository),
):
    """Start an AutoRemedy run (CLU-85 selective remediation support).

    The request body is optional. When omitted (Fix My Course flow),
    the full pipeline runs with empty skip lists and no freshness
    check. When a body is supplied (Run Remediation flow from a
    content-type view), the backend verifies the scan_report_id /
    file_report_id match the latest snapshot and returns 409 Stale
    if the user's review was made against outdated data.
    """
    verify_session_course_id(session, course_id)  # CLU-82

    if body is None:
        body = SelectiveAutoRemedyRequest()

    # Freshness check — 409 Stale if the caller pinned a snapshot ID
    # that isn't the most recent one. When the DB has no report at
    # all (latest_*_id is None), we let the request through — there's
    # nothing to be stale against.
    if body.scan_report_id:
        latest_scan_id = scan_repo.get_latest_report_id(str(course_id))
        if latest_scan_id and body.scan_report_id != latest_scan_id:
            raise HTTPException(
                status_code=409,
                detail="Stale scan — please refresh your selections",
            )

    if body.file_report_id:
        latest_audit_id = file_audit_repo.get_latest_report_id(str(course_id))
        if latest_audit_id and body.file_report_id != latest_audit_id:
            raise HTTPException(
                status_code=409,
                detail="Stale file audit — please refresh your selections",
            )

    job = service.create_job(session.session_id, str(course_id))

    # CLU-85 Task 7: thread the request body's selective skip lists
    # into the orchestrator. ``set([])`` for an empty list is fine —
    # the service merges these with permanent exclusions from the DB
    # before kicking off the phases.
    asyncio.create_task(
        service.run_autoremedy(
            job.id,
            session,
            course_id,
            skip_page_identifiers=set(body.skip_page_identifiers),
            skip_file_ids=set(body.skip_file_ids),
        )
    )
    _logger.info(
        "autoremedy_started",
        job_id=job.id,
        course_id=course_id,
        skip_pages=len(body.skip_page_identifiers),
        skip_files=len(body.skip_file_ids),
        reviewed=body.reviewed_content_types,
    )
    return job


@router.get("/{course_id}/autoremedy/latest")
async def get_latest_autoremedy(
    course_id: int,
    session: InstructorSession,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
):
    """Get the most recent AutoRemedy job for this course."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_latest_job(str(course_id))
    if not job:
        raise HTTPException(status_code=404, detail="No AutoRemedy jobs found")
    return job


class ConversionCandidatesResponse(BaseModel):
    """Response body for ``GET /autoremedy/conversion-candidates`` (CLU-85)."""

    file_report_id: str
    candidates: list[ConversionCandidate]


# Office extensions phase 4 picks up via the third branch fallback (used
# when Canvas returns an empty or unexpected content_type). Matches the
# extension list in ``autoremedy_service._run_convert_and_replace_phase``
# line 390 exactly.
_OFFICE_EXTENSIONS = (".docx", ".pptx", ".xlsx")

# Legacy PowerPoint formats. ``application/vnd.ms-powerpoint`` is already
# in ``CONVERTIBLE_TYPES``, so phase 4 would attempt to convert these via
# branch 2 — but the conversion pipeline (LibreOffice) rejects them at
# the CLI step. We surface them in the Files view so the user sees a
# clear "legacy format" reason; ``classify_file_for_conversion`` handles
# the grayed-out hard-exclusion state.
_LEGACY_POWERPOINT_EXTENSIONS = (".ppt", ".pps")


def _is_phase4_candidate(entry) -> bool:
    """Match phase 4's ``docs_to_convert`` logic at
    ``autoremedy_service.py`` lines 383-391 exactly. Phase 4 converts:

      1. Failed PDFs (``is_pdf AND status == "failed"``)
      2. Any file whose ``content_type`` is in ``CONVERTIBLE_TYPES``
         (this set INCLUDES ``application/pdf``, so clean PDFs match
         here too — they appear in the Files view as default-selected
         rows the user can opt out of)
      3. Non-PDFs with ``.docx`` / ``.pptx`` / ``.xlsx`` extensions —
         the ``content_type`` fallback for files where Canvas didn't
         set a useful mime type

    We also let legacy ``.ppt`` / ``.pps`` through so they appear in
    the Files view as grayed-out rows with a "legacy format" reason —
    ``classify_file_for_conversion`` returns ``hard_excluded=True`` for
    them. (They would already match branch 2 via the legacy
    PowerPoint mime type, this list is belt-and-suspenders for the
    case where ``content_type`` is empty.)

    Hiding clean PDFs would create a silent-mismatch footgun: phase 4
    would convert them anyway, and the user would never have seen them
    in the review list. Showing them with ``default_selected=True`` and
    ``has_audit_issues=False`` lets the user uncheck them if they want.
    """
    # Branch 1: failed PDFs
    if entry.is_pdf and entry.status == "failed":
        return True
    # Branch 2: anything with a convertible content_type (CLEAN PDFs
    # match here too — see docstring)
    if entry.content_type in CONVERTIBLE_TYPES:
        return True
    # Branch 3: Office extension fallback for missing content_type
    filename_lower = entry.filename.lower()
    for ext in _OFFICE_EXTENSIONS:
        if filename_lower.endswith(ext):
            return True
    # Legacy PowerPoint — visible grayed-out row in the Files view
    for ext in _LEGACY_POWERPOINT_EXTENSIONS:
        if filename_lower.endswith(ext):
            return True
    return False


@router.get(
    "/{course_id}/autoremedy/conversion-candidates",
    response_model=ConversionCandidatesResponse,
)
async def get_conversion_candidates(
    course_id: int,
    session: InstructorSession,
    file_audit_repo: FileAuditRepository = Depends(get_file_audit_repository),
    exclusion_repo: ExclusionRepository = Depends(get_exclusion_repository),
):
    """Return files the Files review view should display (CLU-85).

    Returns every file phase 4 of AutoRemedy would attempt to convert —
    failed PDFs, clean PDFs (yes, phase 4 converts these too via the
    ``application/pdf`` ∈ ``CONVERTIBLE_TYPES`` branch), Office docs,
    and legacy .ppt/.pps. The shape mirrors phase 4's
    ``docs_to_convert`` filter at ``autoremedy_service.py:383-391``
    EXACTLY so the UI cannot drift from runtime behavior — if phase 4
    will touch a file, it appears in this list.

    Each row carries the CLU-69 ``FileEligibility`` classification so
    the frontend can render the right visual state:

      - ``default_selected=True`` and no reason → ordinary checkbox
        (default on; user can opt out)
      - ``hard_excluded=True`` → grayed-out, disabled checkbox with
        the reason text (e.g. "Legacy .ppt format not supported")
      - ``default_selected=False`` and ``hard_excluded=False`` → soft
        exclusion (default off; user can re-check, e.g. textbooks)

    The frontend treats the response as the source of truth for what
    AutoRemedy will run; passing the resulting checked subset back into
    the start endpoint is how the user opts files in or out.
    """
    verify_session_course_id(session, course_id)  # CLU-82

    latest_report = file_audit_repo.get_latest_report(str(course_id))
    if not latest_report:
        return ConversionCandidatesResponse(file_report_id="", candidates=[])

    report_id = file_audit_repo.get_latest_report_id(str(course_id)) or ""
    permanent = exclusion_repo.get_identifiers_for_course(str(course_id))

    candidates: list[ConversionCandidate] = []
    for entry in latest_report.entries:
        if not _is_phase4_candidate(entry):
            continue

        eligibility = classify_file_for_conversion(
            filename=entry.filename,
            content_type=entry.content_type or "",
            size_bytes=entry.size or 0,
            page_count=entry.page_count,
        )

        candidates.append(
            ConversionCandidate(
                file_id=entry.file_id,
                filename=entry.filename,
                size_bytes=entry.size or 0,
                page_count=entry.page_count,
                content_type=entry.content_type or "",
                phase4_eligible=eligibility.phase4_eligible,
                default_selected=eligibility.default_selected,
                hard_excluded=eligibility.hard_excluded,
                exclude_reason=eligibility.exclude_reason,
                has_audit_issues=entry.status == "failed",
                permanently_excluded=f"file-{entry.file_id}" in permanent,
            )
        )

    return ConversionCandidatesResponse(
        file_report_id=report_id, candidates=candidates
    )


@router.get("/{course_id}/autoremedy/{job_id}")
async def get_autoremedy_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
):
    """Poll AutoRemedy job status."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="AutoRemedy job not found")
    _ensure_resource_course(job, course_id)
    return job


@router.post("/{course_id}/autoremedy/{job_id}/cancel")
async def cancel_autoremedy(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
):
    """Request cancellation of a running AutoRemedy job (CLU-64).

    Sets cancel_requested=True; the orchestrator polls this between phases
    and inside per-page callbacks and exits cleanly with status=failed and
    error="Cancelled by user request" within a few seconds.
    """
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="AutoRemedy job not found")
    _ensure_resource_course(job, course_id)
    if job.status.value not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status.value}; nothing to cancel",
        )
    job.cancel_requested = True
    service._repo.save_job(job)
    _logger.info("autoremedy_cancel_requested", job_id=job_id, course_id=course_id)
    return job


@router.get("/{course_id}/autoremedy/{job_id}/summary")
async def get_autoremedy_summary(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
):
    """Get a structured summary of the AutoRemedy run."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="AutoRemedy job not found")
    _ensure_resource_course(job, course_id)
    return {
        "job_id": job.id,
        "status": job.status.value,
        "phase": job.phase,
        "html": {
            "pages_total": job.html_pages_total,
            "pages_remediated": job.html_pages_remediated,
            "issues_found": job.issues_found,
            "issues_fixed": job.issues_fixed,
        },
        "files": {
            "files_total": job.files_total,
            "files_audited": job.files_audited,
        },
        "pdfs": {
            "fixed": job.pdfs_fixed,
            "fix_failed": job.pdfs_fix_failed,
        },
        "docs_converted": job.docs_converted,
        "remediation_job_id": job.remediation_job_id,
        "file_report_id": job.file_report_id,
    }


class AutoRemedyApplyRequest(BaseModel):
    page_ids: list[str]
    alt_text_overrides: dict[str, str] = {}


@router.post("/{course_id}/autoremedy/{job_id}/apply")
async def apply_autoremedy(
    course_id: int,
    job_id: str,
    body: AutoRemedyApplyRequest,
    session: InstructorSession,
    service: AutoRemedyService = Depends(_get_autoremedy_service),
):
    """Apply approved AutoRemedy remediation previews to Canvas."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job or not job.remediation_job_id:
        raise HTTPException(
            status_code=404,
            detail="AutoRemedy job not found or not yet remediated",
        )
    _ensure_resource_course(job, course_id)
    from lti_app.services.remediation_service import RemediationService

    rem_svc = RemediationService(service._scan_repo, service._remediation_repo)
    return await rem_svc.apply_previews(
        job.remediation_job_id, session, course_id, body.page_ids, body.alt_text_overrides
    )

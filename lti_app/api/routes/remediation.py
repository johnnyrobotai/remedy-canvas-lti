"""Remediation API endpoints: remediate, preview, and apply."""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    RemediationRepository,
    ScanRepository,
    VersionRepository,
    get_remediation_repository,
    get_scan_repository,
    get_version_repository,
)
from lti_app.models import RemediationRequest
from lti_app.services.remediation_service import RemediationService
from lti_app.services.version_service import VersionService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["remediation"])


def _get_remediation_service(
    scan_repo: ScanRepository = Depends(get_scan_repository),
    remediation_repo: RemediationRepository = Depends(get_remediation_repository),
) -> RemediationService:
    return RemediationService(scan_repo, remediation_repo)


def _ensure_resource_course(resource, course_id: int) -> None:
    if str(getattr(resource, "course_id", "")) != str(course_id):
        raise HTTPException(status_code=404, detail="Resource not found")


def _create_background_task(coro):
    return asyncio.create_task(coro)


# ---------------------------------------------------------------------------
# POST /{course_id}/remediate -- Start remediation job
# ---------------------------------------------------------------------------


@router.post("/{course_id}/remediate")
async def start_remediation(
    course_id: int,
    request: RemediationRequest,
    session: InstructorSession,
    service: RemediationService = Depends(_get_remediation_service),
):
    """Start a background remediation job."""
    verify_session_course_id(session, course_id)
    job = service.create_remediation_job(session.session_id, str(course_id), request)

    _create_background_task(service.run_remediation(job.id, session, course_id))

    _logger.info("remediation_started", job_id=job.id, course_id=course_id)
    return job


# ---------------------------------------------------------------------------
# GET /{course_id}/remediate/{job_id} -- Poll job status
# ---------------------------------------------------------------------------


@router.get("/{course_id}/remediate/{job_id}")
async def get_remediation_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: RemediationService = Depends(_get_remediation_service),
):
    """Poll remediation job status and progress."""
    verify_session_course_id(session, course_id)
    job = service._remediation_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Remediation job not found")
    _ensure_resource_course(job, course_id)
    return job


# ---------------------------------------------------------------------------
# GET /{course_id}/remediate/{job_id}/previews -- List preview summaries
# ---------------------------------------------------------------------------


@router.get("/{course_id}/remediate/{job_id}/previews")
async def get_previews(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: RemediationService = Depends(_get_remediation_service),
):
    """List preview summaries (no full HTML) for a remediation job."""
    verify_session_course_id(session, course_id)
    job = service._remediation_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Remediation job not found")
    _ensure_resource_course(job, course_id)
    previews = service._remediation_repo.get_previews(job_id)
    summaries = [
        {
            "page_id": p.page_id,
            "page_title": p.page_title,
            "content_type": p.content_type.value,
            "issues_fixed_count": len(p.issues_fixed),
            "issues_fixed": p.issues_fixed,
            "canvas_tags_stripped": p.canvas_tags_stripped,
            "canvas_attributes_stripped": p.canvas_attributes_stripped,
            "alt_texts_count": len([
                r for r in p.alt_text_results
                if r.status.value == "generated"
            ]),
        }
        for p in previews
    ]
    return {"previews": summaries}


# ---------------------------------------------------------------------------
# GET /{course_id}/remediate/{job_id}/previews/{page_id} -- Full preview
# ---------------------------------------------------------------------------


@router.get("/{course_id}/remediate/{job_id}/previews/{page_id}")
async def get_preview(
    course_id: int,
    job_id: str,
    page_id: str,
    session: InstructorSession,
    service: RemediationService = Depends(_get_remediation_service),
):
    """Get a single preview with full original and remediated HTML."""
    verify_session_course_id(session, course_id)
    job = service._remediation_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Remediation job not found")
    _ensure_resource_course(job, course_id)
    preview = service._remediation_repo.get_preview(job_id, page_id)
    if not preview:
        raise HTTPException(status_code=404, detail="Preview not found")
    return preview


# ---------------------------------------------------------------------------
# POST /{course_id}/remediate/{job_id}/apply -- Apply approved previews
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    page_ids: list[str]
    alt_text_overrides: dict[str, str] = {}


@router.post("/{course_id}/remediate/{job_id}/apply")
async def apply_previews(
    course_id: int,
    job_id: str,
    body: ApplyRequest,
    session: InstructorSession,
    service: RemediationService = Depends(_get_remediation_service),
):
    """Apply selected remediation previews to Canvas."""
    verify_session_course_id(session, course_id)
    job = service._remediation_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Remediation job not found")
    _ensure_resource_course(job, course_id)

    result = await service.apply_previews(
        job_id, session, course_id, body.page_ids, body.alt_text_overrides
    )
    return result


# ---------------------------------------------------------------------------
# Version history endpoints
# ---------------------------------------------------------------------------

def _get_version_service(
    version_repo: VersionRepository = Depends(get_version_repository),
) -> VersionService:
    return VersionService(version_repo)


@router.get("/{course_id}/versions/{page_id}")
async def get_versions(
    course_id: int,
    page_id: str,
    session: InstructorSession,
    version_service: VersionService = Depends(_get_version_service),
):
    """List version history for a page."""
    verify_session_course_id(session, course_id)
    versions = version_service.get_versions(str(course_id), page_id)
    return {"versions": [v.model_dump(mode="json") for v in versions]}


@router.post("/{course_id}/versions/{version_id}/restore")
async def restore_version(
    course_id: int,
    version_id: str,
    session: InstructorSession,
    version_service: VersionService = Depends(_get_version_service),
):
    """Restore a previous version of a page."""
    verify_session_course_id(session, course_id)
    version = version_service.get_version(version_id)
    if not version or version.course_id != str(course_id):
        raise HTTPException(status_code=404, detail="Version or page not found")
    success = await version_service.restore_version(version_id, session, course_id)
    if not success:
        raise HTTPException(status_code=404, detail="Version or page not found")
    return {"restored": True, "version_id": version_id}


# ---------------------------------------------------------------------------
# Alt text regenerate endpoint
# ---------------------------------------------------------------------------


class RegenerateRequest(BaseModel):
    image_src: str
    page_id: str


@router.post("/{course_id}/alt-text/regenerate")
async def regenerate_alt_text(
    course_id: int,
    body: RegenerateRequest,
    session: InstructorSession,
):
    """Regenerate alt text for a single image."""
    verify_session_course_id(session, course_id)
    from lti_app.canvas.client import CanvasClient
    from lti_app.core.ai.alt_text import AltTextGenerator
    from lti_app.core.ai.image_fetcher import ImageFetcher
    from lti_app.models import CourseImage

    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        image = CourseImage(
            id=f"regen-{body.page_id}",
            src=body.image_src,
            page_id=body.page_id,
            needs_alt_text=True,
        )
        image_fetcher = ImageFetcher(client)
        alt_gen = AltTextGenerator(image_fetcher, run_id="regenerate")
        return await alt_gen.generate_for_image(image)
    finally:
        await client.close()

"""ACR (Accessibility Conformance Report) API routes."""

import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from lti_app.db.repositories import get_acr_repository, get_scan_repository, ACRRepository, ScanRepository
from lti_app.models import ScanStatus
from lti_app.services.acr_service import ACRService
from lti_app.services.acr_export_service import ACRExportService
from lti_app.auth.dependencies import InstructorSession, verify_session_course_id

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses/{course_id}/acr", tags=["acr"])


def _ensure_resource_course(resource, course_id: str) -> None:
    if str(getattr(resource, "course_id", "")) != str(course_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")


class GenerateACRRequest(BaseModel):
    """Request body for ACR generation."""
    evaluator: str = "Remedy Canvas LTI Automated Scanner"
    include_evidence: bool = True
    compare_scan_id: Optional[str] = None


class GenerateACRResponse(BaseModel):
    """Response from ACR generation request."""
    job_id: str
    status: ScanStatus
    message: str


class ACRSummaryResponse(BaseModel):
    """Summary of an ACR for list views."""
    id: str
    course_id: str
    generated_at: str
    overall_status: str
    conformance_percentage: float
    issues_before: int
    issues_after: int
    issues_fixed: int


@router.post("/generate", response_model=GenerateACRResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_acr(
    course_id: str,
    request: GenerateACRRequest,
    session: InstructorSession,
    acr_repo: ACRRepository = Depends(get_acr_repository),
    scan_repo: ScanRepository = Depends(get_scan_repository),
):
    """Start ACR generation for a course.

    Creates a background job that will generate an Accessibility Conformance Report
    from the latest scan results. Optionally compares with a previous scan to show
    remediation impact.
    """
    verify_session_course_id(session, course_id)
    service = ACRService(acr_repo, scan_repo)

    # Get latest scan report for this course
    latest_report = scan_repo.get_latest_report(course_id)
    if not latest_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No scan results found for this course. Run a scan first."
        )

    # Get comparison scan if requested
    compare_report = None
    if request.compare_scan_id:
        compare_report = scan_repo.get_report(request.compare_scan_id)
        if not compare_report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Comparison scan {request.compare_scan_id} not found"
            )
        _ensure_resource_course(compare_report, course_id)

    # Create job
    job = service.create_job(
        session_id=session.session_id,
        course_id=course_id
    )

    # Run generation in background (same pattern as autoremedy)
    course_url = f"{session.canvas_base_url}/courses/{course_id}"
    asyncio.create_task(
        service.generate_acr(
            job_id=job.id,
            course_id=course_id,
            course_name=session.course_name or f"Course {course_id}",
            course_url=course_url,
            evaluator=request.evaluator,
            report=latest_report,
            pre_remediation_report=compare_report,
        )
    )
    _logger.info("acr_generation_started", job_id=job.id, course_id=course_id)

    return GenerateACRResponse(
        job_id=job.id,
        status=job.status,
        message="ACR generation started. Check status with GET /jobs/{job_id}"
    )


@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_acr_job_status(
    course_id: str,
    job_id: str,
    session: InstructorSession,
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Get status of an ACR generation job."""
    verify_session_course_id(session, course_id)
    job = acr_repo.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    _ensure_resource_course(job, course_id)

    return {
        "job_id": job.id,
        "status": job.status.value,
        "course_id": job.course_id,
        "created_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "acr_id": job.acr_id,
        "error": job.error,
    }


@router.get("/reports", response_model=list[ACRSummaryResponse])
async def list_acr_reports(
    course_id: str,
    session: InstructorSession,
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """List all ACR reports for a course."""
    verify_session_course_id(session, course_id)
    acrs = acr_repo.list_acrs_for_course(course_id)

    return [
        ACRSummaryResponse(
            id=acr.id,
            course_id=acr.course_id,
            generated_at=acr.generated_at.isoformat(),
            overall_status=acr.overall_status.value,
            conformance_percentage=acr.conformance_percentage,
            issues_before=acr.issues_before,
            issues_after=acr.issues_after,
            issues_fixed=acr.issues_fixed,
        )
        for acr in sorted(acrs, key=lambda a: a.generated_at, reverse=True)
    ]


@router.get("/reports/latest")
async def get_latest_acr_report(
    course_id: str,
    session: InstructorSession,
    format: str = "json",  # json, html, markdown
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Get the most recent ACR report for a course.

    Returns in requested format: json, html, or markdown.
    """
    verify_session_course_id(session, course_id)
    acr = acr_repo.get_latest_acr(course_id)
    if not acr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ACR reports found for this course"
        )

    export_service = ACRExportService()

    if format == "html":
        html = export_service.export_html(acr, include_evidence=True)
        return {
            "acr_id": acr.id,
            "format": "html",
            "content": html,
            "download_url": f"/api/courses/{course_id}/acr/reports/{acr.id}/download?format=html"
        }

    elif format == "markdown":
        md = export_service.export_markdown(acr)
        return {
            "acr_id": acr.id,
            "format": "markdown",
            "content": md,
        }

    # Default JSON
    return {
        "acr_id": acr.id,
        "format": "json",
        "data": acr.model_dump(mode="json"),
    }


@router.get("/reports/{acr_id}")
async def get_acr_report(
    course_id: str,
    acr_id: str,
    session: InstructorSession,
    format: str = "json",
    include_evidence: bool = True,
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Get a specific ACR report by ID."""
    verify_session_course_id(session, course_id)
    acr = acr_repo.get_acr(acr_id)
    if not acr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACR report not found"
        )
    _ensure_resource_course(acr, course_id)

    export_service = ACRExportService()

    if format == "html":
        html = export_service.export_html(acr, include_evidence=include_evidence)
        return {
            "acr_id": acr.id,
            "format": "html",
            "content": html,
        }

    elif format == "markdown":
        md = export_service.export_markdown(acr)
        return {
            "acr_id": acr.id,
            "format": "markdown",
            "content": md,
        }

    return {
        "acr_id": acr.id,
        "format": "json",
        "data": acr.model_dump(mode="json"),
    }


@router.get("/reports/{acr_id}/download")
async def download_acr_report(
    course_id: str,
    acr_id: str,
    session: InstructorSession,
    format: str = "html",
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Download an ACR report as a file."""
    verify_session_course_id(session, course_id)
    from fastapi.responses import Response

    acr = acr_repo.get_acr(acr_id)
    if not acr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACR report not found"
        )
    _ensure_resource_course(acr, course_id)

    export_service = ACRExportService()

    if format == "html":
        content = export_service.export_html(acr, include_evidence=True)
        filename = f"ACR_{acr.course_name.replace(' ', '_')}_{acr.generated_at.strftime('%Y%m%d')}.html"
        return Response(
            content=content,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    elif format == "json":
        import json
        content = json.dumps(acr.model_dump(mode="json"), indent=2, default=str)
        filename = f"ACR_{acr.course_name.replace(' ', '_')}_{acr.generated_at.strftime('%Y%m%d')}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    elif format == "markdown":
        content = export_service.export_markdown(acr)
        filename = f"ACR_{acr.course_name.replace(' ', '_')}_{acr.generated_at.strftime('%Y%m%d')}.md"
        return Response(
            content=content,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported format: {format}"
    )


@router.delete("/reports/{acr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_acr_report(
    course_id: str,
    acr_id: str,
    session: InstructorSession,
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Delete an ACR report (admin only)."""
    verify_session_course_id(session, course_id)
    acr = acr_repo.get_acr(acr_id)
    if not acr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACR report not found"
        )
    _ensure_resource_course(acr, course_id)

    acr_repo.delete_acr(acr_id)
    return None


@router.get("/comparison/{acr_id_1}/{acr_id_2}")
async def compare_acr_reports(
    course_id: str,
    acr_id_1: str,
    acr_id_2: str,
    session: InstructorSession,
    acr_repo: ACRRepository = Depends(get_acr_repository),
):
    """Compare two ACR reports and show changes over time."""
    verify_session_course_id(session, course_id)
    acr1 = acr_repo.get_acr(acr_id_1)
    acr2 = acr_repo.get_acr(acr_id_2)

    if not acr1 or not acr2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both ACR reports not found"
        )
    _ensure_resource_course(acr1, course_id)
    _ensure_resource_course(acr2, course_id)

    # Build comparison
    comparison = {
        "acr_1": {
            "id": acr1.id,
            "date": acr1.generated_at.isoformat(),
            "conformance_percentage": acr1.conformance_percentage,
            "issues_count": acr1.issues_after,
            "status": acr1.overall_status.value,
        },
        "acr_2": {
            "id": acr2.id,
            "date": acr2.generated_at.isoformat(),
            "conformance_percentage": acr2.conformance_percentage,
            "issues_count": acr2.issues_after,
            "status": acr2.overall_status.value,
        },
        "improvement": {
            "conformance_change": round(acr2.conformance_percentage - acr1.conformance_percentage, 2),
            "issues_change": acr1.issues_after - acr2.issues_after,
            "status_change": acr1.overall_status.value != acr2.overall_status.value,
        }
    }

    return comparison

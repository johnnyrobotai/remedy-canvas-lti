"""Course scan and report API endpoints."""

import asyncio
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    ExclusionRepository,
    ScanRepository,
    get_exclusion_repository,
    get_scan_repository,
)
from lti_app.models import ContentItemSummary
from lti_app.services.scan_service import ScanService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["courses"])


def _get_scan_service(
    scan_repo: ScanRepository = Depends(get_scan_repository),
) -> ScanService:
    return ScanService(scan_repo)


def _ensure_resource_course(resource, course_id: int) -> None:
    if str(getattr(resource, "course_id", "")) != str(course_id):
        raise HTTPException(status_code=404, detail="Resource not found")


def _create_background_task(coro):
    return asyncio.create_task(coro)


@router.post("/{course_id}/scan")
async def start_scan(
    course_id: int,
    session: InstructorSession,
    scan_mode: str = Query("content_only", pattern="^(content_only|full|rendered_only)$"),
    service: ScanService = Depends(_get_scan_service),
):
    """Kick off a background accessibility scan.

    scan_mode:
      - content_only (default): HTML content analysis only
      - rendered_only: Browser-rendered axe-core scan only
      - full: Both content + rendered scans, deduplicated
    """
    verify_session_course_id(session, course_id)
    job = service.create_scan_job(session.session_id, str(course_id), scan_mode=scan_mode)

    # Launch background task
    _create_background_task(service.run_scan(job.id, session, course_id))

    _logger.info("scan_started", job_id=job.id, course_id=course_id, scan_mode=scan_mode)
    return job


@router.get("/{course_id}/scan/{job_id}")
async def get_scan_status(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: ScanService = Depends(_get_scan_service),
):
    """Poll scan job status and progress."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scan job not found")
    _ensure_resource_course(job, course_id)
    return job


@router.get("/{course_id}/report")
async def get_report(
    course_id: int,
    session: InstructorSession,
    service: ScanService = Depends(_get_scan_service),
):
    """Get the latest accessibility report for a course."""
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No scan report found. Run a scan first.")
    # Separate course content vs canvas platform issues for score
    content_issues = [i for i in report.issues if getattr(i, 'source', 'course_content') != 'canvas_platform']
    platform_issues = [i for i in report.issues if getattr(i, 'source', 'course_content') == 'canvas_platform']
    content_errors = sum(1 for i in content_issues if (i.severity.value if hasattr(i.severity, 'value') else i.severity) == 'error')
    content_warnings = sum(1 for i in content_issues if (i.severity.value if hasattr(i.severity, 'value') else i.severity) == 'warning')

    # Per-content-type breakdown computed from the FULL issue list — the dashboard
    # reads this so its Content Overview cards reflect reality. Computing this on
    # the truncated /report/issues slice produced wrong counts (CLU-53).
    by_type: dict[str, dict[str, int]] = {}
    for issue in content_issues:
        ct = getattr(issue, "content_type", None) or "unknown"
        sev = issue.severity.value if hasattr(issue.severity, "value") else issue.severity
        bucket = by_type.setdefault(ct, {"errors": 0, "warnings": 0, "info": 0, "total": 0})
        bucket["total"] += 1
        if sev == "error":
            bucket["errors"] += 1
        elif sev == "warning":
            bucket["warnings"] += 1
        elif sev == "info":
            bucket["info"] += 1

    return {
        "course_id": report.course_id,
        "analyzed_at": report.analyzed_at.isoformat(),
        "total_issues": len(content_issues),
        "errors": content_errors,
        "warnings": content_warnings,
        "info": report.info,
        "pages_analyzed": report.pages_analyzed,
        "images_needing_alt": report.images_needing_alt,
        "score": ScanService.calculate_score(
            content_errors, content_warnings, report.pages_analyzed
        ),
        "platform_issues": len(platform_issues),
        "issues_by_content_type": by_type,
    }


@router.get("/{course_id}/report/issues")
async def get_issues(
    course_id: int,
    session: InstructorSession,
    service: ScanService = Depends(_get_scan_service),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None, description="Single value or comma-separated list"),
    page_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """Get paginated issues from the latest report with optional filters."""
    verify_session_course_id(session, course_id)
    report = service._repo.get_latest_report(str(course_id))
    if not report:
        raise HTTPException(status_code=404, detail="No scan report found")

    issues = report.issues

    if category:
        issues = [i for i in issues if i.category.value == category]
    if severity:
        issues = [i for i in issues if i.severity.value == severity]
    if content_type:
        cts = {c.strip() for c in content_type.split(",") if c.strip()}
        issues = [i for i in issues if i.content_type in cts]
    if page_id:
        issues = [i for i in issues if i.page_id == page_id]

    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: severity_order.get(i.severity.value, 3))

    total = len(issues)
    start = (page - 1) * per_page
    end = start + per_page
    page_issues = issues[start:end]

    return {
        "issues": [i.model_dump(mode="json") for i in page_issues],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


class ItemsResponse(BaseModel):
    """Response body for ``GET /report/items`` (CLU-85)."""

    scan_report_id: str
    items: list[ContentItemSummary]


@router.get("/{course_id}/report/items", response_model=ItemsResponse)
async def get_report_items(
    course_id: int,
    content_type: str,
    session: InstructorSession,
    scan_repo: ScanRepository = Depends(get_scan_repository),
    exclusion_repo: ExclusionRepository = Depends(get_exclusion_repository),
):
    """Return scan items grouped by identifier for the selective
    remediation review view (CLU-85).

    Unlike ``/report/issues`` which returns one row per
    ``AccessibilityIssue``, this endpoint aggregates issues from the
    latest scan report by ``page_identifier`` and returns one row per
    Canvas item with an issue count and severity rollup. The frontend
    uses this for the per-content-type review screens where users
    check/uncheck items (not individual issues) before running
    AutoRemedy.
    """
    verify_session_course_id(session, course_id)  # CLU-82

    latest_report = scan_repo.get_latest_report(str(course_id))
    if not latest_report:
        return ItemsResponse(scan_report_id="", items=[])

    report_id = scan_repo.get_latest_report_id(str(course_id)) or ""

    # Filter issues by content_type and group by page_identifier.
    grouped: dict[str, dict] = {}
    for issue in latest_report.issues:
        if issue.content_type != content_type:
            continue
        identifier = issue.page_identifier or issue.page_id or ""
        if not identifier:
            continue
        entry = grouped.setdefault(
            identifier,
            {
                "identifier": identifier,
                "title": issue.page_title or identifier,
                "content_type": issue.content_type,
                "canvas_url": issue.canvas_url,
                "issue_count": 0,
                "issue_severities": {"error": 0, "warning": 0, "info": 0},
            },
        )
        entry["issue_count"] += 1
        sev = (
            issue.severity.value
            if hasattr(issue.severity, "value")
            else str(issue.severity)
        )
        if sev in entry["issue_severities"]:
            entry["issue_severities"][sev] += 1

    # Resolve permanent exclusions once — one set lookup per item.
    permanent = exclusion_repo.get_identifiers_for_course(str(course_id))

    items = [
        ContentItemSummary(
            identifier=e["identifier"],
            title=e["title"],
            content_type=e["content_type"],
            canvas_url=e["canvas_url"],
            issue_count=e["issue_count"],
            issue_severities=e["issue_severities"],
            permanently_excluded=e["identifier"] in permanent,
        )
        for e in grouped.values()
    ]

    return ItemsResponse(scan_report_id=report_id, items=items)


@router.get("/{course_id}/activity")
async def get_activity(
    course_id: int,
    session: InstructorSession,
    limit: int = Query(10, ge=1, le=50),
    scan_repo: ScanRepository = Depends(get_scan_repository),
):
    """Get recent activity events for the course dashboard."""
    verify_session_course_id(session, course_id)
    from lti_app.db.repositories import (
        get_remediation_repository,
        get_autoremedy_repository,
    )

    events = []
    cid = str(course_id)

    # Scan reports
    try:
        reports = scan_repo.get_recent_reports(cid, limit=5)
        for r in reports:
            events.append({
                "type": "scan",
                "title": "Accessibility Scan",
                "description": f"Scanned {r.pages_analyzed} pages — {r.total_issues} issues found ({r.errors} errors, {r.warnings} warnings)",
                "timestamp": r.analyzed_at.isoformat(),
            })
    except Exception:
        pass

    # Remediation jobs
    try:
        rem_repo = get_remediation_repository()
        rem_jobs = rem_repo.get_recent_jobs(cid, limit=5)
        for j in rem_jobs:
            if j.status.value == "completed" and j.pages_remediated > 0:
                events.append({
                    "type": "remediation",
                    "title": "Remediation Applied",
                    "description": f"Remediated {j.pages_remediated} pages — {j.pages_remediated} issues fixed",
                    "timestamp": (j.completed_at or j.created_at).isoformat(),
                })
    except Exception:
        pass

    # AutoRemedy jobs
    try:
        ar_repo = get_autoremedy_repository()
        ar_jobs = ar_repo.get_recent_jobs(cid, limit=5)
        for j in ar_jobs:
            if j.status.value == "completed":
                parts = []
                if j.html_pages_remediated:
                    parts.append(f"{j.html_pages_remediated} pages remediated")
                if j.docs_converted:
                    parts.append(f"{j.docs_converted} docs converted")
                if j.links_replaced:
                    parts.append(f"{j.links_replaced} links replaced")
                desc = ", ".join(parts) if parts else "Completed"
                events.append({
                    "type": "autoremedy",
                    "title": "AutoRemedy",
                    "description": desc,
                    "timestamp": (j.completed_at or j.created_at).isoformat(),
                })
    except Exception:
        pass

    # Sort by timestamp descending, limit
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


@router.get("/{course_id}/structure-analysis")
async def analyze_course_structure(
    course_id: int,
    session: InstructorSession,
):
    """Analyze course modules and rubrics for accessibility best practices."""
    verify_session_course_id(session, course_id)
    from lti_app.canvas.client import CanvasClient
    from lti_app.core.accessibility.structure_analyzer import CourseStructureAnalyzer

    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        # Fetch modules with items
        modules = []
        async for mod in client.get_paginated(
            f"/api/v1/courses/{course_id}/modules",
            params={"include[]": "items"},
        ):
            modules.append(mod)

        # Fetch rubrics
        rubrics = []
        async for rubric in client.get_paginated(
            f"/api/v1/courses/{course_id}/rubrics"
        ):
            rubrics.append(rubric)

        analyzer = CourseStructureAnalyzer()
        report = analyzer.analyze(modules, rubrics)

        _logger.info(
            "structure_analysis_complete",
            course_id=course_id,
            modules=report.total_modules,
            items=report.total_items,
            issues=len(report.issues),
            score=report.score,
        )

        return report.model_dump()
    except Exception as exc:
        _logger.error("structure_analysis_error", course_id=course_id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Structure analysis failed: {exc}",
        ) from exc
    finally:
        await client.close()


@router.get("/{course_id}/cleanup/orphan-pages")
async def list_orphan_pages(
    course_id: int,
    session: InstructorSession,
):
    """List orphan chapter pages from killed AutoRemedy runs.

    These are wiki pages whose title matches a CLU-67 chapter pattern
    ("{filename} - Pages N-M" or "{filename} - Chapter N: ..."). They
    accumulate when AutoRemedy phase 4 is killed mid-conversion (e.g.
    by a deploy) and the partial chapter pages remain in Canvas.
    """
    verify_session_course_id(session, course_id)
    from lti_app.canvas.client import CanvasClient
    from lti_app.services.cleanup_service import OrphanCleanupService

    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        svc = OrphanCleanupService()
        orphans = await svc.find_orphan_pages(client, course_id)
        return {
            "course_id": course_id,
            "count": len(orphans),
            "pages": [
                {
                    "page_id": p.get("page_id"),
                    "url": p.get("url"),
                    "title": p.get("title"),
                    "published": p.get("published", False),
                }
                for p in orphans
            ],
        }
    finally:
        await client.close()


@router.post("/{course_id}/cleanup/orphan-pages")
async def delete_orphan_pages(
    course_id: int,
    session: InstructorSession,
):
    """Delete all orphan chapter pages on a course.

    Wraps OrphanCleanupService. Idempotent — safe to call multiple
    times. Returns the count of pages actually deleted.
    """
    verify_session_course_id(session, course_id)
    from lti_app.canvas.client import CanvasClient
    from lti_app.services.cleanup_service import OrphanCleanupService

    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        svc = OrphanCleanupService()
        orphans = await svc.find_orphan_pages(client, course_id)
        slugs = [p.get("url") for p in orphans if p.get("url")]
        deleted = await svc.delete_pages(client, course_id, slugs)
        _logger.info(
            "orphan_cleanup_complete",
            course_id=course_id,
            found=len(orphans),
            deleted=deleted,
        )
        return {
            "course_id": course_id,
            "found": len(orphans),
            "deleted": deleted,
        }
    finally:
        await client.close()

"""Canvas content export API endpoints."""

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.canvas.client import CanvasAPIError, CanvasClient
from lti_app.services.course_export_service import CourseExportService
from lti_app.services.remediated_export_service import RemediatedExportService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/courses", tags=["exports"])

# In-process tracking for background remediated-export jobs.
# Keyed by "{course_id}" — one active export per course at a time.
_remediated_tasks: dict[str, asyncio.Task] = {}
_remediated_results: dict[str, dict] = {}


async def _get_course_export_service(
    session: InstructorSession,
) -> AsyncIterator[CourseExportService]:
    client = CanvasClient(
        base_url=session.canvas_base_url,
        access_token=session.canvas_access_token,
        refresh_token=getattr(session, "canvas_refresh_token", ""),
    )
    try:
        yield CourseExportService(client)
    finally:
        await client.close()


def _map_canvas_error(exc: CanvasAPIError) -> HTTPException:
    status_code = exc.status if 400 <= exc.status < 600 else 502
    return HTTPException(status_code=status_code, detail=str(exc))


async def _run_remediated_export(
    session: InstructorSession,
    course_id: int,
) -> None:
    """Background task: run the full remediated export pipeline."""
    key = str(course_id)
    service = RemediatedExportService()
    try:
        _remediated_results[key] = await service.create_export(session, course_id)
    except Exception as exc:
        _logger.error("remediated_export_failed", course_id=course_id, error=str(exc))
        _remediated_results[key] = {"status": "failed", "error": str(exc)}
    finally:
        _remediated_tasks.pop(key, None)


@router.post("/{course_id}/exports")
async def start_course_export(
    course_id: int,
    session: InstructorSession,
    skip_notifications: bool = Query(True),
    service: CourseExportService = Depends(_get_course_export_service),
):
    """Create a fresh Common Cartridge export for the current course state."""
    verify_session_course_id(session, course_id)  # CLU-82
    try:
        return await service.start_course_export(
            course_id=course_id,
            skip_notifications=skip_notifications,
        )
    except CanvasAPIError as exc:
        raise _map_canvas_error(exc) from exc


# --- Remediated export routes (MUST come before {export_id} parameterized routes) ---


@router.post("/{course_id}/exports/remediated")
async def start_remediated_export(
    course_id: int,
    session: InstructorSession,
):
    """Start a remediated IMSCC export."""
    verify_session_course_id(session, course_id)  # CLU-82
    key = str(course_id)
    existing = _remediated_tasks.get(key)
    if existing and not existing.done():
        raise HTTPException(
            status_code=409,
            detail="A remediated export is already in progress for this course",
        )
    _remediated_results.pop(key, None)
    task = asyncio.create_task(_run_remediated_export(session, course_id))
    _remediated_tasks[key] = task
    _logger.info("remediated_export_queued", course_id=course_id)
    return {"status": "started", "course_id": course_id}


@router.get("/{course_id}/exports/remediated/status")
async def get_remediated_export_status(
    course_id: int,
    session: InstructorSession,
):
    """Poll the status of a remediated export job."""
    verify_session_course_id(session, course_id)  # CLU-82
    key = str(course_id)
    if key in _remediated_results:
        result = _remediated_results[key]
        return {
            "status": result.get("status", "unknown"),
            "patched_pages": result.get("patched_pages"),
            # CLU-71: non-zero count = partial export, some Canvas file
            # references in the HTML could not be mapped back to
            # $IMS-CC-FILEBASE$ placeholders (race condition, stale file
            # metadata, or cross-course/user file references). The
            # export is still usable — just those specific references
            # will remain absolute URLs.
            "unresolved_file_refs": result.get("unresolved_file_refs"),
            "error": result.get("error"),
        }
    if key in _remediated_tasks and not _remediated_tasks[key].done():
        return {"status": "in_progress"}
    return {"status": "not_started"}


@router.get("/{course_id}/exports/remediated/download")
async def download_remediated_export(
    course_id: int,
    session: InstructorSession,
):
    """Download the remediated IMSCC file."""
    # CLU-82: reject cross-course access BEFORE looking at
    # _remediated_results so the response does not leak whether an
    # export exists for another course via 404-vs-403 timing.
    verify_session_course_id(session, course_id)
    key = str(course_id)
    result = _remediated_results.get(key)
    if not result or result.get("status") != "complete":
        raise HTTPException(status_code=404, detail="Remediated export not ready")
    download_path = Path(result["download_path"])
    if not download_path.exists():
        _remediated_results.pop(key, None)
        raise HTTPException(status_code=410, detail="Export file already cleaned up")
    # CLU-78: source the course name from the export result, not from the
    # current LTI session. The session can belong to a different course
    # entirely if the user has multiple LTI tabs open — cookies are scoped
    # per-domain, and the most-recent LTI launch wins. The export job
    # captured the right name from Canvas at export time.
    course_name_raw = (result.get("course_name") or "").strip()
    if not course_name_raw:
        course_name_raw = f"course_{course_id}"
    course_name = course_name_raw.replace(" ", "_")
    filename = f"{course_name}_remediated.imscc"
    tmp_dir = download_path.parent

    async def _cleanup() -> None:
        _remediated_results.pop(key, None)
        _remediated_tasks.pop(key, None)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return FileResponse(
        path=str(download_path),
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(_cleanup),
    )


# --- Parameterized export routes (after literal /remediated routes) ---


@router.get("/{course_id}/exports/{export_id}")
async def get_course_export(
    course_id: int,
    export_id: int,
    session: InstructorSession,
    service: CourseExportService = Depends(_get_course_export_service),
):
    """Get status for a specific Canvas content export."""
    verify_session_course_id(session, course_id)  # CLU-82
    try:
        return await service.get_course_export(course_id=course_id, export_id=export_id)
    except CanvasAPIError as exc:
        raise _map_canvas_error(exc) from exc


@router.get("/{course_id}/exports/{export_id}/download")
async def download_course_export(
    course_id: int,
    export_id: int,
    session: InstructorSession,
    service: CourseExportService = Depends(_get_course_export_service),
):
    """Stream a completed Canvas export package through Remedy Canvas LTI."""
    verify_session_course_id(session, course_id)  # CLU-82
    try:
        download = await service.open_course_export_download(
            course_id=course_id,
            export_id=export_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CanvasAPIError as exc:
        raise _map_canvas_error(exc) from exc

    content_disposition = (
        download.content_disposition
        or f'attachment; filename="{download.filename}"'
    )
    return StreamingResponse(
        download.stream,
        media_type=download.media_type,
        headers={"Content-Disposition": content_disposition},
        background=BackgroundTask(download.close),
    )

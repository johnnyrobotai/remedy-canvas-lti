"""Captions API endpoints for CLU Captions."""

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from lti_app.auth.dependencies import InstructorSession, verify_session_course_id
from lti_app.db.repositories import (
    CaptionRepository,
    get_caption_repository,
)
from lti_app.services.caption_service import CaptionService

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["captions"])


def _get_caption_service(
    caption_repo: CaptionRepository = Depends(get_caption_repository),
) -> CaptionService:
    return CaptionService(caption_repo)


# ---------------------------------------------------------------------------
# Request / response bodies
# ---------------------------------------------------------------------------


class TranscribeRequest(BaseModel):
    video_url: str
    video_id: str
    video_title: str = ""
    source_type: str = "youtube"  # "youtube" or "studio"


# ---------------------------------------------------------------------------
# Authenticated endpoints
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/captions/videos")
async def scan_course_videos(
    course_id: int,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """Scan all course pages for YouTube video embeds."""
    verify_session_course_id(session, course_id)
    videos = await service.scan_videos(session, course_id)
    _logger.info("captions_scan_videos", course_id=course_id, count=len(videos))
    return videos


@router.post("/courses/{course_id}/captions/transcribe")
async def start_transcription(
    course_id: int,
    body: TranscribeRequest,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """Start a background transcription job for a video."""
    verify_session_course_id(session, course_id)
    job = service.create_transcription_job(
        session_id=session.session_id,
        course_id=str(course_id),
        video_url=body.video_url,
        video_id=body.video_id,
        video_title=body.video_title,
        source_type=body.source_type,
    )
    asyncio.create_task(service.run_transcription(job.id, session, course_id))
    _logger.info(
        "captions_transcription_started",
        job_id=job.id,
        course_id=course_id,
        video_id=body.video_id,
    )
    return job


@router.get("/courses/{course_id}/captions/jobs")
async def list_transcription_jobs(
    course_id: int,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """List all transcription jobs for a course."""
    verify_session_course_id(session, course_id)
    jobs = service._repo.get_jobs_for_course(str(course_id))
    return jobs


@router.get("/courses/{course_id}/captions/jobs/{job_id}")
async def get_transcription_job(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """Get a single transcription job by ID (use for polling)."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Transcription job not found")
    if job.course_id != str(course_id):
        raise HTTPException(status_code=404, detail="Transcription job not found")
    return job


@router.get("/courses/{course_id}/captions/jobs/{job_id}/vtt")
async def download_vtt(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """Download the generated WebVTT captions for a completed job."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Transcription job not found")
    if job.course_id != str(course_id):
        raise HTTPException(status_code=404, detail="Transcription job not found")
    if not job.vtt_content:
        raise HTTPException(
            status_code=404,
            detail="VTT not yet generated — job may still be in progress",
        )
    return Response(
        content=job.vtt_content,
        media_type="text/vtt",
        headers={
            "Content-Disposition": f'attachment; filename="{job.video_id}.vtt"',
        },
    )


@router.get("/courses/{course_id}/captions/jobs/{job_id}/srt")
async def download_srt(
    course_id: int,
    job_id: str,
    session: InstructorSession,
    service: CaptionService = Depends(_get_caption_service),
):
    """Download the generated SRT captions for a completed job."""
    verify_session_course_id(session, course_id)
    job = service._repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Transcription job not found")
    if job.course_id != str(course_id):
        raise HTTPException(status_code=404, detail="Transcription job not found")
    if not job.srt_content:
        raise HTTPException(
            status_code=404,
            detail="SRT not yet generated — job may still be in progress",
        )
    return Response(
        content=job.srt_content,
        media_type="text/srt",
        headers={
            "Content-Disposition": f'attachment; filename="{job.video_id}.srt"',
        },
    )


# ---------------------------------------------------------------------------
# Public endpoint — no auth, used by injected JS in Canvas pages
# ---------------------------------------------------------------------------


@router.get("/captions/vtt/{video_hash}")
async def serve_vtt_public(
    video_hash: str,
    service: CaptionService = Depends(_get_caption_service),
):
    """Serve VTT captions by video hash (public, no auth required).

    Called by the caption overlay JavaScript injected into Canvas pages.
    Cached for 24 hours with CORS open to allow cross-origin Canvas iframes.
    """
    job = service._repo.get_job_by_hash(video_hash)
    if not job or not job.vtt_content:
        raise HTTPException(status_code=404, detail="Captions not found")

    return Response(
        content=job.vtt_content,
        media_type="text/vtt",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=86400",
        },
    )

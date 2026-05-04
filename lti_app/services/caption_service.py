"""Caption service orchestrator for CLU Captions.

Orchestrates YouTube download → audio extraction → VAD segmentation →
caption transcription → VTT/SRT generation → Firestore storage.
"""

import hashlib
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import structlog
from ulid import ULID

from lti_app.db.repositories import CaptionRepository
from lti_app.models import CourseVideoRecord, ScanStatus, TranscriptionJob

_logger = structlog.get_logger(__name__)


class CaptionService:
    """Orchestrates transcription jobs following the AutoRemedyService pattern."""

    def __init__(self, caption_repo: CaptionRepository):
        self._repo = caption_repo

    # -----------------------------------------------------------------------
    # Video scanning
    # -----------------------------------------------------------------------

    async def scan_videos(self, session, course_id: int) -> list[CourseVideoRecord]:
        """Fetch all course pages via Canvas API and scan HTML for YouTube videos.

        Returns a list of CourseVideoRecord objects (also saved to Firestore).
        """
        from lti_app.canvas.client import CanvasClient
        from lti_app.canvas.content_fetcher import ContentFetcher
        from lti_app.core.captions.video_scanner import VideoScanner

        client = CanvasClient(
            base_url=session.canvas_base_url,
            access_token=session.canvas_access_token,
            refresh_token=getattr(session, "canvas_refresh_token", ""),
        )
        fetcher = ContentFetcher(client)
        all_pages = await fetcher.fetch_all(course_id)

        scanner = VideoScanner()
        youtube_videos = scanner.scan_pages(all_pages)

        records: list[CourseVideoRecord] = []
        for video in youtube_videos:
            record = CourseVideoRecord(
                id=f"{course_id}:{video.video_id}",
                course_id=str(course_id),
                video_id=video.video_id,
                video_url=video.canonical_url,
                thumbnail_url=video.thumbnail_url,
                page_id=video.page_id,
                page_title=video.page_title,
                content_type=video.content_type,
                caption_status="none",
                discovered_at=datetime.now(UTC),
            )
            self._repo.save_video(record)
            records.append(record)

        _logger.info(
            "caption_scan_complete",
            course_id=course_id,
            pages=len(all_pages),
            videos=len(records),
        )
        return records

    # -----------------------------------------------------------------------
    # Job creation
    # -----------------------------------------------------------------------

    def create_transcription_job(
        self,
        session_id: str,
        course_id: str,
        video_url: str,
        video_id: str,
        video_title: str = "",
        source_type: str = "youtube",
    ) -> TranscriptionJob:
        """Create and persist a TranscriptionJob; return it immediately."""
        job = TranscriptionJob(
            id=str(ULID()),
            course_id=course_id,
            session_id=session_id,
            video_id=video_id,
            video_url=video_url,
            video_title=video_title,
            source_type=source_type,
            status=ScanStatus.PENDING,
            phase="downloading",
            created_at=datetime.now(UTC),
        )
        self._repo.save_job(job)
        return job

    # -----------------------------------------------------------------------
    # Background transcription pipeline
    # -----------------------------------------------------------------------

    async def run_transcription(
        self,
        job_id: str,
        session,
        course_id: int,
    ) -> None:
        """Background task: full download → transcribe → store pipeline.

        Phases:
            A: Download audio from YouTube
            B: Extract to 16kHz mono WAV (ffmpeg)
            C: VAD segment detection
            D: Per-segment caption transcription
            E: Generate VTT and SRT
            F: Store in Firestore and clean up temp files
        """
        from lti_app.core.captions.audio_extractor import AudioExtractor
        from lti_app.core.captions.caption_generator import CaptionGenerator
        from lti_app.core.captions.transcriber import (
            CaptionTranscriber,
            TranscriptionSegment,
        )
        from lti_app.core.captions.vad_segmenter import VADSegmenter
        from lti_app.core.captions.youtube_downloader import YouTubeDownloader

        job = self._repo.get_job(job_id)
        if not job:
            _logger.error("caption_job_not_found", job_id=job_id)
            return

        # Working directory for this job
        work_dir = Path(tempfile.gettempdir()) / f"clu_captions_{job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        transcriber = CaptionTranscriber()

        try:
            job.status = ScanStatus.RUNNING
            self._repo.save_job(job)

            # Phase A: Download
            job.phase = "downloading"
            self._repo.save_job(job)
            _logger.info("caption_phase_download", job_id=job_id, url=job.video_url)

            downloader = YouTubeDownloader()
            downloaded_path = await downloader.download_audio(job.video_url, job_id)

            # Phase B: Extract WAV
            job.phase = "extracting"
            self._repo.save_job(job)
            _logger.info("caption_phase_extract", job_id=job_id)

            extractor = AudioExtractor()
            wav_path = work_dir / f"{job_id}.wav"
            await extractor.extract_wav(downloaded_path, wav_path)

            # Phase C: VAD Segmentation
            job.phase = "segmenting"
            self._repo.save_job(job)
            _logger.info("caption_phase_segment", job_id=job_id)

            segments_dir = work_dir / "segments"
            segmenter = VADSegmenter()
            speech_segments = await segmenter.segment(wav_path, segments_dir)

            total_segments = len(speech_segments)
            job.chunks_total = total_segments
            self._repo.save_job(job)

            _logger.info(
                "caption_segments_ready",
                job_id=job_id,
                total_segments=total_segments,
            )

            # Phase D: Transcribe each segment
            job.phase = "transcribing"
            self._repo.save_job(job)

            all_transcription_segments: list[TranscriptionSegment] = []

            for i, speech_seg in enumerate(speech_segments):
                _logger.info(
                    "caption_transcribing_segment",
                    job_id=job_id,
                    index=i,
                    total=total_segments,
                )

                # Stage segment for transcription.
                segment_ref = await transcriber.stage_segment(speech_seg.path, job_id, i)

                # Transcribe
                raw_segments = await transcriber.transcribe_segment(segment_ref, i)

                # Offset timestamps by segment's absolute start time
                for seg in raw_segments:
                    offset_seg = TranscriptionSegment(
                        start=seg.start + speech_seg.start_sec,
                        end=seg.end + speech_seg.start_sec,
                        text=seg.text,
                    )
                    all_transcription_segments.append(offset_seg)

                # Update progress
                job.chunks_transcribed = i + 1
                job.progress = (i + 1) / total_segments if total_segments > 0 else 1.0
                self._repo.save_job(job)

            # Phase E: Generate VTT and SRT
            job.phase = "generating"
            self._repo.save_job(job)
            _logger.info(
                "caption_phase_generate",
                job_id=job_id,
                cues=len(all_transcription_segments),
            )

            generator = CaptionGenerator()
            vtt_content = generator.generate_vtt(all_transcription_segments)
            srt_content = generator.generate_srt(all_transcription_segments)

            # Phase F: Store and complete
            vtt_hash = hashlib.sha256(
                f"{course_id}:{job.video_id}".encode()
            ).hexdigest()

            job.vtt_content = vtt_content
            job.srt_content = srt_content
            job.vtt_hash = vtt_hash
            job.phase = "complete"
            job.status = ScanStatus.COMPLETED
            job.progress = 1.0
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)

            # Update video record caption status if one exists
            video_record = CourseVideoRecord(
                id=f"{course_id}:{job.video_id}",
                course_id=str(course_id),
                video_id=job.video_id,
                video_url=job.video_url,
                video_title=job.video_title,
                page_id="",
                caption_status="ready",
                transcription_job_id=job_id,
            )
            self._repo.save_video(video_record)

            _logger.info(
                "caption_transcription_complete",
                job_id=job_id,
                vtt_hash=vtt_hash,
                cues=len(all_transcription_segments),
            )

        except Exception as exc:
            _logger.error(
                "caption_transcription_failed",
                job_id=job_id,
                error=str(exc),
            )
            job = self._repo.get_job(job_id) or job
            job.status = ScanStatus.FAILED
            job.error = str(exc)
            job.completed_at = datetime.now(UTC)
            self._repo.save_job(job)

        finally:
            # Clean up staged transcription segments.
            try:
                await transcriber.cleanup(job_id)
            except Exception as cleanup_err:
                _logger.warning(
                    "caption_segment_cleanup_failed",
                    job_id=job_id,
                    error=str(cleanup_err),
                )

            # Clean up local temp files
            try:
                if work_dir.exists():
                    shutil.rmtree(work_dir, ignore_errors=True)
                    _logger.info("caption_tempdir_cleaned", job_id=job_id)
            except Exception as cleanup_err:
                _logger.warning(
                    "caption_tempdir_cleanup_failed",
                    job_id=job_id,
                    error=str(cleanup_err),
                )

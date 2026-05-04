import asyncio
import tempfile
from pathlib import Path
from typing import Optional
import structlog

_logger = structlog.get_logger(__name__)


class YouTubeDownloader:
    """Download YouTube video audio using yt-dlp."""

    async def get_video_info(self, url: str) -> dict:
        """Get video metadata without downloading."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_video_info_sync, url)

    def _get_video_info_sync(self, url: str) -> dict:
        import yt_dlp
        ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": False}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    async def download_audio(self, url: str, job_id: str) -> Path:
        """Download best audio from YouTube URL. Returns path to downloaded file."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._download_sync, url, job_id)

    def _download_sync(self, url: str, job_id: str) -> Path:
        import yt_dlp
        download_dir = Path(tempfile.gettempdir()) / f"clu_captions_{job_id}"
        download_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(download_dir / f"{job_id}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "fragment_retries": 3,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        downloaded_files = [
            f for f in download_dir.glob(f"{job_id}.*")
            if not f.suffix.endswith(".part")
        ]
        if not downloaded_files:
            raise RuntimeError(f"Downloaded file not found for job {job_id}")

        _logger.info("youtube_download_complete", job_id=job_id, path=str(downloaded_files[0]))
        return downloaded_files[0]

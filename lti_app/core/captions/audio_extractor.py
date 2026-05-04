import asyncio
from pathlib import Path
from typing import Optional
import structlog

_logger = structlog.get_logger(__name__)


class AudioExtractor:
    """Extract and convert audio to 16kHz mono WAV using ffmpeg."""

    async def extract_wav(self, input_path: Path, output_path: Optional[Path] = None) -> Path:
        """Convert input audio/video to 16kHz mono WAV for speech segmentation."""
        if output_path is None:
            output_path = input_path.with_suffix(".wav")

        cmd = [
            "ffmpeg", "-i", str(input_path),
            "-vn",               # No video
            "-acodec", "pcm_s16le",  # 16-bit PCM
            "-ar", "16000",      # 16kHz sample rate
            "-ac", "1",          # Mono
            "-y",                # Overwrite
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg extract failed: {stderr.decode()[:500]}")

        _logger.info("audio_extracted", output=str(output_path))
        return output_path

    async def extract_segment(self, input_path: Path, output_path: Path,
                               start_sec: float, duration_sec: float) -> Path:
        """Extract a specific time segment from a WAV file."""
        cmd = [
            "ffmpeg", "-i", str(input_path),
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-c", "copy",        # No re-encoding for WAV->WAV
            "-y",
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg segment extract failed: {stderr.decode()[:500]}")

        return output_path

    async def get_duration(self, file_path: Path) -> float:
        """Get duration of audio/video file in seconds."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {stderr.decode()[:500]}")

        return float(stdout.decode().strip())

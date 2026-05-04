"""Caption transcription backend placeholder.

No cloud transcription provider is configured in this deployment build. The
service keeps this interface so caption jobs fail explicitly until a replacement
backend is implemented.
"""

from dataclasses import dataclass
from pathlib import Path

import structlog

_logger = structlog.get_logger(__name__)


@dataclass
class TranscriptionSegment:
    """A single transcribed segment with timestamps."""

    start: float
    end: float
    text: str


class CaptionTranscriber:
    """Placeholder transcriber used until caption transcription is configured."""

    def __init__(self, **kwargs):
        _logger.warning(
            "caption_transcriber_disabled",
            reason="no transcription backend configured",
        )

    async def stage_segment(self, local_path: Path, job_id: str, segment_index: int) -> str:
        raise NotImplementedError(
            "Transcription unavailable - no caption transcription backend configured"
        )

    async def transcribe_segment(
        self,
        segment_ref: str,
        segment_index: int,
    ) -> list[TranscriptionSegment]:
        raise NotImplementedError(
            "Transcription unavailable - no caption transcription backend configured"
        )

    async def cleanup(self, job_id: str) -> None:
        pass

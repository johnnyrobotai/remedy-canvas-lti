"""Generate SRT and WebVTT caption files from transcription segments."""

from __future__ import annotations


class CaptionGenerator:
    """Generate SRT and WebVTT subtitle files from transcription segments."""

    def generate_vtt(self, segments: list) -> str:
        """Generate WebVTT formatted caption content."""
        lines = ["WEBVTT", ""]

        for i, seg in enumerate(segments, start=1):
            start = seg.start if hasattr(seg, "start") else seg["start"]
            end = seg.end if hasattr(seg, "end") else seg["end"]
            text = seg.text if hasattr(seg, "text") else seg["text"]

            start_ts = self._format_vtt_timestamp(start)
            end_ts = self._format_vtt_timestamp(end)

            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(text)
            lines.append("")

        return "\n".join(lines)

    def generate_srt(self, segments: list) -> str:
        """Generate SRT formatted subtitle content."""
        lines = []

        for i, seg in enumerate(segments, start=1):
            start = seg.start if hasattr(seg, "start") else seg["start"]
            end = seg.end if hasattr(seg, "end") else seg["end"]
            text = seg.text if hasattr(seg, "text") else seg["text"]

            start_ts = self._format_srt_timestamp(start)
            end_ts = self._format_srt_timestamp(end)

            lines.append(str(i))
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(text)
            lines.append("")

        return "\n".join(lines)

    def generate_txt(self, segments: list) -> str:
        """Generate plain text from segments."""
        texts = []
        for seg in segments:
            text = seg.text if hasattr(seg, "text") else seg["text"]
            texts.append(text)
        return " ".join(texts)

    def generate_json(self, segments: list) -> list[dict]:
        """Generate JSON-serializable list with timestamps."""
        return [
            {
                "start": seg.start if hasattr(seg, "start") else seg["start"],
                "end": seg.end if hasattr(seg, "end") else seg["end"],
                "text": seg.text if hasattr(seg, "text") else seg["text"],
            }
            for seg in segments
        ]

    def _format_vtt_timestamp(self, seconds: float) -> str:
        """Format seconds to VTT timestamp: HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

    def _format_srt_timestamp(self, seconds: float) -> str:
        """Format seconds to SRT timestamp: HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

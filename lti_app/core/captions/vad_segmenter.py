import asyncio
from dataclasses import dataclass
from pathlib import Path
import structlog

_logger = structlog.get_logger(__name__)

MAX_SEGMENT_DURATION = 600.0  # 10 minutes
MIN_SILENCE_GAP = 2.0  # Merge segments with gaps smaller than this


@dataclass
class SpeechSegment:
    """A detected speech segment with absolute timestamps."""
    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    path: Path  # Path to extracted WAV segment


class VADSegmenter:
    """Silero VAD-based audio segmenter for pre-processing before transcription.

    NOTE: silero-vad 6.x requires torch tensors even when using onnx=True backend.
    The onnx=True flag controls the model runtime (ONNX vs TorchScript) but
    get_speech_timestamps() still accepts torch.Tensor input. torch is therefore
    a hard dependency. torchaudio is also required by silero-vad 6.x.
    """

    def __init__(self):
        self._model = None

    def _load_model(self):
        """Lazy-load Silero VAD model (ONNX backend, no TorchScript required)."""
        if self._model is None:
            from silero_vad import load_silero_vad
            self._model = load_silero_vad(onnx=True)
        return self._model

    async def segment(self, wav_path: Path, output_dir: Path) -> list[SpeechSegment]:
        """Detect speech segments in a 16kHz mono WAV and extract each to a file.

        Args:
            wav_path: Path to 16kHz mono WAV file
            output_dir: Directory to write segment WAV files

        Returns:
            List of SpeechSegment with paths to extracted audio files
        """
        loop = asyncio.get_event_loop()

        # Run VAD detection in thread pool (CPU-bound)
        raw_timestamps = await loop.run_in_executor(
            None, self._detect_speech, wav_path
        )

        if not raw_timestamps:
            _logger.warning("vad_no_speech_detected", path=str(wav_path))
            # Return single segment covering full file
            import soundfile as sf
            info = sf.info(str(wav_path))
            duration = info.duration
            return [SpeechSegment(
                index=0, start_sec=0.0, end_sec=duration,
                duration_sec=duration, path=wav_path,
            )]

        # Merge adjacent segments with small gaps
        merged = self._merge_segments(raw_timestamps)

        # Split any segments longer than MAX_SEGMENT_DURATION
        split = self._split_long_segments(merged)

        _logger.info("vad_segments_detected",
                     raw=len(raw_timestamps), merged=len(merged), final=len(split))

        # Extract each segment to a separate WAV file
        output_dir.mkdir(parents=True, exist_ok=True)
        from lti_app.core.captions.audio_extractor import AudioExtractor
        extractor = AudioExtractor()

        segments = []
        for i, (start, end) in enumerate(split):
            seg_path = output_dir / f"segment_{i:03d}.wav"
            duration = end - start
            await extractor.extract_segment(wav_path, seg_path, start, duration)
            segments.append(SpeechSegment(
                index=i,
                start_sec=start,
                end_sec=end,
                duration_sec=duration,
                path=seg_path,
            ))

        return segments

    def _detect_speech(self, wav_path: Path) -> list[dict]:
        """Synchronous VAD detection (runs in thread pool).

        Uses torch.Tensor as input — required by silero-vad 6.x even with onnx=True.
        """
        import soundfile as sf
        import torch
        from silero_vad import get_speech_timestamps

        model = self._load_model()
        wav_data, sample_rate = sf.read(str(wav_path), dtype="float32")

        # Ensure mono
        if wav_data.ndim == 2:
            wav_data = wav_data.mean(axis=1)

        wav_tensor = torch.from_numpy(wav_data)

        timestamps = get_speech_timestamps(
            wav_tensor, model,
            sampling_rate=sample_rate,
            min_speech_duration_ms=200,
            min_silence_duration_ms=250,
            return_seconds=True,
        )
        return timestamps

    def _merge_segments(self, timestamps: list[dict]) -> list[tuple[float, float]]:
        """Merge adjacent speech segments with gaps < MIN_SILENCE_GAP."""
        if not timestamps:
            return []

        merged = [(timestamps[0]["start"], timestamps[0]["end"])]

        for ts in timestamps[1:]:
            prev_start, prev_end = merged[-1]
            if ts["start"] - prev_end < MIN_SILENCE_GAP:
                # Merge with previous
                merged[-1] = (prev_start, ts["end"])
            else:
                merged.append((ts["start"], ts["end"]))

        return merged

    def _split_long_segments(self, segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Split segments longer than MAX_SEGMENT_DURATION."""
        result = []
        for start, end in segments:
            duration = end - start
            if duration <= MAX_SEGMENT_DURATION:
                result.append((start, end))
            else:
                # Split into roughly equal parts
                num_parts = int(duration / MAX_SEGMENT_DURATION) + 1
                part_duration = duration / num_parts
                for j in range(num_parts):
                    part_start = start + j * part_duration
                    part_end = min(start + (j + 1) * part_duration, end)
                    result.append((part_start, part_end))
        return result

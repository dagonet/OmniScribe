"""Faster-whisper transcriber (lazy-loaded, GPU-friendly)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from faster_whisper import BatchedInferencePipeline, WhisperModel

from omniscribe.output import TranscriptSegment

if TYPE_CHECKING:
    from pathlib import Path

    from omniscribe.config import OmniScribeConfig

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    """Wraps :class:`WhisperModel` + :class:`BatchedInferencePipeline` with lazy init."""

    def __init__(self, config: OmniScribeConfig) -> None:
        self._config = config
        self._pipeline: BatchedInferencePipeline | None = None

    def _ensure_loaded(self) -> BatchedInferencePipeline:
        if self._pipeline is None:
            logger.info(
                "Loading Whisper model %s on %s (compute_type=%s) — first run may download ~1.5 GB",
                self._config.whisper_model,
                self._config.whisper_device,
                self._config.whisper_compute_type,
            )
            model = WhisperModel(
                model_size_or_path=self._config.whisper_model,
                device=self._config.whisper_device,
                compute_type=self._config.whisper_compute_type,
            )
            self._pipeline = BatchedInferencePipeline(model)
        return self._pipeline

    def transcribe(self, audio_path: Path) -> tuple[list[TranscriptSegment], str]:
        """Run ASR on ``audio_path``; return (segments, detected_language)."""
        pipeline = self._ensure_loaded()
        segments_gen, info = pipeline.transcribe(
            str(audio_path),
            language=self._config.whisper_language,
            batch_size=self._config.whisper_batch_size,
            vad_filter=True,
            word_timestamps=False,
        )
        segments = [
            TranscriptSegment(
                start=float(s.start),
                end=float(s.end),
                text=s.text.strip(),
                confidence=getattr(s, "avg_logprob", None),
                language=info.language,
            )
            for s in segments_gen
        ]
        return segments, info.language

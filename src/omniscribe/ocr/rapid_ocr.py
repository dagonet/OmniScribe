"""RapidOCR engine wrapper (ONNX runtime; GPU or CPU).

Mirrors the shape of :class:`omniscribe.asr.whisper.WhisperTranscriber`: lazy
first-call model init, config pulled from :class:`OmniScribeConfig`, returns
:class:`TranscriptSegment` instances tagged ``source="ON-SCREEN"``.

Imports ``RapidOCR`` at module top so tests can patch it at the import site
(``omniscribe.ocr.rapid_ocr.RapidOCR``). No runtime CUDA→CPU fallback — a failing
GPU init surfaces as the native library exception (wrapped by the caller).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rapidocr import LangRec, RapidOCR

from omniscribe.errors import OmniScribeError
from omniscribe.ocr.frame_sampler import sample_frames
from omniscribe.ocr.preprocessor import preprocess
from omniscribe.ocr.ui_filter import mask_zones
from omniscribe.output import TranscriptSegment

if TYPE_CHECKING:
    from pathlib import Path

    from omniscribe.config import OmniScribeConfig
    from omniscribe.platforms.base import PlatformProfile

logger = logging.getLogger(__name__)


class RapidOCREngine:
    """Lazy-init RapidOCR wrapper that extracts on-screen text from a video.

    The underlying :class:`rapidocr.RapidOCR` engine is constructed on the first
    :meth:`extract` call and reused across subsequent calls. GPU vs. CPU is
    selected via ``config.ocr_device`` (``"cuda"`` → ``use_cuda=True``; anything
    else → ``use_cuda=False``).

    ``config.ocr_language`` is coerced to a :class:`rapidocr.LangRec` enum before
    being handed to the engine — the Python ``params`` dict path does strict enum
    validation (unlike the YAML config path which accepts strings). Unsupported
    values raise :class:`OmniScribeError` at first :meth:`extract` call.

    After each :meth:`extract` call, ``self.last_frame_count`` holds the number of
    frames yielded by the sampler — used by the CLI for the
    ``"OCR: N segments from M frames"`` log line.
    """

    def __init__(
        self,
        config: OmniScribeConfig,
        *,
        profile: PlatformProfile | None = None,
    ) -> None:
        self._config = config
        self._profile = profile
        self._engine: RapidOCR | None = None
        self.last_frame_count: int = 0

    def _ensure_loaded(self) -> RapidOCR:
        if self._engine is None:
            try:
                lang = LangRec(self._config.ocr_language)
            except ValueError as exc:
                supported = [m.value for m in LangRec]
                raise OmniScribeError(
                    f"Unsupported OCR language {self._config.ocr_language!r}. "
                    f"Supported values: {supported}"
                ) from exc

            use_cuda = self._config.ocr_device == "cuda"
            logger.info(
                "Loading RapidOCR on %s — first run may download ~15 MB of ONNX models",
                self._config.ocr_device,
            )
            params: dict[str, object] = {
                "EngineConfig.onnxruntime.use_cuda": use_cuda,
                "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0,
                "Rec.lang_type": lang,
                "Det.lang_type": lang,
            }
            try:
                self._engine = RapidOCR(params=params)
            except Exception as exc:
                raise OmniScribeError(
                    f"Failed to initialize RapidOCR on {self._config.ocr_device}: {exc}"
                ) from exc
        return self._engine

    def extract(self, video_path: Path) -> list[TranscriptSegment]:
        """Sample frames from ``video_path`` and return on-screen text segments.

        Each yielded RapidOCR result contributes zero or more
        :class:`TranscriptSegment` instances — one per detected text box whose
        score meets ``config.ocr_min_confidence``. ``start == end`` equals the
        frame timestamp (sampled text has no intrinsic duration).
        """
        engine = self._ensure_loaded()
        threshold = self._config.ocr_min_confidence
        language = self._config.ocr_language

        frame_count = 0
        segments: list[TranscriptSegment] = []
        profile = self._profile
        apply_mask = (
            self._config.ui_filter_enabled
            and profile is not None
            and bool(profile.ui_exclusion_zones)
        )
        for timestamp, frame in sample_frames(video_path, self._config.ocr_sample_fps):
            frame_count += 1
            processed_frame = preprocess(frame)
            if apply_mask and profile is not None:
                processed_frame = mask_zones(processed_frame, profile.ui_exclusion_zones)
            result = engine(processed_frame)
            texts = getattr(result, "txts", ()) or ()
            scores = getattr(result, "scores", ()) or ()
            for text, score in zip(texts, scores, strict=False):
                if score < threshold:
                    continue
                segments.append(
                    TranscriptSegment(
                        start=timestamp,
                        end=timestamp,
                        text=text,
                        source="ON-SCREEN",
                        confidence=float(score),
                        language=language,
                    )
                )
        self.last_frame_count = frame_count
        return segments

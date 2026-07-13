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
from omniscribe.ocr.bbox_aggregator import aggregate_frame_bboxes
from omniscribe.ocr.frame_sampler import sample_frames
from omniscribe.ocr.preprocessor import preprocess
from omniscribe.ocr.ui_filter import mask_zones
from omniscribe.output import TranscriptSegment

if TYPE_CHECKING:
    from pathlib import Path

    from omniscribe.config import OmniScribeConfig
    from omniscribe.eval.funnel import FunnelCounts
    from omniscribe.platforms.base import PlatformProfile

logger = logging.getLogger(__name__)

# ISO 639-1 code → LangRec mapping for ASR-detected language → OCR rec model.
# Values already valid as LangRec members pass through to the enum directly.
# Unmapped codes fall back to LangRec.EN with a warning.
_ISO_TO_LANGREC: dict[str, LangRec] = {
    "en": LangRec.EN,
    # Latin-script European languages → latin rec model
    "de": LangRec.LATIN,
    "fr": LangRec.LATIN,
    "es": LangRec.LATIN,
    "it": LangRec.LATIN,
    "pt": LangRec.LATIN,
    "nl": LangRec.LATIN,
    "pl": LangRec.LATIN,
    "sv": LangRec.LATIN,
    "da": LangRec.LATIN,
    "no": LangRec.LATIN,
    "fi": LangRec.LATIN,
    "tr": LangRec.LATIN,
    "cs": LangRec.LATIN,
    "sk": LangRec.LATIN,
    "hu": LangRec.LATIN,
    "ro": LangRec.LATIN,
    "ca": LangRec.LATIN,
    "vi": LangRec.LATIN,
    "id": LangRec.LATIN,
    "ms": LangRec.LATIN,
    "sw": LangRec.LATIN,
    "tl": LangRec.LATIN,
    # Cyrillic script
    "ru": LangRec.ESLAV,
    "uk": LangRec.ESLAV,
    "be": LangRec.ESLAV,
    "bg": LangRec.CYRILLIC,
    "sr": LangRec.CYRILLIC,
    "mk": LangRec.CYRILLIC,
    "mn": LangRec.CYRILLIC,
    # CJK
    "zh": LangRec.CH,
    "ja": LangRec.JAPAN,
    "ko": LangRec.KOREAN,
    # Other scripts
    "ar": LangRec.ARABIC,
    "hi": LangRec.DEVANAGARI,
    "th": LangRec.TH,
    "el": LangRec.EL,
    "ta": LangRec.TA,
    "te": LangRec.TE,
    "ka": LangRec.KA,
}


def _resolve_ocr_language(ocr_language: str, *, detected_language: str | None = None) -> LangRec:
    """Resolve ``ocr_language`` config value to a :class:`LangRec` enum member.

    Strategy:

    1. If ``ocr_language`` is a valid ``LangRec`` value, use it directly.
    2. If ``ocr_language`` is ``"auto"``, resolve via ``detected_language``
       (falling back to ``"en"`` if ``detected_language`` is ``None``).
    3. Otherwise, treat ``ocr_language`` as an ISO 639-1 code and look it
       up in :data:`_ISO_TO_LANGREC`. Unmapped codes emit a warning and
       fall back to ``LangRec.EN``.

    Returns the resolved :class:`LangRec` member.
    """
    # Already a valid LangRec value? (e.g. "en", "latin", "ch")
    try:
        return LangRec(ocr_language)
    except ValueError:
        pass

    # "auto" → resolve from detected language
    if ocr_language == "auto":
        resolved_iso = detected_language or "en"
        lang = _ISO_TO_LANGREC.get(resolved_iso)
        if lang is None:
            logger.warning(
                "No LangRec mapping for detected language %r; falling back to en",
                resolved_iso,
            )
            return LangRec.EN
        logger.info("OCR language auto-resolved: %r → %s", resolved_iso, lang.value)
        return lang

    # Treat as ISO 639-1 code
    lang = _ISO_TO_LANGREC.get(ocr_language)
    if lang is None:
        logger.warning("Unmapped ISO code %r for OCR; falling back to en", ocr_language)
        return LangRec.EN
    return lang


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
    ``"OCR: N segments from M frames"`` log line. With Sprint 2.5 scene-change
    detection enabled (default), *yielded* frames may be fewer than
    ``ocr_sample_fps * duration``; the counter is the correct denominator for
    downstream frequency-based UI filtering.
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

    def _ensure_loaded(self, *, detected_language: str | None = None) -> RapidOCR:
        if self._engine is None:
            lang = _resolve_ocr_language(
                self._config.ocr_language, detected_language=detected_language
            )

            use_cuda = self._config.ocr_device == "cuda"
            logger.info(
                "Loading RapidOCR on %s — first run may download ~15 MB of ONNX models",
                self._config.ocr_device,
            )
            # Detection model: en covers all latin-script languages.
            # Recognition model: uses actual language for character set.
            det_lang = LangRec.EN if lang == LangRec.LATIN else lang
            params: dict[str, object] = {
                "EngineConfig.onnxruntime.use_cuda": use_cuda,
                "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0,
                "Rec.lang_type": lang,
                "Det.lang_type": det_lang,
            }
            # Sprint 9.4 — optional Det overrides (None = rapidocr config.yaml default).
            # NOTE: rapidocr validates only "Global.*" param keys; a wrong "Det.*" key
            # is silently absorbed by OmegaConf with no error — key strings below are
            # verified against rapidocr's config.yaml and must not be renamed casually.
            if self._config.ocr_det_limit_side_len is not None:
                params["Det.limit_side_len"] = self._config.ocr_det_limit_side_len
            if self._config.ocr_det_thresh is not None:
                params["Det.thresh"] = self._config.ocr_det_thresh
            if self._config.ocr_det_box_thresh is not None:
                params["Det.box_thresh"] = self._config.ocr_det_box_thresh
            try:
                self._engine = RapidOCR(params=params)
            except Exception as exc:
                raise OmniScribeError(
                    f"Failed to initialize RapidOCR on {self._config.ocr_device}: {exc}"
                ) from exc
        return self._engine

    def extract(
        self,
        video_path: Path,
        *,
        detected_language: str | None = None,
        funnel: FunnelCounts | None = None,
    ) -> list[TranscriptSegment]:
        """Sample frames from ``video_path`` and return on-screen text segments.

        Each yielded RapidOCR result contributes zero or more
        :class:`TranscriptSegment` instances — one per **aggregated text line**
        per frame, where same-y-line bounding boxes are joined left-to-right
        into one canonical caption string by
        :func:`omniscribe.ocr.bbox_aggregator.aggregate_frame_bboxes`. The
        per-bbox confidence gate (``score < ocr_min_confidence``) is applied
        inside the aggregator before grouping. ``start == end`` equals the
        frame timestamp (sampled text has no intrinsic duration); cross-frame
        dedup grows the span downstream.

        When ``funnel`` is provided, stage-wise counts are recorded on the
        :class:`FunnelCounts` instance for pipeline diagnostics.
        """
        engine = self._ensure_loaded(detected_language=detected_language)
        threshold = self._config.ocr_min_confidence
        language = self._config.ocr_language

        frame_count = 0
        segments: list[TranscriptSegment] = []
        profile = self._profile
        # Combine UI exclusion zones and auto-caption band (if masking enabled).
        if self._config.ui_filter_enabled and profile is not None:
            mask_rects = list(profile.ui_exclusion_zones)
            if self._config.ocr_mask_auto_captions:
                mask_rects.extend(profile.auto_caption_zones)
        else:
            mask_rects = []
        apply_mask = bool(mask_rects)
        for timestamp, frame in sample_frames(
            video_path,
            self._config.ocr_sample_fps,
            scene_change_enabled=self._config.scene_change_enabled,
            scene_change_threshold=self._config.scene_change_threshold,
        ):
            frame_count += 1
            processed_frame = preprocess(frame)
            if apply_mask:
                processed_frame = mask_zones(processed_frame, mask_rects)
            result = engine(processed_frame)
            # Guard explicitly for ``None`` rather than ``or ()``: ``boxes`` is
            # a numpy array on populated frames and ``or`` raises on numpy
            # truthiness. ``txts`` / ``scores`` are tuples today, but the same
            # pattern keeps the call site robust if RapidOCR ever returns
            # numpy arrays for them too.
            boxes_attr = getattr(result, "boxes", None)
            boxes = boxes_attr if boxes_attr is not None else ()
            texts_attr = getattr(result, "txts", None)
            texts = texts_attr if texts_attr is not None else ()
            scores_attr = getattr(result, "scores", None)
            scores = scores_attr if scores_attr is not None else ()
            if funnel is not None:
                funnel.raw_bboxes += len(boxes)
            aggregated = aggregate_frame_bboxes(
                boxes,
                texts,
                scores,
                min_confidence=threshold,
            )
            if funnel is not None:
                funnel.post_aggregation += len(aggregated)
            for text, mean_score in aggregated:
                segments.append(
                    TranscriptSegment(
                        start=timestamp,
                        end=timestamp,
                        text=text,
                        source="ON-SCREEN",
                        confidence=mean_score,
                        language=language,
                    )
                )
        if funnel is not None:
            funnel.post_extract += len(segments)
        self.last_frame_count = frame_count
        return segments

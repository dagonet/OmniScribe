"""Unit tests for omniscribe.ocr.rapid_ocr — all external boundaries mocked.

Patch targets live at the import site:

* ``omniscribe.ocr.rapid_ocr.RapidOCR``
* ``omniscribe.ocr.rapid_ocr.sample_frames``

A :class:`types.SimpleNamespace` stands in for :class:`rapidocr.utils.output.RapidOCROutput`
— the engine reads only ``.boxes``, ``.txts`` and ``.scores``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from rapidocr import LangRec

from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError
from omniscribe.ocr.rapid_ocr import RapidOCREngine


def _make_config(**overrides: object) -> OmniScribeConfig:
    # Sprint 2.5: ``scene_change_enabled`` / ``scene_change_threshold`` are
    # omitted here on purpose — Pydantic supplies their defaults (True, 0.02).
    # Tests that patch ``sample_frames`` don't care about the values, and
    # ``test_extract_passes_scene_change_kwargs_to_sampler`` overrides explicitly.
    base: dict[str, object] = {
        "ocr_enabled": True,
        "ocr_language": "en",
        "ocr_sample_fps": 1.0,
        "ocr_min_confidence": 0.6,
        "ocr_device": "cuda",
    }
    base.update(overrides)
    return OmniScribeConfig(**base)


def _fake_frame() -> np.ndarray:
    return np.zeros((2, 2, 3), dtype=np.uint8)


def _ocr_output(
    texts: tuple[str, ...],
    scores: tuple[float, ...],
) -> SimpleNamespace:
    """Build a fake RapidOCR result with bboxes stacked vertically.

    Each text gets its own y-line so the post-Sprint-OCR-Recall aggregator
    treats them as separate segments rather than joining them into one line.
    Box i sits at y in ``[i * 100, i * 100 + 30]`` (height 30, gap 70 → far
    larger than the 0.5 * mean_height tolerance).
    """
    n = len(texts)
    if n == 0:
        return SimpleNamespace(
            boxes=np.zeros((0, 4, 2), dtype=np.float32), txts=texts, scores=scores
        )
    boxes = np.zeros((n, 4, 2), dtype=np.float32)
    for i in range(n):
        y_min = float(i) * 100.0
        y_max = y_min + 30.0
        x_min, x_max = 0.0, 100.0
        boxes[i] = [
            [x_min, y_min],
            [x_max, y_min],
            [x_max, y_max],
            [x_min, y_max],
        ]
    return SimpleNamespace(boxes=boxes, txts=texts, scores=scores)


def test_constructor_does_not_load_engine() -> None:
    with patch("omniscribe.ocr.rapid_ocr.RapidOCR") as mock_rapid_cls:
        RapidOCREngine(_make_config())
        mock_rapid_cls.assert_not_called()


def test_extract_lazy_initializes_engine_once(tmp_path: Path) -> None:
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock(name="RapidOCR-engine")
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock) as mock_rapid_cls,
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(0.0, _fake_frame()), (1.0, _fake_frame())]),
        ),
    ):
        ocr = RapidOCREngine(config)
        ocr.extract(video)

    assert mock_rapid_cls.call_count == 1
    assert engine_mock.call_count == 2  # one call per frame


def test_extract_reuses_engine_across_calls(tmp_path: Path) -> None:
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock(name="RapidOCR-engine")
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock) as mock_rapid_cls,
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            side_effect=[iter([]), iter([])],
        ),
    ):
        ocr = RapidOCREngine(config)
        ocr.extract(video)
        ocr.extract(video)

    assert mock_rapid_cls.call_count == 1


def test_init_params_for_cuda_device(tmp_path: Path) -> None:
    config = _make_config(ocr_device="cuda")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock) as mock_rapid_cls,
        patch("omniscribe.ocr.rapid_ocr.sample_frames", return_value=iter([])),
    ):
        RapidOCREngine(config).extract(video)

    _, kwargs = mock_rapid_cls.call_args
    params = kwargs["params"]
    assert params["EngineConfig.onnxruntime.use_cuda"] is True
    assert params["EngineConfig.onnxruntime.cuda_ep_cfg.device_id"] == 0
    assert params["Rec.lang_type"] is LangRec.EN
    assert params["Det.lang_type"] is LangRec.EN


def test_init_params_for_cpu_device(tmp_path: Path) -> None:
    config = _make_config(ocr_device="cpu")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock) as mock_rapid_cls,
        patch("omniscribe.ocr.rapid_ocr.sample_frames", return_value=iter([])),
    ):
        RapidOCREngine(config).extract(video)

    _, kwargs = mock_rapid_cls.call_args
    params = kwargs["params"]
    assert params["EngineConfig.onnxruntime.use_cuda"] is False


def test_extract_filters_below_confidence_threshold(tmp_path: Path) -> None:
    config = _make_config(ocr_min_confidence=0.6)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(
        texts=("keep me", "drop me", "also keep"),
        scores=(0.95, 0.42, 0.60),
    )

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(2.5, _fake_frame())]),
        ),
    ):
        segments = RapidOCREngine(config).extract(video)

    assert [s.text for s in segments] == ["keep me", "also keep"]
    assert [s.confidence for s in segments] == [0.95, 0.60]


def test_extract_handles_empty_frame_result(tmp_path: Path) -> None:
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(0.0, _fake_frame()), (1.0, _fake_frame())]),
        ),
    ):
        segments = RapidOCREngine(config).extract(video)

    assert segments == []


def test_extract_segment_fields(tmp_path: Path) -> None:
    config = _make_config(ocr_language="en", ocr_min_confidence=0.5)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(
        texts=("overlay text",),
        scores=(0.88,),
    )

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(3.75, _fake_frame())]),
        ),
    ):
        segments = RapidOCREngine(config).extract(video)

    assert len(segments) == 1
    seg = segments[0]
    assert seg.start == 3.75
    assert seg.end == 3.75
    assert seg.source == "ON-SCREEN"
    assert seg.language == "en"
    assert seg.text == "overlay text"
    assert seg.confidence == 0.88


def test_extract_logs_info_before_first_init(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            side_effect=[iter([]), iter([])],
        ),
        caplog.at_level(logging.INFO, logger="omniscribe.ocr.rapid_ocr"),
    ):
        ocr = RapidOCREngine(config)
        ocr.extract(video)
        info_first = [
            r.getMessage() for r in caplog.records if "Loading RapidOCR" in r.getMessage()
        ]
        caplog.clear()
        ocr.extract(video)
        info_second = [
            r.getMessage() for r in caplog.records if "Loading RapidOCR" in r.getMessage()
        ]

    assert len(info_first) == 1
    assert info_second == []


def test_extract_raises_on_unsupported_language(tmp_path: Path) -> None:
    """Unknown ocr_language values are rejected at config-construction time
    by field_validator, not left to the OCR engine."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ocr_language"):
        _make_config(ocr_language="xx")


# ── _resolve_ocr_language unit tests ────────────────────────────────


class TestResolveOCRLanguage:
    @staticmethod
    @pytest.mark.parametrize(
        "ocr_lang, detected, expected",
        [
            ("en", None, "en"),
            ("latin", None, "latin"),
            ("ch", None, "ch"),
            ("auto", "de", "latin"),
            ("auto", "fr", "latin"),
            ("auto", "ru", "eslav"),
            ("auto", "zh", "ch"),
            ("auto", "ja", "japan"),
            ("auto", "ar", "arabic"),
            ("auto", None, "en"),
            ("de", None, "latin"),
            ("fr", None, "latin"),
            ("ru", None, "eslav"),
            ("zh", None, "ch"),
        ],
    )
    def test_resolves_to_expected_langrec(
        ocr_lang: str, detected: str | None, expected: str
    ) -> None:
        from omniscribe.ocr.rapid_ocr import _resolve_ocr_language

        result = _resolve_ocr_language(ocr_lang, detected_language=detected)
        assert result.value == expected

    @staticmethod
    def test_auto_with_unmapped_detected_falls_back_to_en(caplog) -> None:
        """When detected language has no mapping, fall back to en with warning."""
        from omniscribe.ocr.rapid_ocr import _resolve_ocr_language

        result = _resolve_ocr_language("auto", detected_language="xx")
        assert result.value == "en"
        assert "No LangRec mapping" in caplog.text

    @staticmethod
    def test_unmapped_iso_falls_back_to_en_with_warning(caplog) -> None:
        """Explicit unmapped ISO code falls back to en with warning.
        (Config validator rejects unmapped values, but _resolve_ocr_language
        handles them defensively.)"""
        from omniscribe.ocr.rapid_ocr import _resolve_ocr_language

        result = _resolve_ocr_language("xx")
        assert result.value == "en"
        assert "Unmapped ISO code" in caplog.text


# ── auto-caption mask zone tests ────────────────────────────────────


def test_extract_excludes_auto_caption_zones_when_masking_disabled(
    tmp_path: Path,
) -> None:
    """When ocr_mask_auto_captions=False, mask_zones receives only
    ui_exclusion_zones, not auto_caption_zones."""
    from omniscribe.platforms.tiktok import TIKTOK_PROFILE

    config = _make_config(ui_filter_enabled=True, ocr_mask_auto_captions=False)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(0.0, _fake_frame())]),
        ),
        patch("omniscribe.ocr.rapid_ocr.mask_zones") as mock_mask,
    ):
        mock_mask.side_effect = lambda gray, zones: gray
        RapidOCREngine(config, profile=TIKTOK_PROFILE).extract(video)

    mock_mask.assert_called_once()
    _, zones = mock_mask.call_args[0]
    assert tuple(zones) == TIKTOK_PROFILE.ui_exclusion_zones


def test_extract_wraps_engine_init_failure_as_omniscribe_error(tmp_path: Path) -> None:
    """RapidOCR constructor failure (e.g. missing CUDA provider, broken ONNX model)
    must surface as ``OmniScribeError`` so the CLI's ``except`` handler can catch it
    and render a clean single-line error instead of a traceback.
    """
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    with (
        patch(
            "omniscribe.ocr.rapid_ocr.RapidOCR",
            side_effect=RuntimeError("CUDAExecutionProvider not available"),
        ),
        patch("omniscribe.ocr.rapid_ocr.sample_frames", return_value=iter([])),
        pytest.raises(OmniScribeError, match="Failed to initialize RapidOCR"),
    ):
        RapidOCREngine(config).extract(video)


def test_extract_calls_mask_zones_when_profile_and_ui_filter_enabled(tmp_path: Path) -> None:
    """With a TikTok profile + ui_filter_enabled=True, mask_zones is called per frame
    with the profile's exclusion zones."""
    from omniscribe.platforms.tiktok import TIKTOK_PROFILE

    config = _make_config(ui_filter_enabled=True)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(0.0, _fake_frame()), (1.0, _fake_frame())]),
        ),
        patch("omniscribe.ocr.rapid_ocr.mask_zones") as mock_mask,
    ):
        mock_mask.side_effect = lambda gray, zones: gray  # pass through
        RapidOCREngine(config, profile=TIKTOK_PROFILE).extract(video)

    assert mock_mask.call_count == 2
    expected_zones = TIKTOK_PROFILE.ui_exclusion_zones + TIKTOK_PROFILE.auto_caption_zones
    for call in mock_mask.call_args_list:
        _, zones = call.args
        assert tuple(zones) == expected_zones


def test_extract_does_not_call_mask_zones_when_ui_filter_disabled(tmp_path: Path) -> None:
    """With ui_filter_enabled=False, mask_zones is never called even if a profile
    is supplied."""
    from omniscribe.platforms.tiktok import TIKTOK_PROFILE

    config = _make_config(ui_filter_enabled=False)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(0.0, _fake_frame()), (1.0, _fake_frame())]),
        ),
        patch("omniscribe.ocr.rapid_ocr.mask_zones") as mock_mask,
    ):
        RapidOCREngine(config, profile=TIKTOK_PROFILE).extract(video)

    mock_mask.assert_not_called()


def test_extract_passes_scene_change_kwargs_to_sampler(tmp_path: Path) -> None:
    """Sprint 2.5 — ``RapidOCREngine.extract`` plumbs scene-change config into
    ``sample_frames`` as kwargs (not positional), so the sampler signature stays
    stable for other callers.
    """
    config = _make_config(scene_change_enabled=True, scene_change_threshold=0.05)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([]),
        ) as mock_sampler,
    ):
        RapidOCREngine(config).extract(video)

    assert mock_sampler.call_count == 1
    _, kwargs = mock_sampler.call_args
    assert kwargs["scene_change_enabled"] is True
    assert kwargs["scene_change_threshold"] == 0.05


def test_extract_records_last_frame_count(tmp_path: Path) -> None:
    """``last_frame_count`` must equal the number of frames the sampler yielded
    (used by the CLI's ``"OCR: N segments from M frames"`` log line).
    """
    config = _make_config()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    engine_mock = MagicMock()
    engine_mock.return_value = _ocr_output(texts=(), scores=())

    sampled_frames = [
        (0.0, _fake_frame()),
        (1.0, _fake_frame()),
        (2.0, _fake_frame()),
    ]

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter(sampled_frames),
        ),
    ):
        ocr = RapidOCREngine(config)
        assert ocr.last_frame_count == 0  # initialized on __init__
        ocr.extract(video)

    assert ocr.last_frame_count == 3


def test_extract_aggregates_same_line_bboxes_into_one_segment(tmp_path: Path) -> None:
    """Sprint OCR-Recall — wiring guard for the bbox aggregator.

    The default ``_ocr_output`` fixture stacks bboxes vertically so each text
    becomes its own segment; that's deliberate (it preserves the intent of
    the original per-bbox tests) but it also means the aggregation call in
    :meth:`RapidOCREngine.extract` could be removed and every other test
    would still pass — false coverage. This test feeds two bboxes on the
    SAME y-line and asserts the engine emits ONE joined segment, locking
    the wiring so an accidental refactor surfaces immediately.
    """
    config = _make_config(ocr_min_confidence=0.6)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    # Two bboxes on the same y-line (y in [0, 30]), x-adjacent: left then
    # right. Aggregator should merge them into ``"left right"``.
    boxes = np.array(
        [
            [[0.0, 0.0], [50.0, 0.0], [50.0, 30.0], [0.0, 30.0]],
            [[60.0, 0.0], [110.0, 0.0], [110.0, 30.0], [60.0, 30.0]],
        ],
        dtype=np.float32,
    )
    same_line_result = SimpleNamespace(boxes=boxes, txts=("left", "right"), scores=(0.9, 0.8))

    engine_mock = MagicMock()
    engine_mock.return_value = same_line_result

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR", return_value=engine_mock),
        patch(
            "omniscribe.ocr.rapid_ocr.sample_frames",
            return_value=iter([(2.5, _fake_frame())]),
        ),
    ):
        segments = RapidOCREngine(config).extract(video)

    assert len(segments) == 1
    seg = segments[0]
    assert seg.text == "left right"
    assert seg.source == "ON-SCREEN"
    assert seg.start == 2.5
    assert seg.end == 2.5
    # Mean confidence of (0.9, 0.8).
    assert seg.confidence == pytest.approx(0.85)

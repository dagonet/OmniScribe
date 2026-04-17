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
    n = len(texts)
    boxes = np.zeros((n, 4, 2), dtype=np.float32) if n else np.zeros((0, 4, 2), dtype=np.float32)
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
    config = _make_config(ocr_language="xx")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")

    with (
        patch("omniscribe.ocr.rapid_ocr.RapidOCR") as mock_rapid_cls,
        patch("omniscribe.ocr.rapid_ocr.sample_frames", return_value=iter([])),
        pytest.raises(OmniScribeError, match="Unsupported OCR language 'xx'"),
    ):
        RapidOCREngine(config).extract(video)

    # Enum coercion fails *before* RapidOCR is constructed.
    mock_rapid_cls.assert_not_called()

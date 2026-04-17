"""Unit tests for omniscribe.ocr.frame_sampler.

All OpenCV I/O is mocked at the import site (``omniscribe.ocr.frame_sampler.cv2.VideoCapture``)
so the suite never touches a real video file and never requires an OpenCV build with
codec support on CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from omniscribe.errors import OmniScribeError
from omniscribe.ocr.frame_sampler import sample_frames


def _make_fake_capture(
    native_fps: float,
    frame_count: int,
    is_opened: bool = True,
) -> MagicMock:
    """Build a MagicMock that mimics ``cv2.VideoCapture``."""
    cap = MagicMock(name="VideoCapture")
    cap.isOpened.return_value = is_opened

    def _get(prop: int) -> float:
        # cv2.CAP_PROP_FPS == 5, cv2.CAP_PROP_FRAME_COUNT == 7.
        if prop == 5:
            return native_fps
        if prop == 7:
            return float(frame_count)
        return 0.0

    cap.get.side_effect = _get

    frames_iter = iter(range(frame_count))

    def _read() -> tuple[bool, Any]:
        try:
            idx = next(frames_iter)
        except StopIteration:
            return (False, None)
        frame = np.full((2, 2, 3), idx, dtype=np.uint8)
        return (True, frame)

    cap.read.side_effect = _read
    return cap


def test_sample_frames_yields_expected_timestamps_at_1fps(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=30.0, frame_count=90)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    timestamps = [t for t, _ in samples]
    assert timestamps == [0.0, 1.0, 2.0]
    for _, frame in samples:
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (2, 2, 3)


def test_sample_frames_raises_when_capture_fails_to_open(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=30.0, frame_count=0, is_opened=False)

    with (
        patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap),
        pytest.raises(OmniScribeError),
    ):
        list(sample_frames(video, fps=1.0))


def test_sample_frames_raises_when_native_fps_is_zero(tmp_path: Path) -> None:
    """A video that reports ``CAP_PROP_FPS == 0.0`` would cause a divide-by-zero
    in the stride calculation; the guard must surface it as ``OmniScribeError``.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=0.0, frame_count=10)

    with (
        patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap),
        pytest.raises(OmniScribeError, match="non-positive native FPS"),
    ):
        list(sample_frames(video, fps=1.0))


def test_sample_frames_releases_capture_on_exception(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=30.0, frame_count=90)

    def _boom() -> tuple[bool, Any]:
        raise RuntimeError("read-path failure")

    cap.read.side_effect = _boom

    with (
        patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap),
        pytest.raises(RuntimeError),
    ):
        list(sample_frames(video, fps=1.0))

    cap.release.assert_called_once()


def test_sample_frames_stride_for_low_fps(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    # 30 fps source, requested 0.5 fps -> stride = round(30 / 0.5) = 60.
    # 180 frames / stride 60 -> frame indices 0, 60, 120 -> timestamps 0.0, 2.0, 4.0.
    cap = _make_fake_capture(native_fps=30.0, frame_count=180)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=0.5))

    timestamps = [t for t, _ in samples]
    assert timestamps == [0.0, 2.0, 4.0]


def test_sample_frames_stride_at_least_one_when_requested_exceeds_native(
    tmp_path: Path,
) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    # Requested fps > native fps -> stride must clamp to 1, yielding every frame.
    cap = _make_fake_capture(native_fps=2.0, frame_count=3)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=10.0))

    assert [t for t, _ in samples] == [0.0, 0.5, 1.0]


def test_sample_frames_passes_string_path_to_videocapture(tmp_path: Path) -> None:
    video = tmp_path / "nested" / "v.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=30.0, frame_count=1)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap) as mock_vc:
        list(sample_frames(video, fps=1.0))

    (arg,), _ = mock_vc.call_args
    assert isinstance(arg, str)
    assert arg == str(video.resolve())

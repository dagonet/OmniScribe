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
    # Sprint 2.5 default turns on scene-change detection; this test asserts
    # stride-math only, so opt out to preserve the original yield count.
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    cap = _make_fake_capture(native_fps=30.0, frame_count=90)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0, scene_change_enabled=False))

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
    # Sprint 2.5: stride-math assertion → opt out of scene-change filtering.
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    # 30 fps source, requested 0.5 fps -> stride = round(30 / 0.5) = 60.
    # 180 frames / stride 60 -> frame indices 0, 60, 120 -> timestamps 0.0, 2.0, 4.0.
    cap = _make_fake_capture(native_fps=30.0, frame_count=180)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=0.5, scene_change_enabled=False))

    timestamps = [t for t, _ in samples]
    assert timestamps == [0.0, 2.0, 4.0]


def test_sample_frames_stride_at_least_one_when_requested_exceeds_native(
    tmp_path: Path,
) -> None:
    # Sprint 2.5: stride-math assertion → opt out of scene-change filtering.
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    # Requested fps > native fps -> stride must clamp to 1, yielding every frame.
    cap = _make_fake_capture(native_fps=2.0, frame_count=3)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=10.0, scene_change_enabled=False))

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


# -- Sprint 2.5 scene-change detection tests --------------------------------
#
# ``_downscale_gray`` requires realistic frame shapes (2x2 BGR inputs collapse
# to trivial 1-pixel grayscale buffers after cv2.resize fallbacks). These tests
# use 180x320 BGR frames sized ``(H=180, W=320, 3)`` so the downscale path is
# a no-op (already at the longest-edge cap) and ``_frame_difference`` reports
# the exact expected value for the constant-color fixtures below.


def _frame(value: int) -> np.ndarray:
    """Build a 180x320 BGR frame of constant ``value`` (0-255)."""
    return np.full((180, 320, 3), value, dtype=np.uint8)


def _capture_from_frames(native_fps: float, frames: list[np.ndarray]) -> MagicMock:
    """Fake ``cv2.VideoCapture`` that yields the given pre-built frame list."""
    cap = MagicMock(name="VideoCapture")
    cap.isOpened.return_value = True

    def _get(prop: int) -> float:
        if prop == 5:  # cv2.CAP_PROP_FPS
            return native_fps
        if prop == 7:  # cv2.CAP_PROP_FRAME_COUNT
            return float(len(frames))
        return 0.0

    cap.get.side_effect = _get

    iterator = iter(frames)

    def _read() -> tuple[bool, Any]:
        try:
            return (True, next(iterator))
        except StopIteration:
            return (False, None)

    cap.read.side_effect = _read
    return cap


def test_frame_difference_range_zero_and_saturated() -> None:
    """``_frame_difference`` returns 0.0 for identical, 1.0 for full-saturation."""
    from omniscribe.ocr.frame_sampler import _frame_difference

    a = np.zeros((4, 4), dtype=np.uint8)
    b = np.zeros((4, 4), dtype=np.uint8)
    assert _frame_difference(a, b) == 0.0

    c = np.full((4, 4), 255, dtype=np.uint8)
    assert _frame_difference(a, c) == 1.0


def test_frame_difference_midrange_value() -> None:
    """Mean-absdiff of abs(50 - 200) over a constant patch = 150 / 255 ~ 0.588."""
    from omniscribe.ocr.frame_sampler import _frame_difference

    a = np.full((4, 4), 50, dtype=np.uint8)
    b = np.full((4, 4), 200, dtype=np.uint8)
    assert abs(_frame_difference(a, b) - (150.0 / 255.0)) < 1e-9


def test_downscale_gray_shape_is_single_channel_and_bounded() -> None:
    """1080p BGR input → single-channel grayscale with longest edge ≤ 320."""
    from omniscribe.ocr.frame_sampler import _downscale_gray

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    small = _downscale_gray(frame)
    assert small.ndim == 2
    assert max(small.shape) <= 320


def test_scene_change_disabled_yields_every_strided_frame(tmp_path: Path) -> None:
    """Phase 2 regression: with scene-change off, every stride-picked frame yields."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100), _frame(100), _frame(100)]
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0, scene_change_enabled=False))

    assert [t for t, _ in samples] == [0.0, 1.0, 2.0]


def test_scene_change_first_frame_always_yields(tmp_path: Path) -> None:
    """First stride-picked frame must yield even if followed by identical frames."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100), _frame(100), _frame(100)]
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert [t for t, _ in samples] == [0.0]


def test_scene_change_step_change_yields_both_segments(tmp_path: Path) -> None:
    """5 identical (value=50), then 5 identical (value=200) → 2 yields: t=0, t=5."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(50)] * 5 + [_frame(200)] * 5
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert [t for t, _ in samples] == [0.0, 5.0]


def test_scene_change_gradient_drift_forces_yield_via_max_gap(tmp_path: Path) -> None:
    """Gradient drift below threshold for 60 frames at fps=1.0 → forced yield fires.

    Threshold 0.02 at constant +1 per frame from a baseline of value=50 means
    each incremental diff is 1/255 ≈ 0.004 (below threshold). After 30 frames
    the cumulative absdiff vs the first yielded frame is 30/255 ≈ 0.118, which
    would trip the scene-change rule. To isolate the *max-gap* rule we build
    frames that stay within threshold of the *last yielded* frame yet drift.

    Practical test: 60 identical frames at fps=1.0 → max_gap_frames = 30 →
    at least the first yield + one forced yield at frame 30.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100)] * 60
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    timestamps = [t for t, _ in samples]
    assert len(timestamps) >= 2
    assert timestamps[0] == 0.0
    # Max-gap is 30 seconds at fps=1.0; forced yield lands at stride index 30.
    assert timestamps[1] == pytest.approx(30.0)


def test_scene_change_end_of_video_force_yields_nonzero_trailing(tmp_path: Path) -> None:
    """End-of-video rule: trailing frame with sub-threshold-but-nonzero diff yields.

    3 identical frames (value=100), then 1 final frame (value=101). The final
    frame's diff is 1/255 ≈ 0.004 — below the 0.02 threshold — but strictly
    greater than 0.0, so the end-of-video rule force-yields it.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100)] * 3 + [_frame(101)]
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    timestamps = [t for t, _ in samples]
    assert timestamps == [0.0, 3.0]


def test_scene_change_end_of_video_skips_pure_duplicate_trailing(tmp_path: Path) -> None:
    """End-of-video rule: pure-duplicate trailing frame is NOT force-yielded.

    4 identical frames → only the first yields; the last has diff == 0.0.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100)] * 4
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert [t for t, _ in samples] == [0.0]


def test_scene_change_defaults_path_reaches_sampler(tmp_path: Path) -> None:
    """Omitting scene-change kwargs uses defaults (enabled=True, threshold=0.02).

    Verifies the default-args path still works end-to-end: 10 unique frames
    with large step changes should yield all 10.
    """
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    values = [0, 50, 100, 150, 200, 250, 10, 60, 110, 160]
    frames = [_frame(v) for v in values]
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert [t for t, _ in samples] == [float(i) for i in range(10)]


def test_scene_change_ten_hard_cut_slides_yield_ten_frames(tmp_path: Path) -> None:
    """Synthetic 10-slide hard-cut test from the AC list → exactly 10 yields."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    # Alternating low/high values guarantee each step diff ≫ threshold.
    frames = [_frame(30), _frame(220)] * 5
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert len(samples) == 10


def test_scene_change_yielded_frame_is_original_bgr_not_downscaled(tmp_path: Path) -> None:
    """Yielded frames must be the original BGR input, not the grayscale downscale."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    frames = [_frame(100), _frame(200)]
    cap = _capture_from_frames(native_fps=1.0, frames=frames)

    with patch("omniscribe.ocr.frame_sampler.cv2.VideoCapture", return_value=cap):
        samples = list(sample_frames(video, fps=1.0))

    assert len(samples) == 2
    for _, frame in samples:
        assert frame.ndim == 3
        assert frame.shape[2] == 3

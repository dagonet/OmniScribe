"""Sparse frame sampling for OCR inference.

OpenCV on Windows does not play well with :class:`pathlib.Path` (particularly
UNC paths), so every video path is coerced to ``str(Path.resolve())`` before
reaching ``cv2.VideoCapture``.

Sprint 2.5 adds pre-OCR **scene-change detection** via frame-to-frame pixel
mean-absdiff at a 320-longest-edge grayscale downscale. Frames near-identical
to the last yielded frame are skipped. First frame always yields; a bounded
max-gap forces periodic yields for slow gradient drift; an end-of-video rule
guarantees the final visible frame reaches OCR unless it is a pure duplicate.
Phase 2 behavior is recoverable via ``scene_change_enabled=False``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import cv2
import numpy as np

from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# Internal safety valve (not user-tunable). Forces a yield at least once per
# ``_MAX_GAP_SECONDS`` of wall-clock time to survive slow gradient drift that
# stays below ``scene_change_threshold`` indefinitely. Deliberately not exposed
# as config: speculative failure-mode mitigation, revisit only on evidence.
_MAX_GAP_SECONDS: float = 30.0

# Downscale longest edge for the absdiff comparison buffer. 320 x (aspect) is
# ~80 KB per frame vs ~8.3 MB for 4K input, preserves signal for slide cuts,
# and keeps the absdiff O(H*W) work bounded regardless of source resolution.
_DOWNSCALE_LONGEST_EDGE: int = 320


def _downscale_gray(frame_bgr: np.ndarray) -> np.ndarray:
    """Return a grayscale ``_DOWNSCALE_LONGEST_EDGE``-longest-edge downscale of ``frame_bgr``.

    Uses ``cv2.INTER_AREA`` (recommended for shrinking) and
    ``cv2.COLOR_BGR2GRAY`` (slide-cut signal is color-invariant). The yielded
    frame from :func:`sample_frames` stays full-resolution BGR — this helper
    produces only the *comparison buffer* for scene-change detection.
    """
    height, width = frame_bgr.shape[:2]
    longest = max(height, width)
    if longest <= _DOWNSCALE_LONGEST_EDGE:
        small_bgr = frame_bgr
    else:
        scale = _DOWNSCALE_LONGEST_EDGE / float(longest)
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        small_bgr = cv2.resize(frame_bgr, new_size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)


def _frame_difference(prev_small: np.ndarray, curr_small: np.ndarray) -> float:
    """Return mean-absdiff between two grayscale buffers, normalized to ``[0.0, 1.0]``.

    ``0.0`` means pixel-identical. ``1.0`` means every pixel saturated (all-white
    vs all-black), implausible in practice but mathematically possible.
    """
    diff = cv2.absdiff(prev_small, curr_small)
    return float(np.mean(diff)) / 255.0


def sample_frames(
    video_path: Path,
    fps: float,
    *,
    scene_change_enabled: bool = True,
    scene_change_threshold: float = 0.02,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(timestamp_seconds, bgr_frame)`` tuples at roughly ``fps`` samples/sec.

    The sampler reads every native frame (cv2's only supported access pattern) and
    emits one frame per ``stride`` frames, where ``stride = max(1, round(native_fps / fps))``.
    End-of-stream (``cap.read() -> (False, _)``) is normal termination and does NOT
    raise. A capture that fails to open raises :class:`OmniScribeError`.

    With ``scene_change_enabled=True`` (default), stride-picked frames are further
    filtered against the previous yielded frame via grayscale mean-absdiff at a
    320-longest-edge downscale. Yield rules (evaluated in order):

    1. First stride-picked frame — always yield (cold start).
    2. ``_frame_difference >= scene_change_threshold`` — yield.
    3. ``frames_since_yield >= max_gap_frames`` — force-yield (bounded gap,
       ``max_gap_frames = max(1, round(fps * _MAX_GAP_SECONDS))``, fps-invariant
       in wall-clock terms).
    4. Else advance without yielding.

    After the read loop terminates, an end-of-video rule force-yields the final
    stride-picked frame iff its ``_frame_difference`` vs the last yielded frame
    is strictly greater than ``0.0`` (any non-zero pixel change). Pure duplicates
    are dropped.

    Args:
        video_path: Local video file. Converted to ``str(Path.resolve())`` before
            being handed to OpenCV.
        fps: Target sampling frequency in frames-per-second. Must be positive —
            enforced upstream by :class:`omniscribe.config.OmniScribeConfig`;
            sampler does not re-validate.
        scene_change_enabled: When ``False``, short-circuits the downscale + diff
            path entirely — every stride-picked frame yields (Phase 2 baseline).
        scene_change_threshold: Mean-absdiff threshold in ``(0.0, 1.0]`` above
            which a stride-picked frame is considered a scene change.

    Yields:
        ``(timestamp_seconds, bgr_frame)`` for each selected frame. ``timestamp_seconds``
        is derived from the source native FPS, not the cv2 per-frame timestamp
        property (which is unreliable for MP4s without presentation timestamps).

    Raises:
        OmniScribeError: ``cv2.VideoCapture`` failed to open ``video_path``.
    """
    cap = cv2.VideoCapture(str(video_path.resolve()))
    try:
        if not cap.isOpened():
            raise OmniScribeError(f"Failed to open video for OCR sampling: {video_path}")

        native_fps = cap.get(cv2.CAP_PROP_FPS)
        if native_fps <= 0.0:
            raise OmniScribeError(
                f"Video reports non-positive native FPS ({native_fps}); cannot sample."
            )

        stride = max(1, round(native_fps / fps))
        max_gap_frames = max(1, round(fps * _MAX_GAP_SECONDS))
        logger.debug(
            "Frame sampler: native_fps=%.3f target_fps=%.3f stride=%d "
            "scene_change=%s threshold=%.4f max_gap_frames=%d",
            native_fps,
            fps,
            stride,
            scene_change_enabled,
            scene_change_threshold,
            max_gap_frames,
        )

        frame_index = 0
        last_yielded_small: np.ndarray | None = None
        # Track the most recent stride-picked frame (original BGR + downscale +
        # timestamp) so the end-of-video rule can force-yield it after the loop
        # terminates if it was suppressed by the scene-change filter.
        last_frame_bgr: np.ndarray | None = None
        last_small: np.ndarray | None = None
        last_timestamp: float = 0.0
        last_was_yielded = False
        # Counts strided frames evaluated since the last yield (including the
        # current one after the increment below). Force-yields when it reaches
        # ``max_gap_frames``; resets to 0 on every yield.
        frames_since_yield = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % stride == 0:
                timestamp = frame_index / native_fps
                if not scene_change_enabled:
                    # Hot-path short-circuit: Phase 2 baseline — skip downscale + diff.
                    yield (timestamp, frame)
                else:
                    curr_small = _downscale_gray(frame)
                    last_frame_bgr = frame
                    last_small = curr_small
                    last_timestamp = timestamp
                    frames_since_yield += 1
                    if last_yielded_small is None:
                        # First stride-picked frame — cold start, always yield.
                        yield (timestamp, frame)
                        last_yielded_small = curr_small
                        frames_since_yield = 0
                        last_was_yielded = True
                    elif (
                        _frame_difference(last_yielded_small, curr_small) >= scene_change_threshold
                    ):
                        yield (timestamp, frame)
                        last_yielded_small = curr_small
                        frames_since_yield = 0
                        last_was_yielded = True
                    elif frames_since_yield >= max_gap_frames:
                        # Forced yield: bounded max gap survived gradient drift.
                        yield (timestamp, frame)
                        last_yielded_small = curr_small
                        frames_since_yield = 0
                        last_was_yielded = True
                    else:
                        last_was_yielded = False
            frame_index += 1

        # End-of-video rule (scene-change path only): force-yield the final
        # stride-picked frame iff it was suppressed and differs from the last
        # yielded frame. Strict ``> 0.0`` — any non-zero pixel change qualifies;
        # pure duplicates drop.
        if (
            scene_change_enabled
            and not last_was_yielded
            and last_frame_bgr is not None
            and last_small is not None
            and last_yielded_small is not None
            and _frame_difference(last_yielded_small, last_small) > 0.0
        ):
            yield (last_timestamp, last_frame_bgr)
    finally:
        cap.release()

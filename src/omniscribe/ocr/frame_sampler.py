"""Sparse frame sampling for OCR inference.

OpenCV on Windows does not play well with :class:`pathlib.Path` (particularly
UNC paths), so every video path is coerced to ``str(Path.resolve())`` before
reaching ``cv2.VideoCapture``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import cv2

from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import numpy as np

logger = logging.getLogger(__name__)


def sample_frames(video_path: Path, fps: float) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(timestamp_seconds, bgr_frame)`` tuples at roughly ``fps`` samples/sec.

    The sampler reads every native frame (cv2's only supported access pattern) and
    emits one frame per ``stride`` frames, where ``stride = max(1, round(native_fps / fps))``.
    End-of-stream (``cap.read() -> (False, _)``) is normal termination and does NOT
    raise. A capture that fails to open raises :class:`OmniScribeError`.

    Args:
        video_path: Local video file. Converted to ``str(Path.resolve())`` before
            being handed to OpenCV.
        fps: Target sampling frequency in frames-per-second.

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

        native_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        if native_fps <= 0.0:
            raise OmniScribeError(
                f"Video reports non-positive native FPS ({native_fps}); cannot sample."
            )

        stride = max(1, round(native_fps / fps))
        logger.debug(
            "Frame sampler: native_fps=%.3f target_fps=%.3f stride=%d",
            native_fps,
            fps,
            stride,
        )

        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            if frame_index % stride == 0:
                timestamp = frame_index / native_fps
                yield (timestamp, frame)
            frame_index += 1
    finally:
        cap.release()

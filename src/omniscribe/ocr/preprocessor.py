"""Frame preprocessing for OCR.

Pure function that converts a BGR frame (as yielded by ``sample_frames``) into
a contrast-enhanced 2D grayscale array suitable for RapidOCR. Two stages:

1. ``cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)`` — drop color channels.
2. CLAHE (``clipLimit=2.0``, ``tileGridSize=(8, 8)``) — local contrast boost
   that tends to help low-contrast overlays (e.g. subtitles on bright scenes)
   without blowing out already-legible text.

RapidOCR accepts 2D uint8 grayscale arrays directly — no need to re-stack to
BGR after enhancement. Kept dependency-free of :mod:`omniscribe.config` so the
function is trivially composable and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np


def preprocess(frame: np.ndarray) -> np.ndarray:
    """Return a CLAHE-enhanced 2D grayscale copy of ``frame``.

    Parameters
    ----------
    frame:
        BGR frame of shape ``(H, W, 3)`` and dtype ``uint8`` (as yielded by
        :func:`omniscribe.ocr.frame_sampler.sample_frames`).

    Returns
    -------
    np.ndarray
        2D array of shape ``(H, W)`` and dtype ``uint8``.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)

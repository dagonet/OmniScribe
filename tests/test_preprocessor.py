"""Unit tests for omniscribe.ocr.preprocessor.

The preprocessor wraps two OpenCV primitives:

* ``cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)`` — BGR→GRAY dimensionality collapse.
* ``cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)`` — local
  contrast enhancement.

We patch ``cv2`` at the preprocessor's import site so tests do not depend on
OpenCV's native contrast delta (which is unreliable on synthetic frames).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from omniscribe.ocr.preprocessor import preprocess


def _bgr_frame(h: int = 4, w: int = 6, value: int = 128) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_preprocess_returns_2d_grayscale_shape() -> None:
    frame = _bgr_frame(h=4, w=6)
    gray_2d = np.full((4, 6), 128, dtype=np.uint8)
    clahe_out = np.full((4, 6), 200, dtype=np.uint8)

    clahe_mock = MagicMock()
    clahe_mock.apply.return_value = clahe_out

    with patch("omniscribe.ocr.preprocessor.cv2") as cv2_mock:
        cv2_mock.cvtColor.return_value = gray_2d
        cv2_mock.createCLAHE.return_value = clahe_mock
        result = preprocess(frame)

    assert result.ndim == 2
    assert result.shape == (4, 6)


def test_preprocess_invokes_cvt_color_with_bgr2gray() -> None:
    frame = _bgr_frame()
    gray_2d = np.zeros((4, 6), dtype=np.uint8)
    clahe_mock = MagicMock()
    clahe_mock.apply.return_value = gray_2d

    with patch("omniscribe.ocr.preprocessor.cv2") as cv2_mock:
        cv2_mock.cvtColor.return_value = gray_2d
        cv2_mock.createCLAHE.return_value = clahe_mock
        cv2_mock.COLOR_BGR2GRAY = 6  # cv2's real enum value; arbitrary sentinel here
        preprocess(frame)

    cv2_mock.cvtColor.assert_called_once()
    args, _ = cv2_mock.cvtColor.call_args
    passed_frame, color_code = args
    assert passed_frame is frame
    assert color_code == cv2_mock.COLOR_BGR2GRAY


def test_preprocess_invokes_create_clahe_with_expected_kwargs() -> None:
    frame = _bgr_frame()
    gray_2d = np.zeros((4, 6), dtype=np.uint8)
    clahe_mock = MagicMock()
    clahe_mock.apply.return_value = gray_2d

    with patch("omniscribe.ocr.preprocessor.cv2") as cv2_mock:
        cv2_mock.cvtColor.return_value = gray_2d
        cv2_mock.createCLAHE.return_value = clahe_mock
        preprocess(frame)

    cv2_mock.createCLAHE.assert_called_once_with(clipLimit=2.0, tileGridSize=(8, 8))


def test_preprocess_applies_clahe_to_grayscale_output() -> None:
    frame = _bgr_frame()
    gray_2d = np.full((4, 6), 50, dtype=np.uint8)
    clahe_out = np.full((4, 6), 222, dtype=np.uint8)

    clahe_mock = MagicMock()
    clahe_mock.apply.return_value = clahe_out

    with patch("omniscribe.ocr.preprocessor.cv2") as cv2_mock:
        cv2_mock.cvtColor.return_value = gray_2d
        cv2_mock.createCLAHE.return_value = clahe_mock
        result = preprocess(frame)

    clahe_mock.apply.assert_called_once()
    (applied_arg,), _ = clahe_mock.apply.call_args
    assert applied_arg is gray_2d
    np.testing.assert_array_equal(result, clahe_out)


def test_preprocess_preserves_spatial_dimensions() -> None:
    frame = np.full((9, 13, 3), 64, dtype=np.uint8)
    gray_2d = np.full((9, 13), 64, dtype=np.uint8)
    clahe_out = np.full((9, 13), 120, dtype=np.uint8)

    clahe_mock = MagicMock()
    clahe_mock.apply.return_value = clahe_out

    with patch("omniscribe.ocr.preprocessor.cv2") as cv2_mock:
        cv2_mock.cvtColor.return_value = gray_2d
        cv2_mock.createCLAHE.return_value = clahe_mock
        result = preprocess(frame)

    assert result.shape == (9, 13)

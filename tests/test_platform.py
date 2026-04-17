"""Tests for platform detection."""

from __future__ import annotations

import pytest

from omniscribe.acquire.platform import Platform, detect_platform


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://www.tiktok.com/@u/video/1", Platform.TIKTOK),
        ("https://www.youtube.com/watch?v=abc", Platform.YOUTUBE),
        ("https://youtu.be/abc", Platform.YOUTUBE),
        ("https://www.instagram.com/reel/abc/", Platform.INSTAGRAM),
        ("https://example.com/video.mp4", Platform.UNKNOWN),
        ("/local/path.mp4", Platform.UNKNOWN),
    ],
)
def test_detect_platform(source: str, expected: Platform) -> None:
    assert detect_platform(source) is expected

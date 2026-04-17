"""Tests for the video downloader.

Patch :class:`yt_dlp.YoutubeDL` at its *import site* within
``omniscribe.acquire.downloader``. Patching at ``yt_dlp.YoutubeDL`` leaves the
already-bound alias inside the downloader module untouched and would execute
the real class.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from omniscribe.acquire.downloader import download_video
from omniscribe.errors import OmniScribeError


def test_local_file_passthrough(silence_wav_path: Path, tmp_path: Path) -> None:
    """Local files are returned unchanged; YoutubeDL is never instantiated."""
    with patch("omniscribe.acquire.downloader.yt_dlp.YoutubeDL") as mock_ydl:
        result = download_video(str(silence_wav_path), tmp_path / "dl")

    assert result == silence_wav_path
    mock_ydl.assert_not_called()


def test_url_download(tmp_path: Path) -> None:
    """HTTP URLs are passed to yt-dlp and the prepared filename is returned."""
    with patch("omniscribe.acquire.downloader.yt_dlp.YoutubeDL") as mock_ydl:
        instance = MagicMock()
        mock_ydl.return_value.__enter__.return_value = instance
        instance.extract_info.return_value = {"id": "abc", "ext": "mp4"}
        instance.prepare_filename.return_value = "/tmp/omni/abc.mp4"

        result = download_video("https://example.com/v/abc", tmp_path / "dl")

    assert result == Path("/tmp/omni/abc.mp4")
    instance.extract_info.assert_called_once_with("https://example.com/v/abc", download=True)


def test_invalid_source_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="neither a file nor an http"):
        download_video("not a url or file", tmp_path / "dl")


def test_download_error_wrapped_as_omniscribe_error(tmp_path: Path) -> None:
    """yt-dlp DownloadError is surfaced as a clean single-line OmniScribeError."""
    with patch("omniscribe.acquire.downloader.yt_dlp.YoutubeDL") as mock_ydl:
        instance = MagicMock()
        mock_ydl.return_value.__enter__.return_value = instance
        instance.extract_info.side_effect = yt_dlp.utils.DownloadError("blocked")

        with pytest.raises(OmniScribeError) as exc_info:
            download_video("https://example.com/v/abc", tmp_path / "dl")

    assert "Download failed" in str(exc_info.value)
    assert "blocked" in str(exc_info.value)
    # ``raise ... from None`` severs the explicit cause chain.
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True

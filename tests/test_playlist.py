"""Tests for playlist / channel expansion (Sprint 8.1)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from omniscribe.acquire.playlist import expand_playlist
from omniscribe.batch import expand_url_list


def _make_ydl_mock(
    extract_info_return: Any | None = None,
    extract_info_raises: Exception | None = None,
) -> MagicMock:
    """Return a YoutubeDL-class mock whose context-manager .extract_info is set."""
    ydl_cls = MagicMock()
    instance = MagicMock()
    if extract_info_raises is not None:
        instance.extract_info.side_effect = extract_info_raises
    else:
        instance.extract_info.return_value = extract_info_return
    ydl_cls.return_value.__enter__.return_value = instance
    ydl_cls.return_value.__exit__.return_value = False
    return ydl_cls


@pytest.mark.parametrize(
    "playlist_url",
    [
        "https://www.youtube.com/playlist?list=PLabc",
        "https://www.youtube.com/@channel/videos",
        "https://www.tiktok.com/@user",
    ],
)
def test_expand_playlist_recognizes_playlist_urls(playlist_url: str) -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "v1", "url": "https://example.com/v1"},
            {"id": "v2", "url": "https://example.com/v2"},
        ],
    }
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist(playlist_url)
    assert out == ["https://example.com/v1", "https://example.com/v2"]


def test_expand_playlist_single_video_returns_none() -> None:
    info = {"_type": "video", "id": "abc", "url": "https://example.com/abc"}
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://www.youtube.com/watch?v=abc")
    assert out is None


def test_expand_playlist_local_file_path_returns_none() -> None:
    ydl_cls = _make_ydl_mock(extract_info_return={"_type": "playlist", "entries": []})
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("./video.mp4")
    assert out is None
    ydl_cls.assert_not_called()


def test_expand_playlist_absolute_path_no_scheme_returns_none() -> None:
    ydl_cls = _make_ydl_mock(extract_info_return={"_type": "playlist", "entries": []})
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("/foo/bar.mp4")
    assert out is None
    ydl_cls.assert_not_called()


def test_expand_playlist_empty_playlist_returns_empty_list() -> None:
    info = {"_type": "playlist", "entries": []}
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/empty-playlist")
    assert out == []


def test_expand_playlist_lazylist_entries_coerced() -> None:
    def _gen():
        yield {"id": "v1", "url": "https://example.com/v1"}
        yield {"id": "v2", "url": "https://example.com/v2"}

    info = {"_type": "playlist", "entries": _gen()}
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/playlist")
    assert out == ["https://example.com/v1", "https://example.com/v2"]


def test_expand_playlist_nested_playlist_entries_not_recursed() -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {
                "_type": "playlist",
                "id": "inner-list",
                "url": "https://example.com/inner-list",
            },
            {"id": "v2", "url": "https://example.com/v2"},
        ],
    }
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/outer")
    assert out == ["https://example.com/inner-list", "https://example.com/v2"]


def test_expand_playlist_url_field_fallback() -> None:
    info = {"_type": "playlist", "entries": [{"url": "https://example.com/only-url"}]}
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/p")
    assert out == ["https://example.com/only-url"]


def test_expand_playlist_webpage_url_fallback() -> None:
    info = {
        "_type": "playlist",
        "entries": [{"webpage_url": "https://example.com/only-webpage"}],
    }
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/p")
    assert out == ["https://example.com/only-webpage"]


def test_expand_playlist_id_reconstruction_fallback() -> None:
    info = {"_type": "playlist", "entries": [{"id": "AbCdEfG"}]}
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://www.youtube.com/playlist?list=PLx")
    assert out == ["https://www.youtube.com/watch?v=AbCdEfG"]


def test_expand_playlist_entry_with_no_resolvable_url_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "v1", "url": "https://example.com/v1"},
            {"title": "no-id-no-url-no-webpage"},
            {"id": "v3", "url": "https://example.com/v3"},
        ],
    }
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with (
        caplog.at_level(logging.WARNING, logger="omniscribe.acquire.playlist"),
        patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls),
    ):
        out = expand_playlist("https://example.com/p")
    assert out == ["https://example.com/v1", "https://example.com/v3"]
    assert any("no resolvable URL" in r.message for r in caplog.records)


def test_expand_playlist_extraction_failure_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from yt_dlp.utils import DownloadError

    ydl_cls = _make_ydl_mock(extract_info_raises=DownloadError("boom"))
    with (
        caplog.at_level(logging.WARNING, logger="omniscribe.acquire.playlist"),
        patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls),
    ):
        out = expand_playlist("https://example.com/p")
    assert out is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_expand_playlist_network_failure_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ydl_cls = _make_ydl_mock(extract_info_raises=OSError("network unreachable"))
    with (
        caplog.at_level(logging.WARNING, logger="omniscribe.acquire.playlist"),
        patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls),
    ):
        out = expand_playlist("https://example.com/p")
    assert out is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_expand_playlist_preserves_feed_order() -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "first", "url": "https://example.com/first"},
            {"id": "second", "url": "https://example.com/second"},
            {"id": "third", "url": "https://example.com/third"},
        ],
    }
    ydl_cls = _make_ydl_mock(extract_info_return=info)
    with patch("omniscribe.acquire.playlist.YoutubeDL", ydl_cls):
        out = expand_playlist("https://example.com/p")
    assert out == [
        "https://example.com/first",
        "https://example.com/second",
        "https://example.com/third",
    ]


def test_expand_url_list_splices_in_place() -> None:
    def _fake_expand(url: str) -> list[str] | None:
        if url == "https://playlist/p":
            return ["https://example.com/v1", "https://example.com/v2"]
        return None

    with patch("omniscribe.batch.expand_playlist", side_effect=_fake_expand):
        out = expand_url_list(
            ["https://example.com/A", "https://playlist/p", "https://example.com/B"]
        )
    assert out == [
        "https://example.com/A",
        "https://example.com/v1",
        "https://example.com/v2",
        "https://example.com/B",
    ]


def test_expand_url_list_passes_through_singles() -> None:
    inputs = ["https://a/1", "https://a/2", "/local/file.mp4"]
    with patch("omniscribe.batch.expand_playlist", return_value=None):
        out = expand_url_list(inputs)
    assert out == inputs


def test_expand_url_list_handles_empty_list() -> None:
    with patch("omniscribe.batch.expand_playlist", return_value=None):
        out = expand_url_list([])
    assert out == []


def test_expand_url_list_handles_extraction_errors_gracefully() -> None:
    def _fake_expand(url: str) -> list[str] | None:
        return None

    with patch("omniscribe.batch.expand_playlist", side_effect=_fake_expand):
        out = expand_url_list(["https://a/1", "https://fail-playlist/p", "https://a/2"])
    assert out == ["https://a/1", "https://fail-playlist/p", "https://a/2"]

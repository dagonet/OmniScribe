"""Unit tests for omniscribe.acquire.photo -- all subprocess/network boundaries mocked."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from omniscribe.acquire.photo import (
    _run_gallery_dl,
    download_photo_post,
    is_photo_post,
    scan_photo_dir,
)
from omniscribe.errors import OmniScribeError

# -- is_photo_post ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.tiktok.com/@user/photo/1234567890", True),
        ("https://vm.tiktok.com/photo/abc123/", True),
        ("https://www.tiktok.com/@user/video/1234567890", False),
        ("https://www.instagram.com/p/ABC123/", False),
        ("/some/local/file.mp4", False),
        ("https://www.tiktok.com/@user/photo/", True),
    ],
)
def test_is_photo_post_matrix(url: str, expected: bool) -> None:
    assert is_photo_post(url) is expected


# -- download_photo_post ------------------------------------------------------


def test_download_photo_post_missing_gallery_dl(tmp_path: Path) -> None:
    """Both module and script invocations fail -> OmniScribeError with hint."""
    with (
        patch("omniscribe.acquire.photo.subprocess.run", side_effect=FileNotFoundError),
        patch("omniscribe.acquire.photo.shutil.which", return_value=None),
        pytest.raises(OmniScribeError, match="photo"),
    ):
        download_photo_post("https://www.tiktok.com/@u/photo/1", tmp_path)


def test_download_photo_post_scans_recursively(tmp_path: Path) -> None:
    """gallery-dl creates nested extractor dirs; scan must find images recursively."""
    # Build nested structure: slides/tiktok/user_123/
    nested = tmp_path / "slides" / "tiktok" / "user_123"
    nested.mkdir(parents=True)

    (nested / "x_01.jpg").write_bytes(b"img1")
    (nested / "x_02.jpg").write_bytes(b"img2")
    (nested / "audio.mp3").write_bytes(b"audio")
    (nested / "meta.json").write_bytes(b"{}")

    with patch("omniscribe.acquire.photo._run_gallery_dl", return_value=None):
        post = download_photo_post("https://www.tiktok.com/@u/photo/1", tmp_path)

    assert len(post.image_paths) == 2
    assert post.image_paths[0].name == "x_01.jpg"
    assert post.image_paths[1].name == "x_02.jpg"
    assert post.audio_path is not None
    assert post.audio_path.name == "audio.mp3"


def test_download_photo_post_no_images_raises(tmp_path: Path) -> None:
    """Subprocess succeeds but empty dir -> OmniScribeError."""
    slides = tmp_path / "slides"
    slides.mkdir(parents=True)

    with (
        patch("omniscribe.acquire.photo._run_gallery_dl", return_value=None),
        pytest.raises(OmniScribeError, match="no slides downloaded"),
    ):
        download_photo_post("https://www.tiktok.com/@u/photo/1", tmp_path)


# -- _run_gallery_dl -----------------------------------------------------------


def test_run_gallery_dl_module_success(tmp_path: Path) -> None:
    """Module invocation succeeds -> return (no error)."""
    mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
    with patch("omniscribe.acquire.photo.subprocess.run", return_value=mock_proc):
        _run_gallery_dl(tmp_path, "https://x.com/photo/1")


def test_run_gallery_dl_module_not_found_raises(tmp_path: Path) -> None:
    """Module fails with 'No module named' in stderr -> OmniScribeError."""
    mock_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=b"", stderr=b"No module named 'gallery_dl'"
    )
    with (
        patch("omniscribe.acquire.photo.subprocess.run", return_value=mock_proc),
        pytest.raises(OmniScribeError, match="uv sync --extra photo"),
    ):
        _run_gallery_dl(tmp_path, "https://x.com/photo/1")


def test_run_gallery_dl_binary_fallback_success(tmp_path: Path) -> None:
    """Module raises FileNotFoundError, binary fallback succeeds -> return."""
    mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
    with (
        patch(
            "omniscribe.acquire.photo.subprocess.run",
            side_effect=[FileNotFoundError, mock_proc],
        ),
        patch("omniscribe.acquire.photo.shutil.which", return_value="/usr/bin/gallery-dl"),
    ):
        _run_gallery_dl(tmp_path, "https://x.com/photo/1")


def test_run_gallery_dl_binary_fallback_not_found_raises(tmp_path: Path) -> None:
    """Both module and binary raise FileNotFoundError -> OmniScribeError."""
    with (
        patch(
            "omniscribe.acquire.photo.subprocess.run",
            side_effect=[FileNotFoundError, FileNotFoundError],
        ),
        patch("omniscribe.acquire.photo.shutil.which", return_value="/usr/bin/gallery-dl"),
        pytest.raises(OmniScribeError, match="gallery-dl failed"),
    ):
        _run_gallery_dl(tmp_path, "https://x.com/photo/1")


# -- scan_photo_dir -----------------------------------------------------------


def test_scan_photo_dir_with_audio(tmp_path: Path) -> None:
    (tmp_path / "slide1.jpg").write_bytes(b"a")
    (tmp_path / "slide2.jpg").write_bytes(b"b")
    (tmp_path / "audio.mp3").write_bytes(b"c")
    (tmp_path / "notes.txt").write_bytes(b"ignore")

    post = scan_photo_dir(tmp_path)

    assert len(post.image_paths) == 2
    assert post.image_paths[0].name == "slide1.jpg"
    assert post.image_paths[1].name == "slide2.jpg"
    assert post.audio_path is not None
    assert post.audio_path.name == "audio.mp3"


def test_scan_photo_dir_no_audio(tmp_path: Path) -> None:
    (tmp_path / "slide1.jpg").write_bytes(b"a")
    (tmp_path / "slide2.png").write_bytes(b"b")

    post = scan_photo_dir(tmp_path)

    assert len(post.image_paths) == 2
    assert post.audio_path is None


def test_scan_photo_dir_no_images_raises(tmp_path: Path) -> None:
    with pytest.raises(OmniScribeError, match="no image files found"):
        scan_photo_dir(tmp_path)

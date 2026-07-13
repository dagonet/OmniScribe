"""Photo-post acquisition -- gallery-dl download of image slideshows + audio.

TikTok ``/photo/`` posts are image slideshows with optional audio. yt-dlp cannot
download them; gallery-dl can. This module wraps gallery-dl for that purpose and
provides local-directory scanning as an alternative input path.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_AUDIO_EXTS = frozenset({".mp3", ".m4a", ".aac", ".wav"})


def is_photo_post(source: str) -> bool:
    """Return True if *source* looks like a TikTok photo-post URL.

    Checks for ``/photo/`` in the URL path. Must be False for ``/video/`` URLs,
    other platforms, and local file paths.
    """
    lower = source.lower()
    if "tiktok.com" not in lower:
        return False
    return "/photo/" in lower


@dataclass(frozen=True)
class PhotoPost:
    """Represents a downloaded photo post.

    Attributes
    ----------
    image_paths:
        Slide image file paths, sorted in presentation order (gallery-dl
        zero-pads slide numbers, so lexical sort is correct).
    audio_path:
        Optional audio track (.mp3, .m4a, .aac, or .wav). ``None`` when the
        post has no audio.
    """

    image_paths: tuple[Path, ...]
    audio_path: Path | None


def _run_gallery_dl(dest: Path, source: str) -> None:
    """Run gallery-dl as a subprocess, trying module invocation first.

    Tries ``python -m gallery_dl`` first, then falls back to ``gallery-dl``
    script. Raises :class:`OmniScribeError` with an install hint if both fail.
    """
    # Try module invocation first.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "-D", str(dest), source],
            capture_output=True,
            timeout=300,
        )
        if result.returncode == 0:
            return
        # Check module-specific failure immediately (result still bound).
        stderr = result.stderr.decode(errors="replace") if result.stderr else ""
        if "No module named" in stderr or "ModuleNotFoundError" in stderr:
            raise OmniScribeError(
                "photo posts need the optional extra - "
                "`uv sync --extra photo` (or `pip install omniscribe[photo]`)"
            )
    except FileNotFoundError:
        pass

    # Fall back to script name.
    gallery_dl_path = shutil.which("gallery-dl")
    if gallery_dl_path is not None:
        try:
            result = subprocess.run(
                [gallery_dl_path, "-D", str(dest), source],
                capture_output=True,
                timeout=300,
            )
            if result.returncode == 0:
                return
        except FileNotFoundError:
            pass

    hint = (
        "photo posts need the optional extra - "
        "`uv sync --extra photo` (or `pip install omniscribe[photo]`)"
    )
    raise OmniScribeError(f"gallery-dl failed - {hint}")


def download_photo_post(source: str, temp_dir: Path) -> PhotoPost:
    """Download a TikTok photo-post from *source* into *temp_dir*.

    gallery-dl may create extractor subdirectories under the base dir, so the
    result is scanned recursively.

    Parameters
    ----------
    source:
        TikTok ``/photo/`` URL.
    temp_dir:
        Parent directory for downloaded files. A ``slides/`` subdirectory is
        created inside it.

    Returns
    -------
    PhotoPost
        Sorted image paths (slide order) and optional audio file.
    """
    dest = temp_dir / "slides"
    dest.mkdir(parents=True, exist_ok=True)
    _run_gallery_dl(dest, source)

    images = sorted(p for p in dest.rglob("*") if p.suffix.lower() in _IMAGE_EXTS)
    if not images:
        raise OmniScribeError("no slides downloaded - is this a photo post?")

    audio: Path | None = None
    for p in dest.rglob("*"):
        if p.suffix.lower() in _AUDIO_EXTS:
            audio = p
            break

    return PhotoPost(image_paths=tuple(images), audio_path=audio)


def scan_photo_dir(directory: Path) -> PhotoPost:
    """Scan a user-provided directory for photo-post content.

    Scans the top level of *directory* only (non-recursive) for images and
    optional audio, returning a :class:`PhotoPost` in image-sort order.

    Parameters
    ----------
    directory:
        Local directory containing slide images and optionally an audio file.

    Returns
    -------
    PhotoPost
        Sorted image paths and optional audio file.
    """
    images = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )
    if not images:
        raise OmniScribeError(f"no image files found in {directory}")

    audio: Path | None = None
    for p in directory.iterdir():
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS:
            audio = p
            break

    return PhotoPost(image_paths=tuple(images), audio_path=audio)

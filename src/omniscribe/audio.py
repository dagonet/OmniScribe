"""Audio extraction via system ffmpeg (subprocess, list form, shell=False)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import TYPE_CHECKING

from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level pre-check. Deliberately does NOT raise at import time so that
# ``--help`` / ``--version`` still work on systems without ffmpeg. The guard
# is enforced inside :func:`extract_audio`.
_FFMPEG: str | None = shutil.which("ffmpeg")


def extract_audio(video: Path, out: Path) -> Path:
    """Extract 16 kHz mono WAV from ``video`` to ``out`` via ffmpeg.

    Raises :class:`OmniScribeError` if ffmpeg is missing on PATH or if the
    ffmpeg subprocess returns a non-zero exit code.
    """
    if _FFMPEG is None:
        raise OmniScribeError("ffmpeg not found on PATH — install it and retry")

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG,
        "-i",
        str(video),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-vn",
        "-f",
        "wav",
        str(out),
        "-y",
    ]
    logger.info("Extracting audio: %s -> %s", video, out)
    try:
        subprocess.run(cmd, check=True, capture_output=True, shell=False)
    except subprocess.CalledProcessError as e:
        if e.stderr:
            detail = e.stderr.decode(errors="replace").splitlines()[-1]
        else:
            detail = f"exit status {e.returncode} with no stderr output"
        raise OmniScribeError(f"ffmpeg failed: {detail}") from None
    return out


def get_duration(path: Path) -> float | None:
    """Return media duration in seconds via ffprobe, or None on failure.

    Used for slide-timestamp spreading in the photo-mode pipeline. Logs a
    warning on any failure (missing ffprobe, parse error, etc.) and returns
    None so callers can fall back to index-based timestamps.
    """
    ffprobe_path: str | None = shutil.which("ffprobe")
    if ffprobe_path is None:
        logger.warning("ffprobe not found on PATH -- cannot determine duration")
        return None

    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe returned non-zero exit status %d", result.returncode)
            return None
        raw = result.stdout.decode(errors="replace").strip()
        if not raw:
            logger.warning("ffprobe produced empty output for %s", path)
            return None
        return float(raw)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None

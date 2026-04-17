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
        last_line = e.stderr.decode(errors="replace").splitlines()[-1] if e.stderr else str(e)
        raise OmniScribeError(f"ffmpeg failed: {last_line}") from None
    return out

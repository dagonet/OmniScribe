"""Video acquisition — local passthrough or yt-dlp download."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from omniscribe.errors import OmniScribeError

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def download_video(source: str, temp_dir: Path) -> Path:
    """Return a local path to the video referenced by ``source``.

    If ``source`` is an existing local file, it is returned unchanged.
    If it is an http(s) URL, yt-dlp downloads it into ``temp_dir`` and the
    resulting path is returned. Any yt-dlp failure — as well as an unrecognised
    source string — is raised as :class:`OmniScribeError` with a single-line
    message (no traceback chain) so the CLI can surface it cleanly.
    """
    if Path(source).is_file():
        logger.debug("Using local file passthrough: %s", source)
        return Path(source)

    if _URL_RE.match(source):
        temp_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts = {
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source, download=True)
                return Path(ydl.prepare_filename(info))
        except DownloadError as e:
            raise OmniScribeError(f"Download failed: {e}") from None

    raise OmniScribeError(f"Invalid source: {source!r} is neither a file nor an http(s) URL")

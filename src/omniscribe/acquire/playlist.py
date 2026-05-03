"""Playlist / channel URL expansion via yt-dlp metadata-only extraction.

Sprint 8.1: feeds the per-line URL list of ``transcribe-many`` through this
module before reconcile so that a single playlist URL becomes the list of its
constituent video URLs, in feed order.

The single public entry point :func:`expand_playlist` returns:

- ``list[str]``: one URL per video for a recognised playlist / channel,
  in yt-dlp's natural feed order.
- ``None``: input is a single-video URL, a local file path / non-URL string,
  or any extraction error (DownloadError, ExtractorError, network /
  SSL-level failure, malformed response). Callers treat ``None`` as
  "not a playlist — keep the original line".
"""

from __future__ import annotations

import logging

from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)

_YDL_OPTS: dict[str, object] = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}


def _entry_to_url(entry: dict) -> str | None:
    """Resolve one playlist entry to a URL via the locked fallback chain.

    Order: ``entry["url"]`` → ``entry["webpage_url"]`` → reconstructed
    ``https://www.youtube.com/watch?v={id}`` if ``entry["id"]`` is present.
    Returns ``None`` when all three are falsy / missing.
    """
    return (
        entry.get("url")
        or entry.get("webpage_url")
        or (f"https://www.youtube.com/watch?v={entry['id']}" if entry.get("id") else None)
    )


def expand_playlist(url: str) -> list[str] | None:
    """Return expanded video URLs for a playlist/channel URL, else ``None``.

    Behaviour:

    - Local-file / non-URL detection is scheme-based: any ``url`` that does
      not start with ``http://`` or ``https://`` returns ``None`` without
      touching yt-dlp (deterministic on Windows backslash paths and on
      Linux absolute paths that may or may not exist).
    - For http(s) URLs, runs ``YoutubeDL.extract_info`` with ``extract_flat``
      so only metadata is fetched (no video download).
    - If the response's ``_type`` is not exactly ``"playlist"``, returns
      ``None`` — single-video URLs flow through the per-video pipeline
      unchanged.
    - Entries are coerced via ``list(info.get("entries") or [])`` to handle
      yt-dlp's ``LazyList`` return shape.
    - Each entry is resolved via :func:`_entry_to_url`. Entries without any
      resolvable URL are dropped + logged at WARNING.
    - Empty playlists return ``[]`` (caller will splice nothing in place).
    - Any exception during extraction → WARNING log + ``None``. Returning
      ``None`` rather than raising lets the caller treat extraction failures
      identically to "not a playlist": the original URL passes through to
      the per-video pipeline, which surfaces the actual extractor error.
    - One level of expansion only: nested ``_type == "playlist"`` entries are
      treated as opaque single URLs (no recursion).
    """
    if not url.startswith(("http://", "https://")):
        return None

    try:
        with YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except Exception as e:
        # yt-dlp raises a wide hierarchy (DownloadError, ExtractorError, ...)
        # plus network/SSL-level errors; treat all as "not a playlist".
        logger.warning("Playlist expansion failed for %s (%s: %s)", url, type(e).__name__, e)
        return None

    if not isinstance(info, dict):
        return None

    if info.get("_type") != "playlist":
        return None

    entries = list(info.get("entries") or [])

    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping playlist entry with no resolvable URL: %r", entry)
            continue
        resolved = _entry_to_url(entry)
        if not resolved:
            logger.warning("Skipping playlist entry with no resolvable URL: %r", entry)
            continue
        out.append(resolved)

    logger.debug("Expanded %s to %d video URL(s)", url, len(out))
    return out

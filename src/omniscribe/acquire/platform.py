"""Platform detection from source URLs."""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    """Known source platforms for video acquisition."""

    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    UNKNOWN = "unknown"


def detect_platform(source: str) -> Platform:
    """Infer the source platform from a URL or file path.

    Returns :class:`Platform.UNKNOWN` for local files and unrecognized URLs.
    """
    s = source.lower()
    if "tiktok.com" in s:
        return Platform.TIKTOK
    if "youtube.com" in s or "youtu.be" in s:
        return Platform.YOUTUBE
    if "instagram.com" in s:
        return Platform.INSTAGRAM
    return Platform.UNKNOWN

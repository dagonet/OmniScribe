"""Platform profile definitions for per-platform UI filtering."""

from omniscribe.platforms.base import GENERIC_PROFILE, PlatformProfile, RelativeRect
from omniscribe.platforms.instagram import INSTAGRAM_PROFILE
from omniscribe.platforms.registry import PROFILES, get_profile, resolve_profile
from omniscribe.platforms.tiktok import TIKTOK_PROFILE
from omniscribe.platforms.youtube import YOUTUBE_PROFILE

__all__ = [
    "GENERIC_PROFILE",
    "INSTAGRAM_PROFILE",
    "PROFILES",
    "TIKTOK_PROFILE",
    "YOUTUBE_PROFILE",
    "PlatformProfile",
    "RelativeRect",
    "get_profile",
    "resolve_profile",
]

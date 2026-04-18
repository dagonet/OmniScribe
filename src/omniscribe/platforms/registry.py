"""Platform profile registry and resolution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from omniscribe.acquire.platform import Platform, detect_platform
from omniscribe.platforms.base import GENERIC_PROFILE, PlatformProfile
from omniscribe.platforms.instagram import INSTAGRAM_PROFILE
from omniscribe.platforms.tiktok import TIKTOK_PROFILE
from omniscribe.platforms.youtube import YOUTUBE_PROFILE

if TYPE_CHECKING:
    from omniscribe.config import OmniScribeConfig

PROFILES: dict[Platform, PlatformProfile] = {
    Platform.TIKTOK: TIKTOK_PROFILE,
    Platform.YOUTUBE: YOUTUBE_PROFILE,
    Platform.INSTAGRAM: INSTAGRAM_PROFILE,
    Platform.UNKNOWN: GENERIC_PROFILE,
    Platform.GENERIC: GENERIC_PROFILE,
}


def get_profile(platform: Platform) -> PlatformProfile:
    """Return the :class:`PlatformProfile` registered for ``platform``."""
    return PROFILES[platform]


def resolve_profile(config: OmniScribeConfig, source: str) -> PlatformProfile:
    """Pick the active profile based on config + source URL.

    ``config.platform_profile == "auto"`` triggers URL-based detection
    via :func:`detect_platform`; any other value is an explicit override
    and is dispatched uniformly through the ``Platform`` enum.
    """
    if config.platform_profile == "auto":
        return get_profile(detect_platform(source))
    return get_profile(Platform(config.platform_profile))

"""Tests for ``omniscribe.platforms.registry``.

Covers the ``PROFILES`` mapping, ``get_profile`` lookup, ``resolve_profile``
dispatch (auto-detect vs. explicit override), and the config validator that
guards ``platform_profile`` at construction time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omniscribe.acquire.platform import Platform
from omniscribe.config import OmniScribeConfig
from omniscribe.platforms import (
    GENERIC_PROFILE,
    INSTAGRAM_PROFILE,
    TIKTOK_PROFILE,
    YOUTUBE_PROFILE,
    get_profile,
    resolve_profile,
)


def _config(**overrides: object) -> OmniScribeConfig:
    """Construct a config with the given overrides via model_copy.

    Bypasses env/``.env`` interference so tests don't depend on the host
    environment. Avoids ``monkeypatch`` overhead for unit-level registry
    tests that don't hit the CLI.
    """
    base = OmniScribeConfig()
    return base.model_copy(update=overrides)


class TestGetProfile:
    """Direct enum → profile lookup."""

    def test_tiktok_returns_tiktok_profile(self) -> None:
        assert get_profile(Platform.TIKTOK) is TIKTOK_PROFILE

    def test_youtube_returns_youtube_profile(self) -> None:
        assert get_profile(Platform.YOUTUBE) is YOUTUBE_PROFILE

    def test_instagram_returns_instagram_profile(self) -> None:
        assert get_profile(Platform.INSTAGRAM) is INSTAGRAM_PROFILE

    def test_unknown_falls_back_to_generic(self) -> None:
        assert get_profile(Platform.UNKNOWN) is GENERIC_PROFILE

    def test_generic_enum_member_maps_to_generic(self) -> None:
        assert get_profile(Platform.GENERIC) is GENERIC_PROFILE


class TestResolveProfile:
    """Config + source URL dispatch to a concrete profile."""

    def test_auto_detects_tiktok_from_url(self) -> None:
        cfg = _config(platform_profile="auto")
        assert resolve_profile(cfg, "https://www.tiktok.com/@user/video/123") is TIKTOK_PROFILE

    def test_auto_detects_youtube_from_url(self) -> None:
        cfg = _config(platform_profile="auto")
        assert resolve_profile(cfg, "https://youtube.com/shorts/abc") is YOUTUBE_PROFILE

    def test_auto_detects_instagram_from_url(self) -> None:
        cfg = _config(platform_profile="auto")
        assert resolve_profile(cfg, "https://www.instagram.com/reel/xyz") is INSTAGRAM_PROFILE

    def test_auto_unknown_source_falls_back_to_generic(self) -> None:
        cfg = _config(platform_profile="auto")
        assert resolve_profile(cfg, "./local.mp4") is GENERIC_PROFILE

    def test_explicit_override_beats_url_detection(self) -> None:
        cfg = _config(platform_profile="youtube")
        assert resolve_profile(cfg, "https://www.tiktok.com/@x/video/1") is YOUTUBE_PROFILE

    def test_explicit_generic_string_maps_to_generic_profile(self) -> None:
        cfg = _config(platform_profile="generic")
        assert resolve_profile(cfg, "https://www.tiktok.com/@x/video/1") is GENERIC_PROFILE


class TestConfigValidator:
    """``platform_profile`` whitelist enforcement at construction time."""

    @pytest.mark.parametrize(
        "value",
        ["auto", "tiktok", "youtube", "instagram", "unknown", "generic"],
    )
    def test_accepts_valid_values(self, value: str) -> None:
        cfg = OmniScribeConfig(platform_profile=value)
        assert cfg.platform_profile == value

    def test_rejects_unknown_string(self) -> None:
        with pytest.raises(ValidationError):
            OmniScribeConfig(platform_profile="bogus")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            OmniScribeConfig(platform_profile="")

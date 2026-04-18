"""Tests for ``omniscribe.platforms`` dataclasses and built-in profiles."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from omniscribe.platforms import (
    GENERIC_PROFILE,
    TIKTOK_PROFILE,
    PlatformProfile,
    RelativeRect,
)


class TestRelativeRect:
    """Validation rules for normalized rectangle coordinates."""

    @pytest.mark.parametrize(
        ("x", "y", "w", "h"),
        [
            (-0.1, 0.0, 0.5, 0.5),
            (1.5, 0.0, 0.1, 0.1),
            (0.0, 0.0, 0.0, 0.5),
            (0.0, 0.0, -0.1, 0.5),
            (0.6, 0.0, 0.5, 0.5),  # x + w > 1
            (0.0, 0.6, 0.5, 0.5),  # y + h > 1
        ],
    )
    def test_invalid_coordinates_rejected(self, x: float, y: float, w: float, h: float) -> None:
        with pytest.raises(ValueError):
            RelativeRect(x=x, y=y, w=w, h=h)

    def test_valid_rect_constructs(self) -> None:
        rect = RelativeRect(x=0.1, y=0.2, w=0.3, h=0.4)
        assert rect.x == pytest.approx(0.1)
        assert rect.h == pytest.approx(0.4)

    def test_edge_full_extent_accepted(self) -> None:
        RelativeRect(x=0.0, y=0.0, w=1.0, h=1.0)


class TestPlatformProfile:
    """Frozen dataclass contract + built-in profile shape."""

    def test_profile_is_hashable(self) -> None:
        assert isinstance(hash(TIKTOK_PROFILE), int)

    def test_profile_is_frozen(self) -> None:
        with pytest.raises(FrozenInstanceError):
            TIKTOK_PROFILE.name = "mutated"  # type: ignore[misc]

    def test_tiktok_handle_pattern(self) -> None:
        handle_pat = TIKTOK_PROFILE.ui_text_patterns[0]
        assert handle_pat.match("@some.user") is not None
        assert handle_pat.match("some.user") is None
        assert handle_pat.match("@") is None

    @pytest.mark.parametrize(
        ("text", "should_match"),
        [
            ("12.3K", True),
            ("456", True),
            ("1M", True),
            ("7k", True),
            ("hello", False),
            ("", False),
            ("1.2.3K", False),
        ],
    )
    def test_tiktok_count_pattern(self, text: str, should_match: bool) -> None:
        count_pat = TIKTOK_PROFILE.ui_text_patterns[1]
        assert (count_pat.match(text) is not None) is should_match

    def test_generic_profile_is_empty(self) -> None:
        assert GENERIC_PROFILE.name == "generic"
        assert GENERIC_PROFILE.ui_exclusion_zones == ()
        assert GENERIC_PROFILE.ui_text_patterns == ()
        assert GENERIC_PROFILE.frequency_threshold == pytest.approx(0.95)

    def test_default_profile_uses_tuples(self) -> None:
        profile = PlatformProfile(name="test")
        assert isinstance(profile.ui_exclusion_zones, tuple)
        assert isinstance(profile.ui_text_patterns, tuple)

"""Core platform profile dataclasses.

``PlatformProfile`` encapsulates the per-platform UI filtering hints used
by the OCR pipeline (Sprint 3.2 and later). Profiles are frozen
dataclasses with tuple-valued collections so they are hashable and safe
to share across threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import re


@dataclass(frozen=True)
class RelativeRect:
    """A rectangle expressed in normalized [0.0, 1.0] frame coordinates.

    Origin is top-left. Coordinates and extents must satisfy
    ``0 <= x, y``, ``0 < w, h``, ``x + w <= 1`` and ``y + h <= 1``.
    """

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        for name, value in (("x", self.x), ("y", self.y)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0.0, 1.0]; got {value!r}")
        for name, value in (("w", self.w), ("h", self.h)):
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0.0, 1.0]; got {value!r}")
        if self.x + self.w > 1.0 + 1e-9:
            raise ValueError(f"x + w must be <= 1.0; got {self.x} + {self.w}")
        if self.y + self.h > 1.0 + 1e-9:
            raise ValueError(f"y + h must be <= 1.0; got {self.y} + {self.h}")


@dataclass(frozen=True)
class PlatformProfile:
    """UI-filtering profile for a single source platform.

    Attributes
    ----------
    name:
        Human-readable platform name (matches ``Platform`` enum value).
    ui_exclusion_zones:
        Rectangles in relative coordinates where OCR detections should
        be suppressed (e.g. right-sidebar action buttons).
    ui_text_patterns:
        Compiled regexes matching strings that are always UI chrome
        (e.g. handle mentions, follower counts).
    frequency_threshold:
        Fraction (0.0, 1.0] of sampled frames a text must appear in
        before it is treated as persistent UI. Default 0.95 keeps
        legitimate multi-second title cards.
    """

    name: str
    ui_exclusion_zones: tuple[RelativeRect, ...] = ()
    ui_text_patterns: tuple[re.Pattern[str], ...] = ()
    frequency_threshold: float = 0.95


GENERIC_PROFILE = PlatformProfile(name="generic")

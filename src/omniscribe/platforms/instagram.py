"""Instagram Reels platform profile.

Right-side rail carries like / comment / share / save / remix icons;
the bottom band exposes the caption and the audio-attribution label.
The top strip is the Reels logo + camera-flip control.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

INSTAGRAM_PROFILE = PlatformProfile(
    name="instagram",
    ui_exclusion_zones=(
        RelativeRect(x=0.86, y=0.20, w=0.14, h=0.70),
        RelativeRect(x=0.0, y=0.85, w=1.0, h=0.15),
        RelativeRect(x=0.0, y=0.0, w=1.0, h=0.06),
    ),
    ui_text_patterns=(
        re.compile(r"^Original audio\b.*", re.IGNORECASE),
        re.compile(r".*\u00b7 Reel$"),
        re.compile(r"^@[\w.\-]+$"),
    ),
)

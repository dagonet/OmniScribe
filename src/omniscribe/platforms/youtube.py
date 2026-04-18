"""YouTube Shorts platform profile.

Shorts UI concentrates action controls on the right edge and an
overlayed Subscribe button with channel handle near the bottom-left.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

YOUTUBE_PROFILE = PlatformProfile(
    name="youtube",
    ui_exclusion_zones=(
        RelativeRect(x=0.88, y=0.10, w=0.12, h=0.80),
        RelativeRect(x=0.0, y=0.88, w=1.0, h=0.12),
        RelativeRect(x=0.0, y=0.0, w=1.0, h=0.05),
    ),
    ui_text_patterns=(
        re.compile(r"^SUBSCRIBE$", re.IGNORECASE),
        re.compile(r"^#shorts$", re.IGNORECASE),
        re.compile(r"^@[\w.\-]+$"),
    ),
)

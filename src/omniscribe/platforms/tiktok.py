"""TikTok platform profile.

Exclusion zones cover the right-side action strip (like/comment/share/
bookmark/avatar), the bottom caption + music bar, and the top search /
"Following | For You" header. Patterns match handles, engagement
counts, and the music-note attribution glyph.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

TIKTOK_PROFILE = PlatformProfile(
    name="tiktok",
    ui_exclusion_zones=(
        RelativeRect(x=0.85, y=0.0, w=0.15, h=1.0),
        RelativeRect(x=0.0, y=0.88, w=1.0, h=0.12),
        RelativeRect(x=0.0, y=0.0, w=1.0, h=0.05),
    ),
    ui_text_patterns=(
        re.compile(r"^@[\w.]+$"),
        re.compile(r"^\d+(\.\d+)?[KkMm]?$"),
        re.compile(r"\u266c.*"),
    ),
)

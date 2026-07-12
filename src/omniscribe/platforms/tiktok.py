"""TikTok platform profile.

Exclusion zones cover the right-side action strip (like/comment/share/
bookmark/avatar), the bottom caption + music bar, and the top search /
"Following | For You" header. Patterns match handles, engagement
counts, and the music-note attribution glyph.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

# Auto-caption band: best-effort default based on platform UI conventions;
# user to refine after manual GPU smoke per
# docs/plans/sprint-7-1-ocr-noise-floor.md. TikTok auto-rolling captions
# render mid-lower screen at roughly y in [0.55, 0.78].
_TIKTOK_AUTO_CAPTION_ZONE = RelativeRect(x=0.05, y=0.55, w=0.90, h=0.23)

TIKTOK_PROFILE = PlatformProfile(
    name="tiktok",
    ui_exclusion_zones=(
        RelativeRect(x=0.85, y=0.0, w=0.15, h=1.0),
        RelativeRect(x=0.0, y=0.88, w=1.0, h=0.12),
        RelativeRect(x=0.0, y=0.0, w=1.0, h=0.05),
    ),
    auto_caption_zones=(_TIKTOK_AUTO_CAPTION_ZONE,),
    ui_text_patterns=(
        re.compile(r"^@[\w.]+$"),
        re.compile(r"^\d+(\.\d+)?[KkMm]?$"),
        re.compile(r"\u266c.*"),
    ),
)

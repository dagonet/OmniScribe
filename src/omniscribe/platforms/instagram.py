"""Instagram Reels platform profile.

Right-side rail carries like / comment / share / save / remix icons;
the bottom band exposes the caption and the audio-attribution label.
The top strip is the Reels logo + camera-flip control.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

# Auto-caption band: best-effort default based on platform UI conventions;
# user to refine after manual GPU smoke per
# docs/plans/sprint-7-1-ocr-noise-floor.md. Instagram Reels auto-captions
# render slightly higher than TikTok's, roughly y in [0.50, 0.75].
# x is capped at 0.86 to avoid layering with the right-rail rect above
# (mask_zones handles overlap fine, but keeping zones disjoint makes the
# intent clearer).
_INSTAGRAM_AUTO_CAPTION_ZONE = RelativeRect(x=0.05, y=0.50, w=0.81, h=0.25)

INSTAGRAM_PROFILE = PlatformProfile(
    name="instagram",
    ui_exclusion_zones=(
        RelativeRect(x=0.86, y=0.20, w=0.14, h=0.70),
        RelativeRect(x=0.0, y=0.85, w=1.0, h=0.15),
        RelativeRect(x=0.0, y=0.0, w=1.0, h=0.06),
    ),
    auto_caption_zones=(_INSTAGRAM_AUTO_CAPTION_ZONE,),
    ui_text_patterns=(
        re.compile(r"^Original audio\b.*", re.IGNORECASE),
        re.compile(r".*\u00b7 Reel$"),
        re.compile(r"^@[\w.\-]+$"),
    ),
)

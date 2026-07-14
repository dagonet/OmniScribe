# Adding a New Platform Profile

Guide to adding support for a new video-sharing platform (e.g. Snapchat Spotlight,
Twitter/X video, LinkedIn video) to OmniScribe's platform-profile system.

## What a platform profile controls

A `PlatformProfile` (defined in `src/omniscribe/platforms/base.py`) bundles four
kinds of platform-specific hints used by the OCR pipeline:

- **`ui_exclusion_zones`** — rectangular regions in normalised frame coordinates
  where OCR detections are suppressed (e.g. action buttons, side rails, headers).
  These are applied as black masks before the OCR engine runs. A platform with no
  persistent UI chrome can leave this empty.
- **`auto_caption_zones`** — regions where the platform renders its own
  auto-generated captions. When `ocr_mask_auto_captions` is `True` (the default),
  these zones are also masked — the pipeline relies on ASR for speech and should
  not OCR the platform's own caption overlay.
- **`ui_text_patterns`** — compiled regexes that match known UI chrome text
  (handles, follower counts, attribution labels). Segments matching any pattern
  are dropped in `filter_by_patterns`. Small, predictable strings only — broad
  patterns risk false positives.
- **`frequency_threshold`** — fraction of sampled frames (0.0–1.0) above which a
  text is treated as persistent UI chrome and filtered out; `0.95` is the
  default.

Each profile also carries a `name` field that matches the corresponding
`Platform` enum value — used for logging and traceability.

Profiles do not control video acquisition (downloader behaviour) or ASR
parameters — those are platform-agnostic.

## Step-by-step

### 1. Add a `Platform` enum value

`src/omniscribe/acquire/platform.py` defines the `Platform` `StrEnum`:

```python
class Platform(StrEnum):
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    UNKNOWN = "unknown"
    GENERIC = "generic"
```

Add your new platform. The value is lowercase, matches the platform name used
in config:

```python
    SNAPCHAT = "snapchat"
```

### 2. Add URL detection

The same file's `detect_platform()` function maps URL substrings to platform
enum values:

```python
def detect_platform(source: str) -> Platform:
    s = source.lower()
    if "tiktok.com" in s:
        return Platform.TIKTOK
    if "youtube.com" in s or "youtu.be" in s:
        return Platform.YOUTUBE
    if "instagram.com" in s:
        return Platform.INSTAGRAM
    return Platform.UNKNOWN
```

Add a clause for your platform's URL pattern. Be specific enough to avoid false
matches — `"snapchat.com"` is safe; a generic word may catch unrelated URLs.

```python
    if "snapchat.com" in s:
        return Platform.SNAPCHAT
```

### 3. Create a profile module

Create `src/omniscribe/platforms/snapchat.py` (or whatever the platform is).
Use TikTok's profile as the template:

```python
"""Snapchat Spotlight platform profile.

Spotlight UI places the camera-flip / timer button top-right and the
caption-bar near the bottom. The centre of the screen is content.
"""

from __future__ import annotations

import re

from omniscribe.platforms.base import PlatformProfile, RelativeRect

SNAPCHAT_PROFILE = PlatformProfile(
    name="snapchat",
    ui_exclusion_zones=(
        RelativeRect(x=0.85, y=0.0, w=0.15, h=0.08),   # top-right controls
        RelativeRect(x=0.0, y=0.88, w=1.0, h=0.12),     # bottom caption bar
    ),
    auto_caption_zones=(
        RelativeRect(x=0.05, y=0.55, w=0.90, h=0.23),   # centre-lower auto-captions
    ),
    ui_text_patterns=(
        re.compile(r"^@[\w.]+$"),                       # handle
        re.compile(r"^\d+(\.\d+)?[KkMm]?$"),            # follower count
        re.compile(r"^\d+[sm]"),                        # timer badge
    ),
)
```

The four attributes described in section 1 are the only fields. `name` must
match the `Platform` value. All `RelativeRect` coordinates are normalised to
`[0.0, 1.0]` with origin at top-left.

If you are unsure about zone positions, start with an empty profile (no zones,
no patterns) and iteratively refine by checking which OCR detections are UI
chrome in sample outputs.

#### Determining zone coordinates

A common workflow:

1. Take a screenshot of the platform's video player in your target aspect ratio.
2. Load it in any image editor and note pixel coordinates.
3. Divide each pixel coordinate by the image dimensions to get normalised values.
   For example, a right-side button strip from pixel-x=850 to 1080 on a
   1080 px wide frame becomes `RelativeRect(x=0.787, y=0.0, w=0.213, h=1.0)`.
4. Round to a few decimal places — sub-pixel precision is unnecessary.

### 4. Register in the registry

`src/omniscribe/platforms/registry.py` imports all profile constants and maps
them in the `PROFILES` dict:

```python
from omniscribe.platforms.snapchat import SNAPCHAT_PROFILE

PROFILES: dict[Platform, PlatformProfile] = {
    Platform.TIKTOK: TIKTOK_PROFILE,
    Platform.YOUTUBE: YOUTUBE_PROFILE,
    Platform.INSTAGRAM: INSTAGRAM_PROFILE,
    Platform.SNAPCHAT: SNAPCHAT_PROFILE,
    Platform.UNKNOWN: GENERIC_PROFILE,
    Platform.GENERIC: GENERIC_PROFILE,
}
```

`resolve_profile()` in the same file dispatches automatically — no other wiring
is needed.

### 5. How profiles are consumed (no further changes needed)

Once registered, the profile is picked up in two places automatically:

- **`pipeline.py`** — `resolve_profile(config, source)` is called early in
  `process_single_video()` to obtain the active profile. The profile is passed
  to `RapidOCREngine` (which applies `ui_exclusion_zones` and
  `auto_caption_zones` via `mask_zones`) and later to `filter_by_patterns` and
  `filter_by_frequency` via the `profile.ui_text_patterns` and
  `profile.frequency_threshold` attributes.
- **`ui_filter.py`** — the two filter functions (`filter_by_patterns`,
  `filter_by_frequency`) consume the profile data directly from the
  `PlatformProfile` object; no additional plumbing required.

### 6. (Optional) Update CLI platform choices

The `--platform` CLI option in `cli.py` derives its choices from the `Platform`
enum automatically — adding the new enum value means `--platform snapchat` will
appear in the help without modifying `cli.py`:

```python
_PLATFORM_CHOICES = sorted(({"auto"} | {p.value for p in Platform}) - {"unknown"})
```

### Complete file checklist

| File | Action |
|---|---|
| `src/omniscribe/acquire/platform.py` | Add `Platform` enum member and `detect_platform` clause |
| `src/omniscribe/platforms/snapchat.py` | Create profile module with `PlatformProfile` constant |
| `src/omniscribe/platforms/registry.py` | Import and register the new profile constant |
| `src/omniscribe/platforms/__init__.py` | No change needed (empty init) |

No changes are needed to `pipeline.py`, `cli.py`, `ui_filter.py`,
`rapid_ocr.py`, or any config files.

## Generic fallback

Platforms not registered in the `PROFILES` dict are handled by
`GENERIC_PROFILE` in `src/omniscribe/platforms/base.py`:

```python
GENERIC_PROFILE = PlatformProfile(name="generic")
```

This is an empty profile — no exclusion zones, no auto-caption zones, no text
patterns, default `frequency_threshold` of `0.95`. It is used whenever
`detect_platform()` returns `UNKNOWN` or the config explicitly sets
`platform_profile` to `"generic"`. The generic profile works for any platform
but will not suppress platform-specific UI chrome.

## Testing

Existing tests in `tests/test_platforms.py` cover profile construction, zone
validation, registry lookups, and URL-based detection. Add tests for your new
platform following the existing patterns:

- Test that the `Platform` enum value exists and round-trips via its string
  value.
- Test that `detect_platform` correctly identifies your platform URLs and
  returns `UNKNOWN` for other sources.
- Test that `get_profile(Platform.YOUR_PLATFORM)` returns the profile you
  registered.
- Test that `RelativeRect` values in your profile pass validation (coordinates
  inside `[0.0, 1.0]`, positive width/height).
- Optionally, smoke-test the profile through the full pipeline by running
  `uv run omniscribe transcribe --platform your_platform <video>` and
  inspecting the output for unwanted UI text.

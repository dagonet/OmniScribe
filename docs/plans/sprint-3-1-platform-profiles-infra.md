# Sprint 3.1 — Platform profile package + config validation + CLI flag

**Phase:** 3 (Platform Profiles & UI Filtering)
**Tier:** T3
**Team:** `dev-1` (python-coder), `reviewer-1` (code-reviewer), `tester-1` (tester)
**Branch:** `feature/sprint-3-1-platform-profiles-infra`
**Parent plan:** [phase-3-platform-profiles.md](./phase-3-platform-profiles.md)

## Goal

`omniscribe transcribe <src> --platform <name>` resolves to a `PlatformProfile` instance at runtime, with auto-detection as the default path. Zero behavior change to OCR output yet — 3.1 is plumbing only.

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `src/omniscribe/acquire/platform.py` (edit) | Add `GENERIC = "generic"` enum member after `UNKNOWN`. `detect_platform` is NOT modified — it still returns `TIKTOK / YOUTUBE / INSTAGRAM / UNKNOWN`. `GENERIC` exists only for explicit CLI override (`--platform generic`) and for the registry to map it to `GENERIC_PROFILE`. | +1 |
| `src/omniscribe/platforms/__init__.py` | `"""Platform profile definitions for per-platform UI filtering."""` — docstring only. Mirrors `ocr/__init__.py`. | 1 |
| `src/omniscribe/platforms/base.py` | Two frozen dataclasses: `RelativeRect(x, y, w, h: float)` with each coord in `[0.0, 1.0]` (top-left origin, validated in `__post_init__` which raises `ValueError` on out-of-range coords or `x + w > 1` / `y + h > 1`), and `PlatformProfile(name: str, ui_exclusion_zones: tuple[RelativeRect, ...] = (), ui_text_patterns: tuple[re.Pattern[str], ...] = (), frequency_threshold: float = 0.95)`. **Default 0.95** — on short 1 fps TikTok samples, 0.8 would drop legitimate 12 s title cards. Tuples over lists so profiles are hashable + trivially frozen. Module-level `GENERIC_PROFILE = PlatformProfile(name="generic")`. | ~45 |
| `src/omniscribe/platforms/tiktok.py` | `TIKTOK_PROFILE = PlatformProfile(...)` with:<br>• zones: right-sidebar `RelativeRect(0.85, 0.0, 0.15, 1.0)`, bottom-bar `RelativeRect(0.0, 0.88, 1.0, 0.12)`, top-bar `RelativeRect(0.0, 0.0, 1.0, 0.05)`<br>• patterns: `re.compile(r"^@[\w.]+$")`, `re.compile(r"^\d+(\.\d+)?[KkMm]?$")`, `re.compile(r"♬.*")`<br>• freq threshold: 0.95 (default). | ~20 |
| `src/omniscribe/platforms/youtube.py` | `YOUTUBE_PROFILE` with Shorts-oriented defaults: right-sidebar action strip, bottom subscribe overlay, patterns for `SUBSCRIBE`, `#shorts`, channel-handle formats. Conservative values to avoid false-masking creator content. | ~20 |
| `src/omniscribe/platforms/instagram.py` | `INSTAGRAM_PROFILE` for Reels: right sidebar (like/comment/share/save/remix), bottom caption/audio bar, top Reels-logo strip. Pattern for Reels audio-attribution format. | ~20 |
| `src/omniscribe/platforms/registry.py` | `PROFILES: dict[Platform, PlatformProfile]` — maps `Platform.TIKTOK → TIKTOK_PROFILE`, `Platform.YOUTUBE → YOUTUBE_PROFILE`, `Platform.INSTAGRAM → INSTAGRAM_PROFILE`, `Platform.UNKNOWN → GENERIC_PROFILE`, `Platform.GENERIC → GENERIC_PROFILE`. `def get_profile(platform: Platform) -> PlatformProfile`. `def resolve_profile(config: OmniScribeConfig, source: str) -> PlatformProfile`: if `config.platform_profile == "auto"` → `get_profile(detect_platform(source))`; else `get_profile(Platform(config.platform_profile))`. Uniform one-line dispatch — works for all enum-value strings including `"generic"`. | ~30 |
| `src/omniscribe/config.py` (edit) | Add `ui_filter_enabled: bool = True` immediately after `platform_profile` (line 42). Add `@field_validator("platform_profile", mode="after")` (explicit `mode="after"` — the default, stated for clarity) accepting `{"auto"} \| {p.value for p in Platform}` — computed from enum so adding `Platform.GENERIC` automatically includes it. Raises `ValueError` otherwise. | +14 |
| `.env.example` (edit, conditional) | Read first. If `OMNI_PLATFORM_PROFILE` exists, only add `OMNI_UI_FILTER_ENABLED=true` and update the comment to `# auto \| tiktok \| youtube \| instagram \| generic — "auto" uses URL detection`. If file not accessible, skip and note in PR description. | +1 to +3 |
| `src/omniscribe/cli.py` (edit) | Add `--platform` Typer option with `click.Choice` for early validation:<br>`platform: Optional[str] = typer.Option(None, "--platform", click_type=click.Choice(["auto", "tiktok", "youtube", "instagram", "generic"]), help="Override OMNI_PLATFORM_PROFILE for this run.")` (import `click` at module top if not already). Runtime merge mirrors `--language`: `if platform is not None: config = config.model_copy(update={"platform_profile": platform})`. **3.1 does not wire the profile into OCR yet** — that lands in 3.2. | +7 |
| `tests/test_platform_profiles.py` | Tests:<br>(a) `RelativeRect` rejects x < 0, x > 1, w ≤ 0, x + w > 1;<br>(b) `PlatformProfile` is hashable and frozen;<br>(c) `TIKTOK_PROFILE.ui_text_patterns[0].match("@some.user")` hits; does NOT hit `"some.user"` or `"@"`;<br>(d) TikTok count pattern matches `"12.3K"`, `"456"`, `"1M"`; rejects `"hello"`;<br>(e) `GENERIC_PROFILE` has empty zones + empty patterns + `frequency_threshold == 0.95`. | ~60 |
| `tests/test_platform_registry.py` | Tests:<br>(a) `get_profile(Platform.TIKTOK) is TIKTOK_PROFILE`;<br>(b) `get_profile(Platform.UNKNOWN) is GENERIC_PROFILE`; `get_profile(Platform.GENERIC) is GENERIC_PROFILE`;<br>(c) `resolve_profile(config(platform_profile="auto"), "https://tiktok.com/...") is TIKTOK_PROFILE`;<br>(d) `resolve_profile(config(platform_profile="youtube"), "https://tiktok.com/...") is YOUTUBE_PROFILE` (override wins);<br>(e) `resolve_profile(config(platform_profile="auto"), "./local.mp4") is GENERIC_PROFILE` (UNKNOWN → GENERIC);<br>(f) `resolve_profile(config(platform_profile="generic"), "https://tiktok.com/...") is GENERIC_PROFILE`;<br>(g) validator accepts `"generic"` at construction;<br>(h) invalid `platform_profile` string raises `ValidationError` before `resolve_profile` reached. | ~65 |
| `tests/test_cli.py` (extend) | Four new tests:<br>(a) `--platform tiktok` on non-TikTok source overrides `platform_profile` in merged config;<br>(b) `--platform bogus` exits non-zero, stderr contains `"Invalid value for '--platform'"` (via `CliRunner` — `click.Choice` produces it, NOT pydantic);<br>(c) `OMNI_PLATFORM_PROFILE=bogus` + no flag raises `pydantic.ValidationError` at config construction;<br>(d) `OMNI_PLATFORM_PROFILE=instagram` + no flag → config has `platform_profile == "instagram"`. | ~55 |

## Acceptance criteria

- [ ] `from omniscribe.platforms import TIKTOK_PROFILE, YOUTUBE_PROFILE, INSTAGRAM_PROFILE, GENERIC_PROFILE, PlatformProfile, RelativeRect, get_profile, resolve_profile` succeeds (empty `__init__.py` + full-path imports also acceptable — decide during implementation).
- [ ] `OmniScribeConfig(platform_profile="auto")` valid; `OmniScribeConfig(platform_profile="xyz")` raises `ValidationError`.
- [ ] `omniscribe transcribe <mp4> --platform tiktok --no-ocr` produces byte-identical output to Phase 2's `--no-ocr` run.
- [ ] `omniscribe transcribe <mp4> --platform tiktok --ocr` produces byte-identical output to Phase 2's `--ocr` run — 3.1 does not change OCR behavior yet.
- [ ] All existing Phase 1 + Phase 2 tests green. New tests green. `uv run ruff format --check . && uv run ruff check .` clean.
- [ ] No new runtime deps in `pyproject.toml`.

## Verification

```
uv run ruff format --check . && uv run ruff check .
uv run pytest -q                          # all green, incl. test_platform_profiles + test_platform_registry
uv run omniscribe transcribe --help       # shows --platform
OMNI_PLATFORM_PROFILE=xyz uv run omniscribe transcribe sample.mp4 --ocr
  # → ValidationError from config field_validator, NOT a crash deep in OCR
uv run omniscribe transcribe sample.mp4 --platform tiktok --ocr --output tt.json
  # → byte-identical to Phase 2 `--ocr` output
```

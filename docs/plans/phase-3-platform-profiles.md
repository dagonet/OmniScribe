# OmniScribe — Phase 3: Platform Profiles & UI Filtering

**Tier:** T3 (both sprints)
**Team per sprint:** python-coder, code-reviewer, tester

## Context

Phase 2 (OCR) merged to `main` at `ba8dd20` on 2026-04-17. `omniscribe transcribe <src> --ocr` now emits unified `SPEECH` + `ON-SCREEN` segments via RapidOCR + CLAHE + cross-frame deduplication. However, OCR currently reads the **entire frame** — so TikTok sidebar icons (`❤ 12.3K`, `@username`), YouTube subscribe overlays, and Instagram Reels UI chrome all end up in the transcript as noise.

Phase 3 adds **platform-aware UI filtering** so OCR output contains only creator content, not app chrome. Three filter layers:

1. **Zone masking** (pre-OCR) — fill platform-specific UI rectangles with black on the grayscale frame so RapidOCR never detects text there.
2. **Pattern filtering** (post-OCR) — drop segments matching regex patterns for known UI text (`@handle`, `12.3K`, music-note attribution).
3. **Frequency filtering** (post-OCR) — drop texts that appear in ≥ `frequency_threshold` fraction of sampled frames (watermarks, static overlays).

Per `IMPLEMENTATION_PLAN.md` §Phase 3 and 2026-04-18 plan-mode user decisions: **profiles + UI filter only** (scene-change detection deferred); **two sprints**; **Python dataclasses** for profile definitions (no YAML).

## Pre-existing surface (reused, not rebuilt)

- `src/omniscribe/acquire/platform.py` — `Platform` StrEnum (`TIKTOK | YOUTUBE | INSTAGRAM | UNKNOWN`) + `detect_platform(source: str) -> Platform` already exist from Phase 1. Phase 3 **adds one member** (`GENERIC = "generic"`) to the enum — additive, does not change `detect_platform` behavior.
- `config.py:41` — `platform_profile: str = "auto"` already exists. Phase 3 adds a validator and a sibling `ui_filter_enabled: bool = True`.
- `TranscriptSegment` — no schema change. Zone masking is pre-OCR (no bbox needed); pattern + frequency filters operate on `.text` only.

## Scope pruning (two rounds of challenge applied)

**Round 1** (13 findings applied):
- `frequency_threshold` default 0.8 → **0.95** (0.8 would drop legitimate 12 s title cards on TikTok).
- Added `"generic"` to validator whitelist; documented `resolve_profile` dispatch for it.
- Specified coordinate clamping in `mask_zones`.
- Required module-level `from omniscribe.ocr.ui_filter import mask_zones` at rapid_ocr import site (for mock patching).
- Made `mode="after"` explicit on validator.
- Cut `docs/adding-platforms.md` entirely.
- Downgraded `.env.example` row to conditional.
- Added overlapping-zones / `frame_count=1` / `"generic"` test cases.
- Added backward-compat acceptance criterion for Phase 2 `test_rapid_ocr.py`.
- Updated watermark criterion to ≥ 95 %.

**Round 2** (9 findings applied):
- Collapsed `"generic"` bifurcation via `Platform.GENERIC = "generic"` enum member — eliminated validator + `resolve_profile` special cases.
- `--platform` uses `click.Choice` for pretty `BadParameter` errors.
- `RapidOCREngine.__init__` `profile` parameter made **keyword-only** (`*,`).
- Specified progress-log math: `N = pre_pattern - post_pattern`, `M = post_pattern - post_freq`.
- `RelativeRect.__post_init__` raises `ValueError` explicitly.
- Added module-top imports to cli.py row for mock patching (`resolve_profile`, `filter_by_patterns`, `filter_by_frequency`).
- `filter_by_frequency` case-folds internally (docstring notes divergence from `filter_by_patterns`).
- Reworded watermark acceptance to "synthetic segment-list fixture" (not MP4).

## Tier assignment

- **Sprint 3.1 — T3** (python-coder + code-reviewer + tester). ~170 prod LOC. Pure data structures + one config validator + CLI flag plumbing. No new external deps, no ML engine, no I/O risk.
- **Sprint 3.2 — T3** (python-coder + code-reviewer + tester). ~100 prod LOC. Three pure functions + one OpenCV `cv2.rectangle` call inside `RapidOCREngine.extract()`.

No architect step. This is additive: all existing tests must stay green; no schema changes; no new runtime deps.

Every spawn prompt MUST include a `## Required Skills` block per `AGENT_TEAM.md` Spawn-Prompt Binding Table — `hooks/require-skills-block.sh` enforces exit 2 on missing block.

## Sprints

- **Sprint 3.1** — Platform profile package + config validation + CLI flag (plumbing only). See `sprint-3-1-platform-profiles-infra.md`.
- **Sprint 3.2** — Zone masking + pattern/frequency filters + OCR wire-in. See `sprint-3-2-ui-filter.md`.

## Design decisions locked

- **Python dataclasses, not YAML** — profiles live in `platforms/*.py` as frozen dataclass instances. Regexes compile once at import.
- **Pre-OCR zone masking, not post-OCR bbox filtering** — avoids extending `TranscriptSegment` with a bbox field. One `cv2.rectangle` call.
- **Tuples over lists in `PlatformProfile`** — hashable, frozen-compatible, nudges "profiles are read-only configs".
- **Frequency filter runs on raw pre-dedup segments** — `count / frame_count` is only meaningful before the deduplicator collapses runs.
- **Exact normalized match for frequency, not fuzzy** — fuzzy would re-implement the deduplicator. Frequency asks "is this the same UI chrome in every frame?"; dedup asks "is this the same overlay held for N seconds?".
- **`platform_profile` validator accepts `{"auto", "tiktok", "youtube", "instagram", "generic"}`** — computed from `{"auto"} | {p.value for p in Platform}` so adding `Platform.GENERIC` auto-includes it.
- **`--platform` CLI flag is a per-run override, `--ui-filter/--no-ui-filter` is a safety kill switch** — both `None`-default and merge-on-set, matching `--language` / `--ocr`.
- **`GENERIC_PROFILE` is fallback for both `Platform.UNKNOWN` and `Platform.GENERIC`** — always have a profile reference; no None-check special case.
- **`Platform.GENERIC` is a first-class enum member, not a magic string** — collapses validator + `resolve_profile` special cases into zero special cases. `detect_platform` still never returns `GENERIC`.
- **`RapidOCREngine`'s `profile` parameter is keyword-only** — `def __init__(self, config, *, profile=None)` prevents accidental positional binding.
- **`--platform` uses `click.Choice` for CLI UX** — clean `BadParameter` error for invalid flag values, not a pydantic traceback. Pydantic validator catches env-var bad values.
- **No new runtime deps** — `re`, `dataclasses` are stdlib; `cv2.rectangle` already present.
- **Mock patch sites stay at import site** — `omniscribe.ocr.rapid_ocr.mask_zones`, `omniscribe.ocr.ui_filter.cv2`, `omniscribe.cli.resolve_profile`.
- **No scene-change detection in Phase 3** — confirmed. Potential standalone Phase 2.5.

## Critical files

- `IMPLEMENTATION_PLAN.md` §Phase 3 (lines 210–246) — authoritative scope.
- `src/omniscribe/acquire/platform.py` — existing `Platform` enum + `detect_platform(source)`. Reused in registry, not rewritten.
- `src/omniscribe/config.py:41` — `platform_profile` insertion point for `ui_filter_enabled` + validator.
- `src/omniscribe/ocr/rapid_ocr.py` — `extract()` method; Sprint 3.2 inserts `mask_zones` call between `preprocess` and `engine`.
- `src/omniscribe/ocr/preprocessor.py` — unchanged; signature `(H,W,3) BGR → (H,W) uint8 gray` confirmed.
- `src/omniscribe/cli.py` — `transcribe` command; wiring point for `--platform`, `--ui-filter`, `resolve_profile`, post-OCR filter chain.

## Libraries to reuse

`re` (stdlib), `dataclasses.dataclass(frozen=True)`, `cv2.rectangle` (already via `opencv-python-headless`); existing `omniscribe.acquire.platform.Platform` + `detect_platform`, `omniscribe.config.OmniScribeConfig`, `omniscribe.output.TranscriptSegment`, `omniscribe.errors.OmniScribeError`, `typer.Option`.

## Out of scope

- **Scene-change / shot-boundary detection** — potential standalone Phase 2.5.
- **YAML profile configs** — rejected; revisit on contributor request.
- **Bbox-aware segment filtering** — would require `TranscriptSegment` schema change.
- **Per-platform `frequency_threshold` tuning** — ship default 0.95; per-platform overrides trivial to add later.
- **`docs/adding-platforms.md` contributor guide** — cut; the pattern lives in the three profile files.
- **`omniscribe platforms list / show` CLI subcommands** — defer until a user asks.
- **ASR↔OCR merge + `source="BOTH"`** — Phase 4.
- **SRT/VTT/MD formatters + `--format` toggle** — Phase 5.
- **Batch mode, LLM cleanup, Docker, web UI, diarization, translation** — Phase 5–6.
- **Platforms beyond TikTok / YouTube / Instagram** — new platforms are contributor PRs.

## Close-out

Phase 3 is **complete**. Shipped via two squash-merged PRs against `main`:

| Sprint | PR | SHA | Summary |
|---|---|---|---|
| 3.1 | #1 | `3d855cc` | Platform profile infra — frozen dataclasses (`PlatformProfile`, `RelativeRect`), TikTok/YouTube/Instagram/Generic profiles, registry with `resolve_profile`, `Platform.GENERIC` enum member, `platform_profile` config validator, `--platform` Typer `click.Choice` flag. Plumbing only; no OCR behavior change. |
| 3.2 | #2 | `05bbe37` | UI filter — `mask_zones` (pre-OCR), `filter_by_patterns` + `filter_by_frequency` (post-OCR); `ui_filter_enabled` config + `--ui-filter/--no-ui-filter` CLI kill switch; `RapidOCREngine` gained kw-only `profile` param; `resolve_profile` wired in `cli.transcribe`. |

Net test delta at ship: 121 tests at 3.1 → 140 tests at 3.2 end of Phase 3.

Follow-ups explicitly **deferred** out of Phase 3 and not yet scheduled:

- Scene-change detection — **shipped in Phase 2.5 (`894fae2`)** (listed as "potential Phase 2.5" in this plan's Out-of-scope; now closed).
- YAML profile configs (rejected; revisit on contributor request).
- Bbox-aware segment filtering (would require `TranscriptSegment` schema change).
- Per-platform `frequency_threshold` tuning (default 0.95 remains; per-platform overrides trivial to add later).
- `docs/adding-platforms.md` contributor guide.
- `omniscribe platforms list / show` CLI subcommands.
- Twitter/X and Facebook profiles (Phase 6 backlog — contributor PRs welcome).

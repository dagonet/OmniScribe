# OmniScribe — Sprint 7.1: OCR Noise-Floor

**Goal:** Cut OCR false-positive noise on text-heavy backgrounds via two complementary, non-overlapping changes: (a) **caption-region masking** that suppresses OCR in the band where TikTok/Instagram auto-captions render, before OCR runs; (b) **fuzzy frequency filter** that collapses near-duplicate recurring text (e.g. "SUBSCRIBE!" / "Subscribe →" / "SUBSCRIBE") into a single bucket, after OCR runs. The two filters target distinct leak paths and ship in one sprint.

## Context

OmniScribe v0.1.0 shipped 2026-04-25 (`main` @ `10d641a`). PR #19 (`chore(deps): drop typer[all] extra`, post-#18 main @ `789d4b4`) is the warm-up that this sprint branch is cut from. PRs #16 (bbox aggregation) + #17 (text-grouped dedup) closed the OCR **recall** side — multi-region per-frame text is now correctly captured and deduped. The remaining failure mode is the **precision** side: text-heavy backgrounds leak into transcripts as ON-SCREEN noise.

The two changes are *complementary, not redundant*:
- Caption-region masking suppresses *all* OCR in a band — catches rolling auto-captions where each chunk is unique and never trips the frequency filter.
- Fuzzy frequency filter catches recurring text *outside* that band (SUBSCRIBE prompts, watermarks) where exact-match `Counter` (today's algorithm) misses spelling/punctuation variants.

Bundling them into one sprint = one manual GPU verification cycle instead of two.

## Tier

**T3** — `python-coder` + `code-reviewer` + `tester`. No architect. ~50–80 prod LOC + ~40 test LOC across 6 files; borderline T2 by line count. Kept T3 because OCR is output-quality-critical and the fuzzy change carries silent-regression risk (see "Behavior-change risk" below) — the reviewer + tester earn their keep.

## Pre-existing surface (reuse, do not rebuild)

- `src/omniscribe/ocr/ui_filter.py:49` — `mask_zones(frame, zones)`. Already converts `RelativeRect` → pixel coords and zeros pixels. **No signature change required** — adding new entries to `ui_exclusion_zones` flows through automatically.
- `src/omniscribe/ocr/ui_filter.py:102` — `filter_by_frequency(segments, frame_count, threshold)`. Current algorithm: `Counter[text.strip().lower()]`, exact-match only. **Extend** with a `fuzzy_threshold` kwarg.
- `src/omniscribe/ocr/deduplicator.py:82` — `dedup_segments(...)`. Uses `rapidfuzz.fuzz.ratio` over `text.casefold().strip()` canonical keys. **The similarity primitive** (canonical-key normalization + the `fuzz.ratio` threshold check) is what `filter_by_frequency` will reuse. The clustering loop itself does NOT carry over (data shapes differ — deduplicator clusters `TranscriptSegment` objects; the filter clusters bare strings).
- `src/omniscribe/platforms/base.py:45` — `PlatformProfile` (frozen dataclass). `ui_exclusion_zones: tuple[RelativeRect, ...]`. **Append new entries; do NOT add a new field.**
- `src/omniscribe/platforms/tiktok.py:15` — existing zones cover right-action strip, bottom caption+music chrome, top header. **Append** mid-band auto-caption rect.
- `src/omniscribe/platforms/instagram.py:13` — existing zones cover right rail, bottom audio label, top logo. **Append** mid-band auto-caption rect.
- `tests/test_ui_filter.py` — existing patterns for `mask_zones` and `filter_by_frequency`. Mirror.
- `tests/test_platform_profiles.py` — existing zone-count / coordinate assertions. Extend.
- `rapidfuzz>=3.9` already in `pyproject.toml` — no new deps.

## Scope pruning (TWO rounds of challenge applied)

**Cut:**
- **Separate `caption_zones: tuple[RelativeRect, ...]` field on `PlatformProfile`** — rejected as premature abstraction. The argument was "future `--no-caption-mask` flag could disable caption masking specifically". That's designing for hypothetical future requirements. Append directly to `ui_exclusion_zones`.
- **Per-platform `frequency_fuzzy_threshold` field on `PlatformProfile`** — rejected. Speculative ("TikTok prompts may need 85, Generic 90") with zero evidence. Hardcode kwarg default at `90.0`; add per-platform override only after real-world testing shows the need.
- **YouTube caption masking** — its existing `ui_exclusion_zones` already covers the bottom 20% where YouTube Shorts captions render. Adding overlapping zones would be dead code. Untouched in this sprint.
- **Wiring into `cli.py`** — falls out of the cut above; no per-profile threshold to thread through.
- **Reusing the deduplicator clustering loop** — rejected as a category error. Data shapes differ (segment objects vs bare strings). Reuse the *similarity primitive* (canonical-key + `fuzz.ratio` threshold) only.
- **CLI flag for disabling caption masking** — out of scope. Add when real users want it.
- **Per-region or per-speaker weighting in frequency filter** — would need richer segment metadata; future sprint.
- **Creator-chosen caption overlay position handling** — would need per-video heuristics; future sprint. This sprint targets *default* platform auto-caption positions only.
- **Lowering `frequency_threshold` (0.95 → 0.85) as default** — explicitly rejected. The fuzzy clustering already raises effective recurrence counts; lowering the threshold compounds the aggression. If anything, the per-profile threshold may need to RAISE (see "Behavior-change risk" below).

**Kept:**
- Append caption-band rects directly to `ui_exclusion_zones` in TikTok + Instagram profiles.
- Single hardcoded `fuzzy_threshold=90.0` kwarg on `filter_by_frequency`. `0` or `None` semantics: NOT supported in this sprint (test coverage cost without a need); `90.0` is just a default callers can change.
- Lift the deduplicator's similarity primitive to a shared private helper in `src/omniscribe/ocr/_text_match.py` (new module) consumed by both `deduplicator.py` and `ui_filter.py`. Two near-duplicate-detection schemes with two similarity primitives is exactly the kind of subtle inconsistency that bites users later.
- Strict 2-PR sequencing: warm-up #19 already merged; this sprint is #20 cut from post-#19 main.

## Sprint 7.1 — Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `docs/plans/sprint-7-1-ocr-noise-floor.md` (this file) | Canonical in-repo plan doc. Gets `## Close-out` footer appended post-merge. | (this) |
| `src/omniscribe/ocr/_text_match.py` (**new**) | Lift the canonical-key normalization + `rapidfuzz.fuzz.ratio` threshold check from `deduplicator.py:82`. Two private helpers: `_canonical_key(text: str) -> str` and `_fuzzy_match(a: str, b: str, threshold: float) -> bool`. Module is private (`_`-prefixed) so it's not part of the public API surface. | +25 |
| `src/omniscribe/ocr/deduplicator.py` (edit) | Replace inline `text.casefold().strip()` + inline `fuzz.ratio` check with calls to `_canonical_key` + `_fuzzy_match`. **No behavior change.** Existing 12 deduplicator tests must pass byte-unchanged. | -10 / +5 |
| `src/omniscribe/ocr/ui_filter.py` (edit) | Extend `filter_by_frequency` signature: `def filter_by_frequency(segments, frame_count, threshold, *, fuzzy_threshold: float = 90.0) -> list[TranscriptSegment]`. Algorithm: (1) build initial Counter using `_canonical_key`; (2) cluster keys by `_fuzzy_match` (greedy single-link: walk keys, merge into existing cluster if any member matches); (3) sum counts across clusters; (4) drop segments whose cluster's combined ratio ≥ `threshold`. | +25 |
| `src/omniscribe/platforms/tiktok.py` (edit) | Append one new `RelativeRect` to `ui_exclusion_zones` covering the TikTok auto-caption band. Coordinates measured from real frames (see "How to set the coordinates" below). One-line comment beside the rect citing the measurement source. | +2 |
| `src/omniscribe/platforms/instagram.py` (edit) | Same pattern: append one new `RelativeRect` covering the Instagram Reels auto-caption band. Comment cites measurement. | +2 |
| `tests/test_text_match.py` (**new**) | Tests for `_canonical_key` (whitespace + casing) and `_fuzzy_match` (boundary cases: identical strings, near-duplicates above threshold, distinct strings below threshold, empty strings). | +30 |
| `tests/test_deduplicator.py` (no edit) | All 12 existing tests must pass byte-unchanged after the refactor. **Regression check.** | 0 |
| `tests/test_ui_filter.py` (extend) | Three new cases: (a) **positive** — `["SUBSCRIBE!", "Subscribe →", "SUBSCRIBE"]` cluster at `fuzzy_threshold=90`, combined ratio exceeds `threshold`, all 3 dropped; (b) **negative** — `["Hello world", "Goodbye sun", "Random text"]` stay as 3 separate buckets, none dropped; (c) **mask-pixel integration** — synthetic all-white `numpy.ndarray` (1080×1920) run through `mask_zones(frame, tiktok_profile.ui_exclusion_zones)` has pixel values of zero in the new caption band. | +40 |
| `tests/test_platform_profiles.py` (extend) | Assert TikTok and Instagram profiles each have ≥1 new `RelativeRect` in `ui_exclusion_zones` whose y-band falls in the mid-lower region (e.g. `0.40 ≤ y_min < y_max ≤ 0.85`). | +15 |

**Explicitly NOT in deliverables:**
- `pyproject.toml` — no change (rapidfuzz already pinned).
- `src/omniscribe/platforms/youtube.py` — untouched.
- `src/omniscribe/platforms/base.py` — untouched (no new field).
- `src/omniscribe/cli.py` — untouched (no per-profile threshold to wire).
- `src/omniscribe/ocr/rapid_ocr.py` — untouched (existing `mask_zones()` call already reads `profile.ui_exclusion_zones` and picks up new entries automatically).
- `IMPLEMENTATION_PLAN.md` — stays as-is; sprint isn't a Phase 5 item.

## How to set the caption-band coordinates (developer instruction)

Do **NOT** ship guessed coordinates.

1. Sample **2–3 frames from different videos per platform** with auto-captions visible. The repo doesn't ship test fixtures with auto-captions; use any locally available video, or temporarily download one with `yt-dlp` to a scratch dir (do NOT commit it).
2. Measure the typical caption band by eye, or with a quick `mcp__plugin_context-mode_context-mode__ctx_execute` script that opens the frames and shows pixel coordinates.
3. Pick a conservative `RelativeRect` (slightly narrower than the widest measurement) covering the *common* position across the samples. Err on the side of NOT masking — masking too much loses legitimate creator text overlays.
4. Add a comment beside each new `RelativeRect` like: `# auto-caption band measured on 3 sample videos; rolls in y∈[0.62, 0.78] for TikTok auto-captions`.
5. If after measurement the band overlaps significantly (>50%) with an existing zone, drop the change for that platform — the existing zone already covers it.

## Acceptance criteria

- [ ] `uv run ruff format --check .` clean.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run python -m pytest -q` green. All prior 296 tests still pass (zero regressions). Expected count: ~302 (296 + 6 new test cases).
- [ ] `_text_match` module is private (`_`-prefixed) and only imported from `deduplicator.py` + `ui_filter.py`.
- [ ] `cleanup` of `deduplicator.py` is **behavior-preserving** — the 12 existing dedup tests pass byte-unchanged.
- [ ] `filter_by_frequency` with `fuzzy_threshold=90.0` and one default `frequency_threshold=0.95`:
  - clusters near-duplicate noise (positive case test passes)
  - preserves distinct text (negative case test passes)
- [ ] Caption-band rects in TikTok + Instagram profiles are populated from measured coordinates (not guessed), with one-line citation comments.
- [ ] `mask_zones` integration test proves pixel values in the new caption band are zeroed for TikTok.
- [ ] No changes to `src/omniscribe/cli.py`, `src/omniscribe/ocr/rapid_ocr.py`, `src/omniscribe/platforms/base.py`, or `src/omniscribe/platforms/youtube.py`.

## Behavior-change risk (READ BEFORE MERGING)

The fuzzy frequency filter raises the *effective* recurrence count for any canonical text. Three near-duplicate captions that each appeared 10× now count as one item with 30 occurrences, so the existing `frequency_threshold ≥ 0.95` drop will fire more aggressively than today on real videos.

**Mitigation:** the manual GPU smoke (deferred to user post-merge) MUST run on **two** kinds of input:
1. A known-noisy TikTok with rolling auto-captions and recurring SUBSCRIBE prompts (validate the noise drops)
2. A known-good video with legitimate creator-typed text overlays (validate that legitimate text is NOT dropped)

If (2) regresses (legitimate text being dropped), the fix is to make the filter **more permissive**, not less:
- **First try:** raise `fuzzy_threshold` (90 → 95) at the call site — more selective clustering means legitimate text stops getting bucketed with noise. Most surgical.
- **Fallback:** raise `frequency_threshold` (0.95 → 0.97 or higher) in the affected profile — accepts that some text recurs more than expected.
- **Do NOT lower `frequency_threshold`** — that drops legitimate text at *lower* frequencies, strictly worse than the regression you're fixing.

## Verification

```bash
# Format + lint
uv run ruff format --check .
uv run ruff check .

# Targeted tests during dev
uv run python -m pytest tests/test_text_match.py tests/test_ui_filter.py tests/test_platform_profiles.py tests/test_deduplicator.py -x

# Full suite
uv run python -m pytest -q
```

Manual GPU smoke (per the dual-input protocol above) — deferred to user post-merge.

## Design decisions locked

1. **`_text_match.py` as a new private module** rather than adding helpers to `deduplicator.py` and importing from there. *Why:* `ui_filter.py` importing from `deduplicator.py` would create a confusing dependency direction (filter step depending on dedup step, when in the pipeline filter runs *before* dedup). A neutral helper module avoids the inversion.
2. **`fuzzy_threshold=90.0` as the default kwarg.** *Why:* matches the high end of dedup's existing fuzzy threshold; tight enough to avoid collapsing legitimately distinct captions, loose enough to catch "SUBSCRIBE!" / "Subscribe →" / "SUBSCRIBE" variants.
3. **Greedy single-link clustering** in `filter_by_frequency`. *Why:* simplest correct algorithm; matches the spirit of the deduplicator's cross-frame grouping. O(n²) in the number of unique keys, but for typical OCR output (~50–500 unique keys per video) this is negligible.
4. **Append to `ui_exclusion_zones`** rather than introducing a `caption_zones` field. *Why:* premature abstraction — we don't have a use case for distinguishing them yet, and adding a field now means writing test coverage for an empty-tuple default that has no consumer.

## Critical files

- `src/omniscribe/ocr/_text_match.py` (new)
- `src/omniscribe/ocr/deduplicator.py` (refactor only)
- `src/omniscribe/ocr/ui_filter.py` (extend)
- `src/omniscribe/platforms/tiktok.py` (append)
- `src/omniscribe/platforms/instagram.py` (append)
- `tests/test_text_match.py` (new)
- `tests/test_ui_filter.py` (extend)
- `tests/test_platform_profiles.py` (extend)

## Libraries to reuse

- `rapidfuzz.fuzz.ratio` (already pinned `>=3.9,<4.0`)
- `numpy` (already a transitive dep via opencv) — for the synthetic-frame mask integration test

## Out of scope

See "Cut" list above. Summary: no per-platform fuzzy thresholds, no CLI flag for disabling caption masking, no creator-chosen overlay handling, no YouTube caption-zone changes, no per-region weighting.

## Close-out

(append post-merge: PR number, squash commit SHA, final test count, any drift between plan and implementation)

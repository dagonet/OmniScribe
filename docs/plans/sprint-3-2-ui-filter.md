# Sprint 3.2 — Zone masking + pattern/frequency filters + OCR wire-in

**Phase:** 3 (Platform Profiles & UI Filtering)
**Tier:** T3
**Team:** `dev-1` (python-coder), `reviewer-1` (code-reviewer), `tester-1` (tester)
**Branch:** `feature/sprint-3-2-ui-filter` (starts after Sprint 3.1 merges)
**Parent plan:** [phase-3-platform-profiles.md](./phase-3-platform-profiles.md)
**Depends on:** Sprint 3.1 must be merged before this starts.

## Goal

`omniscribe transcribe <tiktok-url> --ocr` produces a JSON transcript where TikTok sidebar chrome, bottom bar, and usernames/counts are absent. `--ui-filter/--no-ui-filter` toggles all three filter stages (zone masking, patterns, frequency) via `config.ui_filter_enabled`.

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `src/omniscribe/ocr/ui_filter.py` | Three pure functions, no I/O, no config coupling:<br>**`mask_zones(gray: np.ndarray, zones: tuple[RelativeRect, ...]) -> np.ndarray`** — for each zone compute pixel rect from `gray.shape = (H, W)`: `x1, y1 = int(zone.x * W), int(zone.y * H)`; `x2 = min(int((zone.x + zone.w) * W), W)`; `y2 = min(int((zone.y + zone.h) * H), H)` (clamp to frame bounds). `cv2.rectangle(gray_copy, (x1, y1), (x2, y2), color=0, thickness=cv2.FILLED)` on a defensive **copy** (`gray.copy()` first). No-op when `zones` is empty — returns input unchanged.<br>**`filter_by_patterns(segments, patterns) -> list[TranscriptSegment]`** — SPEECH passes through. For ON-SCREEN, drop if any pattern `.search(seg.text.strip())` hits. Preserves input order.<br>**`filter_by_frequency(segments, frame_count, threshold) -> list[TranscriptSegment]`** — SPEECH pass-through. For ON-SCREEN: count occurrences of `seg.text.strip().lower()` (normalized-exact with case-fold); drop texts where `count / frame_count >= threshold`. `frame_count == 0` → return input unchanged. Docstring MUST note the normalization divergence: `filter_by_patterns` preserves case (regex authors opt into `re.IGNORECASE`), `filter_by_frequency` case-folds internally because UI chrome often flickers between `"SUBSCRIBE"` and `"Subscribe"` across frames.<br>**Critical ordering note** in module docstring: run `filter_by_patterns` + `filter_by_frequency` on *raw* pre-dedup OCR segments, not post-dedup, because the frequency ratio needs raw-frame counts to be meaningful. | ~80 |
| `src/omniscribe/ocr/rapid_ocr.py` (edit) | **Module-level import** (required for mock patching): `from omniscribe.ocr.ui_filter import mask_zones`. Change `__init__` signature to keyword-only for `profile`: `def __init__(self, config: OmniScribeConfig, *, profile: PlatformProfile \| None = None) -> None:`. The `*,` prevents accidental positional binding; `None` default preserves Phase 2 `test_rapid_ocr.py` tests (all call `RapidOCREngine(config)` positionally, single-arg). When `config.ui_filter_enabled and profile is not None and profile.ui_exclusion_zones`, call `mask_zones(processed_frame, profile.ui_exclusion_zones)` between `preprocess(frame)` and `engine(...)`. In production the `None` branch never fires — it's test-only. | +12 |
| `src/omniscribe/cli.py` (edit) | Add module-top imports: `from omniscribe.platforms.registry import resolve_profile`, `from omniscribe.ocr.ui_filter import filter_by_patterns, filter_by_frequency` — required so tests can `patch("omniscribe.cli.resolve_profile")`.<br>In `transcribe`, after `ocr_active` branch enters:<br>1. `profile = resolve_profile(config, src)`.<br>2. `engine = RapidOCREngine(config, profile=profile)`.<br>3. `ocr_segments = engine.extract(video_path)` — raw, pre-dedup.<br>4. (existing) `logger.info("OCR: %d segments from %d frames", ...)`.<br>5. If `config.ui_filter_enabled`:<br>&nbsp;&nbsp;&nbsp;&nbsp;`pre_pattern = len(ocr_segments)`<br>&nbsp;&nbsp;&nbsp;&nbsp;`ocr_segments = filter_by_patterns(ocr_segments, profile.ui_text_patterns)`<br>&nbsp;&nbsp;&nbsp;&nbsp;`post_pattern = len(ocr_segments)`<br>&nbsp;&nbsp;&nbsp;&nbsp;`ocr_segments = filter_by_frequency(ocr_segments, engine.last_frame_count, profile.frequency_threshold)`<br>&nbsp;&nbsp;&nbsp;&nbsp;`post_freq = len(ocr_segments)`<br>&nbsp;&nbsp;&nbsp;&nbsp;`logger.info("UI filter: dropped %d pattern-matches, %d frequency-hits", pre_pattern - post_pattern, post_pattern - post_freq)`<br>6. (existing) `dedup_segments(...)` → `merge_channels(...)`.<br>Also add `--ui-filter/--no-ui-filter` Typer flag, `Optional[bool] = None`; merge: `if ui_filter is not None: config = config.model_copy(update={"ui_filter_enabled": ui_filter})`. | +30 |
| `tests/test_ui_filter.py` | Table-driven:<br>(a) `mask_zones` with empty zones returns input unchanged;<br>(b) `mask_zones` with one zone = full frame → all-zeros;<br>(c) `mask_zones` on `RelativeRect(0.5, 0.0, 0.5, 1.0)` zeros right half only;<br>(d) two **overlapping zones** — no crash, union all-zero;<br>(e) `filter_by_patterns` drops `"@user"` when pattern is `r"^@\w+$"`; keeps `"hello @user"`;<br>(f) `filter_by_patterns` passes SPEECH through untouched even if text matches;<br>(g) `filter_by_patterns` with `r"^$"` drops empty-text ON-SCREEN but SPEECH passes;<br>(h) `filter_by_frequency` drops text at 3/3 ratio when threshold=0.8;<br>(i) keeps text at 1/3 ratio when threshold=0.8;<br>(j) `frame_count=0` passes through unchanged;<br>(k) `frame_count=1` + one ON-SCREEN segment (ratio=1.0 ≥ threshold) → dropped. | ~115 |
| `tests/test_rapid_ocr.py` (extend) | Two new tests:<br>(a) `RapidOCREngine(config(ui_filter_enabled=True), profile=TIKTOK_PROFILE).extract(...)` calls `mask_zones` with `TIKTOK_PROFILE.ui_exclusion_zones`;<br>(b) `RapidOCREngine(config(ui_filter_enabled=False), profile=TIKTOK_PROFILE).extract(...)` does NOT call `mask_zones` (`assert_not_called`). | ~45 |
| `tests/test_cli.py` (extend) | Three new tests:<br>(a) `--platform tiktok --ocr` with mock frames: sidebar `"@creator"` + body `"hello world"` → JSON contains `"hello world"` only;<br>(b) `--no-ui-filter --platform tiktok --ocr` → both segments survive;<br>(c) `OMNI_UI_FILTER_ENABLED=false` + no flag → both survive. | ~60 |

## Acceptance criteria

- [ ] `omniscribe transcribe <tiktok-url> --ocr` on a short TikTok (15–30 s) with a visible sidebar → output JSON does **not** contain any sidebar handle or like-count strings. Manual GPU accept on RTX 4090.
- [ ] `omniscribe transcribe <tiktok-url> --no-ui-filter --ocr` — same video — DOES contain sidebar handle/count strings (proves filter was the reason they were absent above).
- [ ] `omniscribe transcribe <local.mp4> --ocr` with `platform_profile="auto"` → resolves to `GENERIC_PROFILE`; zero zones masked, no patterns dropped, frequency filter only. Behavior effectively unchanged vs. Phase 2 end-state.
- [ ] `--platform generic --ocr` on a TikTok URL overrides auto-detect → same as the `<local.mp4>` case above.
- [ ] Unit test: synthetic **segment-list fixture** (30 ON-SCREEN, 29 with `text="WATERMARK"`, `frame_count=30`, ratio 29/30 ≈ 0.967 ≥ 0.95) → `filter_by_frequency` drops the 29; transient fixture (3/30, ratio 0.1) → all 3 survive.
- [ ] Existing Phase 2 `test_rapid_ocr.py` tests — which construct `RapidOCREngine(config)` without a profile — pass **unchanged**. `profile=None` default preserves backward compat.
- [ ] All Phase 1 + Phase 2 + Sprint 3.1 tests green. New tests green. `uv run ruff format --check . && uv run ruff check .` clean.
- [ ] No new runtime deps.

## Verification

```
uv run pytest -q                                 # all green
uv run omniscribe transcribe https://www.tiktok.com/... --ocr --output tt.json
  # → creator overlays present, NO @handles, NO counts, NO music-note attribution
uv run omniscribe transcribe https://www.tiktok.com/... --ocr --no-ui-filter --output raw.json
  # → contains the sidebar chrome (proves filter is the differentiator)
uv run omniscribe transcribe sample.mp4 --ocr --platform generic --output gen.json
  # → GENERIC_PROFILE applied: no zones, no patterns, freq filter only
```

Manual TikTok acceptance is the only GPU-required test; everything else is CPU-only and mocked.

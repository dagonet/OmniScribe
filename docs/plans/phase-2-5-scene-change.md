# OmniScribe — Phase 2.5: Scene-Change Detection for OCR Frame Sampler

## Context

Phase 2 (OCR, commit `ba8dd20`) shipped `ocr/frame_sampler.py` as a **fixed-interval sampler** — every `1/ocr_sample_fps` seconds yield one frame, no matter what's on it. `IMPLEMENTATION_PLAN.md` §Phase 2 listed scene-change detection as part of the frame-sampler task, but it was deferred to keep Phase 2 shippable. Phase 3 (`05bbe37`, merged 2026-04-18) reiterated it as "potential Phase 2.5".

The shipped behavior burns OCR time on redundant frames. A 60-second screen recording of a static slide currently yields 60 frames at `ocr_sample_fps=1.0` → 60 RapidOCR calls → 60 bursts of near-identical detections that the post-OCR deduplicator must collapse. A 10-slide tutorial cuts from slide to slide once every 6 seconds — we need ~10 OCR passes, not 60.

Phase 2.5 adds **pre-OCR scene-change detection** via frame-to-frame pixel diff. Frames visually near-identical to the previous *yielded* frame are skipped. First frame always yields (cold start); a bounded max-gap forces periodic yields to survive slow gradual drift below threshold; an end-of-video rule guarantees the last visible content reaches OCR.

Scene-change (pixel-level, pre-OCR) is **complementary** to the existing text-level fuzzy deduplicator in `ocr/deduplicator.py` (rapidfuzz, post-OCR). Pixel diff cuts OCR *work*; text dedup cleans OCR *output*. Keep both.

## Pre-existing surface (reused, not rebuilt)

- `src/omniscribe/ocr/frame_sampler.py:26` — `sample_frames(video_path: Path, fps: float) -> Iterator[tuple[float, np.ndarray]]`. Signature must not change; RapidOCREngine iterates it at `rapid_ocr.py:113`.
- `cv2` already imported in `frame_sampler.py` and `preprocessor.py` — no new deps for `cv2.absdiff`, `cv2.resize`, `cv2.cvtColor`.
- `numpy` already transitive via `cv2` + `opencv-python-headless` in `pyproject.toml`.
- `src/omniscribe/config.py` Pydantic `BaseSettings` with `@field_validator` pattern already established for `ocr_sample_fps`, `ocr_min_confidence`, etc. — new config keys drop in using the same shape.
- `tests/test_frame_sampler.py` mock pattern: patches `omniscribe.ocr.frame_sampler.cv2.VideoCapture`, mock exposes `.isOpened()`, `.get(prop_id)`, `.read() -> (ok, frame)`, `.release()`. New tests reuse this mock shape.

## Scope pruning (one round of challenge applied)

Cut:
- **SSIM** — rejected. Requires `scikit-image` (new runtime dep, ~20 MB on disk) for ~0.5 F1 accuracy gain over mean-absdiff on slide content. Not worth it.
- **Histogram correlation** — rejected as primary. `cv2.compareHist(HISTCMP_CORREL)` at 320×180 is ~30% cheaper than absdiff on 4K frames but sensitive to global lighting drift (fade-ins trigger false positives). Worse tuning story.
- **Motion-vector extraction from decoded H.264** — requires ffmpeg-python or PyAV and per-codec quirks. No. Pixel domain only.
- **Per-platform scene-change threshold tuning** — ship one default (`0.02`); revisit on real-world data. Same logic as `frequency_threshold` in Phase 3.
- **User-tunable `scene_change_max_gap_seconds`** — collapsed to internal constant `_MAX_GAP_SECONDS = 30.0` in `frame_sampler.py`. Speculative safety valve; no evidence it needs user tuning.
- **Adaptive thresholding (percentile over window)** — deferred. Static threshold is predictable, easy to tune, easy to document. Adaptive is a Phase 2.6 or later concern if static proves inadequate.
- **Surfacing `--scene-change-threshold` as a CLI flag** — config + env only for first ship. Almost nobody wants to tune this at the command line; env var covers power-user + docker-compose cases.

Kept:
- **`--scene-change/--no-scene-change` CLI kill switch** — same safety-valve rationale as `--no-ui-filter`: users reach for it when something looks wrong.

## Tier assignment

**T3** (python-coder + code-reviewer + tester). ~150 prod LOC plus tests. One pure function added to `frame_sampler.py`, two config validators, one CLI flag, ~8 new tests. No architect — algorithm is one numpy mean + threshold comparison.

Every spawn prompt MUST include a `## Required Skills` block per `AGENT_TEAM.md` Spawn-Prompt Binding Table — `hooks/require-skills-block.sh` enforces exit 2 on missing block.

---

## Sprint 2.5 — Scene-change frame sampler + config + CLI kill switch (T3)

**Goal:** `sample_frames(video_path, fps)` yields only frames that materially differ from the previous yielded frame (pixel-level absdiff mean ≥ threshold), with first-frame-always-yield, max-gap forced-yield, and end-of-video guard. Phase 2 behavior is recoverable via `scene_change_enabled=False` / `--no-scene-change`.

### Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `src/omniscribe/ocr/frame_sampler.py` (edit) | Module-level constant `_MAX_GAP_SECONDS: float = 30.0` (internal safety valve, not user-tunable — speculative, revisit if gradient-drift pathology is observed).<br>Add private helper `_frame_difference(prev_small: np.ndarray, curr_small: np.ndarray) -> float`: returns `float(np.mean(cv2.absdiff(prev_small, curr_small))) / 255.0` (range `[0.0, 1.0]`). Add private helper `_downscale_gray(frame_bgr: np.ndarray) -> np.ndarray` — hardcoded 320-longest-edge via `cv2.cvtColor(COLOR_BGR2GRAY)` + `cv2.resize(INTER_AREA)`. Bounds absdiff cost on 4K input (320×180 is ~80 KB vs 8.3 MB for 4K, same signal for slide cuts). No tunable param — YAGNI.<br>In `sample_frames`, extend loop:<br>1. **Hot-path short-circuit**: if `not scene_change_enabled`, yield every sampled frame unconditionally — skip the downscale + diff entirely. This is the Phase 2 baseline path, must stay cheap.<br>2. Else build `gray_small = _downscale_gray(frame)` for each sampled frame. Track `last_yielded_small` and `frames_since_yield`.<br>3. Yield rules (in order, scene-change-enabled path only):<br>&nbsp;&nbsp;• `last_yielded_small is None` → first sampled frame, always yield.<br>&nbsp;&nbsp;• `_frame_difference(last_yielded_small, gray_small) >= scene_change_threshold` → yield.<br>&nbsp;&nbsp;• `frames_since_yield >= max_gap_frames` → force-yield (bounded gap).<br>&nbsp;&nbsp;• else: advance without yielding.<br>4. **After the `for` loop terminates** (outside the loop body), apply end-of-video rule: if the final iteration's `gray_small` was not yielded AND `_frame_difference(last_yielded_small, gray_small) > 0.0` (strict any-nonzero diff — no magic epsilon), force-yield it with its original timestamp. Pure-duplicate trailing frames are dropped (expected).<br>5. Signature gains two keyword-only params: `scene_change_enabled: bool = True`, `scene_change_threshold: float = 0.02`. Compute `max_gap_frames = max(1, int(round(fps * _MAX_GAP_SECONDS)))` — `fps` is caller's nominal `ocr_sample_fps` (post-stride scene-change rate), so `fps=1.0` → 30 sampled-frame gap → 30s wall-clock; `fps=2.0` → 60 sampled-frame gap → still 30s wall-clock. Formula is fps-invariant in wall-clock terms. **Precondition**: `fps > 0`, enforced by `OmniScribeConfig.ocr_sample_fps` validator upstream — sampler does not re-validate.<br>**Preserve positional `(video_path, fps)` signature** — existing callers and tests remain valid. | +65 |
| `src/omniscribe/config.py` (edit) | Add two fields after `ocr_sample_fps`:<br>• `scene_change_enabled: bool = True`<br>• `scene_change_threshold: float = 0.02`<br>Add `@field_validator("scene_change_threshold", mode="after")` raising `ValueError` if not in `(0.0, 1.0]` — 0.0 means "every frame passes" which defeats the feature, 1.0 means "absdiff must fully saturate" which is implausible. Raise on out-of-range.<br>Env vars auto-bind: `OMNI_SCENE_CHANGE_ENABLED`, `OMNI_SCENE_CHANGE_THRESHOLD`. | +14 |
| `src/omniscribe/ocr/rapid_ocr.py` (edit) | In `extract()`, replace `for timestamp, frame in sample_frames(video_path, self._config.ocr_sample_fps)` with a kwargs pass-through:<br>`for timestamp, frame in sample_frames(video_path, self._config.ocr_sample_fps, scene_change_enabled=self._config.scene_change_enabled, scene_change_threshold=self._config.scene_change_threshold):`<br>No other changes. `last_frame_count` semantics shift from "frames sampled" to "frames yielded" — document in the `extract()` docstring. This is the correct denominator for Phase 3's `filter_by_frequency` ratio: a watermark in every unique slide still hits `count/frame_count ≈ 1.0`. | +5 |
| `src/omniscribe/cli.py` (edit) | Add Typer flag `--scene-change/--no-scene-change` after `--ui-filter`:<br>`scene_change: bool \| None = typer.Option(None, "--scene-change/--no-scene-change", help="Enable or disable scene-change detection in OCR frame sampler (overrides OMNI_SCENE_CHANGE_ENABLED).")`<br>Merge: `if scene_change is not None: config = config.model_copy(update={"scene_change_enabled": scene_change})`. Mirrors existing `--ui-filter` / `--ocr` patterns. Above the `filter_by_frequency` call add a 1-line comment: `# frame_count is yielded frames (may be scene-change-reduced from nominal fps * duration)`. | +10 |
| `.env.example` (edit, conditional) | **Developer: read `.env.example` first if accessible.** If file exists and `OMNI_OCR_SAMPLE_FPS` appears, add below it:<br>`OMNI_SCENE_CHANGE_ENABLED=true`<br>`OMNI_SCENE_CHANGE_THRESHOLD=0.02`<br>If the file is not accessible at implementation time, skip this row and note in the PR description. Not a blocker for acceptance criteria. | +2 |
| `tests/test_frame_sampler.py` (extend) | **Pre-flight step before writing new tests:** read existing tests; any test that feeds identical mock frames and asserts ≥2 yields must add `scene_change_enabled=False` kwarg — the new default would collapse it to 1 yield. Enumerate these in the PR description.<br>Add fixtures for synthetic frames: `def _frame(value: int) -> np.ndarray: return np.full((180, 320, 3), value, dtype=np.uint8)`. **Value-delta guidance**: use `value=50` vs `value=200` (diff `≈ 0.59`, well above 0.02 threshold) for "change" fixtures; identical `value=N` for "no-change"; small delta like `value=100` vs `value=102` (diff `≈ 0.008`, below threshold) to test sub-threshold suppression. Build a `_mock_capture` helper that returns `iter` of `(ok, frame)` tuples.<br>New tests:<br>(a) `scene_change_enabled=False` → every stride-picked frame yields (Phase 2 baseline regression test).<br>(b) First frame always yields even if subsequent frames are identical.<br>(c) 5 identical frames then 5 different frames (step change at frame 5) → 2 yields: first, and frame 5.<br>(d) Gradient drift below threshold for 60 sampled frames at fps=1.0 (max_gap_frames=30) → at least 2 yields (first + one forced-gap). Verify force-yield fires.<br>(e) End-of-video rule: 3 identical frames then 1 distinct final frame (difference above 0.0 but below threshold) → final frame force-yielded.<br>(f) End-of-video rule: 3 identical frames then 1 identical final frame → final frame NOT force-yielded (pure duplicate, `_frame_difference == 0.0`).<br>(g) Existing `sample_frames` default-args tests still pass — omit new kwargs, verify default `scene_change_enabled=True` path works. | +120 |
| `tests/test_config.py` (extend or add) | Config validator tests:<br>(a) Parametrized `@pytest.mark.parametrize("bad", [0.0, 1.5, -0.1])` — all raise `ValidationError`.<br>(b) `scene_change_threshold=1.0` accepted (upper boundary).<br>(c) `OMNI_SCENE_CHANGE_ENABLED=false` env var parses to `False`.<br>(d) Both new fields have documented defaults: `scene_change_enabled=True`, `scene_change_threshold=0.02`. | +28 |
| `tests/test_cli.py` (extend) | Three tests:<br>(a) `--scene-change` (explicit on) merges `scene_change_enabled=True` into config.<br>(b) `--no-scene-change` merges `scene_change_enabled=False`.<br>(c) `OMNI_SCENE_CHANGE_ENABLED=false` + no flag → merged config has `scene_change_enabled=False`. | +35 |
| `tests/test_rapid_ocr.py` (extend) | One test: with `scene_change_enabled=True` mock config, verify `sample_frames` is called with kwargs `scene_change_enabled=True, scene_change_threshold=0.02` (patch `omniscribe.ocr.rapid_ocr.sample_frames` and inspect call_args). Sanity check that the kwargs actually plumb through. | +25 |

### Acceptance criteria (Sprint 2.5)

- [ ] `uv run ruff format --check .` + `uv run ruff check .` clean.
- [ ] `uv run pytest -q` — all existing Phase 1 / 2 / 3 tests + new tests green.
- [ ] Synthetic 10-frame all-identical-slide test at `ocr_sample_fps=1.0`, default threshold → 1 yielded frame (first) + 0 forced last (pure duplicate, `_frame_difference == 0.0`).
- [ ] Synthetic 10-slide hard-cut test → exactly 10 yielded frames.
- [ ] `scene_change_enabled=False` → yielded count equals pre-Phase-2.5 stride-based count (regression safety).
- [ ] Max-gap forced-yield fires: synthetic 60 identical frames at fps=1.0 (internal max_gap=30s → max_gap_frames=30) → ≥ 2 yields, first at t=0, forced at t≈30.
- [ ] End-of-video rule yields the final sampled frame iff `_frame_difference > 0.0` vs last yielded.
- [ ] `pyproject.toml` runtime deps (`[project.dependencies]`) unchanged — zero new runtime deps.
- [ ] Existing `tests/test_frame_sampler.py` tests pass with the new default `scene_change_enabled=True` (update tests only if default-behavior assertions break; prefer preserving existing test shapes via `scene_change_enabled=False` where they test stride math specifically).
- [ ] `omniscribe transcribe sample.mp4 --ocr --no-scene-change` produces byte-identical output to a Phase 3 `--ocr` baseline capture (regression proof).
- [ ] `omniscribe transcribe sample.mp4 --ocr` (defaults on) produces fewer-or-equal yielded frames than `--no-scene-change` on the same input — verified via `"OCR: N segments from M frames"` log line M value.

### Verification

```
uv run ruff format --check . && uv run ruff check .
uv run pytest -q
uv run pytest tests/test_frame_sampler.py -v              # new scene-change tests
uv run omniscribe transcribe sample.mp4 --ocr --no-scene-change --output baseline.json
uv run omniscribe transcribe sample.mp4 --ocr --output sc.json
# Compare log lines: sc.json M-frame count <= baseline.json M-frame count
# On a tutorial with static slides, expect sc.json M-frame ~10-20% of baseline
OMNI_SCENE_CHANGE_THRESHOLD=0.0 uv run omniscribe transcribe sample.mp4 --ocr
  # → ValidationError from config, NOT a crash in sampler
```

---

## Design decisions locked

- **Mean-absdiff at 320×180 downscale** — chosen over histogram correlation (lighting-sensitive) and SSIM (requires scikit-image, new dep, marginal gain on slide content). `cv2.absdiff` + `np.mean` is O(H·W), branchless, numerically stable, and has a single intuitive tunable (`threshold ∈ (0.0, 1.0]`). The 320-longest-side downscale bounds cost at ~80 KB per compare regardless of 1080p vs 4K source.
- **`scene_change_enabled=True` as default** — most users benefit; opt-out via `--no-scene-change` / `OMNI_SCENE_CHANGE_ENABLED=false` recovers exact Phase 2 behavior. Phase 3's `--ui-filter/--no-ui-filter` set the pattern.
- **Scene-change operates on post-stride frames, not raw native-fps frames** — stride picks 1-in-N at `ocr_sample_fps`, scene-change filters *that* sequence. Rationale: running absdiff at native 60 fps would be 60× the compute for ~0 gain (consecutive native frames are visually near-identical by definition). Stride first, diff second.
- **`gap_tolerance = 2.0 / config.ocr_sample_fps` at `cli.py:163` stays tied to nominal fps, not effective post-scene-change rate** — dedup's job is to collapse adjacent identical OCR outputs; if scene-change yields one frame every 6 seconds on a static slide, the dedup window (`2.0 / 1.0 = 2 seconds`) won't span them — but that's fine, because with scene-change on we're *not* generating 6 identical OCR outputs. Dedup stays a safety net for overlapping text across visually distinct frames; nominal tolerance is correct.
- **First-frame rule** — cold start, no previous; always yield. Non-negotiable.
- **Max-gap forced yield, internal constant not user config** — hardcoded `_MAX_GAP_SECONDS = 30.0` in `frame_sampler.py`. Guards against slow gradient drift and the worst-case "zero yields" pathology. Deliberately *not* exposed as a config knob — it's a safety valve for a speculative failure mode; surfacing it bloats the tuning surface for no evidence-based reason. Revisit only if gradient-drift pathology is observed on real input.
- **End-of-video rule uses strict `> 0.0`, no epsilon** — any nonzero difference yields; zero difference (pure duplicate) drops. No magic constants. At uint8 quantization the minimum nonzero `_frame_difference` over 320×180 is ~`7e-8`, so "strict nonzero" is equivalent to "at least one pixel-bit changed somewhere" — exactly what we want.
- **Yielded-frame count semantics in `last_frame_count`** — with scene-change on, `RapidOCREngine.last_frame_count` equals yielded frames, not clock-sampled frames. Correct denominator for `filter_by_frequency`: a watermark appearing in every unique slide still hits `count/frame_count ≈ 1.0`. Document in `rapid_ocr.py:extract()` docstring.
- **No scene-change CLI threshold flag** — config + env only. Adding flags for every tuning knob bloats `--help`. Env var covers per-run override: `OMNI_SCENE_CHANGE_THRESHOLD=0.05 omniscribe transcribe ...`.
- **Scene-change rate is fps-invariant in wall-clock** — `max_gap_frames = int(round(fps * _MAX_GAP_SECONDS))`. At `ocr_sample_fps=1.0`, 30 sampled frames = 30 s wall-clock. At `ocr_sample_fps=2.0`, 60 sampled frames = still 30 s wall-clock. The constant expresses "force yield at least once per 30 wall-clock seconds" regardless of sampling rate.
- **Static threshold, not adaptive** — adaptive over a rolling window adds state, introduces order-dependence in tests, and complicates the mental model. Ship static; revisit only if real-world tuning data shows it's inadequate.
- **Downscale comparison buffer is gray, yielded frame is BGR** — comparison needs no color info (grayscale mean absdiff is robust); downstream `preprocess()` needs BGR input and rebuilds its own grayscale + CLAHE. Don't short-circuit the downstream path.
- **No new runtime deps** — `cv2.resize`, `cv2.absdiff`, `cv2.cvtColor`, `np.mean` all already available via `opencv-python-headless` + transitive `numpy`.
- **Mock patch sites stay at import site** — `omniscribe.ocr.frame_sampler.cv2.VideoCapture` pattern from Phase 2 preserved; new tests use same surface.

## Critical files

- `G:\git\OmniScribe\IMPLEMENTATION_PLAN.md` §Phase 2 (frame-sampler task) + §Phase 3 ("potential Phase 2.5" note) — authoritative scope.
- `G:\git\OmniScribe\src\omniscribe\ocr\frame_sampler.py:26` — signature + loop to extend. Only file with non-trivial algorithm changes.
- `G:\git\OmniScribe\src\omniscribe\ocr\rapid_ocr.py:113` — single caller. kwargs pass-through.
- `G:\git\OmniScribe\src\omniscribe\config.py` — Pydantic `BaseSettings`; insertion point for two new fields + one validator.
- `G:\git\OmniScribe\src\omniscribe\cli.py:163` — `gap_tolerance` contract; add 1-line comment documenting frame_count semantics.
- `G:\git\OmniScribe\tests\test_frame_sampler.py` — mock pattern for `cv2.VideoCapture`; new tests mirror existing `_mock_capture` helper style if present, else add one.
- `G:\git\OmniScribe\docs\plans\phase-3-platform-profiles.md` — format template for Phase 2.5 plan persistence files.

## Libraries to reuse

`cv2.absdiff`, `cv2.resize(INTER_AREA)`, `cv2.cvtColor(COLOR_BGR2GRAY)`, `np.mean` (all already transitively depended on); existing `omniscribe.config.OmniScribeConfig` Pydantic shape, `typer.Option` pattern from `--ui-filter`, existing `tests/test_frame_sampler.py` mock surface.

## Out of scope

- **SSIM, histogram correlation, motion vectors** — rejected; absdiff ships.
- **Adaptive / rolling-window thresholds** — deferred to 2.6+.
- **Per-platform `scene_change_threshold` overrides** — ship one default; `PlatformProfile` dataclasses can gain the field later if needed.
- **CLI flags for threshold and max-gap** — env + config only first ship.
- **Scene-change awareness in text deduplicator** — dedup already operates on text, not frames; the two layers stay independent.
- **Shot-boundary detection via background subtractor / optical flow** — Phase 2.6+ if needed.
- **Real-video fixture-based tests** — synthetic np.array frames are deterministic, fast, and sufficient; fixture mp4s add repo weight for no signal.
- **Phase 4+** — ASR↔OCR BOTH merge, SRT/VTT formatters, batch mode, LLM cleanup — untouched.

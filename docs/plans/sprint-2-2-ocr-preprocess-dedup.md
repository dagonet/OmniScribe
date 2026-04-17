# OmniScribe — Sprint 2.2: Preprocessor + Deduplicator

**Tier:** T3
**Team:** python-coder, code-reviewer, tester

Parent plan: `docs/plans/phase-2-ocr.md`. Sprint 2.1 must be merged first.

## Goal

Cleaner OCR input via CLAHE; collapse near-duplicate ON-SCREEN text across consecutive frames into single segments with `[start, end]` spans.

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `src/omniscribe/ocr/preprocessor.py` | `preprocess(frame: np.ndarray) -> np.ndarray`. Pure function: `cv2.cvtColor(frame, COLOR_BGR2GRAY)` → `cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(...)`. No config coupling — PaddleOCR accepts grayscale 2D arrays. | ~25 |
| `src/omniscribe/ocr/paddle_ocr.py` (edit) | Insert `preprocess(frame)` between `sample_frames` yield and `self._engine.ocr(...)`. One import + one line. | +3 |
| `src/omniscribe/ocr/deduplicator.py` | `dedup_segments(segments, threshold, min_duration) -> list[TranscriptSegment]`. Operates only on `source=="ON-SCREEN"` (SPEECH passes through in order). For each ON-SCREEN segment, `rapidfuzz.fuzz.ratio(seg.text, cluster.last.text) / 100` against active cluster tail; if ≥ `threshold` **and** within gap `≤ 2 × 1/config.ocr_sample_fps`, extend cluster's `end`. Frames with no OCR result contribute zero segments — naturally skipped by gap tolerance, no special case. Close clusters shorter than `min_duration` → drop. Emit collapsed (first-text, `start=first.start`, `end=last.end`, `confidence=mean(confidences)`). Pure function. | ~80 |
| `src/omniscribe/cli.py` (edit) | In `transcribe`, after OCR extraction and before `merge_channels`, pipe OCR segments through `dedup_segments(ocr_segments, config.dedup_similarity_threshold, config.dedup_min_duration)`. | +2 |
| `tests/test_preprocessor.py` | (a) BGR 3-channel → 2D grayscale; (b) `cv2.createCLAHE` invoked with `clipLimit=2.0, tileGridSize=(8,8)` via mock (NOT numerical contrast delta — variance on synthetic frames is unreliable); (c) shape `(H, W)` preserved. `np.zeros` / `np.full` fixtures. | ~50 |
| `tests/test_deduplicator.py` | (a) 3 identical ON-SCREEN frames collapse to `[0, 2]`; (b) similarity < threshold → 2 segments; (c) `min_duration=0.5` drops 0.2s blip; (d) SPEECH untouched, ordered; (e) interleaved SPEECH + ON-SCREEN preserves order; (f) empty input → empty output. | ~70 |
| `tests/test_cli.py` (extend) | 3 identical ON-SCREEN + 1 SPEECH → JSON has 1 collapsed ON-SCREEN + 1 SPEECH, sorted by `start`. | ~25 |

## Acceptance criteria (Sprint 2.2 only)

- [ ] Local MP4 with overlay text repeating across ~3 consecutive sampled frames → JSON has 1 collapsed ON-SCREEN segment spanning those frames (manual GPU accept).
- [ ] `OMNI_DEDUP_SIMILARITY_THRESHOLD=0.99` → minimal dedup.
- [ ] `OMNI_DEDUP_MIN_DURATION=1.0` → sub-second blips dropped.
- [ ] All unit tests green on CPU-only CI.
- [ ] `ruff format --check .` + `ruff check .` pass.

## Verification (Phase 2 complete)

```
uv run pytest -q                       # all green
uv run pytest -q -m slow               # gpu/slow cleanly deselected
uv run omniscribe transcribe sample-with-repeated-overlay.mp4 --ocr --output out.json
uv run omniscribe transcribe "https://www.tiktok.com/..." --ocr --output tt.json
```

## Out of scope

Scene-change / ROI (Phase 3). Platform filters (Phase 3). ASR↔OCR merge + `source="BOTH"` (Phase 4). SRT/VTT/MD (Phase 5).

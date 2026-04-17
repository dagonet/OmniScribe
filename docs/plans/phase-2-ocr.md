# OmniScribe — Phase 2: OCR (On-Screen Text)

**Parent plan.** Sprints:
- `docs/plans/sprint-2-1-ocr-foundation.md` (T4)
- `docs/plans/sprint-2-2-ocr-preprocess-dedup.md` (T3)

Phase 1 MVP shipped at `91bfaa7` on 2026-04-17.

## Goal

`omniscribe transcribe <src> --ocr` emits a JSON transcript interleaving `SPEECH` segments from faster-whisper with `ON-SCREEN` segments from PaddleOCR, deduplicated across consecutive frames.

## Architecture

Phase 2 adds a second signal channel to the pipeline:

```
download_video → ┬→ extract_audio → WhisperTranscriber → speech_segments
                 └→ sample_frames → PaddleOCREngine    → ocr_segments
                                                        │
                                   dedup_segments ←─────┘
                                          │
                   merge_channels(speech, ocr) → Transcript → write_json
```

- `ocr/frame_sampler.py` — fixed-interval frame extraction (driven by `config.ocr_sample_fps`). Scene-change detection is deferred.
- `ocr/paddle_ocr.py` — lazy-init `PaddleOCREngine` mirroring `WhisperTranscriber`'s class pattern. GPU-by-default.
- `ocr/preprocessor.py` (Sprint 2.2) — grayscale + CLAHE. ROI detection is deferred to Phase 3.
- `ocr/deduplicator.py` (Sprint 2.2) — `rapidfuzz` cross-frame cluster collapse.
- `output.merge_channels(speech, ocr)` — stable sort by `start`. Phase 4 merge engine (collapsing text that appears in both channels) replaces this.
- `cli.py` — `--ocr/--no-ocr` + `--ocr-language` flags; runtime merges with `OMNI_OCR_*` env.

## User decisions (plan-mode 2026-04-17)

- **Two sprints** (2.1 T4 + 2.2 T3).
- **GPU-by-default PaddleOCR** — `paddlepaddle-gpu` promoted to main dep. Architect Step 0 validates CUDA 12 coexistence with faster-whisper's torch.
- **Fixed-interval frame sampling in 2.1.** Scene-change deferred.
- **Grayscale + CLAHE preprocessor in 2.2.** ROI deferred to Phase 3.

## Critical files

- `G:\git\OmniScribe\IMPLEMENTATION_PLAN.md` — §Phase 2 deliverables list.
- `G:\git\OmniScribe\src\omniscribe\config.py` — OCR fields already present; `ocr_device` is the one new field.
- `G:\git\OmniScribe\src\omniscribe\output.py` — `TranscriptSegment.source: str` already accepts `"ON-SCREEN"` — zero breaking change.
- `G:\git\OmniScribe\src\omniscribe\asr\whisper.py` — reference pattern for lazy-init engine class (mirrored by `PaddleOCREngine`).
- `G:\git\OmniScribe\pyproject.toml` — `[gpu]` extra retired, `paddlepaddle-gpu` promoted to main, `numpy` pinned.

## Out of scope

Scene-change detection (Phase 2.5 or Phase 3). ROI detection (Phase 3 — owned by platform profiles). Platform-specific UI filters (Phase 3). ASR↔OCR merge + `source="BOTH"` (Phase 4). SRT/VTT/MD formatters (Phase 5). Batch mode, LLM cleanup, Docker, web UI, diarization, translation (Phase 5–6). Runtime CUDA→CPU fallback. `OCRResult` Pydantic mid-layer.

### Known Phase 2 limitation (accepted)

`Transcript.language` is always set from faster-whisper's `info.language`. On a silent video where the only signal is OCR, the ASR fallback (typically `"en"`) appears as `Transcript.language` even when on-screen text is in another language. Per-segment `TranscriptSegment.language` is correct. Reconciliation is Phase 4 merge-engine work.

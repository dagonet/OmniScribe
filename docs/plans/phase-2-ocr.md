# OmniScribe — Phase 2: OCR (On-Screen Text)

**Parent plan.** Sprints:
- `docs/plans/sprint-2-1-ocr-foundation.md` (T3)
- `docs/plans/sprint-2-2-ocr-preprocess-dedup.md` (T3)

Phase 1 MVP shipped at `91bfaa7` on 2026-04-17.

## Goal

`omniscribe transcribe <src> --ocr` emits a JSON transcript interleaving `SPEECH` segments from faster-whisper with `ON-SCREEN` segments from RapidOCR (ONNX Runtime), deduplicated across consecutive frames.

## Architecture

Phase 2 adds a second signal channel to the pipeline:

```
download_video → ┬→ extract_audio → WhisperTranscriber → speech_segments
                 └→ sample_frames → RapidOCREngine     → ocr_segments
                                                        │
                                   dedup_segments ←─────┘
                                          │
                   merge_channels(speech, ocr) → Transcript → write_json
```

- `ocr/frame_sampler.py` — fixed-interval frame extraction (driven by `config.ocr_sample_fps`). Scene-change detection is deferred.
- `ocr/rapid_ocr.py` — lazy-init `RapidOCREngine` mirroring `WhisperTranscriber`'s class pattern. GPU-by-default via `onnxruntime-gpu`'s CUDA provider.
- `ocr/preprocessor.py` (Sprint 2.2) — grayscale + CLAHE. ROI detection is deferred to Phase 3.
- `ocr/deduplicator.py` (Sprint 2.2) — `rapidfuzz` cross-frame cluster collapse.
- `output.merge_channels(speech, ocr)` — stable sort by `start`. Phase 4 merge engine (collapsing text that appears in both channels) replaces this.
- `cli.py` — `--ocr/--no-ocr` + `--ocr-language` flags; runtime merges with `OMNI_OCR_*` env.

## User decisions (plan-mode 2026-04-17, revised post-PaddleOCR pivot)

- **Two sprints** (2.1 T3 + 2.2 T3). Original 2.1 T4 call was driven by CUDA coexistence risk with `paddlepaddle-gpu`; RapidOCR eliminates that risk (ONNX Runtime bundles its own CUDA libs per-wheel alongside torch's bundled CUDA).
- **GPU-by-default RapidOCR** — `rapidocr` + `onnxruntime-gpu` as main deps, pure PyPI (no `[tool.uv.sources]`, no special index). CPU fallback via `params={"EngineConfig.onnxruntime.use_cuda": False}` on the same wheel.
- **Fixed-interval frame sampling in 2.1.** Scene-change deferred.
- **Grayscale + CLAHE preprocessor in 2.2.** ROI deferred to Phase 3.
- **PaddleOCR rejected (2026-04-17):** empirical `uv pip compile` probes confirmed `paddlepaddle-gpu` CU123 index has zero stable Windows wheels (only pre-release `3.0.0rc1`), and PyPI `paddlepaddle-gpu` tops at `2.6.2` (CUDA 11.8 only). See `docs/plans/sprint-2-1-step-0-preflight.md` SUPERSEDED banner for audit-trail probes.

## Critical files

- `G:\git\OmniScribe\IMPLEMENTATION_PLAN.md` — §Phase 2 deliverables list.
- `G:\git\OmniScribe\src\omniscribe\config.py` — OCR fields already present; `ocr_device` is the one new field.
- `G:\git\OmniScribe\src\omniscribe\output.py` — `TranscriptSegment.source: str` already accepts `"ON-SCREEN"` — zero breaking change.
- `G:\git\OmniScribe\src\omniscribe\asr\whisper.py` — reference pattern for lazy-init engine class (mirrored by `RapidOCREngine`).
- `G:\git\OmniScribe\pyproject.toml` — `[gpu]` extra removed (was `paddlepaddle-gpu`), `rapidocr` + `onnxruntime-gpu` + `numpy` + `rapidfuzz` added to main deps, `requires-python = ">=3.11,<3.13"` tightened for onnxruntime-gpu Windows wheel coverage.

## Out of scope

Scene-change detection (Phase 2.5 or Phase 3). ROI detection (Phase 3 — owned by platform profiles). Platform-specific UI filters (Phase 3). ASR↔OCR merge + `source="BOTH"` (Phase 4). SRT/VTT/MD formatters (Phase 5). Batch mode, LLM cleanup, Docker, web UI, diarization, translation (Phase 5–6). Runtime CUDA→CPU fallback. `OCRResult` Pydantic mid-layer.

### Known Phase 2 limitation (accepted)

`Transcript.language` is always set from faster-whisper's `info.language`. On a silent video where the only signal is OCR, the ASR fallback (typically `"en"`) appears as `Transcript.language` even when on-screen text is in another language. Per-segment `TranscriptSegment.language` is correct. Reconciliation is Phase 4 merge-engine work.

## Close-out

Phase 2 is **complete**. Shipped via two squash-merged PRs against `main`:

| Sprint | PR | SHA | Summary |
|---|---|---|---|
| 2.1 | — | `e9b18c1` | RapidOCR engine swap from PaddleOCR (Windows wheel gap); sparse frame sampler at configurable `ocr_sample_fps`; `RapidOCREngine` lazy-init wrapper; `--ocr` / `--ocr-language` CLI flags and config integration. |
| 2.2 | — | `ba8dd20` | Grayscale + CLAHE frame preprocessor; cross-frame fuzzy-match deduplicator via rapidfuzz; dedup pipeline integration before merge; all 78 tests passing. |

Net test delta at ship: 78 tests at `ba8dd20` (Phase 1's 38 + Phase 2's 40).

Follow-ups explicitly **deferred** out of Phase 2 and not yet scheduled:

- Scene-change detection (Phase 2.5 — **shipped in Phase 2.5 (`894fae2`)**).
- ROI detection for on-screen UI elements (Phase 3).
- Platform-specific UI zone filtering (Phase 3).
- ASR↔OCR merge engine with `source="BOTH"` tag (Phase 4).
- SRT/VTT/MD output formatters (Phase 4 in merged PR #5).
- Batch mode, LLM cleanup via Ollama, Docker deployment (Phase 5+).

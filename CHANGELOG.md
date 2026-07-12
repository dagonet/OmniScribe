# Changelog

All notable changes to OmniScribe will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-07-12

### Changed

- **`ocr_language` default flipped from `"en"` to `"auto"`** (#33). OCR now resolves the recognition language at runtime using the ASR-detected language. Falls back to EN when detection is unavailable or unmapped. Latin rec model preserves umlauts/accents on German/French/Spanish/etc. content.

### Added

- **ISO-639 language mapping** (#32). 50+ ISO 639-1 codes mapped to PP-OCRv4 recognition models. Latin-script languages (de/fr/es/…) → `latin` rec model; Cyrillic → `cyrillic`, Slavic → `eslav`, CJK → `ch`/`japan`/`korean`. Config field_validator accepts `"auto"`, all LangRec values, and mapped ISO codes; rejects unknown values at construction.
- **Toggleable auto-caption mask** (#32). New `ocr_mask_auto_captions` config flag (default `true`, env `OMNI_OCR_MASK_AUTO_CAPTIONS`). Caption-band zones moved from `ui_exclusion_zones` to `auto_caption_zones` on TikTok/Instagram profiles — mask them independently of UI exclusion zones.
- **CLI wiring**: `detected_language` from ASR passed to OCR engine for runtime language resolution (#32).

## [0.1.2] - 2026-07-12

### Added

- **Docker containerization** (Phase 5). Single-stage `nvidia/cuda:12.6.3-runtime-ubuntu22.04` image with Whisper `large-v3-turbo` and RapidOCR models pre-downloaded. GPU passthrough via NVIDIA Container Toolkit; CPU fallback via `OMNI_WHISPER_DEVICE=cpu`. Added missing `opencv-python-headless` dependency.
- **Playlist / channel auto-expansion in `transcribe-many`** (Sprint 8.1). Lines in the URL list that resolve to a playlist or channel are automatically expanded via yt-dlp's `extract_flat`, in feed order, before per-video processing. Mix freely with single-video URLs and local file paths in the same `urls.txt`. Sequential expansion + processing; no caching across runs (yt-dlp's `extract_flat` is metadata-only and cheap).

### Changed

- Internal: `cli.transcribe()`'s orchestration body extracted into a module-level `process_single_video()` helper so the batch command can reuse it. No behavior change for the single-video path.
- **`transcribe-many` URL list semantics** (Sprint 8.1). Lines that yt-dlp resolves to a playlist URL now auto-expand inline. Previously such lines failed at the per-video extractor with an opaque error. Existing `urls.txt` files containing single-video URLs and local file paths are unaffected.

## [0.1.1] - 2026-04-30

### Fixed

- **Windows GPU now works without a system CUDA install** (Sprints 7.2–7.4, PRs #22–#24). `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, and `nvidia-cufft-cu12` are now bundled on Windows via pip (gated `sys_platform == 'win32'`). A new module-import shim in `src/omniscribe/asr/whisper.py` registers each `nvidia/*/bin` directory and ctypes-preloads `cudart64_12.dll → cublas64_12.dll → cudnn64_9.dll + all cuDNN sub-libraries (glob "cudnn_*.dll") → cufft64_11.dll`, so both faster-whisper / CTranslate2 and onnxruntime-gpu's `CUDAExecutionProvider` (used by RapidOCR) find their dependencies at inference time. Smoke-validated end-to-end on a 41-min video at ~2.7× realtime.
- **Inclusive merge boundary for `[BOTH]` segments** (`681fa03`). `merge_channels` previously used strict `<` overlap; the loosened `≤` boundary correctly emits a single `[BOTH]` segment when speech and OCR end at the same timestamp.
- **LLM cleanup robustness** (`681fa03`). Added a model pre-warm step, carriage-return stripping in cleaned output, and configurable `keep_alive` for the Ollama client.
- **typer dep cleanup** (`0e2ab46`, PR #19). Replaced the deprecated `typer[all]` extra with `typer>=0.13`, which now bundles `rich` and `shellingham` as direct deps.

### Added

- **Caption-region masking + fuzzy frequency filter** (Sprint 7.1, PR #20). New `src/omniscribe/ocr/_text_match.py` module with `_canonical_key` / `_fuzzy_match` primitives shared between the cross-frame deduplicator and UI filter. Platform profiles for TikTok and Instagram now carry `RelativeRect` caption-band coordinates so OCR-side noise (rolling auto-captions, recurring SUBSCRIBE prompts) gets zeroed before detection. Default `fuzzy_threshold=90` (rapidfuzz `WRatio`).

### Changed

- **DeepWiki badge added to README** (`d9a98e6`).
- **Template sync** (`898ed1b`). Pulled upstream agent / settings / CLAUDE.md updates from the `claude-code-toolkit` template (`f229832 → 788902d`). No user-facing behavior change.

## [0.1.0] - 2026-04-25

First public alpha. Core pipeline ships: video acquisition (yt-dlp) →
audio extraction (ffmpeg) → ASR (faster-whisper large-v3-turbo) +
OCR (RapidOCR) → cross-frame OCR dedup → cross-source merge →
multi-format output (JSON / TXT / SRT / Markdown).

### Added

#### Phase 1 — Foundation + ASR
- Project skeleton (`pyproject.toml`, ruff, mypy, pytest) targeting Python 3.11/3.12.
- Configuration via pydantic-settings with `OMNI_*` environment-variable namespace.
- Video acquisition pipeline using yt-dlp; audio extraction via ffmpeg.
- ASR using `faster-whisper large-v3-turbo` on CUDA (CPU fallback).
- Output writers for JSON, TXT, SRT, and Markdown.

#### Phase 2 — OCR
- RapidOCR engine wrapper with CUDA / CPU device selection.
- Frame sampler with scene-change detection (Sprint 2.5; PR #3).
- Per-frame bbox aggregation: same-y-line bboxes joined into canonical
  caption strings before dedup (Sprint OCR Recall Part 1; PR #16).
- Cross-frame deduplicator clustering same-text overlays held across
  multiple sampled frames. Refactored to text-grouped clustering so
  multi-region-per-frame layouts collapse correctly (Sprint OCR Recall
  Part 2; PR #17).

#### Phase 3 — Platform profiles
- Profile system for TikTok, YouTube (incl. Shorts), Instagram Reels.
- UI text filtering (regex patterns + frequency-based) to drop platform
  chrome (`@username`, like counts, "Original Sound by …", channel pills).

#### Phase 4 — Merge engine
- `merge_channels` collapses temporally-overlapping SPEECH and OCR
  segments with WRatio similarity ≥ 0.85 into single `[BOTH]` segments;
  unmatched OCR is preserved as `[ON-SCREEN]`.
- Case-insensitive comparison via `processor=str.lower` (Sprint OCR Recall
  Part 1 risk-2 fix).

#### Phase 5 — Trust + CI
- Sprint 5.1: docs-only trust-repair pass after Phase 5 audit.
- Sprint 5.2: GitHub Actions CI (ruff format, ruff check, pytest) on push
  and PR; status badge in README.

#### Phase 6 — LLM cleanup (opt-in)
- Sprint 6.1 (PR #12): LLM cleanup infrastructure plus on-screen text
  artifact-fix prompt; opt-in via `--llm-cleanup` and `[llm]` extras.
  Requires a local Ollama server.
- Sprint 6.2 (PR #14): LLM punctuation cleanup on speech segments;
  opt-in via `--asr-cleanup`.

### Configuration
- `dedup_min_duration` defaults to `0.0` post-aggregation. Validator
  rejects negative values.
- `merge_similarity_threshold` defaults to `0.85`.
- `dedup_similarity_threshold` defaults to `0.85`.
- All `OMNI_*` env vars documented in `src/omniscribe/config.py`.

### Test suite
- 296 unit and integration tests covering ASR, OCR, dedup, UI filter,
  platform profiles, merge, output formats, LLM cleanup, and CLI plumbing.

### Known limitations
See README "Known Limitations" — OCR noise on text-heavy backgrounds and
strict-`<` boundary in `[BOTH]` emission are the two areas tracked for
post-0.1.0 work.

[0.1.1]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.1
[0.1.0]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.0

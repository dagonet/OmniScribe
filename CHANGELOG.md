# Changelog

All notable changes to OmniScribe will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.0

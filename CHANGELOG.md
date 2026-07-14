# Changelog

All notable changes to OmniScribe will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.4] - 2026-07-14

### Added

- **Eval samples manifest + fetch script** — `tests/fixtures/eval/README.md` documents three eval samples (two TikTok PHOTO posts, one TikTok VIDEO) with source URLs, fixture paths, ground-truth schema, and known-good baselines. `scripts/fetch_eval_samples.py` automates the download (gallery-dl for PHOTO, yt-dlp for VIDEO); idempotent, skips existing files.
- **Opt-in `eval` integration suite** — `tests/test_eval_integration.py` runs the full OCR pipeline against local fixtures and asserts recall >= 1.0 baseline. Gated behind the `eval` pytest marker (excluded from default runs; invoke with `uv run pytest -m eval`). The `pyproject.toml` `addopts` excludes both `integration` and `eval` markers from CI.
- **Unit tests for fetch script** — `tests/test_fetch_eval_samples.py` (5 tests): already-downloaded skip, `--sample` filter, photo-download dir creation, video-download dir creation.

## [0.2.3] - 2026-07-14

### Added

- **Coverage gate enforced in CI at 95%** — `ci.yml` now runs `pytest --cov=omniscribe --cov-report=term-missing --cov-fail-under=95`. The explicit `--cov-fail-under` flag guarantees enforcement even if `pyproject.toml`'s `[tool.coverage.report] fail_under` is not honoured by pytest-cov.
- **Error-path tests** for `audio.py` (ffprobe missing / non-zero exit / empty output / CalledProcessError without stderr), `acquire/photo.py` (`_run_gallery_dl` module + binary fallback paths), `batch.py` (state parse errors, video-ID extraction), and `merge/llm_cleanup.py` (Ollama response-shape guard branches).

### Fixed

- **pytest temp-dir artifact** — `pytest-of-*/` added to `.gitignore` so the temporary factory directory (which was landing in the repo root on this machine) no longer risks accidental tracking.

## [0.2.2] - 2026-07-14

### Added

- **`OcrEngine` protocol** (`omniscribe.ocr.protocol`) — structural `typing.Protocol` describing the OCR backend surface (`extract`, `extract_images`, `last_frame_count`); the extension seam for alternative backends (e.g. the roadmap vision-LLM engine). `RapidOCREngine` conforms.
- **`output.write_transcript(transcript, path, fmt)`** — registry-based output-format dispatcher replacing the CLI-side `match/case`; unknown formats raise `OmniScribeError`.
- **Documentation**: `docs/architecture.md` (module map, pipeline flow, layering rules, extension seams), `docs/configuration.md` (full `OMNI_*` reference with defaults, precedence, validators), `docs/adding-platforms.md` (new-platform guide) — promised by IMPLEMENTATION_PLAN.md's tree since Phase 1, now real. The project-structure tree is synced to reality.

### Changed

- **Internal: pipeline orchestration extracted** from `cli.py` into new `omniscribe.pipeline` module. API server imports from `pipeline` directly, breaking the layering violation where `api/server.py` depended on the CLI module. Test patch seams and CLI call sites now target `omniscribe.pipeline` directly (no re-export shim). `omniscribe.pipeline.process_single_video` is the supported programmatic entry point (optional `console` parameter controls rich output; the API passes none).

## [0.2.1] - 2026-07-14

### Added

- **New `transcribe-many` flags** — `--ocr-language`, `--ui-filter/--no-ui-filter`, and `--scene-change/--no-scene-change` are now available on `transcribe-many`, matching `transcribe` (#52).

### Changed

- **Internal: shared CLI options** — common option definitions are single-sourced via shared `Annotated` type aliases and a unified `_apply_cli_overrides` helper. A parity test (`test_cli_option_parity_between_transcribe_and_transcribe_many`) now fails if the two commands' common option sets drift in name, flag decls, or help text (#52).

### Fixed

- **CI green again / headless Linux imports** — rapidocr hard-depends on the full `opencv-python` wheel, which dlopens `libGL` at import; when GitHub rolled the `ubuntu-latest` runner image on 2026-07-13 (dropping libGL), `import cv2` started failing and every CI run since Sprint 9.6 was red (6 test modules failed collection). A `[tool.uv] override-dependencies` entry now removes `opencv-python` from resolution via an unsatisfiable marker, leaving `opencv-python-headless` (API-identical for rapidocr's usage) as the only `cv2` provider. Note for existing local venvs: run `uv sync` and, if `import cv2` then fails, `uv pip install --reinstall opencv-python-headless` once — uninstalling the full wheel can orphan the shared `cv2/` files.

## [0.2.0] - 2026-07-13

### Added

- **HTTP API mode** (#55) — new `omniscribe serve` command starts a FastAPI server with `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, and `GET /healthz` endpoints. Requires the `[api]` extra: `uv sync --extra api`. Single-worker executor with per-job temp directories. v1 limitations documented in README (no auth, no persistence, shutdown-hang).

## [0.1.9] - 2026-07-13

### Added

- **Speech translation** — new `--translate/--no-translate` CLI flag (both `transcribe` and `transcribe-many`) and `OMNI_WHISPER_TASK` env var expose Whisper's native `task=translate`: speech from any supported source language is transcribed directly into English. On-screen text (OCR) intentionally stays in the source language, so translated runs emit `[SPEECH]` (English) + `[ON-SCREEN]` (source) without cross-language `[BOTH]` merges. Speech segment `language` fields report `en` under translate; the top-level transcript language remains the detected source language.

## [0.1.8] - 2026-07-13

### Fixed

- **Docker image now bundles the `[photo]` extra** (gallery-dl): TikTok `/photo/` post URLs previously failed in-container because gallery-dl was not installed. Both `uv sync` layers in the Dockerfile now pass `--extra photo`. README Docker section documents the bundled extras (and that `[llm]` intentionally stays out — Ollama is host-external).

## [0.1.7] - 2026-07-13

### Added

- **Photo-mode-native pipeline** (#46): native processing of TikTok `/photo/` posts — slides + audio are downloaded via gallery-dl (new `[photo]` extra), OCR'd at native resolution (extract_images), and spread across audio duration for timestamped output. `omniscribe transcribe <TikTok-photo-URL>` and `omniscribe transcribe <local-dir>` both work. `scripts/eval_ocr.py --images DIR` for evaluation. GPU-verified: sample-1 native recall **1.0** vs 0.25 stitched (raw det boxes 320 vs 136). Closes #46.

### Fixed

- **Position-aware intra-frame dedup** (#40): `aggregate_frame_bboxes` now checks spatial overlap (axis-aligned intersection on both axes) instead of frame-wide text-only matching when deduplicating same-text detections. Same text in different columns or rows is no longer silently dropped; overlapping double-detections (RapidOCR's most common duplicate pattern) are still deduped correctly. Closes #40.

## [0.1.6] - 2026-07-13

### Added

- **RapidOCR det knobs** (#41): three optional env overrides — `OMNI_OCR_DET_LIMIT_SIDE_LEN`, `OMNI_OCR_DET_THRESH`, `OMNI_OCR_DET_BOX_THRESH` — expose RapidOCR's `Det.*` params for the #41 grid search on dense-small-text content. Defaults are None (zero behavior change) until data-driven values are chosen.
- **RapidOCR model-variant knobs** (#41 phase 2): four optional env overrides — `OMNI_OCR_DET_MODEL_TYPE`, `OMNI_OCR_DET_OCR_VERSION`, `OMNI_OCR_REC_MODEL_TYPE`, `OMNI_OCR_REC_OCR_VERSION` — expose RapidOCR's higher-capacity model variants (server / PP-OCRv5) for the #41 GPU capability probe. Defaults are None (rapidocr default: mobile / PP-OCRv4). **CH-det-lang auto-override**: when det model type is `server` or det OCR version is `PP-OCRv5`, the det language parameter is forced to `CH` because rapidocr's model registry ships det models only as `ch_*` for those variants. Part of #41.

### Notes

- #41 grid verdict: all det variants (v3/v4/v5, mobile/server) plateau on the stitched photo-post fixture; the default mobile det reads native slide images fine (56 vs ~17 boxes/slide). Bottleneck is the stitched-video representation of photo-mode posts — addressed by the photo-mode-native pipeline (next). Knobs remain as diagnostics.

## [0.1.5] - 2026-07-13

### Fixed

- **Column-aware line splitting (`aggregate_frame_bboxes`)** (#39): aggregator now retains x-extents and splits same-y-line boxes at gaps > 2.0x frame-wide mean box height; word gaps stay joined, column gutters split. New ``x_gap_tolerance_ratio`` parameter (default ``2.0``). GPU-measured: split granularity confirmed (85 → 132 segments on the infographic sample); that sample's recall remains detection-limited — small dense text never reaches OCR output (tracked in #41).
- **Greedy triple eval matching** (#39): 3-line GT texts now matchable via greedy extend-best-pair; gated to run only when singles+pairs fall below threshold. New ``_best_triple_extension`` helper. GPU-verified: sample-2 recall 0.833 → 1.0; sample-3 unchanged at 1.0.

## [0.1.4] - 2026-07-12

### Fixed

- **Multi-line eval matching**: GT texts spanning multiple visual lines now match when OCR emits per-line segments; scoring tries pairwise concatenation (both orders) of segments within a 2.0 s start-span window; precision counts pair participants via union with the existing per-segment semantics. Verified on GPU: sample-3 recall 0.5 → 1.0, precision 0.333 → 0.667.
- **Frequency-filter min-frame guard**: new ``ocr_frequency_min_frame_count`` config (default 10, env ``OMNI_OCR_FREQUENCY_MIN_FRAME_COUNT``); ``filter_by_frequency`` skips filtering below the minimum so ≤9-frame clips (photo slideshows) no longer lose all text.

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

[0.2.4]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.4
[0.2.4]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.4
[0.2.3]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.3
[0.2.2]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.2
[0.2.1]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.1
[0.2.0]: https://github.com/dagonet/OmniScribe/releases/tag/v0.2.0
[0.1.9]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.9
[0.1.8]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.8
[0.1.7]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.7
[0.1.6]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.6
[0.1.5]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.5
[0.1.4]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.4
[0.1.3]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.3
[0.1.2]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.2
[0.1.1]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.1
[0.1.0]: https://github.com/dagonet/OmniScribe/releases/tag/v0.1.0

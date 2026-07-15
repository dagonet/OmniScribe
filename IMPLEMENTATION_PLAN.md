# OmniScribe — Implementation Plan

> Extract **complete transcripts** from any video by combining speech recognition (ASR) with on-screen text extraction (OCR) — something existing tools don't do.

## The Problem

Dozens of tools transcribe the *spoken audio* of videos (ElevenLabs, Descript, TokScript, etc.). But video creators — especially on TikTok, YouTube Shorts, and Instagram Reels — heavily rely on **on-screen text overlays**: instructions, captions, labels, commentary that is never spoken aloud. Existing tools miss all of it. OmniScribe combines both sources into a single, unified transcript.

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    INPUT LAYER                       │
│  Video URL (TikTok, YouTube, Reels, Shorts, ...)    │
│  ──or──  Local video file (.mp4/.mov/.webm)         │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│               VIDEO ACQUISITION                      │
│  yt-dlp (download + metadata extraction)             │
│  Platform auto-detection                             │
│  Output: video file + metadata JSON                  │
└──────────────────────┬──────────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
┌──────────────────┐  ┌──────────────────────┐
│   AUDIO TRACK    │  │    VIDEO FRAMES      │
│   Extraction     │  │    Extraction        │
│   (ffmpeg)       │  │    (ffmpeg/OpenCV)   │
└────────┬─────────┘  └──────────┬───────────┘
         │                       │
         ▼                       ▼
┌──────────────────┐  ┌──────────────────────┐
│   ASR ENGINE     │  │    OCR ENGINE        │
│   faster-whisper │  │    RapidOCR          │
│   large-v3-turbo │  │    (GPU-accelerated) │
│   (GPU, FP16)    │  │                      │
│                  │  │  Smart frame sampling│
│  Output:         │  │  + deduplication     │
│  Timestamped     │  │  + UI filtering      │
│  speech segments │  │    (per platform)    │
│                  │  │                      │
│                  │  │  Output:             │
│                  │  │  Timestamped         │
│                  │  │  on-screen text      │
└────────┬─────────┘  └──────────┬───────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│                 MERGE ENGINE                         │
│  - Align ASR + OCR segments by timestamp             │
│  - Deduplicate (spoken captions ≈ on-screen text)    │
│  - Classify text source: [SPEECH] vs [ON-SCREEN]     │
│  - Optional: LLM post-processing for cleanup         │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                 OUTPUT LAYER                         │
│  - Plain text transcript (.txt)                      │
│  - Timestamped transcript (.json)                    │
│  - SRT/VTT subtitle file                             │
│  - Markdown with source annotations                  │
└─────────────────────────────────────────────────────┘
```

## Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| **Language** | Python 3.11 or 3.12 | Ecosystem support for ML/AI libs |
| **ASR** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (large-v3-turbo) | Up to 4x faster than openai/whisper, CTranslate2 backend, FP16/INT8 on GPU, batched inference |
| **OCR** | [RapidOCR](https://github.com/RapidAI/RapidOCR) | PP-OCR models via ONNXRuntime — lighter deps than PaddleOCR (no `paddlepaddle-gpu` wheel needed), same PP-OCRv4/v5 accuracy on scene text, 80+ languages, GPU optional |
| **Video download** | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | De-facto standard, supports hundreds of platforms, metadata extraction |
| **Video processing** | ffmpeg + OpenCV | Audio extraction, frame sampling, image preprocessing |
| **Text dedup/merge** | Custom + optional LLM | Fuzzy matching (rapidfuzz), timestamp alignment, optional local LLM for cleanup |
| **CLI framework** | [Typer](https://github.com/fastapi/typer) | Clean CLI with auto-generated help, type hints |
| **HTTP API (optional)** | FastAPI + uvicorn (`[api]` extra) | Job-based server (`omniscribe serve`) wrapping the same pipeline |
| **Config** | pydantic-settings | Typed configuration with env var support |
| **Package management** | uv | Fast, modern Python package manager |

### Why these specific choices?

**faster-whisper over openai/whisper:** The CTranslate2 backend gives near-identical accuracy at 4x speed and lower VRAM. On an RTX 4090, `large-v3-turbo` with FP16 will process a typical 60-second video in ~2-3 seconds.

**RapidOCR over PaddleOCR/EasyOCR/Tesseract:** Video overlays are "scene text" — styled fonts, colored backgrounds, animations. RapidOCR ships the same PP-OCRv4/v5 models as PaddleOCR through ONNXRuntime, which avoids pulling the heavy `paddlepaddle-gpu` wheel while keeping the accuracy advantage over Tesseract (designed for clean printed text) and EasyOCR. It also handles slanted/rotated text boxes, which is common in short-form video.

**yt-dlp over platform APIs:** No API keys required, works with public videos across hundreds of platforms, extracts metadata (description, hashtags, author) which enriches the transcript.

## Project Structure

```
omniscribe/
├── pyproject.toml              # Project config (uv), extras: [photo] [llm] [api] [dev]
├── README.md
├── LICENSE                     # MIT
├── IMPLEMENTATION_PLAN.md      # This file
├── .env.example                # Example config
├── Dockerfile                  # CUDA runtime image (models pre-downloaded, [photo] bundled)
│
├── src/
│   └── omniscribe/
│       ├── __init__.py         # __version__
│       ├── cli.py              # Typer CLI (transcribe / transcribe-many / serve) + shared option aliases
│       ├── pipeline.py         # process_single_video orchestration + output-format resolution (programmatic entry point)
│       ├── config.py           # pydantic-settings config (OMNI_* env vars)
│       ├── audio.py            # ffmpeg audio extraction + ffprobe duration
│       ├── batch.py            # transcribe-many state/resume + URL-list expansion helpers
│       ├── errors.py           # OmniScribeError (intended future hierarchy documented in docstring)
│       ├── output.py           # Transcript models + merge_channels + writers + write_transcript registry
│       │
│       ├── acquire/
│       │   ├── downloader.py   # yt-dlp wrapper
│       │   ├── photo.py        # TikTok photo-post acquisition (gallery-dl subprocess)
│       │   ├── platform.py     # Platform enum + URL-based detection
│       │   └── playlist.py     # Playlist/channel auto-expansion (extract_flat)
│       │
│       ├── api/
│       │   └── server.py       # FastAPI job server (omniscribe serve, [api] extra)
│       │
│       ├── asr/
│       │   └── whisper.py      # faster-whisper transcription (+ Windows CUDA DLL shim)
│       │
│       ├── eval/
│       │   ├── funnel.py       # OCR pipeline stage counters
│       │   ├── models.py       # GroundTruth / EvalResult
│       │   └── scoring.py      # Recall/precision scoring (pair + triple matching)
│       │
│       ├── merge/
│       │   └── llm_cleanup.py  # Opt-in Ollama cleanup for OCR/ASR segments ([llm] extra)
│       │
│       ├── ocr/
│       │   ├── _text_match.py      # Canonical-key + fuzzy-match primitives
│       │   ├── bbox_aggregator.py  # Same-line joining, column splitting, spatial dedup
│       │   ├── deduplicator.py     # Cross-frame text dedup
│       │   ├── frame_sampler.py    # Scene-change + interval frame extraction
│       │   ├── preprocessor.py     # Image preprocessing
│       │   ├── protocol.py         # OcrEngine Protocol (backend extension seam)
│       │   ├── rapid_ocr.py        # RapidOCR (ONNXRuntime) engine
│       │   └── ui_filter.py        # Platform UI filtering (zones + patterns + frequency)
│       │
│       └── platforms/
│           ├── base.py         # PlatformProfile dataclass (zones, patterns)
│           ├── registry.py     # Profile selection (auto-detect, generic fallback)
│           ├── tiktok.py       # TikTok UI regions & patterns
│           ├── youtube.py      # YouTube/Shorts UI regions
│           └── instagram.py    # Instagram Reels UI regions
│
├── scripts/
│   └── eval_ocr.py             # Standalone eval harness (video or --images vs ground truth)
│
├── tests/                      # 27 test modules mirroring src (541 tests)
│   └── fixtures/eval/          # example-gt.json tracked; media + real GT gitignored
│
└── docs/
    ├── architecture.md         # Module map, pipeline flow, extension seams
    ├── configuration.md        # Full OMNI_* field/env reference + precedence
    ├── adding-platforms.md     # Guide for adding new platform profiles
    └── plans/                  # Historical sprint/phase plans (kept as project history)
```

## Implementation Phases

### Phase 1: Foundation & ASR (MVP)

**Goal:** CLI tool that downloads a video and produces a speech transcript.

**Tasks:**
1. Project scaffolding with `uv init`, pyproject.toml, basic CLI
2. `acquire/downloader.py` — yt-dlp wrapper for URL → video file + metadata
3. `acquire/platform.py` — Auto-detect platform from URL (TikTok, YouTube, Instagram, etc.)
4. Audio extraction from video via ffmpeg (subprocess or `pydub`)
5. `asr/whisper.py` — faster-whisper integration with GPU support
   - Load `large-v3-turbo` model with FP16
   - Batched inference pipeline
   - Return timestamped segments: `[{start, end, text, language, confidence}]`
6. Basic `output/formatters.py` — plain text + JSON output
7. CLI: `omniscribe transcribe <url_or_file> --output transcript.txt`
8. Unit tests for downloader, platform detection, and ASR module

**Deliverable:** `omniscribe transcribe https://tiktok.com/@user/video/123` → speech transcript

---

### Phase 2: OCR Pipeline

**Goal:** Extract on-screen text from video frames with smart sampling and deduplication.

**Tasks:**
1. `ocr/frame_sampler.py` — Intelligent frame extraction
   - **Scene change detection** (OpenCV histogram comparison or structural similarity)
   - Fallback: fixed interval sampling (e.g. every 0.5s for short videos)
   - Skip near-duplicate frames to avoid redundant OCR processing
   - Extract keyframes where text overlay changes (not just scene changes)
2. `ocr/preprocessor.py` — Frame preprocessing for OCR accuracy
   - Region-of-interest detection (text typically appears in top/center/bottom zones)
   - Contrast enhancement, denoising for styled text on busy backgrounds
3. `ocr/rapid_ocr.py` — RapidOCR (ONNXRuntime) wrapper
   - GPU-accelerated inference
   - Configurable language (default: auto-detect)
   - Return: `[{text, confidence, bbox, frame_timestamp}]`
4. `ocr/deduplicator.py` — Cross-frame text deduplication
   - Same text appearing across multiple consecutive frames → single entry with time range
   - Use rapidfuzz for fuzzy string matching (handles slight OCR variations)
   - Track text appearance/disappearance timestamps
5. Unit tests with sample frames

**Deliverable:** OCR module that extracts and deduplicates on-screen text with timestamps.

---

### Phase 3: Platform Profiles & UI Filtering

**Goal:** Platform-specific UI element filtering so OCR results only contain creator content, not app chrome.

**Tasks:**
1. `platforms/base.py` — Base platform profile interface
   ```python
   class PlatformProfile:
       name: str
       ui_exclusion_zones: list[RelativeRect]   # regions to mask before OCR
       ui_text_patterns: list[re.Pattern]        # regex patterns to filter
       frequency_threshold: float                # % of frames to consider "persistent UI"
   ```
2. `platforms/tiktok.py` — TikTok profile
   - Right sidebar (rightmost ~15%): like, comment, share, bookmark icons
   - Bottom bar (bottom ~12%): music info, username, description
   - Top bar (top ~5%): status bar artifacts from screen recordings
   - Patterns: `@username`, like/share counts (`12.3K`, `456`), music note attribution
3. `platforms/youtube.py` — YouTube / Shorts profile
   - Bottom overlay: title, channel name, subscribe button
   - Progress bar region
   - Shorts-specific: right sidebar actions, comment preview
4. `platforms/instagram.py` — Instagram Reels profile
   - Bottom bar: username, caption, audio attribution
   - Right sidebar: like, comment, share, save, remix icons
   - Top bar: Reels logo, camera icon
5. `ocr/ui_filter.py` — Apply platform profile to OCR results
   - Positional filtering via exclusion zones
   - Frequency filtering: text in >80% of frames = persistent UI
   - Pattern filtering: regex matching
   - Fallback for unknown platforms: frequency + pattern filtering only
6. `docs/adding-platforms.md` — Guide for contributors to add new profiles
7. Tests for each platform profile

**Deliverable:** Clean OCR results free of UI clutter, per platform.

**Key design principle:** Platform profiles are **data-driven configs**, not hardcoded logic. Adding a new platform means adding a new YAML/Python config with exclusion zones and patterns — no pipeline changes needed.

---

### Phase 4: Merge Engine

**Goal:** Combine ASR and OCR results into a unified, deduplicated transcript.

**Tasks:**
1. `merge/aligner.py` — Timestamp-based alignment
   - Create a unified timeline from ASR segments and OCR text appearances
   - Handle overlapping timestamps (speech + text visible simultaneously)
   - Produce merged segments with source annotation: `[SPEECH]`, `[ON-SCREEN]`, `[BOTH]`
2. `merge/dedup.py` — Cross-source deduplication
   - Many creators display captions that match their speech verbatim
   - Detect when OCR text ≈ ASR text (fuzzy match, normalized)
   - When they match: keep as single entry tagged `[BOTH]`, prefer ASR text (usually cleaner)
   - When they differ: keep both with source annotations
3. Output model finalization
   - `TranscriptSegment`: `{start, end, text, source, confidence, metadata}`
   - Full `Transcript`: segments + video metadata + platform + processing stats
4. Extended output formats: SRT, VTT, annotated Markdown
5. Integration tests with end-to-end pipeline

**Deliverable:** Complete pipeline producing unified transcripts.

**Deduplication strategy detail:**

```
ASR:  [0.0-3.5] "Here are three tips for better sleep"
OCR:  [0.2-4.0] "3 Tips for Better Sleep"         ← title overlay, similar to speech
OCR:  [4.0-7.0] "1. No screens 1hr before bed"    ← on-screen only, not spoken
ASR:  [4.5-8.0] "First, put away your phone..."   ← speech elaborates on point 1

Merged output:
[0.0-3.5] [BOTH]      "Here are three tips for better sleep"
[4.0-7.0] [ON-SCREEN]  "1. No screens 1hr before bed"
[4.5-8.0] [SPEECH]     "First, put away your phone..."
```

---

### Phase 5: Polish & Extensibility

**Goal:** Production-ready tool with good DX and optional advanced features.

**Tasks:**
1. `merge/llm_cleanup.py` — Optional local LLM post-processing
   - Use ollama or a local model to:
     - Fix OCR artifacts (broken words, garbled text)
     - Improve ASR punctuation and capitalization
     - Generate a clean summary paragraph from the raw transcript
   - Completely optional, disabled by default
   - Support configurable model (e.g. `llama3`, `mistral`, `gemma`)
2. Batch processing mode
   - Process multiple URLs from a file or playlist
   - Progress bar, resume on failure
3. Configuration system
   - `.env` / CLI flags / config file for: model paths, GPU device, language, output format, OCR confidence threshold, dedup similarity threshold
4. Docker support
   - Dockerfile with CUDA support for easy deployment
   - docker-compose for quick start
5. Comprehensive README with examples, GIFs, benchmarks
6. CI/CD: GitHub Actions for linting, type checking, tests

---

### Phase status

| Phase | Status | Reference |
|---|---|---|
| Phase 1 — Foundation & ASR | Complete | `docs/plans/phase-1-foundation-asr.md`; merged through Sprint 1.2 |
| Phase 2 — OCR pipeline | Complete | `docs/plans/phase-2-5-scene-change.md` (Sprint 2.5, PR #3 — `894fae2`); Sprints 2.1–2.2 merged earlier |
| Phase 3 — Platform profiles & UI filtering | Complete | Sprint 3.1 (`3d855cc`), Sprint 3.2 (`05bbe37`) |
| Phase 4 — Merge engine | Complete | `docs/plans/phase-4-merge-engine.md`; Sprint 4.1 (PR #4, `5c81ced`) and Sprint 4.2 (PR #5, `b2a89d6`) merged |
| Phase 5 — Polish & extensibility | Complete | Sprints 5.1 (PR #6, `530902f`, doc trust-repair), 5.2 (PR #7, `db3e4b1`, CI/CD), 5.3 (PR #8, `3605a19`, doc/code drift), 5.4 (PR #26, `bf4ef74`, batch processing) merged; LLM cleanup shipped via Sprints 6.1 (PR #12), 6.2 (PR #14), and `681fa03` robustness; **Docker** shipped v0.1.2 |
| Hardening & OCR-quality campaign (post-Phase-5) | Complete | Windows GPU without system CUDA (Sprints 7.2–7.4, v0.1.1); playlist/channel batch expansion (Sprint 8.1, v0.1.2); OCR language auto-resolution + caption-mask toggles (v0.1.3); eval-matching + aggregation fixes (Sprints 9.2–9.3, v0.1.4–v0.1.5); det/model diagnostic knobs (Sprints 9.4–9.5, v0.1.6); **photo-mode-native pipeline** + spatial dedup (Sprints 9.6–9.7, v0.1.7) — three-sample eval matrix at recall 1.0; Docker photo extra (v0.1.8) |
| Health pass (post-v0.2.1) | Complete | **Architecture refactor** (Sprints 10.1, PRs #62–#65, v0.2.2): `omniscribe.pipeline` extraction, `OcrEngine` protocol, `write_transcript` registry, `docs/architecture+configuration+adding-platforms.md`; **coverage gate** (Sprint 10.2, PR #67, v0.2.3): CI enforces ≥95%, total 98.40%; **eval-samples infrastructure** (Sprint 10.3, PR #69, v0.2.4): manifest + fetch script + opt-in `eval` regression suite (sample-3 recall 1.0 re-verified live post-refactor) |
| Phase 6 — Advanced features | In progress | **Speech translation** shipped v0.1.9 (Sprint 9.9, PR #53); **API mode** shipped v0.2.0 (Sprint 9.10, PR #55); playlist/channel support shipped earlier (Sprint 8.1, v0.1.2); remaining items open — see list below |

### Phase 6: Advanced Features

Shipped:

- **Playlist/channel support** (shipped Sprint 8.1, v0.1.2) — Transcribe all videos from a creator or playlist via `transcribe-many` auto-expansion
- **Speech translation** (shipped Sprint 9.9, v0.1.9) — `--translate` / `OMNI_WHISPER_TASK` use Whisper's native `task=translate` (any source language → English speech). General any-to-any transcript translation remains open (would ride the existing Ollama `[llm]` plumbing)
- **API mode** (shipped Sprint 9.10, v0.2.0) — `omniscribe serve` FastAPI job server (`[api]` extra); v1 is local-only (no auth/persistence)

Still open, not scheduled:

- **Web UI** — Simple Gradio or Streamlit interface (an API-backed frontend now that `serve` exists)
- **Speaker diarization** — WhisperX integration for multi-speaker videos
- **Content analysis** — Sentiment, topic extraction, hashtag correlation (candidate for the existing Ollama `[llm]` plumbing)
- **Browser extension** — Transcribe while browsing TikTok/YouTube/Instagram (requires API mode — now available)
- **Twitter/X + Facebook platform profiles** — UI-filter profiles for additional platforms (tracked in README "Supported Platforms"; pipeline is already platform-agnostic)
- **YouTube chapters mode** — segment the transcript by detected chapters on long videos; yt-dlp already exposes chapter metadata (T2-T3)
- **v3-EN-det retirement measurement** — the default "EN det" OCR model is actually a PP-OCRv3 mobile model; a GPU A/B (CH-v4 det on eval samples 2/3) could yield free quality and retire the v3 routing (T2, one GPU session)
- **Instagram carousel support** — extend `is_photo_post` for IG carousels; gallery-dl already handles the extractor, the whole photo-mode-native pipeline is reused (T2-T3)
- **API v1 hardening** — deliberate v1 non-goals, revisit on demand: auth, job persistence, cancellation, and `JobRequest` option parity with the CLI (`ui_filter`/`scene_change`/LLM-cleanup fields were an explicit Sprint 9.11 carveout). Groundwork exists since v0.2.2: `errors.py` documents the intended `AcquireError`/`TranscriptionError`/`OcrError` hierarchy to implement alongside API error-kind surfacing
- **Vision-LLM OCR backend** — alternative engine (e.g. Qwen2.5-VL, Llama 3.2 Vision) for stylized text; promoted from Open Questions now that the extension seam exists: implement the `OcrEngine` protocol (`ocr/protocol.py`, v0.2.2 — structural, no inheritance needed). Needs new eval samples first — the current three-sample matrix is already at recall 1.0, so this only pays on harder content classes
- **Graceful audio-less video handling + download format hardening** — pipeline currently dies with raw "ffmpeg failed: Error opening output files: Invalid argument" when a video has no audio stream (extract_audio); TikTok bytevc1/1080p format variants download video-only despite yt-dlp metadata claiming aac. Wanted: skip ASR channel gracefully (like photo posts without audio) + `download_video` format selection that verifies/repairs audio (probe + fallback format)
- **Frequency filter vs persistent real content** — on long videos with a persistent title banner (eval sample-6, 8:51), `filter_by_frequency` drops it as UI chrome (funnel 172→76 segments); persistent REAL content is indistinguishable from chrome by frequency alone. Needs scene-aware or position-aware exemption design. Evidence: eval sample-6 required-recall capped ~0.6 with title unmatched
- **OCR language resolution for speech-less posts** — `ocr_language="auto"` resolves via the ASR-detected language, but music-only photo posts give Whisper nothing to detect (defaults to "en"), so German text gets the EN rec model and loses umlauts (measured: full-pipeline run of eval sample-5 emitted "WORUBER DU DIR" / "WIRD VOLLIG VERGESSEN SEIN"; the eval harness scores 1.0 only because it passes the GT language directly). Wanted: language fallback that doesn't depend on speech — e.g. script/diacritic detection on an OCR sample, platform/user hint, or config default per run
- **Template sync** *(dev-infra)* — pull the current claude-code-toolkit template to restore `hooks/run-gate.sh` (referenced by CLAUDE.md's gate rule but absent from this repo)
- **Docker CI build job** *(dev-infra)* — add a build-only GitHub Actions job for the Dockerfile; image changes are currently verified by inspection only

## Configuration Model

See `src/omniscribe/config.py` (`OmniScribeConfig`) for the authoritative field list; the `OMNI_` env prefix maps `OMNI_WHISPER_MODEL` → `whisper_model` etc.

## CLI Interface

```bash
# Basic usage — transcribe from URL (platform auto-detected)
omniscribe transcribe https://www.tiktok.com/@user/video/123456
omniscribe transcribe https://www.youtube.com/watch?v=abc123
omniscribe transcribe https://www.instagram.com/reel/xyz789

# From local file
omniscribe transcribe ./video.mp4

# With options
omniscribe transcribe <url> \
  --output transcript.json \
  --format json \               # json | txt | srt | md
  --language de \               # force ASR language (--language en, de, ...)
  --ocr-language en \           # RapidOCR LangRec value (en, ch, japan, ...)
  --platform tiktok \           # override platform auto-detect
  --ocr / --no-ocr \            # toggle OCR channel
  --ui-filter / --no-ui-filter \  # toggle zone/pattern/frequency chrome filters
  --scene-change / --no-scene-change \  # toggle OCR frame-sampler scene-change mode
  --translate                   # speech → English (Whisper task=translate); OCR stays source-language

# Batch — one URL / path / playlist per line, resume-on-failure
omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md

# HTTP API server (requires the [api] extra)
omniscribe serve --host 127.0.0.1 --port 8000
```

## Key Technical Decisions

### Platform Profile System

Each supported platform gets a profile that defines:
- **UI exclusion zones** — Relative screen regions (e.g. "right 15%, bottom 12%") where app UI lives
- **Text patterns** — Regex patterns for UI text (usernames, counts, attribution)
- **Frequency threshold** — Text appearing in >N% of frames is treated as persistent UI

Profiles are additive: unknown platforms still get baseline filtering (frequency + common patterns). Adding a new platform requires no pipeline changes — just a new profile config.

### Frame Sampling Strategy

Short-form videos (15s–3min) allow dense sampling. The strategy:

1. **Scene change detection first** — Use OpenCV's `cv2.absdiff` or structural similarity (SSIM) between consecutive frames. When a scene change is detected, always sample that frame.
2. **Text region change detection** — Even within a scene, text overlays can appear/disappear. Compare the text regions (top 20%, center band, bottom 20%) separately.
3. **Minimum interval** — Never sample more than 4 frames/second (diminishing returns for OCR).
4. **Maximum gap** — Always sample at least every 2 seconds, even if no change detected.

For longer videos (YouTube), sampling is sparser by default but configurable.

### ASR ↔ OCR Deduplication

This is the trickiest part. Video creators often:
- Display captions that exactly match speech → deduplicate
- Display abbreviated/paraphrased text → fuzzy match needed
- Display unrelated text (hashtags, CTAs) → keep as separate OCR entry
- Use text-to-speech where displayed text IS the script → deduplicate

Strategy:
1. For each OCR segment, find temporally overlapping ASR segments
2. Compute normalized fuzzy similarity (rapidfuzz.fuzz.token_sort_ratio)
3. If similarity > threshold (default 0.85): merge as `[BOTH]`
4. If 0.5 < similarity < 0.85: keep both, flag as `[RELATED]`
5. If similarity < 0.5: keep both as independent entries

## Dependencies

```toml
[project]
name = "omniscribe"
description = "Extract complete video transcripts — speech AND on-screen text"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "faster-whisper>=1.1.0",
    "rapidocr>=2.0",               # ONNXRuntime-backed PP-OCR (replaces paddleocr)
    "yt-dlp>=2024.0",
    "opencv-python-headless>=4.9",
    "typer>=0.13",                 # 0.13+ bundles rich + shellingham; [all] extra dropped
    "pydantic-settings>=2.0",
    "rapidfuzz>=3.0",
    "rich>=13.0",                  # pretty terminal output
]

[project.optional-dependencies]
llm = ["ollama>=0.4"]
dev = [
    "pytest>=8.0",
    "pytest-cov",
    "ruff",
    "mypy",
]

[project.scripts]
omniscribe = "omniscribe.cli:app"
```

## Hardware Requirements

| Configuration | VRAM | Performance (60s video) |
|---|---|---|
| **RTX 4090 (recommended)** | 24 GB | ~5-8 seconds total |
| RTX 3070/3080 | 8-10 GB | ~12-20 seconds total |
| CPU only | — | ~60-120 seconds total |

The RTX 4090 can comfortably run faster-whisper (large-v3-turbo, ~1.5 GB VRAM) and RapidOCR (~0.5 GB VRAM via ONNXRuntime) simultaneously, leaving plenty of headroom.

## License

MIT — open source, free to use and modify.

## Open Questions

Still open:

- [ ] Should the merge engine surface a unified per-segment confidence/uncertainty flag? (Segments already carry raw confidences — ASR `avg_logprob`, OCR detection confidence — but nothing interprets them.)

Resolved:

- [x] Caching strategy for models — **lazy download on first use** locally (faster-whisper/RapidOCR default); the Docker image pre-downloads both at build time (v0.1.2).
- [x] Platform profile format — **Python classes** (`src/omniscribe/platforms/`); settled since Phase 3, no YAML need has appeared.
- [x] YouTube "chapters" mode — answered yes in principle; promoted to the Phase 6 backlog list above (not scheduled).
- [x] Vision-LLM OCR backend — answered yes in principle; the `OcrEngine` protocol (v0.2.2) is the implementation seam. Promoted to the Phase 6 backlog list above (not scheduled — needs new eval samples first).

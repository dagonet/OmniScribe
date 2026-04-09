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
│   faster-whisper │  │    PaddleOCR         │
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
| **Language** | Python 3.11+ | Ecosystem support for ML/AI libs |
| **ASR** | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (large-v3-turbo) | Up to 4x faster than openai/whisper, CTranslate2 backend, FP16/INT8 on GPU, batched inference |
| **OCR** | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Best accuracy on scene text / overlays, GPU-accelerated, handles rotated/styled text well, 80+ languages |
| **Video download** | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | De-facto standard, supports hundreds of platforms, metadata extraction |
| **Video processing** | ffmpeg + OpenCV | Audio extraction, frame sampling, image preprocessing |
| **Text dedup/merge** | Custom + optional LLM | Fuzzy matching (rapidfuzz), timestamp alignment, optional local LLM for cleanup |
| **CLI framework** | [Typer](https://github.com/fastapi/typer) | Clean CLI with auto-generated help, type hints |
| **Config** | pydantic-settings | Typed configuration with env var support |
| **Package management** | uv | Fast, modern Python package manager |

### Why these specific choices?

**faster-whisper over openai/whisper:** The CTranslate2 backend gives near-identical accuracy at 4x speed and lower VRAM. On an RTX 4090, `large-v3-turbo` with FP16 will process a typical 60-second video in ~2-3 seconds.

**PaddleOCR over EasyOCR/Tesseract:** Video overlays are "scene text" — styled fonts, colored backgrounds, animations. PaddleOCR's PP-OCRv5 handles this significantly better than Tesseract (designed for clean printed text) and is faster than EasyOCR on GPU. It also handles slanted/rotated text boxes, which is common in short-form video.

**yt-dlp over platform APIs:** No API keys required, works with public videos across hundreds of platforms, extracts metadata (description, hashtags, author) which enriches the transcript.

## Project Structure

```
omniscribe/
├── pyproject.toml              # Project config (uv/pip)
├── README.md
├── LICENSE                     # MIT
├── IMPLEMENTATION_PLAN.md      # This file
├── .env.example                # Example config
│
├── src/
│   └── omniscribe/
│       ├── __init__.py
│       ├── cli.py              # Typer CLI entry point
│       ├── config.py           # pydantic-settings config
│       ├── pipeline.py         # Main orchestration pipeline
│       │
│       ├── acquire/
│       │   ├── __init__.py
│       │   ├── downloader.py   # yt-dlp wrapper
│       │   ├── metadata.py     # Video metadata extraction
│       │   └── platform.py     # Platform detection & profiles
│       │
│       ├── asr/
│       │   ├── __init__.py
│       │   └── whisper.py      # faster-whisper transcription
│       │
│       ├── ocr/
│       │   ├── __init__.py
│       │   ├── frame_sampler.py    # Smart frame extraction
│       │   ├── preprocessor.py     # Image preprocessing
│       │   ├── paddle_ocr.py       # PaddleOCR wrapper
│       │   ├── deduplicator.py     # Cross-frame text dedup
│       │   └── ui_filter.py        # Platform UI element filtering
│       │
│       ├── platforms/
│       │   ├── __init__.py
│       │   ├── base.py         # Base platform profile
│       │   ├── tiktok.py       # TikTok UI regions & patterns
│       │   ├── youtube.py      # YouTube/Shorts UI regions
│       │   └── instagram.py    # Instagram Reels UI regions
│       │
│       ├── merge/
│       │   ├── __init__.py
│       │   ├── aligner.py      # Timestamp-based alignment
│       │   ├── dedup.py        # ASR↔OCR deduplication
│       │   └── llm_cleanup.py  # Optional LLM post-processing
│       │
│       └── output/
│           ├── __init__.py
│           ├── formatters.py   # txt, json, srt, vtt, md
│           └── models.py       # Pydantic output models
│
├── tests/
│   ├── conftest.py
│   ├── test_pipeline.py
│   ├── test_asr.py
│   ├── test_ocr.py
│   ├── test_merge.py
│   ├── test_platforms.py
│   └── fixtures/               # Sample video clips, expected outputs
│
└── docs/
    ├── architecture.md
    ├── configuration.md
    └── adding-platforms.md     # Guide for adding new platform profiles
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
3. `ocr/paddle_ocr.py` — PaddleOCR wrapper
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

### Phase 6 (Future): Advanced Features

Ideas for later, not in initial scope:

- **Web UI** — Simple Gradio or Streamlit interface
- **Speaker diarization** — WhisperX integration for multi-speaker videos
- **Translation** — Transcript translation to other languages
- **Content analysis** — Sentiment, topic extraction, hashtag correlation
- **API mode** — FastAPI server for integration with other tools
- **Browser extension** — Transcribe while browsing TikTok/YouTube/Instagram
- **Playlist/channel support** — Transcribe all videos from a creator or playlist

## Configuration Model

```python
class OmniScribeConfig(BaseSettings):
    # ASR
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"            # cuda, cpu, auto
    whisper_compute_type: str = "float16"   # float16, int8_float16, int8
    whisper_language: str | None = None      # None = auto-detect
    whisper_batch_size: int = 16

    # OCR
    ocr_engine: str = "paddleocr"
    ocr_languages: list[str] = ["en", "de"]
    ocr_confidence_threshold: float = 0.6
    ocr_frame_interval: float = 0.5         # seconds between sampled frames
    ocr_use_scene_detection: bool = True

    # Platform
    platform_auto_detect: bool = True
    platform_override: str | None = None    # force a specific platform profile
    ui_filter_enabled: bool = True

    # Merge
    dedup_similarity_threshold: float = 0.85  # fuzzy match threshold
    dedup_strategy: str = "prefer_asr"        # prefer_asr, prefer_ocr, keep_both

    # LLM (optional)
    llm_cleanup_enabled: bool = False
    llm_model: str = "llama3"
    llm_base_url: str = "http://localhost:11434"  # ollama default

    # Output
    output_format: str = "json"              # json, txt, srt, vtt, md
    include_metadata: bool = True
    include_confidence_scores: bool = False

    # General
    temp_dir: str = "/tmp/omniscribe"
    keep_temp_files: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_prefix="OMNI_")
```

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
  --format json \
  --language de \
  --ocr-languages en,de \
  --platform tiktok          # override auto-detection
  --no-ocr                   # speech-only mode
  --no-asr                   # OCR-only mode
  --llm-cleanup              # enable LLM post-processing

# Batch mode
omniscribe batch urls.txt --output-dir ./transcripts/

# Model management
omniscribe models download    # download whisper + paddleocr models
omniscribe models list        # show installed models

# Platform profiles
omniscribe platforms list     # show available platform profiles
omniscribe platforms show tiktok  # show details of a profile
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
version = "0.1.0"
description = "Extract complete video transcripts — speech AND on-screen text"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
    "faster-whisper>=1.1.0",
    "paddleocr>=2.9",
    "paddlepaddle-gpu>=3.0",      # or paddlepaddle for CPU
    "yt-dlp>=2024.0",
    "opencv-python-headless>=4.9",
    "typer[all]>=0.12",
    "pydantic-settings>=2.0",
    "rapidfuzz>=3.0",
    "rich>=13.0",                  # pretty terminal output
    "pydub>=0.25",                 # audio processing
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

The RTX 4090 can comfortably run faster-whisper (large-v3-turbo, ~1.5 GB VRAM) and PaddleOCR (~0.5 GB VRAM) simultaneously, leaving plenty of headroom.

## License

MIT — open source, free to use and modify.

## Open Questions

- [ ] Should the OCR module support a "vision LLM" backend (e.g. Qwen2.5-VL, Llama 3.2 Vision) as an alternative to PaddleOCR for higher accuracy on stylized text?
- [ ] Caching strategy for models — pre-download on install vs. lazy download on first use?
- [ ] Should the merge engine output a "confidence score" per segment to flag uncertain results?
- [ ] Platform profile format — Python classes vs. YAML config files?
- [ ] For long YouTube videos: should there be a "chapters" mode that segments the transcript by detected chapters?

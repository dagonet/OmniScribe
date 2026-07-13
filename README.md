[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dagonet/omniscribe)
[![CI](https://github.com/dagonet/OmniScribe/actions/workflows/ci.yml/badge.svg)](https://github.com/dagonet/OmniScribe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11 | 3.12](https://img.shields.io/badge/Python-3.11_%7C_3.12-blue.svg)](https://www.python.org/downloads/)
[![Status: In Development](https://img.shields.io/badge/Status-In%20Development-orange.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

# OmniScribe

**Extract complete transcripts from any video — speech AND on-screen text, combined.**

Existing transcription tools only capture what's *spoken*. But video creators — on TikTok, YouTube, Instagram, and beyond — pack critical information into **on-screen text overlays**: instructions, captions, labels, commentary that never appears in audio-only transcripts. OmniScribe combines **speech recognition (ASR)** with **on-screen text extraction (OCR)** to produce a unified, timestamped transcript that captures *everything*.

## How It Works

```
Video URL (TikTok, YouTube, Reels, Shorts, ...) or local file
        │
        ├──▶ Audio ──▶ faster-whisper (large-v3-turbo) ──▶ Speech transcript
        │
        └──▶ Frames ──▶ RapidOCR (GPU via ONNXRuntime) ──▶ On-screen text
                                                    │
                              ┌──────────────────────┘
                              ▼
                    Merge + Deduplicate
                              │
                              ▼
                   Unified Transcript
              [SPEECH] + [ON-SCREEN] + [BOTH]
```

## Quick Start

```bash
# Install
uv pip install omniscribe

# Transcribe a TikTok
omniscribe transcribe https://www.tiktok.com/@user/video/123456

# YouTube video
omniscribe transcribe https://www.youtube.com/watch?v=abc123

# Instagram Reel
omniscribe transcribe https://www.instagram.com/reel/xyz789

# Local file
omniscribe transcribe ./video.mp4 --format json --output transcript.json

# Speech-only (no OCR)
omniscribe transcribe <url> --no-ocr

# SubRip subtitles
omniscribe transcribe ./video.mp4 --format srt --output transcript.srt

# Markdown digest
omniscribe transcribe ./video.mp4 --format md --output transcript.md

# LLM-cleaned OCR (opt-in; requires `uv sync --extra llm` + running Ollama)
omniscribe transcribe ./video.mp4 --ocr --llm-cleanup --output transcript.json

# LLM punctuation cleanup on speech segments (opt-in; same extras + Ollama)
omniscribe transcribe ./video.mp4 --llm-cleanup --asr-cleanup --output transcript.md

# Batch — one URL per line in urls.txt; outputs land in transcripts/
omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md

# Batch a whole YouTube channel or playlist (auto-expanded inline)
echo "https://www.youtube.com/@channel/videos" > urls.txt
omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md
```

Playlist + channel URLs in the URL list are automatically expanded via yt-dlp;
mix freely with single-video URLs and local file paths in the same `urls.txt`.

Re-running `transcribe-many` with the same `--output-dir` resumes from
`{output_dir}/.omniscribe-batch-state.json` — completed items are skipped, and
`pending`/`failed` items are re-attempted. Delete the state file to start fresh.

## Supported Platforms

OmniScribe uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) under the hood, which supports **hundreds of platforms** out of the box. The ASR and OCR pipeline is fully platform-agnostic. Platform-specific **UI filtering profiles** (to exclude like buttons, share icons, etc. from OCR) are provided for:

- ✅ TikTok
- ✅ YouTube / YouTube Shorts
- ✅ Instagram Reels
- 🔲 Twitter/X (Phase 6 backlog)
- 🔲 Facebook (Phase 6 backlog)

Videos from any other platform work too — just without UI-specific filtering.

## Features

- **Dual extraction** — Speech (ASR) + on-screen text (OCR) combined into one transcript
- **Smart deduplication** — Detects when spoken words match displayed text, avoids duplicates
- **Platform-aware** — UI element filtering profiles for TikTok, YouTube, Instagram
- **Fully local** — All processing runs on your machine, no API keys or cloud services
- **GPU-accelerated** — Optimized for NVIDIA GPUs (CUDA), works on CPU too
- **Multiple output formats** — JSON, TXT, SRT, Markdown
- **Multilingual** — Supports 80+ languages for both speech and text recognition
- **LLM OCR cleanup (optional)** — Fix OCR artefacts on screen-text segments via a local Ollama model. Opt-in with `--llm-cleanup`. Requires `uv sync --extra llm` and a running Ollama with the configured model pulled (default `llama3.2:3b`).
- **LLM ASR punctuation cleanup (optional)** — Improve punctuation and capitalization on speech segments via a local Ollama model. Opt-in with `--asr-cleanup`. Reuses the same `[llm]` extras and Ollama host as OCR cleanup.

## TikTok Photo Posts

TikTok ``/photo/`` posts are image slideshows with optional audio. yt-dlp cannot
download these; OmniScribe uses **gallery-dl** instead.

```bash
# Install with the photo extra
uv sync --extra photo

# Transcribe a TikTok photo post (auto-detected)
omniscribe transcribe https://www.tiktok.com/@user/photo/1234567890

# Process a local directory of slides + optional audio
omniscribe transcribe ./my-photo-dir/
```

**Timestamp semantics:** When the photo post has an audio track, slides are evenly
spread across the audio duration (slide i of n gets timestamp i/n through
(i+1)/n of total duration). Without audio, each slide gets a 1-second index-based
window (slide 0: 0-1s, slide 1: 1-2s, ...). The OCR runs at native resolution on
each slide, unlike stitched-video processing where resolution is constrained by
the video codec (see #46 and #41 for benchmarks — native slides yield ~56
detection boxes vs ~17 on stitched frames).

## Known Limitations

OmniScribe is in active development (alpha). The pipeline produces a usable
combined transcript on most short-form videos, but two areas are known to
produce noisy or under-recalled output:

### OCR noise on text-heavy backgrounds

Videos with persistently visible background text — diplomas/certificates on
a wall, dense channel-branding overlays, on-set documents — produce per-frame
OCR detections that vary slightly between frames (different bounding-box
slicing, different sub-word fragments). Each variant lands in its own
canonical-text bucket, defeats cross-frame dedup, and survives the UI
frequency filter (because no single canonical string repeats often enough
to cross the threshold). The result is dozens of sub-second `[ON-SCREEN]`
artifact segments mixed in with real captions.

The real captions still cluster correctly into multi-second `[ON-SCREEN]`
segments. The noise sits alongside them.

**Workarounds today:**
- `--no-ocr` — speech-only transcript. Fastest if you don't need on-screen
  text at all.
- Post-process the JSON output: `jq '.segments |= map(select(.end - .start
  >= 1.0))'` (or equivalent) drops sub-second artifacts and keeps the
  multi-second clusters that represent real captions. The `|=` form
  preserves the wrapping object (language, source path metadata); plain
  `|` would flatten to just the filtered array.
- Tune `OMNI_OCR_MIN_CONFIDENCE` (default `0.6`) higher to suppress
  low-confidence partial detections, at the cost of also missing some real
  text.
- The `OMNI_OCR_DET_LIMIT_SIDE_LEN` / `OMNI_OCR_DET_THRESH` / `OMNI_OCR_DET_BOX_THRESH` env overrides expose RapidOCR's detection-model knobs for experimenting with dense-small-text content (defaults tuned for caption overlays). Sprint 9.5 adds model-variant overrides (`OMNI_OCR_{DET,REC}_{MODEL_TYPE,OCR_VERSION}`) for switching to higher-capacity models (server / PP-OCRv5), with an automatic CH-det-lang override when those variants are selected (registry limitation — only `ch_*` det models ship for server/v5).

## Docker

```bash
# Build
docker build -t omniscribe .

# GPU transcription
docker run --gpus all --rm -v ./output:/output omniscribe transcribe \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" -o /output/transcript.json

# CPU-only (override defaults)
docker run --rm -e OMNI_WHISPER_DEVICE=cpu -e OMNI_OCR_DEVICE=cpu omniscribe transcribe \
  ./video.mp4 -o /output/transcript.json
```

The image bundles Whisper `large-v3-turbo` (~1.5 GB) and RapidOCR models (~15 MB)
so transcription starts instantly — no model downloads at runtime. The `[photo]`
extra (gallery-dl) is included, so TikTok `/photo/` posts work in-container.
The `[llm]` extra is **not** bundled — LLM cleanup (`--llm-cleanup` / `--asr-cleanup`)
targets a host-local Ollama server and is intended for non-container installs.
GPU passthrough requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

## Requirements

- Python 3.11 or 3.12
- NVIDIA GPU with CUDA 12.x (recommended, 8+ GB VRAM). Verify: `python -c "import onnxruntime as ort; print(ort.get_available_providers())"` — should list `CUDAExecutionProvider`
- ffmpeg
- Docker 20.10+ (optional — for containerized deployment)

On Windows, CUDA 12 runtime libraries (cuda_runtime, cublas, cudnn, cufft) are bundled via pip — no separate CUDA toolkit install required. A system CUDA install, if present, is not used.

## Status

🚧 **Under active development** — See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) for the roadmap.

## License

MIT

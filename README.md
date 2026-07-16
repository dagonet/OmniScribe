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

# Speech translation: transcribe German speech as English text
omniscribe transcribe ./video.mp4 --translate --output transcript.json
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
- **Speech translation** — Translate speech from any supported language directly into English with `--translate` (uses Whisper's native `task=translate`). On-screen text stays in the source language.
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

## Translation

When using ``--translate`` (or ``OMNI_WHISPER_TASK=translate``), Whisper transcribes
source-language speech directly into English. Segment-level ``language`` fields
report ``en`` (the text language), while the top-level transcript ``language`` field
retains the detected source language — this ensures OCR language auto-resolution
still works on on-screen text, which stays in the source language. Cross-language
``[BOTH]`` merges do not fire under translation (WRatio < 0.85 between English
speech and source-language OCR), so segments remain ``[SPEECH]`` + ``[ON-SCREEN]``.

## API Mode (HTTP Server)

OmniScribe provides an HTTP API for submitting transcription jobs and polling
for results. The server is single-worker (one job at a time) and uses the same
pipeline as the CLI.

```bash
# Install with the API extra
uv sync --extra api

# Start the server (default: http://127.0.0.1:8000)
omniscribe serve

# Custom host/port
omniscribe serve --host 127.0.0.1 --port 9000
```

### Endpoints

```bash
# Health check
curl http://127.0.0.1:8000/healthz
# {"status":"ok","version":"..."}   # reports the installed omniscribe package version

# Submit a job
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"source": "https://www.youtube.com/watch?v=abc123"}'
# {"job_id":"a1b2c3d4e5f6..."}

# With overrides (same flags as the CLI)
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"source": "video.mp4", "language": "de", "translate": true, "ocr": false}'

# Poll for results
curl http://127.0.0.1:8000/jobs/a1b2c3d4e5f6...
# {"id":"a1b2c3...","source":"...","status":"done","result":{...}}

# List all jobs (summary only)
curl http://127.0.0.1:8000/jobs
# [{"id":"a1b2c3...","source":"...","status":"done","created_at":"..."}]
```

### Security

The API has **no authentication** and triggers downloads of arbitrary URLs. It
binds to `127.0.0.1` by default. **Do not expose it publicly** — bind to
localhost or use a reverse proxy with authentication.

### v1 Limitations

- **No persistence**: restarting the server loses all in-progress and completed
  jobs. Results should be saved externally by the caller.
- **Shutdown hang**: Ctrl+C blocks until the current job finishes (non-daemon
  threads). In-flight jobs are lost — there is no graceful handoff.
- **No cancellation**: once submitted, a job runs to completion or failure.
- **Single worker**: one GPU means one job at a time.
- **JSON output only**: the API always returns JSON results regardless of the
  CLI's ``--format`` flag.
- **Poll-based**: no SSE, no webhooks — poll ``GET /jobs/{id}``.

## Known Limitations

OmniScribe is in active development (alpha). The pipeline produces a usable
combined transcript on most short-form videos. The most visible current
constraint is documented below; the full set of tracked limitations and
planned improvements lives in [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
under **Phase 6 — Advanced Features** (single source of truth for what is
being worked on).

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
- The `OMNI_OCR_DET_LIMIT_SIDE_LEN` / `OMNI_OCR_DET_THRESH` / `OMNI_OCR_DET_BOX_THRESH` env overrides expose RapidOCR's detection-model knobs for experimenting with dense-small-text content (defaults tuned for caption overlays). Model-variant overrides (`OMNI_OCR_{DET,REC}_{MODEL_TYPE,OCR_VERSION}`) switch to higher-capacity models (server / PP-OCRv5), with an automatic CH-det-lang override when those variants are selected (registry limitation — only `ch_*` det models ship for server/v5). `OMNI_OCR_DET_LANG` (`en` | `ch` | `multi`) selects the detection model independently of the recognition language; the default `en` (`en_PP-OCRv3_det_mobile`) is retained after a Sprint 13 A/B, and `multi` (`multi_PP-OCRv3_det_mobile`, the multilingual detector) is an opt-in for hard / low-recall latin-script content — it trades ~3–5× more raw detections for a small quality edge, so it is not the default (see [`docs/plans/2026-07-16-ocr-det-ab.md`](docs/plans/2026-07-16-ocr-det-ab.md)).

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

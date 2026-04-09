# OmniScribe

**Extract complete transcripts from any video — speech AND on-screen text, combined.**

Existing transcription tools only capture what's *spoken*. But video creators — on TikTok, YouTube, Instagram, and beyond — pack critical information into **on-screen text overlays**: instructions, captions, labels, commentary that never appears in audio-only transcripts. OmniScribe combines **speech recognition (ASR)** with **on-screen text extraction (OCR)** to produce a unified, timestamped transcript that captures *everything*.

## How It Works

```
Video URL (TikTok, YouTube, Reels, Shorts, ...) or local file
        │
        ├──▶ Audio ──▶ faster-whisper (large-v3-turbo) ──▶ Speech transcript
        │
        └──▶ Frames ──▶ PaddleOCR (GPU) ──▶ On-screen text
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

# OCR-only (no speech)
omniscribe transcribe <url> --no-asr

# Batch mode
omniscribe batch urls.txt --output-dir ./transcripts/
```

## Supported Platforms

OmniScribe uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) under the hood, which supports **hundreds of platforms** out of the box. The ASR and OCR pipeline is fully platform-agnostic. Platform-specific **UI filtering profiles** (to exclude like buttons, share icons, etc. from OCR) are provided for:

- ✅ TikTok
- ✅ YouTube / YouTube Shorts
- ✅ Instagram Reels
- 🔲 Twitter/X (planned)
- 🔲 Facebook (planned)

Videos from any other platform work too — just without UI-specific filtering.

## Features

- **Dual extraction** — Speech (ASR) + on-screen text (OCR) combined into one transcript
- **Smart deduplication** — Detects when spoken words match displayed text, avoids duplicates
- **Platform-aware** — UI element filtering profiles for TikTok, YouTube, Instagram
- **Fully local** — All processing runs on your machine, no API keys or cloud services
- **GPU-accelerated** — Optimized for NVIDIA GPUs (CUDA), works on CPU too
- **Multiple output formats** — JSON, plain text, SRT, VTT, Markdown
- **Multilingual** — Supports 80+ languages for both speech and text recognition
- **Optional LLM cleanup** — Use a local LLM (via ollama) to polish the transcript

## Requirements

- Python 3.11+
- NVIDIA GPU with CUDA (recommended, 8+ GB VRAM)
- ffmpeg

## Status

🚧 **Under active development** — See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) for the roadmap.

## License

MIT

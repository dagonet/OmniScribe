# OmniScribe Architecture

System-level overview of how OmniScribe transcribes videos by combining
Automatic Speech Recognition (ASR) with Optical Character Recognition (OCR),
then merging the two streams into a single transcript.

## Overview

OmniScribe takes a video URL or local file, downloads it if needed, extracts
audio and transcribes it via Whisper (ASR), optionally samples frames and runs
OCR to capture on-screen text, then merges both streams into a unified
transcript in JSON, TXT, SRT, or Markdown format. A platform-profile system
tunes OCR behaviour per source (TikTok, YouTube, Instagram) by defining UI
exclusion zones, auto-caption bands, and text patterns.

## Module map

| Module | Responsibility |
|---|---|
| `omniscribe/cli.py` | Typer CLI entry points: `transcribe`, `transcribe-many`, `serve` |
| `omniscribe/pipeline.py` | Orchestration logic — routes video/photo sources through the ASR/OCR/merge chain |
| `omniscribe/config.py` | Pydantic-settings config loaded from `OMNI_` env vars and `.env` file |
| `omniscribe/output.py` | `Transcript` / `TranscriptSegment` models, format writers, `merge_channels` cross-source dedup |
| `omniscribe/errors.py` | `OmniScribeError` base class and intended hierarchy |
| `omniscribe/batch.py` | Batch state dataclass, URL parsing, output-path collision resolution |
| `omniscribe/audio.py` | Audio extraction (16 kHz mono WAV) via subprocess ffmpeg |
| `omniscribe/acquire/downloader.py` | yt-dlp wrapper for video URL download; local-file passthrough |
| `omniscribe/acquire/platform.py` | `Platform` enum + URL keyword detection |
| `omniscribe/acquire/photo.py` | Photo-post download via gallery-dl and local-directory scanning |
| `omniscribe/acquire/playlist.py` | YouTube playlist / channel URL expansion |
| `omniscribe/asr/whisper.py` | Faster-Whisper wrapper with lazy model init, batched inference, Windows DLL preload |
| `omniscribe/ocr/protocol.py` | `OcrEngine` typing.Protocol for swappable backends |
| `omniscribe/ocr/rapid_ocr.py` | RapidOCR engine wrapper — frame sampling, preprocessing, zone masking, OCR inference, bbox aggregation |
| `omniscribe/ocr/frame_sampler.py` | Video frame sampling with optional scene-change detection |
| `omniscribe/ocr/preprocessor.py` | Frame-to-grayscale conversion for OCR |
| `omniscribe/ocr/ui_filter.py` | Zone masking, regex-pattern filtering, frequency-based UI chrome suppression |
| `omniscribe/ocr/deduplicator.py` | Same-source OCR segment deduplication |
| `omniscribe/ocr/bbox_aggregator.py` | Per-frame bounding-box y-line grouping and left-to-right text joining |
| `omniscribe/ocr/_text_match.py` | Internal canonical-key and fuzzy-match helpers |
| `omniscribe/merge/llm_cleanup.py` | Ollama-backed per-segment OCR artefact repair and ASR punctuation cleanup |
| `omniscribe/platforms/base.py` | `RelativeRect` and `PlatformProfile` frozen dataclasses |
| `omniscribe/platforms/registry.py` | Profile registry + `resolve_profile` from config + source URL |
| `omniscribe/platforms/tiktok.py` | TikTok profile: UI exclusion zones, auto-caption band, patterns |
| `omniscribe/platforms/youtube.py` | YouTube Shorts profile |
| `omniscribe/platforms/instagram.py` | Instagram Reels profile |
| `omniscribe/api/server.py` | FastAPI app with single-worker job queue, per-job temp dirs |
| `omniscribe/eval/funnel.py` | `FunnelCounts` — stage-wise segment count diagnostics |
| `omniscribe/eval/models.py` | Evaluation datamodels |
| `omniscribe/eval/scoring.py` | Scoring metrics |

## Pipeline flow

```
                   ┌─────────────────────────────────────┐
                   │         Entry points                 │
                   │  cli.py / api/server.py / direct     │
                   └──────────────┬──────────────────────┘
                                  │
                                  v
                   ┌──────────────────────────────┐
                   │   pipeline.process_single_   │
                   │   video(source, config,      │
                   │            output_path, …)   │
                   └──────────────┬───────────────┘
                                  │
                    ┌──────┬──────┴──────┬──────┐
                    │      │             │      │
                    v      v             v      v
               local dir? photo URL?   URL    local file
                    │      │             │      │
                    v      v             v      v
               scan_photo download_  download_  passthrough
               _dir()   photo_post() _video()   Path
                    │      │             │
                    │      v             v
                    │  extract_audio()  extract_audio()
                    │      │             │
                    └──────┴──────┬──────┘
                                  │
                                  v
                         WhisperTranscriber
                         .transcribe(audio)
                                  │
                    ─────┬────────┴────────┬─────
                         │                 │
                    OCR disabled       OCR enabled
                         │                 │
                         │        resolve_profile()
                         │                 │
                         │         RapidOCREngine
                         │         .extract() / .extract_images()
                         │          ── sample_frames()
                         │          ── preprocess()
                         │          ── mask_zones()  (exclusion + caption bands)
                         │          ── RapidOCR inference
                         │          ── aggregate_frame_bboxes()
                         │                 │
                         │         filter_by_patterns()
                         │         filter_by_frequency()
                         │         dedup_segments()
                         │                 │
                         └────┬────────────┘
                              │
                              v
                    merge_channels(speech, ocr)
                              │
                     ┌───────┴───────┐
                     │               │
              llm_cleanup_    llm_cleanup_
              ocr_segments    speech_segments
              (ON-SCREEN,     (SPEECH)
               BOTH)
                     │               │
                     └───────┬───────┘
                             │
                             v
                   Transcript(segments, language)
                             │
                             v
                   write_transcript(path, fmt)
                      dispatch to write_json /
                      write_txt / write_srt /
                      write_markdown
```

### Photo-mode routing

When `source` is a local directory, `scan_photo_dir` collects images and optional
audio. When it is a TikTok `/photo/` URL, `download_photo_post` runs gallery-dl.
The photo pipeline runs ASR on the audio (if present), then OCR on each image
(with optionally computed slide timestamps spread over the audio duration).

### Video routing

URLs go through yt-dlp; local files pass through directly. Audio is extracted to
16 kHz mono WAV via ffmpeg. The OCR pipeline samples frames at `ocr_sample_fps`
(default 1 fps), optionally via scene-change detection that reduces frames on
static shots.

### Filter / dedup / merge

The UI-filter stage runs on raw pre-dedup OCR segments: pattern matching drops
chrome text like handles and follower counts, then frequency filtering drops
persistent UI elements that appear in a large fraction of frames. The surviving
segments are deduplicated via text-similarity clustering, then cross-source merged
with speech segments using RapidFuzz `WRatio` comparison. Segments whose speech
and OCR text are similar enough become `[BOTH]`.

### LLM cleanup

Two optional post-merge passes run sequentially against a local Ollama model:
OCR-cleaning targets `ON-SCREEN` and `BOTH` segments (fixing OCR artefacts),
ASR-cleaning targets `SPEECH` segments (punctuation and capitalisation). Both
are opt-in via `--llm-cleanup` and `--asr-cleanup` (or `OMNI_LLM_CLEANUP_ENABLED`,
`OMNI_LLM_ASR_CLEANUP_ENABLED`).

## Layering rules

```
cli ──→ pipeline ──→ subsystems (asr, ocr, acquire, merge, platforms, output)
api ──→ pipeline          (no cli import)
     ──→ config
     ──→ errors
```

- No module outside `cli` imports from `cli.py`.
- Pipeline does not import from `api`.
- Subsystem modules (`asr/`, `ocr/`, `acquire/`, `merge/`, `platforms/`) do not
  import from each other — all coordination is in `pipeline`.
- The cross-module data types are `TranscriptSegment`, `Transcript`, and
  `OmniScribeConfig`.
- No circular imports exist between any two modules.

## Extension seams

| Seam | Mechanism | Key file |
|---|---|---|
| New OCR backend | Implement the `OcrEngine` Protocol (structural typing — no inheritance required) | `src/omniscribe/ocr/protocol.py` |
| New platform profile | Define a `PlatformProfile` frozen dataclass with zones and patterns; register in `PROFILES` dict | `src/omniscribe/platforms/base.py` + `registry.py` |
| New output format | Write a `write_*` function; add it to the `_writer_registry` dict in `write_transcript` | `src/omniscribe/output.py` |
| New error type | Subclass `OmniScribeError` (future hierarchy noted in docstring) | `src/omniscribe/errors.py` |

Unregistered platforms still work — `resolve_profile` returns `GENERIC_PROFILE`
(no exclusion zones, no patterns, no auto-caption bands) for any `Platform`
that maps to `UNKNOWN` or `GENERIC`.

## Threading and GPU

- The pipeline is single-threaded per call. GPU serialisation (Whisper on CUDA,
  RapidOCR on ONNX Runtime CUDA) makes concurrent processing counterproductive.
- The API server uses `ThreadPoolExecutor(max_workers=1)`, so jobs queue
  rather than competing for GPU memory.
- Each API job gets a unique temp directory (`base_temp / job_id`) to prevent
  workdir collisions across jobs.
- Windows CUDA DLL loading is handled by `asr/whisper.py` via
  `os.add_dll_directory()` and `ctypes.CDLL` preload for cublas, cudnn, and
  cufft.

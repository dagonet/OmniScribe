# OmniScribe — Sprint 1.2: Audio, ASR, Output, CLI transcribe

**Tier:** T4
**Team:** architect, coder, code-reviewer, tester

Parent plan: `docs/plans/phase-1-foundation-asr.md`. Sprint 1.1 merged at `3479621`.

## Goal

`omniscribe transcribe <url_or_file> --output <path>` produces a JSON transcript end-to-end.

## Context

This sprint adds audio extraction (ffmpeg subprocess), the faster-whisper transcriber, the transcript data model + JSON writer, and the `transcribe` CLI command. Mocks drive all external boundaries in unit tests; a real GPU smoke test on the RTX 4090 box is a manual acceptance step.

## Scope pruning (from parent plan — two-pass challenge already applied)

**Challenge 1** removed premature modules (`pipeline.py`, split output files, `--no-asr` flag, re-export façades) and fixed a `batch_size` API bug (kwarg exists only on `BatchedInferencePipeline`, not `WhisperModel.transcribe`).

**Challenge 2** cut:

- `Transcript` shrinks to `{segments, language}` — no `metadata`/`platform`/`processing_stats` until a consumer exists in Phase 4.
- `TranscriptSegment.source` is a plain `str` defaulting to `"SPEECH"` — no `Literal` until Phase 2 introduces `"ON-SCREEN"`.
- Dropped `--format txt|json` toggle — JSON only this phase; the toggle returns in Phase 5 alongside SRT/VTT/MD.
- `BatchedInferencePipeline` wrapper is mandatory for `OMNI_WHISPER_BATCH_SIZE`.

## Step 0 — Architect pre-flight (blocking)

Before dev work begins, the architect confirms via `mcp__plugin_context7_context7__query-docs` on `faster-whisper >=1.1.0`:

1. `BatchedInferencePipeline(model).transcribe(path, batch_size=N, language=None)` signature — does it accept `batch_size` and `language` kwargs identically to `WhisperModel.transcribe`?
2. Auto-detect parity — does the batched path honour `language=None` identically to the non-batched path?
3. Current `WhisperModel.__init__` parameters — which kwargs (`cpu_threads`, `num_workers`, `compute_type`, `device`) are current in ≥1.1.0 (the signature has shifted across minor releases).

Architect reports findings (one-page note posted back to the PO). Only after that do dev spawns begin. If Context7 is unavailable, the architect may fall back to the faster-whisper GitHub README at the pinned version.

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `src/omniscribe/audio.py` | Module-load `shutil.which("ffmpeg")` pre-check raising `OmniScribeError`. `extract_audio(video, out) -> Path` via `subprocess.run(["ffmpeg","-i",str(video),"-ar","16000","-ac","1","-vn","-f","wav",str(out),"-y"], check=True, capture_output=True, shell=False)`. | 50 |
| `src/omniscribe/output.py` | `TranscriptSegment(start, end, text, source="SPEECH", confidence, language)`; `Transcript(segments, language)`; `write_json(t, path)` via `t.model_dump_json(indent=2)`. | 60 |
| `src/omniscribe/asr/__init__.py` | Empty. | 0 |
| `src/omniscribe/asr/whisper.py` | `WhisperTranscriber(config)` — lazy-loads `WhisperModel` wrapped in `BatchedInferencePipeline` on first `transcribe()`; logs INFO before model init ("Loading model %s — first run may download ~1.5 GB…"). `transcribe(audio_path) -> tuple[list[TranscriptSegment], str]` — iterates `segments_gen` eagerly, returns list + `info.language`. | 80 |
| `src/omniscribe/cli.py` (update) | Add `transcribe` command: positional `source`, `--output/-o` (default `transcript.json`), `--language`. Orchestration: `download_video → extract_audio → WhisperTranscriber.transcribe → Transcript(...) → write_json`. `try/finally` that `shutil.rmtree(temp_dir)` unless `config.keep_temp_files`. Root callback stores config on `typer.Context.obj` so subcommands can reuse it (resolves the Sprint 1.1 review finding). | 50 |
| `tests/test_audio.py` | Patch `omniscribe.audio.subprocess.run` and `omniscribe.audio.shutil.which` at import site; verify ffmpeg arg list and missing-ffmpeg → `OmniScribeError`. | 40 |
| `tests/test_whisper.py` | Patch `omniscribe.asr.whisper.WhisperModel` and `omniscribe.asr.whisper.BatchedInferencePipeline` at import site; verify generator consumption, config arg flow, `info.language` propagation, lazy-load (`transcribe` first call triggers model init). | 60 |
| `tests/test_output.py` | Round-trip `Transcript → JSON → Transcript`; `write_json` creates file. | 40 |
| `tests/test_cli.py` (extend) | `CliRunner` full pipeline with every boundary mocked; temp-dir cleanup honours `keep_temp_files`; silent video (empty segments list) produces zero-segment `Transcript` without crash. | 60 |

## Design decisions locked (from parent plan)

- Audio via system `ffmpeg` subprocess (list form, `shell=False`) — no `pydub`.
- `BatchedInferencePipeline` wrapper required for batch_size.
- Generator iterated eagerly — return `list`, not the lazy generator.
- Lazy model load so `--help`, `--version`, and unit tests never probe CUDA.
- Single shared `rich.console.Console` for logs + `Progress`.
- All tests patch at the import site.
- pytest `--strict-markers`: any slow/gpu test carries its marker or CI fails.
- `opencv-python-headless` currently a main dep; Sprint 1.2 PR should note the plan to move it into an `[ocr]` extra when Phase 2 lands.

## Acceptance criteria (Sprint 1.2 only)

- [ ] `omniscribe transcribe` on a local MP4 with speech → JSON with ≥1 `SPEECH` segment.
- [ ] Silent MP4 → valid zero-segment `Transcript`, no crash.
- [ ] URL transcription end-to-end on the RTX 4090 machine (manual acceptance).
- [ ] Temp files deleted unless `--keep-temp-files` / `OMNI_KEEP_TEMP_FILES=true`.
- [ ] Missing ffmpeg raises clear `OmniScribeError`.
- [ ] First `WhisperTranscriber` load logs an INFO message before the ~1.5 GB model fetch.
- [ ] `ruff format --check .` + `ruff check .` pass with zero changes.
- [ ] `uv run pytest -q` green on CPU-only CI; `pytest -q -m slow` cleanly deselects non-slow tests.
- [ ] No network / model downloads during `pytest`.

## Out of scope

Platform UI filters (Phase 3). ASR↔OCR merge, SRT/VTT/MD formatters, `source="BOTH"|"ON-SCREEN"` (Phase 4). Batch mode, LLM cleanup, Docker, `--format` toggle (Phase 5). OCR frame sampling (Phase 2).

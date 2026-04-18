# OmniScribe — Phase 1: Foundation & ASR (MVP)

**Tier:** T3
**Team:** coder, code-reviewer, tester

> Phase 1 is sprinted as two tranches. Sprint 1.1 (below) runs at T3. Sprint 1.2 is re-tiered to T4 (team becomes architect, coder, code-reviewer, tester) — re-tag this header when Sprint 1.1 merges and Sprint 1.2 begins, or split into two files.

## Context

`IMPLEMENTATION_PLAN.md` defines a six-phase roadmap. Phase 1 delivers the **MVP**: a CLI that takes a video URL or local file, downloads it (if URL), extracts audio, transcribes speech with `faster-whisper` on GPU, and writes a JSON transcript. OCR, platform-profile UI filtering, merge engine, LLM post-processing, SRT/VTT, batch mode, and plain-text output are deferred to later phases.

Template toolkit files already applied (`.claude/`, `AGENT_TEAM.md`, `CLAUDE.md`). Phase 1 is pure greenfield scaffolding — `pyproject.toml` and `.env.example` are complete; no Python code exists yet.

**User decisions:** two sprints; mock-heavy tests with synthetic 1 s silence WAV fixture.

## Scope pruning (two rounds of challenge applied)

**Challenge 1** removed premature modules (`pipeline.py`, split output files, `--no-asr` flag, re-export façades) and fixed a `batch_size` API bug. **Challenge 2** additionally cut:

- `Transcript` shrinks to `{segments, language}` — `metadata`/`platform`/`processing_stats` return in Phase 4.
- `TranscriptSegment.source` becomes a plain `str` defaulting to `"SPEECH"` — no `Literal` until Phase 2.
- Dropped `--format txt|json` toggle — ship JSON only; `--format` returns in Phase 5.
- Replaced `version` subcommand with `--version` callback (Typer idiom).
- `download_video` returns `Path`, not a Pydantic `DownloadResult`.
- Inlined `setup_logging` into `cli.py` — dropped dedicated `logging_config.py`.

## Tier assignment

- **Sprint 1.1 — T3** (dev + reviewer + tester) ≈ 215 production LOC.
- **Sprint 1.2 — T4** (architect + dev + reviewer + tester) ≈ 240 production LOC; faster-whisper + ffmpeg integration risk warrants architect review.

Each spawn prompt must include a `## Required Skills` block per `AGENT_TEAM.md` → Spawn-Prompt Binding Table, or `hooks/require-skills-block.sh` blocks with exit 2.

## Sprint 1.1 — Scaffolding, Config, Acquire  (T3)

**Goal:** package skeleton + download + `omniscribe --version` works.

| Path | Purpose | ~Lines |
|---|---|---|
| `src/omniscribe/__init__.py` | `__version__` via `importlib.metadata.version("omniscribe")` wrapped in `try/except PackageNotFoundError → "0.0.0+unknown"` | 10 |
| `src/omniscribe/config.py` | `OmniScribeConfig(BaseSettings)` — ports fields from `IMPLEMENTATION_PLAN.md:327-368`; `env_prefix="OMNI_"`; `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`. Windows-safe `temp_dir` default via `tempfile.gettempdir()`. `@field_validator("whisper_language", mode="before")` maps `""` → `None` | 60 |
| `src/omniscribe/errors.py` | `class OmniScribeError(Exception)` — single user-facing error type | 10 |
| `src/omniscribe/acquire/__init__.py` | Empty | 0 |
| `src/omniscribe/acquire/platform.py` | `Platform` enum; `detect_platform(source: str) -> Platform` | 40 |
| `src/omniscribe/acquire/downloader.py` | `download_video(source, temp_dir) -> Path`. Dispatch: local file → passthrough; `^https?://` → yt-dlp; else `ValueError`. `outtmpl="%(id)s.%(ext)s"`. Catches `yt_dlp.utils.DownloadError` → `OmniScribeError` | 60 |
| `src/omniscribe/cli.py` | `typer.Typer()` app; `--version` callback; root callback initialises config + logging with shared `rich.console.Console` | 60 |
| `tests/__init__.py` | Empty | 0 |
| `tests/conftest.py` | Fixtures: `tmp_config`; `silence_wav_path` (1 s 16 kHz mono PCM via `wave` — comment flags it as mock-only). Autouse `reset_logging` fixture | 50 |
| `tests/test_config.py` | Env var loading, platform-aware temp_dir default, `OMNI_WHISPER_LANGUAGE=""` → `None` | 40 |
| `tests/test_platform.py` | Table-driven URL → enum | 40 |
| `tests/test_downloader.py` | Patch `omniscribe.acquire.downloader.YoutubeDL` at import site; assert URL/local/invalid branches; `DownloadError` → `OmniScribeError` | 70 |
| `.gitignore` | Append `*.ctranslate2`, keep `.pytest_cache/`. Do not add `~/.cache/huggingface/` | — |

## Sprint 1.2 — Audio, ASR, Output, CLI transcribe  (T4)

**Goal:** `omniscribe transcribe <url_or_file> --output <path>` produces a JSON transcript end-to-end.

**Step 0 (architect, pre-implementation):** Confirm via `mcp__plugin_context7_context7__query-docs` on `faster-whisper ≥1.1.0`:
1. `BatchedInferencePipeline(model).transcribe(path, batch_size=N, language=None)` signature,
2. whether batched path supports `language=None` auto-detect identically to `WhisperModel.transcribe`,
3. current `WhisperModel.__init__` parameters (`cpu_threads`, `num_workers` shifted across minors).

Only after Step 0 do dev spawns begin.

| Path | Purpose | ~Lines |
|---|---|---|
| `src/omniscribe/audio.py` | Module-load `shutil.which("ffmpeg")` pre-check raising `OmniScribeError`. `extract_audio(video, out) -> Path` via `subprocess.run(["ffmpeg","-i",str(video),"-ar","16000","-ac","1","-vn","-f","wav",str(out),"-y"], check=True, capture_output=True, shell=False)` | 50 |
| `src/omniscribe/output.py` | `TranscriptSegment(start, end, text, source="SPEECH", confidence, language)`; `Transcript(segments, language)`; `write_json(t, path)` via `model_dump_json(indent=2)` | 60 |
| `src/omniscribe/asr/__init__.py` | Empty | 0 |
| `src/omniscribe/asr/whisper.py` | `WhisperTranscriber(config)` — lazy-loads `WhisperModel` wrapped in `BatchedInferencePipeline` on first `transcribe()`; logs INFO before model init. Returns `(list[TranscriptSegment], info.language)` — iterates generator eagerly | 80 |
| `src/omniscribe/cli.py` (update) | Add `transcribe` command: positional `source`, `--output/-o`, `--language`. Orchestration: download → extract → transcribe → write_json. `try/finally` temp cleanup honouring `config.keep_temp_files`. Shared Console with `rich.progress.Progress` | 50 |
| `tests/test_audio.py` | Mock `subprocess.run` and `shutil.which` at import site; verify args; verify missing-ffmpeg raises `OmniScribeError` | 40 |
| `tests/test_whisper.py` | Patch `omniscribe.asr.whisper.WhisperModel` and `BatchedInferencePipeline` at import site; verify generator consumption, config flow, `info.language` propagation | 60 |
| `tests/test_output.py` | Round-trip `Transcript` → JSON → `Transcript`; `write_json` creates file | 40 |
| `tests/test_cli.py` | `CliRunner`: `--version`, `transcribe --help`, full pipeline with all boundaries mocked; temp-dir cleanup honours `keep_temp_files`; silent video produces zero-segment `Transcript` | 60 |

## Design decisions locked

- Audio via system `ffmpeg` subprocess (list form, `shell=False`) — no `pydub` dep for Phase 1.
- `BatchedInferencePipeline` wrapper required for `OMNI_WHISPER_BATCH_SIZE` (plain `WhisperModel.transcribe` has no such kwarg).
- Generator iterated eagerly — faster-whisper's segments are lazy; iteration drives inference. Return `list`.
- `WhisperTranscriber` class with lazy model-load so `--help` and tests don't probe CUDA.
- Single shared `rich.console.Console` so `RichHandler` and `Progress` don't corrupt each other's output.
- Windows path safety: `temp_dir` via `tempfile.gettempdir()`; yt-dlp `outtmpl="%(id)s.%(ext)s"`; `shell=False`.
- User-facing errors flow through `OmniScribeError` — no raw tracebacks for yt-dlp/ffmpeg/invalid source.
- Tests patch at import site (`omniscribe.asr.whisper.WhisperModel`, not `faster_whisper.WhisperModel`).
- `opencv-python-headless` currently a main dep — move to `[ocr]` optional extra when Phase 2 lands (note in PROJECT_STATE.md).
- pytest `--strict-markers` already in `pyproject.toml` — every slow/gpu test must carry its marker.

## Critical files

- `G:\git\OmniScribe\IMPLEMENTATION_PLAN.md` — §Config Model (325-368) is ground truth.
- `G:\git\OmniScribe\pyproject.toml` — pytest markers + `--strict-markers`; entry point `omniscribe = "omniscribe.cli:app"`.
- `G:\git\OmniScribe\.env.example` — every key maps 1:1 to a `OmniScribeConfig` field (inverse check in review).
- `G:\git\OmniScribe\AGENT_TEAM.md` — T3/T4 workflows; Spawn-Prompt Binding Table.
- `G:\git\OmniScribe\CLAUDE.md` / `CLAUDE.local.md` — ruff, snake_case, type hints, pathlib, `logging` not `print`, MCP tool-use rules.

## Libraries to reuse

`faster_whisper.WhisperModel` + `BatchedInferencePipeline`; `yt_dlp.YoutubeDL`; `typer.Typer` + `typer.testing.CliRunner`; `pydantic.BaseModel` + `pydantic_settings.BaseSettings` (+ `field_validator`); `rich.console.Console` + `rich.progress.Progress` + `rich.logging.RichHandler`; stdlib `wave`, `subprocess`, `shutil.which`, `shutil.rmtree`, `tempfile.gettempdir`, `importlib.metadata.version`, `unittest.mock.patch`.

## Verification

**After Sprint 1.1:**
```
uv sync
uv run ruff format --check . && uv run ruff check .
uv run pytest -q                     # all green; no gpu/slow tests executed
uv run omniscribe --help             # Typer usage
uv run omniscribe --version          # prints 0.1.0
```

**After Sprint 1.2 (Phase 1 complete):**
```
uv run pytest -q                     # unit tests green; markers clean
uv run pytest -q -m slow             # gpu/slow skipped cleanly on CPU CI
uv run omniscribe transcribe --help
uv run omniscribe transcribe path/to/sample.mp4 --output /tmp/out.json
uv run omniscribe transcribe "https://www.youtube.com/watch?v=<clip>" \
  --output /tmp/yt.json                                # manual, GPU machine
```

**Acceptance criteria:**
- [ ] All unit tests green on CPU-only CI; gpu/slow tests skip cleanly.
- [ ] `omniscribe transcribe` on a local MP4 with speech → JSON with ≥1 `SPEECH` segment.
- [ ] Silent MP4 → valid zero-segment `Transcript`, no crash.
- [ ] URL transcription end-to-end works on the RTX 4090 machine.
- [ ] Temp files deleted unless `--keep-temp-files` / `OMNI_KEEP_TEMP_FILES=true`.
- [ ] Missing ffmpeg raises clear `OmniScribeError`.
- [ ] Private / geo-blocked URL raises clear `OmniScribeError` (single line, no traceback).
- [ ] `OMNI_WHISPER_LANGUAGE=""` in `.env` treated as auto-detect (`None`).
- [ ] `ruff format --check .` and `ruff check .` pass with zero changes.
- [ ] No network / model downloads occur during `pytest`.
- [ ] First `WhisperTranscriber` load logs an INFO message before the ~1.5 GB model fetch.

## Out of scope

OCR / frame sampling / dedup (Phase 2). Platform UI filters (Phase 3). ASR↔OCR merge, SRT/VTT/MD formatters, `source="BOTH"|"ON-SCREEN"` (Phase 4). Batch mode, LLM cleanup, Docker, `--format` toggle, `--no-asr`/`--no-ocr` flags (Phase 5). Diarisation, translation, web UI (Phase 6). `acquire/metadata.py`, `pipeline.py`, `logging_config.py`, `DownloadResult` Pydantic model, `TranscriptSegment.source` Literal, `Transcript.metadata/platform/processing_stats` — deferred until a concrete consumer appears.

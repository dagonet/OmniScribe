# OmniScribe — Phase 4: Merge Engine

**Tier:** T3 per sprint (dev + reviewer + tester)
**Team:** coder, code-reviewer, tester

## Context

`IMPLEMENTATION_PLAN.md` defines a six-phase roadmap. Phase 4 delivers the
**merge engine** that unifies speech (ASR) and on-screen text (OCR) into a
single transcript with explicit source tags — the feature that distinguishes
OmniScribe from pure-ASR tools. Phase 4 also wires up the long-deferred output
formats beyond raw JSON.

Phase 1 (foundation + ASR), Phase 2 (OCR pipeline + scene-change sampling),
and Phase 3 (platform profiles + UI filter) are merged to `main`. Phase 5
(LLM cleanup, batch mode, Docker, CI polish) and Phase 6 (web UI, diarisation,
translation) remain out of scope.

## Goals

1. Collapse temporally-overlapping speech and OCR cues that contain the same
   text into a single `[BOTH]`-tagged segment — keeping `[SPEECH]` and
   `[ON-SCREEN]` distinct when they diverge.
2. Ship three additional output format writers (`.txt`, `.srt`, `.md`)
   alongside the existing `.json` writer, routed via a new `--format` flag.
3. Make format selection predictable via a documented precedence chain
   (flag > env > path-extension > default).

## Non-goals (deferred to Phase 5+)

- VTT subtitle output (SRT covers the same use cases for the MVP audience).
- LLM post-processing of merged cues (punctuation repair, OCR artefact
  cleanup, summary generation) — revisited in Phase 5 via Ollama.
- Batch / playlist mode.
- Cross-source segments with `[RELATED]` source (partial match): kept out to
  avoid a third category whose semantics users would have to learn.
- A generic `write_transcript(transcript, path, format=...)` dispatcher. CLI
  uses inline `match`; YAGNI until a second caller appears.

## Sprint breakdown

### Sprint 4.1 — `merge_channels` rewrite *(merged, PR #4, `5c81ced`)*

- New `output.merge_channels(speech, ocr, threshold)` with strict-overlap and
  `rapidfuzz.fuzz.WRatio` similarity; emits `[BOTH]` on collapse.
- `TranscriptSegment.source` tightened to `Literal["SPEECH","ON-SCREEN","BOTH"]`.
- New `OmniScribeConfig.merge_similarity_threshold: float = 0.85` (separate
  from the same-source dedup threshold so the two can diverge later).
- CLI pipeline re-wired: `dedup_segments(ocr)` feeds into `merge_channels(...)`
  rather than a naive `sorted(speech + ocr)`.

### Sprint 4.2 — output format writers + `--format` flag *(in review)*

- New writers in `src/omniscribe/output.py`:
  - `write_txt(transcript, path)` — one segment per line, no annotations.
  - `write_srt(transcript, path)` — 1-indexed cues, `HH:MM:SS,mmm` stamps,
    blank-line separator; newline-in-cue stripping via `_normalize_cue`.
  - `write_markdown(transcript, path)` — one line per segment in the form
    `**[SOURCE] m:ss–m:ss** text`; `|` and `` ` `` escaped.
- `OmniScribeConfig.output_format` tightened to
  `Literal["json","txt","srt","md"]` with a `mode="before"` validator that
  produces a friendly error listing allowed values (belt-and-suspenders
  over the pydantic `Literal` rejection).
- CLI gains `--format {json,txt,srt,md}` via `click.Choice`.
- New helper `_resolve_output_format(...)` implements the precedence chain:
  1. `--format` flag.
  2. `OMNI_OUTPUT_FORMAT` env var (validated value reflected from config).
  3. Output-path suffix (`.json` / `.txt` / `.srt` / `.md`).
  4. Hard default `"json"`.
- CLI dispatches with `match/case` on the resolved format — no helper
  function, no table lookup.

## Trade-offs accepted

- **Lossy on `[BOTH]` collapse.** When OCR carries richer detail than the
  matching speech cue (e.g. speech "as I mentioned" vs OCR
  "AcmeCloud Enterprise v4.2"), the merged `text` is still `speech.text`.
  Concatenating would produce awkward output; users who need the detail can
  raise `merge_similarity_threshold` to suppress marginal collapses.
- **`confidence = speech.confidence`** on `[BOTH]` segments. Whisper
  confidence is log-prob-derived; RapidOCR confidence is pixel-match-derived.
  Mixing scales with `max()` would be meaningless. Speech is the consistent
  anchor because the emitted text is speech-sourced.
- **`fuzz.WRatio` over `fuzz.token_sort_ratio`.** WRatio is the RapidFuzz
  "composite" scorer: it considers token-sort, token-set, and partial ratios
  and picks the best. That makes it more robust to word-order / substring
  differences which are common between speech and overlays.
- **SRT garbage-in-garbage-out for angle brackets.** `<` / `>` are not
  escaped: different SRT players interpret them as tags or as literal
  characters, and no escape is portable. Callers control cue content.
- **No dispatcher function** (`write_transcript(fmt, ...)`). Inline `match`
  in the CLI has one caller today; adding a dispatcher now forces a format
  registry the codebase doesn't need.
- **`M:SS` timestamps in Markdown, no hour wrap.** 60 minutes becomes
  `60:00` rather than `1:00:00`. Short-form video is the primary use case;
  long-form users still get correct seconds — just a wider minute column.
- **No VTT writer.** SRT covers subtitle use cases; VTT adds styling/cue
  settings nobody has asked for. Revisit if a consumer appears.

## Follow-ups deferred to Phase 5+

- VTT writer + `--format vtt` choice.
- LLM cleanup pass over the merged transcript
  (`merge/llm_cleanup.py`, Ollama-backed, opt-in).
- Batch mode: `omniscribe batch urls.txt --output-dir ./out/`.
- `--format` default from `OMNI_OUTPUT_FORMAT` when the env value is
  deliberately set to `"json"` — currently the hard default is reached via
  the precedence chain regardless of whether the user explicitly set the env
  to `"json"` or left it unset. Matters only for observability, not behaviour.
- Richer `[BOTH]` text (e.g. `"{speech} ({ocr extra tokens})"`) gated on a
  tuning knob — defer until users report the loss.
- Surface OCR language / confidence inside the Markdown writer when the
  segment diverges from the transcript-level language.

## Verification

```
uv run pytest -q                     # all green; ~218 tests after Sprint 4.2
uv run ruff format --check .
uv run ruff check .
uv run omniscribe transcribe path/to/sample.mp4 -o /tmp/out.srt       # SRT
uv run omniscribe transcribe path/to/sample.mp4 -o /tmp/out.md        # MD
uv run omniscribe transcribe path/to/sample.mp4 -o /tmp/out.txt       # TXT
uv run omniscribe transcribe path/to/sample.mp4 -o /tmp/out.json      # JSON
uv run omniscribe transcribe path/to/sample.mp4 -o /tmp/out.any \
  --format srt                                                        # flag wins
```

## Critical files

- `G:\git\OmniScribe\src\omniscribe\output.py` — merge + writers.
- `G:\git\OmniScribe\src\omniscribe\config.py` — `output_format`,
  `merge_similarity_threshold` validators.
- `G:\git\OmniScribe\src\omniscribe\cli.py` — `--format` flag, precedence
  resolver, dispatch.
- `G:\git\OmniScribe\tests\test_output.py` — writer + `merge_channels` tests.
- `G:\git\OmniScribe\tests\test_cli.py` — format dispatch + precedence tests.

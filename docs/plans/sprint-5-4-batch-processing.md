# OmniScribe — Sprint 5.4: Batch Processing

## Context

`v0.1.1` shipped 2026-04-30 (`f0f74d7`) closing out the CUDA-12 Windows alignment work. `IMPLEMENTATION_PLAN.md:286-289` (Phase 5 task 2) specifies a batch processing mode: *"Process multiple URLs from a file or playlist, progress bar, resume on failure."* This sprint implements that against the existing single-video orchestrator with no pipeline changes.

The user-facing shape: `omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md`. Failures don't abort the run — transient errors retry, fatal errors are recorded and skipped. Re-running the same command resumes from the state file.

## Tier

**T3** (multi-file, ≤ 200 LoC new code, dev + reviewer + tester team).

## Pre-existing surface (reuse, do not rebuild)

| What | Where | How batch uses it |
|---|---|---|
| Single-video orchestrator | `src/omniscribe/cli.py:transcribe()` (~lines 70–180) — currently inline in the typer command | Extract sequential body into `process_single_video(source, config, output_path) -> None` helper; `transcribe()` calls it; `transcribe_many()` calls it in a loop |
| `download_video()` | `src/omniscribe/downloader.py:25-41` | Already per-URL; called once per item. No change to call signature |
| Output writers | `src/omniscribe/output.py` (`write_json` / `write_txt` / `write_srt` / `write_md`) | Reuse as-is. Batch computes `{output_dir}/{stem}.{ext}` and passes through |
| `OmniScribeError` | `src/omniscribe/errors.py` | Caught at the per-item boundary; failure recorded in state. No subclassing in this sprint |
| Config singleton | `src/omniscribe/config.py` (`OmniScribeConfig`, pydantic-settings) | Inherited as-is. No new config fields needed |
| `typer.testing.CliRunner` | `tests/test_cli.py` | Mirror existing pattern for new `transcribe-many` invocation tests |
| `rich.progress` | already a transitive dep via `typer>=0.13`; `rich>=13.0` is a direct dep | Use for progress bar |

**Do NOT add:** new download library, new state-management framework, retry / error-classification logic, exception subclasses, config schema fields, `--state-file` override flag. yt-dlp's `-a filename` flag exists but is CLI-only; the Python API is per-URL — iterate in Python. Resume-on-failure is the *only* failure-handling mechanism in this sprint: failures are recorded in the state file and the next run picks up `pending` items. If users later report transient failures that should auto-retry, a v0.1.3 sprint adds categorization on top of this foundation.

## Scope — Sprint 5.4 only

**In scope:**
- New subcommand `omniscribe transcribe-many <urls-file>` accepting one URL or local file path per line.
- Per-item processing via the existing single-video pipeline.
- Progress bar showing `{n}/{total}` + truncated current source.
- Resume-on-failure via a state file at `{output_dir}/.omniscribe-batch-state.json`.
- Output naming: `{output_dir}/{stem}.{format_ext}` per input.
- Tests covering empty list, mixed valid/invalid sources, state resume, output collisions, corrupt-state-file recovery, Windows-safe atomic writes, Ctrl+C-mid-item recovery.
- README Quick Start gets one batch example. CHANGELOG `[Unreleased]` block.

**Out of scope (deferred):**
- Playlist/channel URL expansion. v0.1.2 batch = URL-list-only; users wanting "transcribe a creator" can pipe `yt-dlp --flat-playlist --print id` into a list file. Native playlist support is a Phase 6 item.
- Parallel execution. Sequential only — batch is bounded by GPU on the hot path; concurrent transcribes contend for VRAM.
- Cross-format dispatch (one input → multiple formats in one pass). Single `--format` per run, mirroring `transcribe`.
- Per-item config override (different `--language` per URL). Inherits the run-level config.
- Notifications / webhooks on completion.
- **Transient-vs-fatal error categorization and per-item auto-retry.** Resume-on-failure already covers the transient case (re-run picks up `pending` items). Categorization adds a yt-dlp message-text coupling and ~40 LoC of regex matching for speculative benefit — defer to v0.1.3 if real user reports surface retry-worthy patterns.
- **`--state-file` override flag.** Hardcoded path is sufficient; flag added later if requested.

## Deliverables (file-by-file)

### 1. `src/omniscribe/cli.py` — extract orchestrator + add `transcribe-many`

- Extract the body of `transcribe()` into a module-level helper:
  ```python
  def process_single_video(
      source: str | Path,
      config: OmniScribeConfig,
      output_path: Path,
      *,
      ocr: bool,
      llm_cleanup: bool,
      asr_cleanup: bool,
      platform: str | None,
      language: str | None,
      output_format: OutputFormat,
  ) -> None:
  ```
  Behavior is byte-identical to the current inline path. `transcribe()` becomes a thin wrapper that resolves args and calls this.
- **Import-stability constraint.** Existing `tests/test_cli.py` patches at module-level paths (`omniscribe.cli.WhisperTranscriber`, etc.). Keep all `from .X import Y` statements at the top of `cli.py` — do NOT move imports into the helper. The patches must continue to resolve.
- Add the new command:
  ```python
  @app.command("transcribe-many")
  def transcribe_many(
      urls_file: Path = typer.Argument(..., exists=True, dir_okay=False),
      output_dir: Path = typer.Option(..., "--output-dir", "-o"),
      output_format: OutputFormat = typer.Option(OutputFormat.MD, "--format"),
      # plus all forwarded transcribe options: language, ocr, platform, llm_cleanup, asr_cleanup
  ) -> None:
  ```
  Resume is **implicit** — if `{output_dir}/.omniscribe-batch-state.json` exists, it's loaded; users wanting a fresh run delete the state file. No `--resume/--no-resume` flag.
- **Up-front guards (fail fast, before downloading anything):**
  1. `output_dir.mkdir(parents=True, exist_ok=True)`, then probe-write a temp file in it and delete — fail with a clear message if the directory isn't writable.
  2. If state file exists, log at INFO: `"Resuming batch started {state.started_at} from {state.input_file}; {N} pending, {M} failed, {K} done"`. This is the read-path that justifies retaining `started_at` and `input_file` in the state schema.
- **Body — write-cycle ordering for Ctrl+C safety:** parse the URL list (UTF-8-sig + per-line `.strip()` — see `parse_url_list` below), reconcile against loaded state (URL list is source of truth — see "edited-list reconcile" rule below), iterate with a `rich.progress.Progress` bar. **Per item:** (a) mark item `pending` in state and persist; (b) call `process_single_video`; (c) on success, mark `done` with `output_path` and persist; on `OmniScribeError`, mark `failed` with `error` text and persist; on `KeyboardInterrupt`, leave the item as `pending` (it was persisted in step a) and re-raise so typer exits cleanly.
- **Logging level inside the batch path:** `process_single_video` inherits the single-video logger which emits INFO per pipeline stage. Across many items this drowns out the progress bar. Demote those INFO records to DEBUG when invoked from `transcribe_many` — the simplest mechanism is wrapping the call in `with logging.getLogger("omniscribe").setLevel(logging.WARNING):` (or a per-call context manager that restores the prior level afterward). The progress bar itself is the user-facing feedback.

### 2. `src/omniscribe/batch.py` — NEW module

```python
@dataclass
class BatchItem:
    source: str
    status: Literal["pending", "done", "failed"]
    output_path: Path | None = None
    error: str | None = None

@dataclass
class BatchState:
    version: int  # = 1
    started_at: datetime  # logged on resume; not used in reconcile logic
    input_file: Path  # logged on resume; not used as a checksum or drift detector
    output_dir: Path
    format: str
    items: list[BatchItem]
```

Helpers:
- `load_state(path: Path) -> BatchState | None` — returns `None` if the file is missing, has a version mismatch, **or fails to JSON-decode** (corrupt / truncated / hand-edited gone wrong). On corrupt files, log a `warning` and return `None` so the run starts fresh; do NOT crash.
- `save_state(state: BatchState, path: Path) -> None` — writes atomically. **Windows-safe form:** create the temp via `tempfile.mkstemp(dir=path.parent, prefix=".omniscribe-batch-state.", suffix=".tmp")`, write + close, then `os.replace(temp, path)`. Same-volume guarantee + handle-cleanup are both required on Windows; `tempfile.NamedTemporaryFile` defaults to the system temp dir which is often a different volume and would `os.replace`-fail with `WinError 17`. On exception during write, `os.unlink` the temp.
- `parse_url_list(path: Path) -> list[str]` — open with `encoding='utf-8-sig'` (transparently strips UTF-8 BOM that Notepad-on-Windows adds), iterate lines, `.strip()` each (handles CRLF, trailing whitespace, leading/trailing spaces), drop empty results. **No comment-prefix support** — users who want to annotate their list can pre-process with `grep -v '^#'`. (Cut after second-pass YAGNI review.)
- `compute_output_path(source: str, output_dir: Path, ext: str, taken: set[Path]) -> Path` — derives the stem then resolves collisions:
  - **Stem derivation:** for URLs, attempt yt-dlp video-ID extraction (cheap metadata-only call); on extraction failure (generic URL, network down, ambiguous, or any exception) fall back to `sha256(source.encode()).hexdigest()[:12]`. For local-file sources, use the input's `Path(source).stem` after sanitizing (replace path separators, control chars, and Windows-reserved chars `<>:"|?*` with `_`).
  - **Length cap:** truncate the stem to **200 chars** to stay well under Windows `MAX_PATH` (260) once the output_dir prefix and extension are added.
  - **Collision against `taken`:** the `taken` parameter is required because two pending items in the same run can derive identical stems *before* either output file exists on disk — a filesystem-only check (`path.exists()`) would miss this case. On Windows (case-insensitive NTFS), the comparison is case-folded: `path.lower()` lookup so `Foo.md` and `foo.md` are treated as colliding.
  - **Suffix sequence:** append `(2)`, `(3)`, … on collision; assert and abort if a single stem requires `>999` suffixes.
  - **Resume rule:** when reconciling a loaded state, items already carrying an `output_path` keep that path verbatim — collision detection only runs for new (`pending`-without-`output_path`) items.
- `reconcile(state: BatchState | None, urls: list[str]) -> BatchState` — **URL list is the source of truth on each run.** State items whose `source` is no longer in the parsed URL list are dropped silently. URLs in the list but not in state are appended as new `pending` items, preserving list order. Existing items keep their status (`done` items are not re-run; `pending` and `failed` items are re-attempted).

Roughly 90–120 LoC; pure data + IO, no transcribe logic.

### 3. `tests/test_batch.py` — NEW

- `test_parse_url_list_strips_blanks_and_whitespace`
- `test_parse_url_list_handles_bom_and_crlf` — Windows Notepad writes a UTF-8 BOM and CRLF; assert both are transparently handled (regression guard for Windows-edited URL lists)
- `test_parse_url_list_handles_local_paths`
- `test_compute_output_path_url` — expect yt-dlp video ID stem
- `test_compute_output_path_local_file`
- `test_compute_output_path_video_id_extraction_fails_uses_hash` — monkeypatch the yt-dlp metadata call to raise; assert the stem is the 12-char sha256 hex prefix
- `test_compute_output_path_truncates_long_stems` — input with 300-char title; assert stem ≤ 200 chars
- `test_compute_output_path_collision` — second item with same stem gets `(2)` suffix
- `test_compute_output_path_collision_case_insensitive_on_windows` — `Foo.md` already taken; new stem `foo` collides on Windows (case-folded lookup)
- `test_compute_output_path_resume_honors_existing` — pre-set `output_path` on a state item is preserved verbatim, no recomputation
- `test_state_round_trip` — save then load returns equivalent state
- `test_state_load_missing_returns_none`
- `test_state_load_version_mismatch_returns_none`
- `test_state_load_corrupt_returns_none` — feed truncated / non-JSON content; expect `None` + a warning log, no exception
- `test_save_state_atomic` — assert no partial file on simulated mid-write crash (monkeypatch `os.replace` to raise, confirm original state file untouched, temp file cleaned up)
- `test_save_state_uses_same_volume_tempfile` — assert the temp file is created in `path.parent`, not the system temp dir (regression guard against the `NamedTemporaryFile` Windows footgun)
- `test_reconcile_drops_orphan_items_not_in_list`
- `test_reconcile_appends_new_urls_as_pending`
- `test_reconcile_preserves_done_status_for_existing_urls`

### 4. `tests/test_cli.py` — extend

- `test_transcribe_many_empty_file` — empty URL list, exit 0, no items processed.
- `test_transcribe_many_unwritable_output_dir_fails_fast` — point `--output-dir` at a read-only path (or a path under a read-only parent on POSIX; on Windows, monkeypatch the probe-write to raise `PermissionError`). Assert exit code 1 and that `process_single_video` is never called.
- `test_transcribe_many_all_succeed` — three URLs, all `process_single_video` calls return cleanly, state shows all three `done`, three output files written.
- `test_transcribe_many_mixed_valid_invalid` — two URLs succeed, one raises `OmniScribeError`. Run exit code: `0` if at least one item succeeded, `1` only if all items failed. State records the failed item with `status=failed` and `error` text.
- `test_transcribe_many_resume_skips_done` — pre-populate state file with one item already `done`, run again, assert `process_single_video` is NOT called for that item.
- `test_transcribe_many_resume_retries_failed_and_pending` — pre-populate state with one `failed` and one `pending`; assert both are re-attempted on the next run. (Resume *is* the retry mechanism in this sprint.)
- `test_transcribe_many_resume_against_edited_list_drops_orphans_and_appends_new` — pre-populate state with URLs A, B (done), C (failed); edit the list to B, C, D. Assert: A is dropped, B is skipped (already done), C is re-attempted, D is appended as a new `pending` and processed.
- `test_transcribe_many_ctrl_c_mid_item_keeps_state_valid` — monkeypatch `process_single_video` to raise `KeyboardInterrupt` mid-batch; assert the state file is valid JSON, the in-flight item is in `pending` status (persisted before the call), and the run exits non-zero. A subsequent run resumes that item.

All call `process_single_video` via `mock.patch` so no real downloads / transcription happens.

### 5. `README.md`

In **Quick Start**, after the local-file example, add:

```bash
# Batch transcribe (resume-on-failure)
omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md
```

One sentence noting that re-running the same command resumes from the state file.

### 6. `CHANGELOG.md`

Insert at top (above `[0.1.1]`):

```markdown
## [Unreleased]

### Added

- **Batch transcription** (`omniscribe transcribe-many`). Reads URLs (one per line) from a file, processes each via the existing single-video pipeline, writes per-input outputs into a target directory, and resumes on failure via `.omniscribe-batch-state.json`. Failures are recorded with the error text and skipped; re-running the same command picks up `pending` and `failed` items. Sequential execution — no concurrent transcribes (GPU contention).

### Changed

- Internal: `cli.transcribe()`'s orchestration body extracted into a module-level `process_single_video()` helper so the batch command can reuse it. No behavior change for the single-video path.
```

The `[0.1.2]: …` link reference is added at release-prep time, not in this sprint.

## State file schema

Path: `{output_dir}/.omniscribe-batch-state.json` (hardcoded — no override flag in v0.1.2).

```json
{
  "version": 1,
  "started_at": "2026-04-30T15:00:00Z",
  "input_file": "/abs/path/to/urls.txt",
  "output_dir": "/abs/path/to/transcripts",
  "format": "md",
  "items": [
    {
      "source": "https://www.tiktok.com/@u/video/1",
      "status": "done",
      "output_path": "/abs/path/to/transcripts/1.md"
    },
    {
      "source": "https://www.tiktok.com/@u/video/2",
      "status": "failed",
      "error": "Download failed: Video unavailable: this video is private"
    },
    {"source": "https://www.tiktok.com/@u/video/3", "status": "pending"}
  ]
}
```

Schema-version bump (`version: 2`) is the migration path if fields ever change. v1 readers seeing v2 (or any unrecognized future field) → return `None` from `load_state` (the run starts fresh; user keeps a backup if they care).

**Failure handling.** All `OmniScribeError` raises (download / ASR / OCR / merge / output) are caught at the per-item boundary, recorded as `status=failed` with the error text, and the run continues. There is no retry-in-loop; resume-on-rerun is the only retry mechanism in this sprint.

## Acceptance criteria

1. `uv run ruff format .` and `uv run ruff check .` clean.
2. `uv run pytest` — all existing tests still green + new tests pass. **Target ≥ 352** (325 baseline + 19 new in `test_batch.py` + 8 new in `test_cli.py`; some may be combined or split during implementation, ≥ 348 acceptable). Existing `tests/test_cli.py::test_transcribe_*` tests must pass without modification (regression gate on the orchestrator extraction).
3. Manual smoke: a 3-URL list (one valid TikTok, one valid YouTube, one obviously broken URL) produces:
   - 2 output files in `--output-dir`
   - 1 state-file entry with `status=failed` and an `error` field
   - Re-running the command picks up the `failed` item and re-attempts it (fails again, harmlessly); the two `done` items are skipped
4. `omniscribe transcribe-many --help` lists all forwarded transcribe options (language, ocr, platform, llm-cleanup, asr-cleanup) plus the batch-specific ones (`--output-dir`, `--format`).
5. State file is human-readable JSON with stable key ordering (`json.dumps(sort_keys=True, indent=2)`).
6. Process is interruptible: Ctrl+C *during* an item's transcribe leaves the state file valid; the in-flight item remains `pending` (it was persisted as `pending` before the call started). Next run resumes from there.
7. Empty URL list: exit code `0`, no items processed, no state file written.
8. Read-only `--output-dir`: fail-fast at command entry with a clear error message; `process_single_video` is never invoked.

## Design decisions locked

- **State file location:** `{output_dir}/.omniscribe-batch-state.json`, hardcoded. Hidden file in the same directory as outputs keeps the artifact set together. No `--state-file` override flag in v0.1.2.
- **State format:** JSON, not pickle / sqlite. Human-readable, hand-editable in emergencies, no schema-migration framework needed.
- **Atomic writes:** `tempfile.mkstemp(dir=path.parent)` + `os.replace`. **Same-volume temp is required on Windows** — `NamedTemporaryFile`'s default system temp dir often lives on a different volume and triggers `WinError 17` from `os.replace`. Cleanup the temp on exception via `os.unlink`.
- **Resume is implicit, not flag-gated.** If the state file exists, it's loaded and reconciled. Users wanting a fresh run delete the state file. `started_at` and `input_file` are logged at INFO on resume so the user can identify which prior run is being continued.
- **Edited URL list reconcile:** the URL list is the source of truth on every run. Items in state but missing from the list are dropped silently; items in the list but missing from state are appended as new `pending`. `done` items keep their status; `pending` and `failed` items are re-attempted.
- **Failure handling:** Catch `OmniScribeError` at the per-item boundary, record on the item, continue. **No retry-in-loop, no error categorization** in this sprint — resume covers re-attempts.
- **Write-cycle ordering:** for each item, persist `status=pending` *before* calling `process_single_video`, persist final status *after*. Guarantees Ctrl+C / crash leaves a recoverable state file.
- **Resume + collision interaction:** items already in state with a non-null `output_path` keep that path verbatim on resume — don't recompute and risk a different collision-suffix sequence.
- **Exit code:** `0` if the run completed all items with at least one success, **OR** if the URL list was empty (no work requested = no failure); `1` if at least one item was attempted and they all failed; `1` if up-front guards (output-dir writability, state-file load) hard-fail.
- **Output collision policy:** Append `(2)`, `(3)`, …. Case-insensitive lookup on Windows (NTFS treats `Foo.md` and `foo.md` as the same file). Assert and abort if a single stem requires `>999` suffixes (degenerate case).
- **Output stem length cap:** truncate to 200 chars before suffixing. Keeps the full output path well under Windows `MAX_PATH` (260) without enabling long-path mode.
- **Logging during batch:** per-item INFO-level logs from `process_single_video` are demoted to DEBUG when invoked from `transcribe-many`. The progress bar is the user-facing feedback. WARNING / ERROR records still surface.
- **Progress reporting:** `rich.progress.Progress` with one task showing `{n}/{total}` + truncated source. No per-item sub-progress (download/ASR/OCR phases stay quiet) — would be nice but adds complexity beyond scope.

## Verification

- Run new test file in isolation first: `uv run pytest tests/test_batch.py -v`.
- Then full suite: `uv run pytest`.
- Manual smoke per acceptance criterion 3, against a list including a known-private TikTok and an intentionally malformed URL to exercise both fatal paths.
- After merge, the `[Unreleased]` block is the v0.1.2 release-prep starting point.

## Behavior-change risk (READ BEFORE MERGING)

- **`process_single_video` extraction is a refactor of `transcribe()`'s body.** Risk: subtle ordering change between download/ASR/OCR/merge/output, OR breaking the `mock.patch("omniscribe.cli.X")` resolution paths in existing tests. Mitigations: (a) keep all `from .module import X` statements at the top of `cli.py`; do NOT move imports into the helper. (b) existing `tests/test_cli.py::test_transcribe_*` tests must pass without modification — that's the regression gate. If any existing test needs editing, treat it as a regression and re-think the extraction.
- **State file gets written into `--output-dir`.** If a user points `--output-dir` at a directory containing existing files with names matching the inferred stems, the existing files are overwritten without warning. Document this in the README example. Adding a `--no-clobber` is a future enhancement, not in scope.
- **Resume reprocesses `failed` items by default.** A user who runs the command twice on a list with one permanently-broken URL will see that URL re-attempted (and re-failed) on every run. Documented in the README example. A `--skip-failed` flag is a future enhancement, not in scope.
- **Concurrent runs against the same `--output-dir` are unsupported.** The atomic state-file write protects file integrity, not logical consistency: two `transcribe-many` invocations targeting the same directory will race, last writer wins, items can be lost from state. No locking is added in this sprint. Document the constraint in the README; add a lockfile in v0.1.3 if anyone reports it.
- **Windows `MAX_PATH` is mitigated, not eliminated.** Stems are truncated to 200 chars before extension + suffix. This handles common cases (long video titles) but a deeply nested `--output-dir` could still exceed 260 chars. Users hitting this can either move `--output-dir` closer to the drive root or enable Windows long-path support.
- **Large-list state-write cost.** Each item triggers two full state-file rewrites (pre- and post- transition). Tested up to ~1,000 items; for 10,000+ URLs the linear constant becomes noticeable. Sharding the URL list into multiple smaller batches (different `--output-dir` per shard) is the workaround. Optimization (incremental writes, append-only journal) is a v0.2+ concern.

## Required Skills

- karpathy-guidelines
- superpowers:test-driven-development
- superpowers:verification-before-completion
- superpowers:receiving-code-review

## Close-out

_TBD — filled in after the PR merges._

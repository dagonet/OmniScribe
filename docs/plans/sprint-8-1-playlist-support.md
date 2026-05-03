# OmniScribe — Sprint 8.1: Playlist / Channel Support

## Context

`v0.1.1` shipped with batch transcription (Sprint 5.4, PR #26 `bf4ef74`). `omniscribe transcribe-many` reads one URL or local file path per line and processes each via the existing single-video pipeline. The README explicitly punts the "transcribe my whole channel" use case to a shell workaround:

```bash
yt-dlp --flat-playlist --print id <channel-url> | omniscribe transcribe-many /dev/stdin --output-dir transcripts/
```

`IMPLEMENTATION_PLAN.md:321` lists "Playlist/channel support — Transcribe all videos from a creator or playlist" as a Phase 6 candidate. This sprint folds the workaround into the tool: lines in `urls.txt` that resolve to a playlist or channel auto-expand into per-video URLs, then run through the existing `transcribe_many` orchestrator.

The user-facing shape: `urls.txt` becomes a mixed list of single-video URLs, local file paths, **and** playlist/channel URLs. Each playlist line expands inline. No new subcommand, no new flag.

## Tier

**T3** (multi-file, ≤ ~150 LoC new prod code, dev + reviewer + tester team).

## Pre-existing surface (reuse, do not rebuild)

| What | Where | How this sprint uses it |
|---|---|---|
| `transcribe_many` orchestrator | `src/omniscribe/cli.py:407+` | Wraps the new expansion step before reconcile + iteration. No structural change to the per-item loop. |
| `BatchItem` / `BatchState` / `save_state` / `load_state` | `src/omniscribe/batch.py` | State schema unchanged. The expanded video URLs are top-level items; the playlist URL itself is **not** persisted. |
| `parse_url_list` | `src/omniscribe/batch.py:177+` | Extended (or wrapped) so each line passes through playlist-detection before being added to the URL list. |
| `compute_output_path` | `src/omniscribe/batch.py:198+` | Per-video stem derivation works unchanged: yt-dlp video IDs are stable per video. |
| `process_single_video` | `src/omniscribe/cli.py:282+` | One call per expanded video. No change. |
| `download_video` | `src/omniscribe/acquire/downloader.py:25+` | Per-video, unchanged. |
| `yt_dlp.YoutubeDL.extract_info(url, process=False, download=False, extract_flat=True)` | yt-dlp Python API | Returns `{'_type': 'playlist', 'entries': [{...}, {...}, ...]}` for playlist/channel URLs; `{'_type': 'video', ...}` (or absence of `_type`) for singles. This is the detection mechanism. |

**Do NOT add:** new download library, custom playlist-detection logic that hand-rolls per-platform URL pattern matching, configuration fields for playlist behavior, parallel video downloads, channel-pagination caps, expansion caching across runs.

## Scope — Sprint 8.1

**In scope:**
- New helper module `src/omniscribe/acquire/playlist.py` exposing `expand_playlist(url: str) -> list[str] | None` — returns the list of expanded video URLs for a playlist/channel URL; returns `None` for single-video URLs, local file paths, or any expansion error (extraction failure, malformed URL, network error).
- `transcribe_many` integration: each non-empty line from the parsed URL list is fed through `expand_playlist` before reconcile. If the call returns a list, the original line is replaced by its expansion in-place. If it returns `None`, the line is kept verbatim (single-video URL or local file).
- Order preservation: expanded video URLs appear in the same position the playlist URL had, in playlist order (yt-dlp's natural feed order).
- Resume + reconcile work transparently: the *expanded* URL list is the source of truth on each run. State items keyed by per-video `source`. A user re-running with the same `urls.txt` will re-fetch the playlist expansion (cheap via `extract_flat`) but re-use existing state for already-processed videos.
- Mixed lists: `urls.txt` may contain any mix of single-video URLs, local file paths, and playlist/channel URLs. Each line expands or doesn't independently.
- Tests covering: playlist URL expansion (mocked yt-dlp), single-video URL → `None`, local file path → `None`, extraction failure → `None`, mixed-list integration, position-preservation, resume after partial completion.
- README Quick Start: one playlist example added.
- CHANGELOG `[Unreleased]` block extended.

**Out of scope (deferred):**
- **Expansion caching across runs.** Re-fetching the playlist on every run is wasteful for long-lived channels but yt-dlp's `extract_flat` is metadata-only (no video downloads) — cost is seconds, not minutes. A `.omniscribe-playlist-cache.json` would solve it cleanly but adds schema and invalidation concerns. Defer to v0.2.x if signal surfaces.
- **Parallel video processing.** Sequential only — same GPU-contention reasoning as Sprint 5.4.
- **Per-platform URL pattern matching.** yt-dlp already detects playlist vs video; rolling our own regex is fragile and duplicative.
- **Custom expansion ordering** (`--reverse`, `--newest-first`, etc.). Use yt-dlp's natural order.
- **Pagination caps.** No `--max-videos`. Channels with thousands of videos take seconds to enumerate via `extract_flat`; per-video processing is what costs time, and that's already user-bounded.
- **Live videos / scheduled premieres handling.** yt-dlp's per-video extraction will surface these; they fall through to the existing `OmniScribeError` path and get recorded as `failed` items, which is correct.
- **Auto-detection in single-video `transcribe`.** Feeding `transcribe` a playlist URL still errors as before. Users wanting playlist expansion use `transcribe-many`. Keeps the single-video path predictable.
- **Nested playlist recursion** (a playlist whose entries are themselves playlists). One level of expansion only. yt-dlp's `extract_flat` returns nested entries flat by default — verify in tests.

## Deliverables (file-by-file)

### 1. `src/omniscribe/acquire/playlist.py` — NEW

```python
def expand_playlist(url: str) -> list[str] | None:
    """Return expanded video URLs for a playlist/channel URL, else None.

    Returns:
        - list[str]: one entry per video in the playlist, in feed order.
        - None: input is a single-video URL, a local file path, malformed,
          or yt-dlp raised any exception during extraction.
    """
```

Behavior:
- **Local-file detection ordering (locked):** if `not url.startswith(("http://", "https://"))`, return `None` immediately — covers local file paths and any non-URL string. **Do NOT** call `Path(url).exists()` first; scheme-based detection is cheaper and deterministic across platforms (Windows backslashes, Linux absolute paths that may or may not exist).
- Otherwise call `yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}).extract_info(url, process=False, download=False)`. (`no_warnings` suppresses yt-dlp's stderr deprecation/extractor chatter that would otherwise drown out the progress bar.)
- Inspect the return: if `info.get("_type") != "playlist"`, return `None`.
- **Entries iteration (locked):** coerce defensively — `entries = list(info.get("entries") or [])`. Some yt-dlp versions return a `LazyList`; `list(...)` materializes it. Empty / missing entries → `[]`.
- **Per-entry URL extraction order (locked):** for each `entry` in `entries`, resolve to a URL with this fallback chain:
  ```python
  entry.get("url") or entry.get("webpage_url") or (
      f"https://www.youtube.com/watch?v={entry['id']}" if entry.get("id") else None
  )
  ```
  If all three are falsy / missing, skip the entry and log at WARNING (`Skipping playlist entry with no resolvable URL: {entry!r}`). The smoke test verifies this against real yt-dlp output before merge.
- Catch broad `Exception` (yt-dlp raises `DownloadError`, `ExtractorError`, plus network/SSL-level errors) → log at WARNING with the URL + exception class, return `None`. **Rationale for returning `None` rather than raising:** the caller (`expand_url_list`) treats `None` as "not a playlist" and keeps the original line. Two outcomes follow naturally — (a) if the line really was a single-video URL, processing continues unaffected; (b) if the line was meant to be a playlist but extraction failed, the per-video pipeline will fail the same URL later with the actual extractor error message, which is more actionable than a generic "playlist expansion failed."

Roughly 50–80 LoC. Pure function, no state.

### 2. `src/omniscribe/batch.py` — extend `parse_url_list`

Add a second helper `expand_url_list(urls: list[str]) -> list[str]` that:
- Iterates the parsed URL list.
- For each entry, calls `expand_playlist(entry)`. If it returns a list, splice that list into the position the entry occupied. If it returns `None`, keep the entry as-is.
- Returns the new flat list.

`parse_url_list` itself stays unchanged (it just reads + strips). The expansion is a separate step so unit tests can exercise parsing and expansion independently.

Wire-up: in `cli.py::transcribe_many`, after the existing `parse_url_list` call, immediately call `expand_url_list` on the result before passing to `reconcile`.

Roughly 15–25 LoC.

### 3. `src/omniscribe/cli.py` — wire the expansion

Single change: insert the `expand_url_list` call between `parse_url_list` and `reconcile`. No signature changes, no new flags.

```python
urls = parse_url_list(urls_file)
urls = expand_url_list(urls)  # new line
state = reconcile(prior_state, urls)
```

Roughly 1–3 LoC of actual change. The new import lands at the top of `cli.py` per the existing import-stability constraint.

### 4. `tests/test_playlist.py` — NEW

- `test_expand_playlist_recognizes_playlist_urls` (`@pytest.mark.parametrize`'d across `[("youtube_playlist_url", ...), ("youtube_channel_url", ...), ("tiktok_user_url", ...)]`) — mock `yt_dlp.YoutubeDL.extract_info` to return `{"_type": "playlist", "entries": [...]}`; assert correct URL extraction. (Replaces the three near-identical platform-specific tests.)
- `test_expand_playlist_single_video_returns_none` — mock returns `{"_type": "video", ...}`.
- `test_expand_playlist_local_file_path_returns_none` — pass a path-like string `"./video.mp4"` (no scheme); expand returns `None` without calling yt-dlp at all (verified via `mock.patch` asserting `extract_info` was not called).
- `test_expand_playlist_absolute_path_no_scheme_returns_none` — pass `"/foo/bar.mp4"` (no scheme, may or may not exist on disk); returns `None`. Locks the scheme-based detection contract.
- `test_expand_playlist_empty_playlist_returns_empty_list` — mock returns `{"_type": "playlist", "entries": []}`; assert `[]`. Locks the empty-playlist contract.
- `test_expand_playlist_lazylist_entries_coerced` — mock returns `{"_type": "playlist", "entries": <iterator-not-list>}` (e.g. a generator); assert `list(...)` coercion works and the result is a real list. Regression guard against yt-dlp's `LazyList` return shape.
- `test_expand_playlist_nested_playlist_entries_not_recursed` — mock returns a playlist whose `entries` contains an entry with `_type == "playlist"`; assert outer expansion does NOT recurse and treats the nested playlist entry as an opaque single-URL entry (using the standard URL-extraction fallback chain). Locks the one-level-only contract.
- `test_expand_playlist_url_field_fallback` — entry has only `url`; assert it's used.
- `test_expand_playlist_webpage_url_fallback` — entry has only `webpage_url` (no `url`); assert it's used.
- `test_expand_playlist_id_reconstruction_fallback` — entry has only `id` (no `url` or `webpage_url`); assert YouTube-style watch URL is reconstructed.
- `test_expand_playlist_entry_with_no_resolvable_url_skipped` — entry has none of `url` / `webpage_url` / `id`; assert that entry is dropped from the output and a WARNING is logged.
- `test_expand_playlist_extraction_failure_returns_none` — mock raises `yt_dlp.utils.DownloadError`; assert `None` + WARNING log.
- `test_expand_playlist_network_failure_returns_none` — mock raises `OSError`; same.
- `test_expand_playlist_preserves_feed_order` — entries are returned in insertion order.
- `test_expand_url_list_splices_in_place` — input `[url-A, playlist-url, url-B]` with `playlist-url` expanding to `[v1, v2]` returns `[url-A, v1, v2, url-B]` (assert exact list equality including position).
- `test_expand_url_list_passes_through_singles` — list of all single-video URLs returns identical list.
- `test_expand_url_list_handles_empty_list` — empty input returns empty list.
- `test_expand_url_list_handles_extraction_errors_gracefully` — input `[url-A, fail-playlist, url-B]` where `fail-playlist` returns `None`; output is exactly `[url-A, fail-playlist, url-B]` — assert position-preservation, not just count.

### 5. `tests/test_cli.py` — extend

- `test_transcribe_many_expands_playlist_url` — `urls.txt` contains a playlist URL; mock `expand_playlist` to return 3 video URLs; assert `process_single_video` is called 3 times in order, state file records all 3 as separate items.
- `test_transcribe_many_mixed_playlist_and_singles` — `urls.txt` contains `[single-A, playlist-X, single-B]`; mock playlist-X to expand to `[v1, v2]`; assert `process_single_video` called 4 times in order `[single-A, v1, v2, single-B]`.
- `test_transcribe_many_playlist_url_not_in_state` — after a successful run, state contains the per-video URLs only; the playlist URL string never appears in `state.items`.
- `test_transcribe_many_playlist_resume_skips_done` — pre-populate state with one of the expanded videos already `done`; re-run with the same playlist URL in `urls.txt`; assert that video is skipped and the others re-attempt (or skip if already done).
- `test_transcribe_many_playlist_extraction_failure_keeps_url` — `expand_playlist` returns `None` for a URL we believe is a playlist; the URL is kept verbatim and processed via `process_single_video` (which will likely fail and record `status=failed` — confirm that's the surfaced behavior).

**Mock patch paths (locked):** `expand_url_list` is imported into `cli.py` at module scope, so tests must patch `omniscribe.cli.expand_url_list` (NOT `omniscribe.batch.expand_url_list` — that's the wrong site for module-level patching). Same convention as `tests/test_cli.py::_patched_pipeline()` from Sprint 5.4. `process_single_video` continues to be patched at `omniscribe.cli.process_single_video`.

### 6. `README.md`

After the existing batch example, add:

```bash
# Batch a whole YouTube channel or playlist (auto-expanded inline)
echo "https://www.youtube.com/@channel/videos" > urls.txt
omniscribe transcribe-many urls.txt --output-dir transcripts/ --format md
```

One-sentence note: playlist + channel URLs in the URL list are automatically expanded via yt-dlp; mix freely with single-video URLs and local files.

### 7. `CHANGELOG.md`

Extend `[Unreleased]` (currently has the batch entry):

```markdown
### Added

- **Batch transcription** … (existing entry, unchanged)
- **Playlist / channel auto-expansion in `transcribe-many`.** Lines in the URL list that resolve to a playlist or channel are automatically expanded via yt-dlp's `extract_flat`, in feed order, before per-video processing. Mix freely with single-video URLs and local file paths in the same `urls.txt`. Sequential expansion + processing; no caching across runs (yt-dlp's `extract_flat` is metadata-only and cheap).

### Changed

- Internal: `cli.transcribe()`'s orchestration body extracted… (Sprint 5.4 entry, unchanged)
- **`transcribe-many` URL list semantics.** Lines that yt-dlp resolves to a playlist URL now auto-expand inline. Previously such lines failed at the per-video extractor with an opaque error. Existing `urls.txt` files containing single-video URLs and local file paths are unaffected.
```

## Design decisions locked

- **Expansion lives in `src/omniscribe/acquire/playlist.py`**, not in `batch.py`. `batch.py` is pure data + IO over a flat URL list; playlist expansion is a yt-dlp / network concern that belongs alongside `downloader.py` under `acquire/`.
- **Detection is via `_type == "playlist"`**, not URL pattern matching. yt-dlp already knows what's a playlist; reusing that knowledge means new platforms (added by yt-dlp upstream) work for free.
- **Failure mode is "treat as single-video URL".** If `expand_playlist` raises, we return `None` and pass the original line through. The downstream pipeline's per-video error handling surfaces the actual problem with a useful message.
- **No expansion caching in v0.1.x.** Re-fetching every run is acceptable cost. State schema stays unchanged.
- **Playlist URL itself is NOT persisted in state.** Only the expanded per-video URLs are tracked. This keeps the resume / reconcile semantics from Sprint 5.4 working without modification.
- **Order preservation:** expansion happens in-place so the order of `urls.txt` is preserved at the per-video granularity.
- **`transcribe` (single-video) does NOT auto-expand.** Feeding a playlist URL to `transcribe` errors as before. Users wanting expansion use `transcribe-many`. Keeps the single-video path's behavior predictable.
- **One level of expansion.** yt-dlp's `extract_flat` returns flat entries by default; we don't recurse on `_type=="playlist"` entries inside a playlist. Verify in tests that this matches yt-dlp's actual behavior on a channel-of-playlists URL; document if not.

## State file interaction

State schema is **unchanged** from Sprint 5.4. Items are keyed by per-video `source`. The playlist URL never appears in state.

Resume semantics:
- First run: `urls.txt = [playlist-X]` → expand to `[v1, v2, v3]` → process all → state has 3 `done` items.
- Second run with same `urls.txt`: re-expand to `[v1, v2, v3]` → reconcile keeps existing `done` items → no work needed.
- Second run with edited `urls.txt = [playlist-X, single-Y]`: re-expand to `[v1, v2, v3, single-Y]` → reconcile keeps `v1, v2, v3` as `done`, appends `single-Y` as `pending`.
- Second run with edited `urls.txt = [single-Y]` (playlist removed): expansion returns `[single-Y]` → reconcile drops `v1, v2, v3` (orphaned per source-of-truth rule), appends `single-Y` as `pending`. **This is intentional**: the URL list is the source of truth on every run.
- **Second run when the playlist's contents changed upstream** (creator added or removed videos between runs): re-expansion picks up the new feed → reconcile drops orphaned `done` items whose URL is no longer in the playlist, appends new entries as `pending`. State always reflects the current expansion — it is *not* a permanent record of what the playlist used to contain. This is the mainline use case ("transcribe my channel weekly") and matches the source-of-truth contract.

## Acceptance criteria

1. `uv run ruff format .` and `uv run ruff check .` clean.
2. `uv run pytest` — all existing tests still green + new tests pass. **Target ≥ 370** (353 baseline + 16 new in `test_playlist.py` post-parametrization + 5 new in `test_cli.py`). **Pre-flight:** dev re-runs `uv run pytest --collect-only -q | tail -1` on `main` before starting; if the baseline drifted from 353, update the target proportionally. Existing `tests/test_cli.py::test_transcribe_*` and `tests/test_cli.py::test_transcribe_many_*` (Sprint 5.4 tests) must pass without modification — that's the regression gate on the expansion integration.
3. Manual smoke: `urls.txt` containing one valid YouTube channel URL + one local file. Run `transcribe-many --output-dir transcripts/ --format md`. Expected: N+1 output files (N videos from the channel + 1 from the local file). State file shows N+1 items, all keyed by per-video URL or local-file path. Re-running picks up nothing to do.
4. `omniscribe transcribe-many --help` is unchanged from Sprint 5.4 (no new flags introduced).
5. State file contents verified by inspection: no playlist URLs appear in `items[*].source`.

## Verification

- Run new test file in isolation first: `uv run pytest tests/test_playlist.py -v`.
- Then `uv run pytest tests/test_cli.py -k "playlist or transcribe_many" -v` to confirm new + existing transcribe-many tests both pass.
- Then full suite: `uv run pytest`.
- Manual smoke per acceptance criterion 3, against a small public YouTube channel (any creator with 3-5 short videos) to keep the test cheap. **Smoke is metadata-only via `extract_flat` plus per-video processing of the FIRST expanded video only.** Ctrl+C after the first video succeeds — the goal is to verify expansion + state + first-item processing, not to transcribe the whole channel during developer smoke. State file should record one `done` and several `pending` items.
- **Smoke also verifies the entry-URL extraction contract against real yt-dlp:** inspect the expanded list before any download starts (`logger.debug` line in `expand_playlist` should print the extracted URLs). Confirm the URL chosen by the resolution-order chain (`url` → `webpage_url` → reconstructed from `id`) is what real yt-dlp produces for the chosen channel. If the unit-test mock shape diverges, update the test fixtures.

## Behavior-change risk (READ BEFORE MERGING)

- **`transcribe-many`'s URL list semantics change.** Lines that yt-dlp recognizes as playlists previously would have been processed as single-video URLs (and failed). After this sprint, those same lines auto-expand. Documented in CHANGELOG. Users with existing `urls.txt` files containing playlist URLs as a workaround for "expand each manually" will see those lines now expand inline — the behavior is more correct, but technically a breaking change for the rare user expecting the old failure mode.
- **yt-dlp upgrade sensitivity.** `_type == "playlist"` is a stable yt-dlp API surface but field naming inside `entries` (`url` vs `webpage_url` vs reconstructed-from-id) has shifted in past releases. Pin behavior in tests by mocking the exact dict shape and verifying the implementation handles all three. Document the chosen extraction in `expand_playlist`'s docstring.
- **Network failure during expansion is silent.** If yt-dlp can't reach a playlist URL (transient network error, rate limit), `expand_playlist` returns `None` and the original line passes through to the per-video pipeline, which will then fail the same URL with a more visible error. Net effect: the user sees a failure on the playlist URL rather than the constituent videos. This is acceptable but worth noting in the logs (already covered by the WARNING log).
- **Playlist with zero entries.** A valid but empty playlist URL → `expand_playlist` returns `[]`. `expand_url_list` splices nothing in place, removing the playlist line entirely. The user sees no items processed for that line. Acceptable; document in the docstring.
- **Channel-of-playlists.** A channel URL whose entries are themselves playlists. yt-dlp's `extract_flat` typically returns the inner videos flat, but on some platforms it may return the inner playlists. If it returns inner playlists, the current implementation treats them as single-video URLs and the per-video pipeline fails them. **Verify behavior on YouTube channels in the smoke test;** if it surfaces, either accept as known limitation or add one level of recursion. Don't recurse blindly without evidence. The unit-test `test_expand_playlist_nested_playlist_entries_not_recursed` already pins the no-recurse contract at unit level.
- **Concurrent-run state-file race window widens.** Sprint 5.4 documented "concurrent runs against same `--output-dir` are unsupported." This sprint adds a slow network operation (yt-dlp metadata fetch) BEFORE any state read/write. Two parallel `transcribe-many` invocations against the same `--output-dir` can both run their expansion phase, then race on the first `save_state` call — the loser's expansion is discarded. Constraint unchanged from Sprint 5.4 but window is wider. Lockfile remains a v0.2.x consideration.

## Required Skills

- karpathy-guidelines
- superpowers:test-driven-development
- superpowers:verification-before-completion
- superpowers:receiving-code-review

## Close-out

**Merged 2026-05-03** as PR [#27](https://github.com/dagonet/OmniScribe/pull/27), squash SHA `7134ede`.

- **Tests**: 353 → 378 (+25). Plan target ≥370 met.
- **Style**: `uv run ruff format .` + `uv run ruff check .` clean.
- **Sprint 5.4 regression gate**: all 8 pre-existing `test_transcribe_many_*` tests pass without modification.
- **State schema**: `BatchItem` / `BatchState` unchanged — playlist URL never enters state, only per-video URLs.
- **CI**: `test` workflow ✅ on PR HEAD `45012d5`.
- **Pipeline**: dev (python-coder) → reviewer (code-reviewer, APPROVE 0 CRITICAL / 1 non-blocking WARN / 2 SUGGEST) → tester (PASS) → squash-merge.

**Reviewer findings carried forward** (non-blocking, candidate follow-ups):
1. Structural placement of `expand_url_list` in `batch.py` couples it to a network/yt-dlp dep transitively. Plan section 2 was the explicit instruction; design-rationale prose was looser. Either drop a comment near the import in `batch.py:24`, or move `expand_url_list` to `acquire/playlist.py` and re-export. Low priority.
2. Distinct WARNING log message for the non-dict entry-skip branch vs the no-resolvable-URL branch in `acquire/playlist.py`. Cosmetic.
3. Add `test_transcribe_many_all_empty_playlists_no_state_written` for the post-expand empty-list short-circuit at `cli.py:504`. Edge-case test coverage.

**Tooling note**: the python-coder and tester subagent definitions did not have access to `git-tools` MCP or `MCP_DOCKER` (GitHub) tools. Both completed their substantive work successfully (implementation + verification) but the PO posted commits, the PR, the review/verification GitHub comments, and merged on the dev's behalf. If this becomes a recurring friction point, expand those subagent tool surfaces.

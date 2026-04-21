# OmniScribe — Sprint 6.2: ASR Punctuation Cleanup

**Goal:** Extend the Sprint 6.1 LLM-cleanup infra with an opt-in, Ollama-backed per-segment **punctuation + capitalization** pass on `SPEECH` segments. Ships as a sibling to `cleanup_ocr_segments`, reusing the availability / model-presence / safety-rail infrastructure already shipped.

## Context

Sprint 6.1 (PR #12 `d1aafc8`, closed out in PR #13 `d1d022c`) shipped `merge/llm_cleanup.py` with `cleanup_ocr_segments` — opt-in OCR-artefact cleanup on `ON-SCREEN` + `BOTH` segments. Its close-out footer explicitly reserves ASR punctuation cleanup for Sprint 6.2: *"reuses the same Client, gates, and safety rails; new prompt template targeting punctuation + capitalization; extends the target-source gate."*

faster-whisper (large-v3-turbo) already produces reasonable punctuation on most languages, but it's inconsistent on short utterances and weak on languages where the model was under-trained. A local LLM post-pass can (a) tighten terminal punctuation, (b) fix sentence-initial capitalization, (c) add commas for parsed list structure — without touching any word content.

The user selected this as the next sprint for **infra-reuse momentum**: 6.1 already designed every moving part; 6.2 is the second-consumer test of that design.

## Tier

**T3** — python-coder + code-reviewer + tester. No architect. ~90 prod LOC new (near-copy of `cleanup_ocr_segments` with different prompt + target source; **no refactor of the 6.1 module**) + ~100 test LOC. Sits on the T2 / T3 border but touches 6 files and fans out through `_patched_pipeline`; T3 keeps the reviewer+tester safety net.

## Pre-existing surface (reuse, do not rebuild)

- `src/omniscribe/merge/llm_cleanup.py` — `cleanup_ocr_segments`, `_PROMPT_TEMPLATE`, `_MAX_LENGTH_MULTIPLIER`, `_TARGET_SOURCES`, module-top `try/except` Ollama import. Every gate + rail is already there.
- `src/omniscribe/config.py:51-52` area — existing `llm_cleanup_*` fields. Reuse `llm_cleanup_model`, `llm_cleanup_host`, `llm_cleanup_timeout_s` for ASR cleanup too. **Add only one new field: `llm_asr_cleanup_enabled`.**
- `src/omniscribe/cli.py:186-193` — `--llm-cleanup/--no-llm-cleanup` Typer shape. Mirror exactly.
- `src/omniscribe/cli.py:270-278` — call-site for `cleanup_ocr_segments`. ASR cleanup invocation goes immediately after it (same `try` block).
- `tests/conftest.py` — `mock_ollama_client` fixture already exists. Reuse directly.
- `tests/test_llm_cleanup.py` — 13 tests, patterns to mirror: import-site patching at `omniscribe.merge.llm_cleanup.Client`, `caplog` for WARNING assertions, `SimpleNamespace` for list-response shape.
- `tests/test_cli.py` — `_patched_pipeline` is already a 5-tuple. Extend to 6-tuple with `cleanup_speech_segments` mock (or keep 5-tuple — see design decision below).
- `pyproject.toml` — `integration` marker + `addopts` already in place. No change.
- `docs/plans/sprint-6-1-llm-cleanup-infra.md` — structural template for the new in-repo plan doc.

## Scope pruning (one round of challenge applied)

**Cut:**
- **Extracting shared private helpers** (`_ensure_client_and_gates`, `_run_cleanup_loop`) — rejected for this sprint (Rule of Three). Two consumers isn't enough. Sprint 6.3 (summary generation) is whole-transcript, not per-segment, so those extracted helpers wouldn't fit 6.3 either — we'd pay the abstraction cost twice. Ship `cleanup_speech_segments` as ~90 LOC of near-copy duplication. Re-evaluate extraction only if a third compatible consumer emerges.
- **Generalizing `cleanup_ocr_segments` into `cleanup_segments(kind="ocr"|"speech")`** — rejected. Adds a dispatcher for one call site, couples two features in one code path. Two focused functions preserve the 6.1 public API (zero caller refactor).
- **Whole-transcript (not per-segment) LLM call for ASR punctuation** — rejected. Whole-transcript calls would have richer cross-sentence context BUT (a) break the hallucination-length rail at segment granularity, (b) exceed context window on long videos, (c) complicate error isolation. 6.1's per-segment decision stands.
- **Separate `asr_cleanup_model` / `asr_cleanup_temperature` / `asr_cleanup_host` config fields** — rejected. One model handles both tasks; splitting config doubles surface area for no evidence-backed need. User can still override model via `OMNI_LLM_CLEANUP_MODEL` (applies to both).
- **Separate system prompt vs user prompt structure** — rejected. 6.1 uses a single user-message prompt; same shape here.
- **Language-aware prompt templating** — rejected. English-default prompt; the model's instruction-following will carry to most EN-adjacent languages. Per-language tuning is a future sprint if evidence emerges.
- **Retries on per-segment failures** — rejected, same as 6.1.
- **Running ASR cleanup on `BOTH` segments** — rejected. `BOTH` is claimed by the OCR pass (6.1 design decision). Target-source gate is strict: SPEECH only.
- **Combining `--llm-cleanup` and `--asr-cleanup` into a single `--llm-cleanup` mega-flag** — rejected. Users may want OCR cleanup without ASR cleanup (overlay-heavy videos with already-clean speech), or vice versa. Two flags for two behaviors.
- **Renaming `cleanup_ocr_segments` to `cleanup_screen_segments`** for symmetry with a new `cleanup_speech_segments` — rejected. Ship-stable public API; cross-sprint renames are a separate hygiene concern.

**Kept:**
- Per-segment calls, sequential (no parallelism).
- Single hardcoded prompt template (`_ASR_PROMPT_TEMPLATE`), module constant.
- Same `_MAX_LENGTH_MULTIPLIER = 2.0` hallucination rail + empty-response rail.
- Same lazy-import + `Client = None` fallback for graceful absence of `[llm]` extras.
- Strict target-source gate (`frozenset({"SPEECH"})` for the ASR function).
- `temperature=0.0` for determinism.

## Sprint 6.2 — Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `docs/plans/sprint-6-2-asr-punctuation-cleanup.md` (new) | **Canonical in-repo plan doc.** Copy this plan's Context + Pre-existing + Scope pruning + Deliverables + Acceptance + Design decisions + Out of scope + Verification sections verbatim. Mirror `docs/plans/sprint-6-1-llm-cleanup-infra.md` shape. Gets `## Close-out` footer appended post-merge. | +140 |
| `src/omniscribe/merge/llm_cleanup.py` (edit — **extend only, no refactor**) | **Do NOT refactor `cleanup_ocr_segments`.** It stays byte-identical so the 13 existing 6.1 tests pass unmodified. **Add:** `_ASR_PROMPT_TEMPLATE` module constant (draft: *"Add or correct punctuation and capitalization only. Preserve every word, number, and rare term exactly as given. Do not paraphrase, split, merge, reorder, or add content. If the text is already well-punctuated, return it unchanged. Respond with ONLY the corrected text, no explanations. TEXT: {text}"*). Add `_SPEECH_TARGET_SOURCES: frozenset[str] = frozenset({"SPEECH"})`. Add public `cleanup_speech_segments(segments, config) -> list[TranscriptSegment]` — near-copy of `cleanup_ocr_segments` with `_ASR_PROMPT_TEMPLATE`, `_SPEECH_TARGET_SOURCES`, and log prefix `"LLM ASR cleanup:"` (OCR keeps `"LLM cleanup:"` for backward compat). Same no-op short-circuit, missing-extra gate, availability gate, model-presence gate, per-segment loop, hallucination + empty rails, INFO count log. Module docstring updated to list both public functions **and explicitly note the historical log-prefix asymmetry** (`cleanup_ocr_segments` emits `"LLM cleanup: ..."` from 6.1, `cleanup_speech_segments` emits `"LLM ASR cleanup: ..."` introduced in 6.2 — the OCR prefix stayed unchanged to keep the sprint's zero-touch-to-6.1 principle). | +90 |
| `src/omniscribe/config.py` (edit) | Add one field after `llm_cleanup_timeout_s`: `llm_asr_cleanup_enabled: bool = False` (strict opt-in). Env binding: `OMNI_LLM_ASR_CLEANUP_ENABLED` — naming stays inside the existing `OMNI_LLM_CLEANUP_*` namespace for discoverability / ontology consistency with 6.1's `llm_cleanup_enabled`. No new validator — it's a bool. | +3 |
| `src/omniscribe/cli.py` (edit) | (a) Typer option after the `--llm-cleanup` block: `asr_cleanup: bool \| None = typer.Option(None, "--asr-cleanup/--no-asr-cleanup", help="Enable Ollama-backed punctuation + capitalization cleanup on [SPEECH] segments; overrides OMNI_LLM_ASR_CLEANUP_ENABLED. Requires: uv sync --extra llm.")`. (b) Config merge after the `llm_cleanup` merge block: `if asr_cleanup is not None: config = config.model_copy(update={"llm_asr_cleanup_enabled": asr_cleanup})`. (c) Invocation **immediately after** the `cleanup_ocr_segments` call (same `try` block): `if config.llm_asr_cleanup_enabled: segments = cleanup_speech_segments(segments, config)`. (d) Module-top import: `from omniscribe.merge.llm_cleanup import cleanup_ocr_segments, cleanup_speech_segments`. Note: the CLI flag stays the short `--asr-cleanup`; only the config field + env var carry the full `llm_asr_cleanup_enabled` / `OMNI_LLM_ASR_CLEANUP_ENABLED` namespacing. | +8 |
| `tests/test_llm_cleanup.py` (extend) | Straight sibling tests for `cleanup_speech_segments` — no parametrization (named tests are easier to debug). New tests: (a) SPEECH segment cleaned; (b) mixed batch `[SPEECH, ON-SCREEN, BOTH, SPEECH]` → exactly 2 chat calls + INFO log `"LLM ASR cleanup: 2 target segments processed (of 4 total), 2 modified"` (this also proves ON-SCREEN + BOTH passthrough — no need for dedicated single-source tests); (c) hallucination-length rail; (d) empty-response rail; (e) no-op short-circuit on ON-SCREEN-only input skips Ollama; (f) missing-extra gate. **Plus one cross-function invariant test:** `test_sequential_cleanup_respects_disjoint_targets` — call `cleanup_ocr_segments` then `cleanup_speech_segments` on a mixed batch; verify each segment was modified at most once AND its `source` field was never mutated. Plus one `@pytest.mark.integration` smoke with real Ollama. Reuse `mock_ollama_client` fixture. | +100 |
| `tests/test_config.py` (extend) | Tests: (a) `llm_asr_cleanup_enabled` default is `False`; (b) `OMNI_LLM_ASR_CLEANUP_ENABLED=true` env parses to `True`; (c) case-insensitivity for env bool. | +10 |
| `tests/test_cli.py` (extend) | Extend `_patched_pipeline` to return a **6-tuple** adding `cleanup_speech_segments` mock (or add a dedicated `_patched_speech_cleanup` helper — see design decision). Update all existing `_patched_pipeline` unpacking sites. New tests: (a) `--help` contains `--asr-cleanup` and `--no-asr-cleanup`; (b) `--asr-cleanup` → `cleanup_speech_segments` mock called once; (c) no flag/env → NOT called; (d) `OMNI_LLM_ASR_CLEANUP_ENABLED=true` + no flag → called; (e) `--no-asr-cleanup` + `OMNI_LLM_ASR_CLEANUP_ENABLED=true` → NOT called (negation-overrides-env, mirrors 6.1 review-gap fix); (f) both `--llm-cleanup --asr-cleanup` → both mocks called in order (OCR first, then ASR). | +90 |
| `README.md` (edit) | Under the existing "LLM OCR cleanup (optional)" Features bullet, add a sibling: `- **LLM ASR punctuation cleanup (optional)** — Improve punctuation and capitalization on speech segments via a local Ollama model. Opt-in with \`--asr-cleanup\`. Reuses the same `[llm]` extras and Ollama host as OCR cleanup.` Usage snippet: `omniscribe transcribe ./video.mp4 --llm-cleanup --asr-cleanup --output transcript.md`. | +6 |

**Explicitly NOT in deliverables:**
- `pyproject.toml` — no change (markers + deps + addopts already present).
- `tests/conftest.py` — `mock_ollama_client` already exists and is reusable.
- `.env.example` — file doesn't exist in repo.
- `IMPLEMENTATION_PLAN.md` — stays "In progress"; 5.4 + 5.5 still pending.

## Acceptance criteria

- [ ] `uv run ruff format --check .` clean.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run pytest -q` green. All prior 249 tests still pass (zero regressions — `cleanup_ocr_segments` is untouched). Expected count: ~265 (249 + ~16 new tests).
- [ ] `cleanup_speech_segments` is pure: input list untouched; returns a new list.
- [ ] **Target-source invariant:** SPEECH cleaned, ON-SCREEN passthrough, **BOTH passthrough** (strict gate).
- [ ] **Cross-function invariant:** running `cleanup_ocr_segments` followed by `cleanup_speech_segments` on a mixed batch processes each target source exactly once; a segment is never sent to both prompts.
- [ ] **All 6.1 gates + rails prove out for 6.2 via dedicated per-function tests** on `cleanup_speech_segments`: availability gate, model-presence gate, missing-extra gate, hallucination-length rail, empty-response rail.
- [ ] **Zero changes to `cleanup_ocr_segments`** — the 13 existing 6.1 tests in `test_llm_cleanup.py` pass byte-unchanged.
- [ ] **Cross-function `source`-field invariant:** `test_sequential_cleanup_respects_disjoint_targets` proves running both cleanups in sequence never mutates any segment's `source`, and each segment is modified at most once.
- [ ] **ASR log prefix is `"LLM ASR cleanup:"`** so log consumers can distinguish from OCR cleanup (`"LLM cleanup:"`).
- [ ] **No-op short-circuit:** `cleanup_speech_segments([on_screen_seg])` does NOT touch Ollama; logs `"no target-source segments"`.
- [ ] **Opt-in default:** `uv run omniscribe transcribe sample.mp4 -o out.json` (no flag, no env) produces byte-identical output vs pre-sprint-6.2 baseline. Neither cleanup function is invoked.
- [ ] **Both flags together:** `--llm-cleanup --asr-cleanup` invokes OCR cleanup first, then ASR cleanup, on the same segment list; final segment list contains cleaned text from both prompts (no conflict because target sources are disjoint).
- [ ] **Negation-overrides-env for the new flag:** `OMNI_LLM_ASR_CLEANUP_ENABLED=true` + `--no-asr-cleanup` → cleanup NOT invoked (mirrors the 6.1 review-gap fix).
- [ ] `pyproject.toml` `[project.dependencies]` unchanged. `[project.optional-dependencies.llm]` unchanged (still `ollama>=0.4`).
- [ ] CI does NOT install `[llm]` extras; `test_llm_cleanup.py` unit tests pass in CI via import-site patching (same as 6.1).

## Verification

```bash
uv run ruff format --check . && uv run ruff check .
uv run pytest -q                                       # default: skips integration
uv run pytest tests/test_llm_cleanup.py -v
uv run pytest tests/test_cli.py -v -k "llm or asr"
uv run pytest tests/test_config.py -v -k "asr"

# Integration (requires local Ollama + llama3.2:3b):
uv run pytest -m integration

# Smoke: both flags on a real video, compare outputs
uv run omniscribe transcribe sample.mp4 --output baseline.md
uv run omniscribe transcribe sample.mp4 --asr-cleanup --output asr.md
uv run omniscribe transcribe sample.mp4 --llm-cleanup --output ocr.md
uv run omniscribe transcribe sample.mp4 --llm-cleanup --asr-cleanup --output both.md
diff baseline.md asr.md        # diffs only on [SPEECH] lines
diff baseline.md ocr.md        # diffs only on [ON-SCREEN] / [BOTH] lines
diff asr.md both.md            # diffs only on [ON-SCREEN] / [BOTH] lines (ASR unchanged)
```

## Design decisions locked

- **Two focused functions, near-copy duplication** — Rule of Three (two consumers isn't enough for abstraction). Ship ~90 LOC of duplication in `cleanup_speech_segments` that mirrors `cleanup_ocr_segments`. If Sprint 6.3's summary feature ever produces a third per-segment consumer with compatible shape, extract then; today's duplication is honest and easy to diff.
- **Reuse `llm_cleanup_model/host/timeout_s`; add only `llm_asr_cleanup_enabled`** — one model for both tasks; avoids config surface doubling. Users who want separate models can re-run with different env vars.
- **Strict target-source gate on `cleanup_speech_segments` — SPEECH only, not BOTH** — BOTH is claimed by 6.1; cross-claim would double-process. Documented in module docstring alongside the 6.1 decision.
- **Per-segment, not whole-transcript** — same rationale as 6.1: rails work at segment granularity, bounded prompt length, error isolation.
- **Single hardcoded ASR prompt, module constant** — same discipline as 6.1. Per-language or per-platform prompts are a future sprint.
- **`--asr-cleanup/--no-asr-cleanup` is independent of `--llm-cleanup`** — two flags, two behaviors, combinable. No consolidated mega-flag.
- **OCR cleanup runs before ASR cleanup when both enabled** — deterministic order. Target sources are disjoint so order doesn't matter semantically, but a stable order simplifies log reading and test assertions.
- **`_patched_pipeline` grows to 6-tuple** — one more mock, one more unpacking update across call sites. Alternative (dedicated `_patched_speech_cleanup` helper) was considered and rejected: the tuple is the established pattern since sprint-6.1, consistency wins over novelty.
- **`cleanup_ocr_segments` is untouched** — zero edits to 6.1 code path; the 13 existing 6.1 tests serve as the regression baseline without modification.
- **Opt-in default (`llm_asr_cleanup_enabled=False`)** — strict, same as 6.1.
- **No language-aware prompt branching** — English-default prompt works across EN-adjacent languages for basic punctuation/casing. Revisit on evidence.
- **Integration smoke tests for 6.2 live alongside 6.1's in `test_llm_cleanup.py`** — no new file, one marker applies to both.

## Critical files

- `G:\git\OmniScribe\docs\plans\sprint-6-2-asr-punctuation-cleanup.md` (new) — canonical plan.
- `G:\git\OmniScribe\src\omniscribe\merge\llm_cleanup.py` — extension only (new `cleanup_speech_segments` + `_ASR_PROMPT_TEMPLATE` + `_SPEECH_TARGET_SOURCES`; zero edits to existing code).
- `G:\git\OmniScribe\src\omniscribe\config.py` — one new field.
- `G:\git\OmniScribe\src\omniscribe\cli.py` — flag + merge + invocation.
- `G:\git\OmniScribe\tests\test_llm_cleanup.py` — parametrized / sibling tests.
- `G:\git\OmniScribe\tests\test_cli.py` — flag + env + negation-overrides-env tests; `_patched_pipeline` → 6-tuple.
- `G:\git\OmniScribe\tests\test_config.py` — three small tests for `llm_asr_cleanup_enabled`.
- `G:\git\OmniScribe\README.md` — one bullet + one snippet.

## Libraries to reuse

`ollama.Client` (already in `[llm]` extra, consumed by 6.1); the 6.1 gate / rail patterns (duplicated, not extracted — see design decisions); `OmniScribeError`; `TranscriptSegment.model_copy`; `typer.Option` pattern from `--llm-cleanup`; `mock_ollama_client` conftest fixture; `_patched_pipeline` CLI test helper.

## Out of scope

- **Summary generation to `<output>.summary.txt`** — Sprint 6.3.
- **Multi-provider LLM abstraction** — rejected; one provider continues.
- **Per-language / per-platform prompt templates** — deferred.
- **Batch mode** (`omniscribe batch urls.txt`) — Sprint 5.4 under existing numbering.
- **Docker / docker-compose** — Phase 5.5.
- **Streaming, parallelism, prompt caching, retries** — all continue to be rejected (same as 6.1).
- **Running ASR cleanup on BOTH segments** — strict SPEECH-only gate; BOTH is claimed by OCR cleanup.
- **Renaming `cleanup_ocr_segments`** for naming symmetry — public API stability wins.
- **`IMPLEMENTATION_PLAN.md` status bump** — stays "In progress".

## Close-out

Sprint 6.2 is **complete**. Shipped via one squash-merged PR against `main`:

| Sprint | PR | SHA | Summary |
|---|---|---|---|
| 6.2 | #14 | `3339f89` | Opt-in Ollama-backed per-segment punctuation + capitalization cleanup on `[SPEECH]` segments. New `cleanup_speech_segments` in `merge/llm_cleanup.py` as a near-copy of Sprint 6.1's `cleanup_ocr_segments` (Rule of Three duplication held). Strict `_SPEECH_TARGET_SOURCES = frozenset({"SPEECH"})` gate — `BOTH` stays claimed by OCR cleanup. `"LLM ASR cleanup:"` log prefix distinguishes from 6.1's `"LLM cleanup:"` (module docstring documents the asymmetry). One new config field (`llm_asr_cleanup_enabled: bool = False`); `--asr-cleanup/--no-asr-cleanup` CLI flag; `_patched_pipeline` 5-tuple → 6-tuple. `cleanup_ocr_segments` byte-locked — 13 existing 6.1 tests unchanged. Zero new runtime deps. |

Net test delta at ship: 249 → **268** tests passing (+19 new: 8 `test_llm_cleanup.py` per-function gate/rail/invariant + 1 integration smoke, 6 `test_cli.py` flag/env/negation-overrides-env/both-flags, 3 `test_config.py` env round-trips, +1 review-gap fixup cross-function invariant). Subsequent review-gap fixup added 2 more per-function gate tests for `cleanup_speech_segments` (availability + model-presence), bringing the final count to **270**. Integration smoke (`@pytest.mark.integration`) remains excluded by `addopts`; runs locally with `uv run pytest -m integration`.

**Design-decision call-outs validated at ship:**
- **Duplication over abstraction (Rule of Three)** — two consumers (`cleanup_ocr_segments` + `cleanup_speech_segments`) is not yet enough to extract shared helpers. Sprint 6.3's whole-transcript summary won't fit a per-segment helper anyway. Re-evaluate only if a third compatible consumer emerges.
- **Module-top `try/except ImportError: Client = None`** — the 6.1 pattern held for 6.2. Tests patch at module scope, CLI import stays lazy-safe.
- **`BOTH` passthrough on the ASR side** — strict SPEECH-only gate preserved the disjoint-targets invariant; the cross-function test `test_sequential_cleanup_respects_disjoint_targets` proves neither `source` is mutated and no segment is processed twice.
- **Two flags, not a mega-flag** — `--llm-cleanup` and `--asr-cleanup` remain independent. Combined invocation runs OCR first, then ASR, in a deterministic order.

Follow-ups explicitly **deferred** out of Sprint 6.2:

- **Sprint 6.3** — Summary generation to `<output>.summary.txt`. Reuses the same `Client` + availability gate; different prompt shape (whole-transcript input) and output path. Third consumer would not share the per-segment loop, so 6.2's duplication is not a blocker.
- **Per-language / per-platform prompt templates** — deferred; English-default prompt held for the tested locales.
- **Multi-provider LLM abstraction, prompt caching, streaming, per-segment parallelism, retries** — all continue to be rejected (same as 6.1). Revisit only on concrete evidence.
- **`_patched_pipeline` tuple-growth** — the 6-tuple works but is a code-smell warning. If Sprint 6.3 grows it to 7-tuple, convert to a `NamedTuple`/dataclass in that sprint.
- `IMPLEMENTATION_PLAN.md` Phase 5 status stays "In progress" — 5.4 (batch mode) and 5.5 (Docker) are the remaining Phase 5 sprints. 5.3 (LLM cleanup) is now functionally complete across OCR (6.1) and ASR (6.2); 6.3 summary is optional polish.


# OmniScribe — Sprint 6.1: LLM Cleanup Infrastructure + OCR Artifact Fix

## Context

Phase 2 OCR (RapidOCR + CLAHE + dedup) and Phase 4 merge-engine (`merge_channels`) ship transcripts where on-screen text often contains recognisable OCR artefacts — broken words, transposed letters, missing spaces — that survive the fuzzy deduplicator and the cross-source merge. The text-level dedup (`rapidfuzz.fuzz.WRatio`) collapses near-identical repeats but does not *fix* the underlying noise; merge writes `speech.text` on `BOTH` collapse but non-overlapping ON-SCREEN segments and OCR-origin tokens in `BOTH` pass through unchanged.

Sprint 6.1 introduces **opt-in, per-segment LLM cleanup** on `ON-SCREEN` and `BOTH` segments via a local Ollama model. Infra must be reusable for later sprints (6.2 ASR punctuation, 6.3 summary) but **not pre-built** for them — one narrow feature, one narrow code path.

## Scope — Sprint 6.1 only (T3)

**Goal:** Ship an opt-in, Ollama-backed per-segment OCR-artefact cleanup pass wired through config + CLI, with safety rails against hallucination and zero cost to users who don't opt in.

Sprints 6.2 (ASR punctuation cleanup on `SPEECH` segments) and 6.3 (summary generation to `<output>.summary.txt`) are explicitly out-of-scope. Infra must be reusable but NOT pre-built for them.

## Pre-existing surface (reuse, do not rebuild)

- `src/omniscribe/cli.py:270-277` — `merge_channels(...)` finishes at line 274; `transcript = Transcript(segments=segments, ...)` at line 278. **Insertion point: between line 277 (blank) and 278**, mutating the `segments` list before Transcript construction.
- `src/omniscribe/cli.py:186-193` — `--scene-change/--no-scene-change` Typer option shape (`bool | None = None`). Mirror exactly for `--llm-cleanup/--no-llm-cleanup`.
- `src/omniscribe/cli.py:213-222` — `config.model_copy(update={...})` merge pattern for Optional[bool] flags. Extend after line 222.
- `src/omniscribe/config.py:51-52` (pair of `scene_change_*` fields) and `:94-104` (threshold validator). Template for four new `llm_cleanup_*` fields + one validator.
- `src/omniscribe/output.py:27-35` — `TranscriptSegment` has `source: Literal["SPEECH", "ON-SCREEN", "BOTH"]`. Gate on `source in ("ON-SCREEN", "BOTH")`. **No schema change.**
- `src/omniscribe/errors.py` — `OmniScribeError` base class. All new error paths use it.
- `tests/test_cli.py:45-56` — `_patched_pipeline` helper (4-tuple currently: download/extract/whisper/ocr). Extend to 5-tuple with `cleanup_ocr_segments` mock. **Update ALL call sites that unpack the tuple.**
- `tests/conftest.py` — existing fixtures. Add a new `mock_ollama_client` fixture.
- `pyproject.toml` — `[project.optional-dependencies.llm] = ["ollama>=0.4"]` already present. **Do NOT** add it; only register pytest markers.

## Deliverables

| Path | Purpose |
|---|---|
| `docs/plans/sprint-6-1-llm-cleanup-infra.md` (new) | **This file.** Canonical in-repo plan doc. Will get a `## Close-out` footer appended by the PO after merge. |
| `src/omniscribe/merge/__init__.py` (new) | Package marker. Docstring only. |
| `src/omniscribe/merge/llm_cleanup.py` (new, ~92 LOC) | Module constants: `_PROMPT_TEMPLATE` (single hardcoded prompt); `_MAX_LENGTH_MULTIPLIER = 2.0`; `_TARGET_SOURCES = frozenset({"ON-SCREEN", "BOTH"})`. **Lazy import of `ollama` inside the function** (NOT module-top). Public `cleanup_ocr_segments(segments, config) -> list[TranscriptSegment]` with no-op short-circuit, availability gate, model-presence gate, per-segment chat call, empty-response + length-multiplier safety rails, immutable input. |
| `src/omniscribe/config.py` (edit) | Add four `llm_cleanup_*` fields + one timeout validator. Env vars auto-bind via `env_prefix="OMNI_"`. |
| `src/omniscribe/cli.py` (edit) | Add `--llm-cleanup/--no-llm-cleanup` Typer option, config merge, and the cleanup call inside the existing `try` block so `OmniScribeError` flows to the CLI error path. |
| `pyproject.toml` (edit) | Register `integration` pytest marker; add `addopts = "-m 'not integration'"` so CI skips integration by default. NO dependency changes. |
| `tests/conftest.py` (extend) | New `mock_ollama_client` fixture returning a pre-shaped `MagicMock`. |
| `tests/test_llm_cleanup.py` (new, ~160 LOC) | Twelve unit tests covering target-source gating, availability / model-presence gates, hallucination + empty-response rails, lazy-import failure path, no-op short-circuit, input-immutability, narrow-exception propagation. Plus one `@pytest.mark.integration` smoke. |
| `tests/test_config.py` (extend) | Parametrized rejection of non-positive `llm_cleanup_timeout_s`, lower-edge acceptance, env round-trip, documented defaults. |
| `tests/test_cli.py` (extend) | Extend `_patched_pipeline` to 5-tuple. `--help` surface, flag enables, env enables, error propagation, `--no-ocr` + `--llm-cleanup` compatibility. |
| `README.md` (edit) | Features bullet + a two-line usage snippet. NO `IMPLEMENTATION_PLAN.md` edit — Phase 5 stays "In progress". |

## Acceptance criteria

- [ ] `uv run ruff format --check .` clean.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run pytest -q` green (integration excluded by addopts). All prior 224 tests still pass; new tests all green.
- [ ] Target-source invariant: SPEECH segments unchanged. BOTH and ON-SCREEN segments get cleanup.
- [ ] All gate / rail assertions proven by the `test_llm_cleanup.py` suite.
- [ ] No-op short-circuit: SPEECH-only input does NOT import `ollama` or construct `Client`.
- [ ] Opt-in default: running without `--llm-cleanup` produces byte-identical output vs pre-sprint-6.1 baseline.
- [ ] `pyproject.toml` `[project.dependencies]` unchanged. `[project.optional-dependencies.llm]` unchanged.

## Design decisions locked

- Per-segment LLM calls, not whole-transcript call — bounded prompt, per-segment error isolation, hallucination rail only works at segment granularity.
- Ollama-only, not pluggable — YAGNI.
- Lazy `ollama` import — mandatory; users without `[llm]` extras must not see `ImportError` at startup.
- Narrow exception catch in availability gate — bare `except Exception` would mask our own bugs.
- `_MAX_LENGTH_MULTIPLIER = 2.0` — catches pathological hallucination; typical OCR fixes are ±10% length. Non-negotiable.
- Empty-response rail treats refusal identically to hallucination.
- Default model `llama3.2:3b` — smallest pull (2.0 GB), CPU-viable, widely pulled. User can override via `OMNI_LLM_CLEANUP_MODEL`.
- Opt-in default (`llm_cleanup_enabled=False`) — strict.
- `BOTH` segments are cleaned — Phase 4 emits `speech.text` on collapse but OCR-origin tokens can still bleed through; cleanup is valuable. Document in `llm_cleanup.py` module docstring.
- `SPEECH` segments are NOT cleaned — that's Sprint 6.2.
- Single hardcoded prompt — module constant, no runtime override. Phase 6.4+ if anyone asks.
- No new `OmniScribeError` subclass — all gate failures share exit 1 + clear message; subclass adds no caller value.

## Out of scope

- ASR punctuation cleanup on SPEECH — Sprint 6.2.
- Summary generation — Sprint 6.3.
- Multi-provider LLM abstraction.
- Prompt caching, streaming, parallelism, retries.
- Pluggable / per-platform prompt templates.
- Any change to `IMPLEMENTATION_PLAN.md`.
- Any git ops (commit/push/PR).

## Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest -q              # full suite; integration excluded by addopts
uv run pytest tests/test_llm_cleanup.py -v
uv run pytest tests/test_config.py -v -k llm
uv run pytest tests/test_cli.py -v -k llm
```

All must be green. If anything fails, fix it before returning.

## Close-out

Sprint 6.1 is **complete**. Shipped via one squash-merged PR against `main`:

| Sprint | PR | SHA | Summary |
|---|---|---|---|
| 6.1 | #12 | `d1aafc8` | Opt-in Ollama-backed per-segment OCR-artefact cleanup on `[ON-SCREEN]` + `[BOTH]` segments. New `merge/llm_cleanup.py` with no-op short-circuit, missing-extra gate, availability gate (narrow `ConnectionError` / `TimeoutError` / `OSError` / `httpx.HTTPError`), model-presence gate (defensive dual-attribute parse for ollama-python churn), per-segment `chat` loop with 2.0× hallucination length rail + empty-response rail, INFO count log. Four `llm_cleanup_*` config fields + `--llm-cleanup/--no-llm-cleanup` CLI flag. Zero new runtime deps. |

Net test delta at ship: 224 → **249** tests passing (+25 new: 13 `test_llm_cleanup.py` + 7 `test_cli.py` + 4 `test_config.py` + 1 review-gap fixup). Integration smoke (`@pytest.mark.integration`) excluded by `addopts = "-m 'not integration'"` on CI; runs locally with `uv run pytest -m integration`.

**Design refinement during implementation (flagged in PR):** the plan originally specified a function-local lazy `from ollama import Client`, but tests patch `omniscribe.merge.llm_cleanup.Client` at the module scope `unittest.mock.patch` targets — a function-local import leaves that name unbound and all patches fail. Shipped pattern: **module-top `try/except ImportError: Client = None`**, with a missing-extra gate inside the function. Still satisfies the "no `ImportError` at CLI startup" constraint, makes tests patch-natural.

Follow-ups explicitly **deferred** out of Sprint 6.1 and tracked as next sprints:

- **Sprint 6.2** — ASR punctuation cleanup on `SPEECH` segments. Reuses the same `Client`, gates, and safety rails; new prompt template targeting punctuation + capitalization; extends the target-source gate.
- **Sprint 6.3** — Summary generation to a separate `<output>.summary.txt` artifact. Reuses the same `Client` + availability gate; different prompt shape (whole-transcript input) and output path.
- Multi-provider LLM abstraction, prompt caching, streaming, per-segment parallelism, retries, pluggable prompt templates — all remain rejected. Revisit only on concrete evidence.
- `IMPLEMENTATION_PLAN.md` Phase 5 status stays "In progress" — 5.4 (batch mode) and 5.5 (Docker) are the remaining Phase 5 sprints.

"""Opt-in, Ollama-backed per-segment LLM cleanup (Sprints 6.1 + 6.2).

Public entry points:

- :func:`cleanup_ocr_segments` (Sprint 6.1) — fixes OCR artefacts on
  ``ON-SCREEN`` and ``BOTH`` segments. Emits log lines prefixed
  ``"LLM cleanup:"``.
- :func:`cleanup_speech_segments` (Sprint 6.2) — fixes punctuation and
  capitalization on ``SPEECH`` segments only. Emits log lines prefixed
  ``"LLM ASR cleanup:"``.

**Target-source partitioning is disjoint by design:** ``BOTH`` is claimed by
the OCR pass (Phase-4 collapse can leak OCR artefacts into the merged text);
``SPEECH`` is claimed by the ASR pass. A single segment is never sent through
both prompts, which keeps the cross-function invariant simple: run both
cleanups sequentially on a mixed batch and every segment is modified at most
once, with its ``source`` field never mutated.

Both functions run after :func:`omniscribe.output.merge_channels` and before
:class:`omniscribe.output.Transcript` construction.

**Log-prefix asymmetry (historical):** ``cleanup_ocr_segments`` emits
``"LLM cleanup: ..."`` (shipped unchanged since 6.1). ``cleanup_speech_segments``
emits ``"LLM ASR cleanup: ..."`` (introduced in 6.2). The OCR prefix stayed
unchanged to honour the sprint-6.2 zero-touch-to-6.1 principle — renaming
would have forced edits to the 13 existing 6.1 tests and risked byte drift.

The ``ollama`` dependency is imported at module top via ``try/except
ImportError`` so (a) users who don't install the ``[llm]`` extras never see
an ``ImportError`` at CLI startup; (b) tests can patch
``omniscribe.merge.llm_cleanup.Client`` directly. Function-local lazy imports
would hide ``Client`` from ``unittest.mock.patch`` because the bound name
wouldn't exist at module scope.

The no-op short-circuit in each function (empty input, or no segments with
the function's target source) returns before any network call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from omniscribe.config import OmniScribeConfig
    from omniscribe.output import TranscriptSegment

logger = logging.getLogger(__name__)

# Import ``ollama`` at module top in a try/except so (a) users without the
# ``[llm]`` extras don't see an ImportError at CLI startup — they only see an
# actionable OmniScribeError the moment they opt in; (b) tests can patch
# ``omniscribe.merge.llm_cleanup.Client`` / ``.httpx`` directly. A function-
# local ``from ollama import Client`` would hide ``Client`` from tests'
# ``unittest.mock.patch`` because the bound name wouldn't exist at the
# module scope ``patch`` targets.
try:
    from ollama import Client
except ImportError:  # pragma: no cover — exercised via monkeypatch in tests
    Client = None  # type: ignore[assignment,misc]

try:
    import httpx
except ImportError:  # pragma: no cover — httpx ships transitively with ollama>=0.4
    httpx = None  # type: ignore[assignment]

# Single hardcoded prompt — module constant, no runtime override. Narrow and
# conservative wording: fix only OCR errors, preserve everything else, respond
# with just the corrected text so we can accept the response verbatim.
_PROMPT_TEMPLATE = (
    "Fix only OCR errors (broken words, transposed letters, missing spaces). "
    "Preserve all intentional formatting, rare words, and punctuation. "
    "Respond with ONLY the corrected text, no explanations. TEXT: {text}"
)

# Hallucination rail: accept the cleaned text only if it stays within
# 2.0x the original length. Typical OCR fixes are ±10% length; anything longer
# is almost certainly the model adding commentary or fabricating content.
_MAX_LENGTH_MULTIPLIER: float = 2.0

# Segments with these source values get cleanup; everything else passes through
# untouched. Deliberately a frozenset so the membership check is O(1) and the
# set cannot be mutated by callers.
_TARGET_SOURCES: frozenset[str] = frozenset({"ON-SCREEN", "BOTH"})

# Sprint 6.2 ASR cleanup prompt — punctuation + capitalization only. Narrow
# and conservative: every word / number / rare term must survive verbatim; the
# model should return the original text unchanged when it is already clean.
_ASR_PROMPT_TEMPLATE = (
    "Add or correct punctuation and capitalization only. "
    "Preserve every word, number, and rare term exactly as given. "
    "Do not paraphrase, split, merge, reorder, or add content. "
    "If the text is already well-punctuated, return it unchanged. "
    "Respond with ONLY the corrected text, no explanations. TEXT: {text}"
)

# Sprint 6.2 target-source gate: SPEECH only. ``BOTH`` is claimed by
# :func:`cleanup_ocr_segments` — cross-claim would double-process.
_SPEECH_TARGET_SOURCES: frozenset[str] = frozenset({"SPEECH"})


def cleanup_ocr_segments(
    segments: list[TranscriptSegment],
    config: OmniScribeConfig,
) -> list[TranscriptSegment]:
    """Return a new segment list with OCR artefacts cleaned on target sources.

    Target sources are ``ON-SCREEN`` and ``BOTH``. ``SPEECH`` segments are
    passed through byte-identical. The input list is not mutated; the returned
    list is always a new object.

    Gates (fail-fast, all raise :class:`OmniScribeError`):

    1. **No-op short-circuit** — if no segment has a target source, return the
       input list unchanged without importing ``ollama`` or opening a client.
    2. **Lazy import** — ``ImportError`` on ``ollama`` is translated to an
       actionable install hint.
    3. **Availability gate** — ``client.list()`` must succeed; connection /
       timeout / generic HTTP errors translate to a message pointing at the
       configured host and the ``--no-llm-cleanup`` escape hatch.
    4. **Model-presence gate** — the configured model must appear in the
       ``list()`` response; absence translates to an ``ollama pull`` hint.

    Safety rails (per-segment, non-fatal — log a warning and keep the
    original text):

    - Empty / whitespace-only response.
    - Response longer than ``len(original) * _MAX_LENGTH_MULTIPLIER``.
    """
    total = len(segments)
    # (1) No-op short-circuit BEFORE any import. SPEECH-only or empty input
    # must not construct a Client, must not call list(), must not import
    # ollama. This is the fast path for users who opt in globally but run on a
    # video with no on-screen text.
    if not any(seg.source in _TARGET_SOURCES for seg in segments):
        logger.info("LLM cleanup: no target-source segments; skipping")
        return segments

    # (2) Missing-extra gate. If ``Client`` is ``None``, the ``[llm]`` extras
    # weren't installed. Surface a single actionable line — never an
    # ImportError traceback.
    if Client is None:
        raise OmniScribeError(
            "LLM cleanup requires the 'ollama' package. Install with: uv sync --extra llm"
        )

    client = Client(host=config.llm_cleanup_host, timeout=config.llm_cleanup_timeout_s)

    # (3) Availability gate. Narrow catch: a bare ``except Exception`` would
    # mask bugs in our own parsing below. ``httpx.HTTPError`` covers connect /
    # timeout / network-layer failures surfaced by the ollama client.
    http_error_types: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, OSError)
    if httpx is not None:
        http_error_types = (*http_error_types, httpx.HTTPError)
    try:
        tags = client.list()
    except http_error_types as e:
        raise OmniScribeError(
            f"Ollama not reachable at {config.llm_cleanup_host}: {e}. "
            "Start Ollama or use --no-llm-cleanup."
        ) from e

    # (4) Model-presence gate. Defensively check both ``.model`` and ``.name``
    # attributes — ollama-python has churned on which one it exposes. Also
    # tolerate plain dicts in case the shape changes again.
    model_name = config.llm_cleanup_model
    available_models: list[str] = []
    models_iter = getattr(tags, "models", None) or (
        tags.get("models") if isinstance(tags, dict) else None
    )
    if models_iter is None:
        models_iter = tags  # last-ditch: maybe it's already a sequence
    for entry in models_iter:
        name = getattr(entry, "model", None) or getattr(entry, "name", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("model") or entry.get("name")
        if name is not None:
            available_models.append(str(name))

    if model_name not in available_models:
        raise OmniScribeError(f"Model '{model_name}' not pulled. Run: ollama pull {model_name}")

    # Per-segment cleanup loop. Sequential — parallelism is out of scope
    # (Sprint 6.1 design decision). Each segment is an independent call so
    # errors isolate per-segment.
    cleaned_segments: list[TranscriptSegment] = []
    processed = 0
    modified = 0
    for seg in segments:
        if seg.source not in _TARGET_SOURCES:
            cleaned_segments.append(seg)
            continue

        processed += 1
        response = client.chat(
            model=model_name,
            messages=[{"role": "user", "content": _PROMPT_TEMPLATE.format(text=seg.text)}],
            options={"temperature": 0.0},
            keep_alive=config.llm_cleanup_keep_alive_s,
        )

        # Response shape: ollama-python returns ``{"message": {"content": ...}}``
        # as a dict-like; also tolerate attribute-style access for forward-compat.
        cleaned: str | None = None
        message = response.get("message") if isinstance(response, dict) else None
        if message is None:
            message = getattr(response, "message", None)
        if isinstance(message, dict):
            cleaned = message.get("content")
        else:
            cleaned = getattr(message, "content", None)

        if cleaned is not None:
            cleaned = cleaned.replace("\r", "")

        # Safety rails — empty/whitespace-only AND overlong responses both keep
        # the original text. Empty is treated identically to hallucination:
        # both signal the model wasn't useful on this segment.
        if not cleaned or not cleaned.strip():
            logger.warning(
                "LLM cleanup: empty response for segment at %.2fs; keeping original",
                seg.start,
            )
            cleaned_segments.append(seg)
            continue
        if len(cleaned) > len(seg.text) * _MAX_LENGTH_MULTIPLIER:
            logger.warning(
                "LLM cleanup: response length %d exceeds %.1fx original (%d) "
                "for segment at %.2fs; keeping original",
                len(cleaned),
                _MAX_LENGTH_MULTIPLIER,
                len(seg.text),
                seg.start,
            )
            cleaned_segments.append(seg)
            continue

        cleaned_segments.append(seg.model_copy(update={"text": cleaned}))
        modified += 1

    logger.info(
        "LLM cleanup: %d target segments processed (of %d total), %d modified",
        processed,
        total,
        modified,
    )
    return cleaned_segments


def cleanup_speech_segments(
    segments: list[TranscriptSegment],
    config: OmniScribeConfig,
) -> list[TranscriptSegment]:
    """Return a new segment list with punctuation + capitalization cleanup on SPEECH.

    Sprint 6.2 sibling to :func:`cleanup_ocr_segments`. Target source is
    strictly ``SPEECH``; ``ON-SCREEN`` and ``BOTH`` pass through byte-
    identical (``BOTH`` is claimed by the OCR pass). The input list is not
    mutated; the returned list is always a new object.

    Gates (fail-fast, all raise :class:`OmniScribeError`):

    1. **No-op short-circuit** — if no segment has a ``SPEECH`` source,
       return the input list unchanged without constructing a client.
    2. **Missing-extra gate** — ``Client is None`` translates to an
       actionable install hint.
    3. **Availability gate** — ``client.list()`` must succeed.
    4. **Model-presence gate** — the configured model must appear in the
       ``list()`` response.

    Safety rails (per-segment, non-fatal — log a warning and keep the
    original text):

    - Empty / whitespace-only response.
    - Response longer than ``len(original) * _MAX_LENGTH_MULTIPLIER``.

    Log prefix is ``"LLM ASR cleanup:"`` so log consumers can distinguish
    from OCR cleanup's ``"LLM cleanup:"`` prefix.
    """
    total = len(segments)
    # (1) No-op short-circuit BEFORE any Client construction. ON-SCREEN /
    # BOTH-only or empty input must not open a connection.
    if not any(seg.source in _SPEECH_TARGET_SOURCES for seg in segments):
        logger.info("LLM ASR cleanup: no target-source segments; skipping")
        return segments

    # (2) Missing-extra gate. Mirror the 6.1 error message exactly — users
    # don't need to know which feature they opted into; the remediation is
    # the same.
    if Client is None:
        raise OmniScribeError(
            "LLM cleanup requires the 'ollama' package. Install with: uv sync --extra llm"
        )

    client = Client(host=config.llm_cleanup_host, timeout=config.llm_cleanup_timeout_s)

    # (3) Availability gate.
    http_error_types: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, OSError)
    if httpx is not None:
        http_error_types = (*http_error_types, httpx.HTTPError)
    try:
        tags = client.list()
    except http_error_types as e:
        raise OmniScribeError(
            f"Ollama not reachable at {config.llm_cleanup_host}: {e}. "
            "Start Ollama or use --no-asr-cleanup."
        ) from e

    # (4) Model-presence gate. Same tolerant shape handling as the OCR
    # function — ollama-python has churned on ``.model`` vs ``.name``.
    model_name = config.llm_cleanup_model
    available_models: list[str] = []
    models_iter = getattr(tags, "models", None) or (
        tags.get("models") if isinstance(tags, dict) else None
    )
    if models_iter is None:
        models_iter = tags
    for entry in models_iter:
        name = getattr(entry, "model", None) or getattr(entry, "name", None)
        if name is None and isinstance(entry, dict):
            name = entry.get("model") or entry.get("name")
        if name is not None:
            available_models.append(str(name))

    if model_name not in available_models:
        raise OmniScribeError(f"Model '{model_name}' not pulled. Run: ollama pull {model_name}")

    # Per-segment cleanup loop. Sequential — parallelism is out of scope.
    cleaned_segments: list[TranscriptSegment] = []
    processed = 0
    modified = 0
    for seg in segments:
        if seg.source not in _SPEECH_TARGET_SOURCES:
            cleaned_segments.append(seg)
            continue

        processed += 1
        response = client.chat(
            model=model_name,
            messages=[{"role": "user", "content": _ASR_PROMPT_TEMPLATE.format(text=seg.text)}],
            options={"temperature": 0.0},
            keep_alive=config.llm_cleanup_keep_alive_s,
        )

        cleaned: str | None = None
        message = response.get("message") if isinstance(response, dict) else None
        if message is None:
            message = getattr(response, "message", None)
        if isinstance(message, dict):
            cleaned = message.get("content")
        else:
            cleaned = getattr(message, "content", None)

        if cleaned is not None:
            cleaned = cleaned.replace("\r", "")

        if not cleaned or not cleaned.strip():
            logger.warning(
                "LLM ASR cleanup: empty response for segment at %.2fs; keeping original",
                seg.start,
            )
            cleaned_segments.append(seg)
            continue
        if len(cleaned) > len(seg.text) * _MAX_LENGTH_MULTIPLIER:
            logger.warning(
                "LLM ASR cleanup: response length %d exceeds %.1fx original (%d) "
                "for segment at %.2fs; keeping original",
                len(cleaned),
                _MAX_LENGTH_MULTIPLIER,
                len(seg.text),
                seg.start,
            )
            cleaned_segments.append(seg)
            continue

        cleaned_segments.append(seg.model_copy(update={"text": cleaned}))
        modified += 1

    logger.info(
        "LLM ASR cleanup: %d target segments processed (of %d total), %d modified",
        processed,
        total,
        modified,
    )
    return cleaned_segments

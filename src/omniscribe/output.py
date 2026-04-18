"""Transcript data models and JSON writer."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Soft cap: above this cross-product the merge warns but still runs.
# RapidFuzz handles ~1M comparisons in <1s; this surfaces pathological inputs
# rather than engineering for them.
_MERGE_SOFT_CAP: int = 5_000_000

# Collapse whitespace (including newlines) to a single space on [BOTH] emit
# so multi-line cues don't corrupt downstream format writers.
_WHITESPACE_RE = re.compile(r"\s+")


class TranscriptSegment(BaseModel):
    """A single transcript segment (speech or on-screen text)."""

    start: float
    end: float
    text: str
    source: Literal["SPEECH", "ON-SCREEN", "BOTH"] = "SPEECH"
    confidence: float | None = None
    language: str | None = None


class Transcript(BaseModel):
    """Full transcript: ordered segments plus detected language."""

    segments: list[TranscriptSegment]
    language: str


def write_json(transcript: Transcript, path: Path) -> None:
    """Write ``transcript`` as pretty JSON to ``path`` (UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")


def _overlaps(speech: TranscriptSegment, ocr: TranscriptSegment) -> bool:
    """Strict temporal overlap: touching boundaries do NOT overlap.

    Two segments overlap iff ``speech.start < ocr.end`` AND ``ocr.start < speech.end``.
    Example: ``speech=[0.0, 5.0]`` and ``ocr=[5.0, 10.0]`` do NOT overlap.
    """
    return speech.start < ocr.end and ocr.start < speech.end


def _normalize_cue(text: str) -> str:
    """Collapse internal whitespace (incl. ``\\n``/``\\r``) to single spaces and strip."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def merge_channels(
    speech: list[TranscriptSegment],
    ocr: list[TranscriptSegment],
    threshold: float,
) -> list[TranscriptSegment]:
    """Cross-source dedup: collapse overlapping, text-similar speech+OCR into ``[BOTH]``.

    Algorithm
    ---------
    For each SPEECH segment, find all OCR segments that temporally overlap it
    (strict rule: ``speech.start < ocr.end AND ocr.start < speech.end`` —
    touching boundaries do NOT overlap). Among overlapping OCR segments,
    compute ``rapidfuzz.fuzz.WRatio(speech.text, ocr.text)`` (returns 0-100).
    Any OCR meeting ``WRatio >= threshold * 100`` is a candidate to collapse
    with the speech segment. Pick the candidate with the highest WRatio
    (ties → earliest ``ocr.start``) and emit::

        TranscriptSegment(
            source="BOTH",
            text=speech.text,                     # whitespace-normalized
            start=min(speech.start, ocr.start),
            end=max(speech.end, ocr.end),
            confidence=speech.confidence,         # not max() — scales differ
            language=speech.language,
        )

    The chosen OCR segment is **consumed**; other overlapping OCR segments
    stay as ``[ON-SCREEN]``. Each OCR segment collapses at most once.

    Trade-offs
    ----------
    * **Lossy on collapse.** The emitted ``text`` is ``speech.text`` even when
      the OCR segment holds richer detail (e.g. speech: "as I mentioned";
      OCR: "AcmeCloud Enterprise v4.2"). Concatenating OCR onto speech would
      produce awkward output; users who need the OCR detail can read the
      consumed OCR segment (currently dropped — revisit if users report loss).
    * **``confidence=speech.confidence``.** Whisper confidence is log-prob
      derived; RapidOCR confidence is pixel-match derived. Mixing scales with
      ``max()`` would be meaningless. Since ``text`` is speech-sourced, the
      speech confidence is the consistent anchor.
    * **Whitespace normalized.** The merged text has internal ``\\n``/``\\r``
      and consecutive spaces collapsed to single spaces so downstream format
      writers (SRT, MD) don't corrupt on multi-line cues.

    Parameters
    ----------
    speech:
        SPEECH segments (ASR output).
    ocr:
        ON-SCREEN segments (post-dedup OCR output).
    threshold:
        Similarity floor in ``[0.0, 1.0]`` — scaled by 100 before comparison
        with ``WRatio``. ``0.85`` is the default from
        ``OmniScribeConfig.merge_similarity_threshold``.

    Returns
    -------
    list[TranscriptSegment]
        Stable-sorted by ``start``; SPEECH-first on equal starts (because
        speech is appended before OCR prior to the stable sort).
    """
    if not speech and not ocr:
        return []
    if not speech:
        return list(ocr)
    if not ocr:
        return list(speech)

    # Soft cap — don't engineer for pathological inputs, just flag them.
    if len(speech) * len(ocr) > _MERGE_SOFT_CAP:
        logger.warning(
            "merge_channels cross-product exceeds soft cap "
            "(len(speech)=%d, len(ocr)=%d, product=%d > %d); proceeding",
            len(speech),
            len(ocr),
            len(speech) * len(ocr),
            _MERGE_SOFT_CAP,
        )

    score_cutoff = threshold * 100.0
    consumed_ocr_idx: set[int] = set()
    emitted: list[TranscriptSegment] = []

    for sp in speech:
        best_idx: int | None = None
        best_score: float = -1.0
        best_start: float = 0.0
        for idx, oc in enumerate(ocr):
            if idx in consumed_ocr_idx:
                continue
            if not _overlaps(sp, oc):
                continue
            score = fuzz.WRatio(sp.text, oc.text)
            if score < score_cutoff:
                continue
            # Highest score wins; ties → earliest ocr.start.
            if score > best_score or (score == best_score and oc.start < best_start):
                best_idx = idx
                best_score = score
                best_start = oc.start

        if best_idx is None:
            emitted.append(sp)
            continue

        oc = ocr[best_idx]
        consumed_ocr_idx.add(best_idx)
        emitted.append(
            TranscriptSegment(
                source="BOTH",
                text=_normalize_cue(sp.text),
                start=min(sp.start, oc.start),
                end=max(sp.end, oc.end),
                confidence=sp.confidence,
                language=sp.language,
            )
        )

    # Add OCR segments not consumed by any speech collapse.
    for idx, oc in enumerate(ocr):
        if idx not in consumed_ocr_idx:
            emitted.append(oc)

    # Stable sort by start — SPEECH/BOTH appended before unconsumed OCR keeps
    # speech-first ordering on equal starts (matches prior behavior).
    emitted.sort(key=lambda s: s.start)
    return emitted

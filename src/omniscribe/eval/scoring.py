"""Scoring function: compare OCR output against ground truth."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from omniscribe.eval.models import EvalResult, GroundTruth

if TYPE_CHECKING:
    from omniscribe.output import TranscriptSegment

_NEAR_MISS_LOWER: float = 0.50


def score_video(
    segments: list[TranscriptSegment],
    ground_truth: GroundTruth,
    fuzzy_threshold: float = 0.85,
) -> EvalResult:
    """Score OCR output against ground truth.

    Parameters
    ----------
    segments:
        OCR output segments (post-dedup, pre-merge).
    ground_truth:
        Ground truth data for this video.
    fuzzy_threshold:
        Similarity floor in ``[0.0, 1.0]``; a segment is considered matching
        when ``fuzz.ratio(text, gt_text, processor=str.lower) / 100 >= threshold``.
        Default 0.85 matches the dedup threshold convention.

    Returns
    -------
    EvalResult
        Recall, precision, mean-match-similarity, and per-text breakdown.
    """
    similarity_lookup = _build_similarity_lookup(segments, ground_truth, fuzzy_threshold)

    per_text_results: list[dict] = []
    total_required = 0
    matched_required = 0
    match_similarities: list[float] = []

    for expected in ground_truth.expected_texts:
        entry = similarity_lookup.get(expected.text)
        if entry is not None:
            best_candidate, similarity = entry
            matched = similarity >= fuzzy_threshold
        else:
            best_candidate = None
            similarity = None
            matched = False

        near_miss = False
        if similarity is not None:
            near_miss = _NEAR_MISS_LOWER <= similarity < fuzzy_threshold

        per_text_results.append(
            {
                "expected_text": expected.text,
                "matched": matched,
                "best_candidate": best_candidate,
                "similarity": similarity,
                "near_miss": near_miss,
            }
        )

        if expected.required:
            total_required += 1
            if matched:
                matched_required += 1
                if similarity is not None:
                    match_similarities.append(similarity)

    recall = matched_required / total_required if total_required > 0 else 1.0

    # Precision: fraction of output segments that match ANY GT text
    # (within the GT time window, when one is specified).
    if not segments:
        precision = 1.0
    else:
        matched_segments = _count_matched_segments(segments, ground_truth, fuzzy_threshold)
        precision = matched_segments / len(segments)

    mean_match_similarity = (
        sum(match_similarities) / len(match_similarities) if match_similarities else None
    )

    return EvalResult(
        recall=recall,
        precision=precision,
        mean_match_similarity=mean_match_similarity,
        per_text_results=per_text_results,
        funnel=None,
    )


def _build_similarity_lookup(
    segments: list[TranscriptSegment],
    ground_truth: GroundTruth,
    fuzzy_threshold: float,
) -> dict[str, tuple[str | None, float]]:
    """For each GT text, find the best-matching output segment.

    Returns a dict keyed by ``expected.text``, with ``(best_candidate, similarity)``.
    Segments outside the time window (when the GT specifies one) are excluded.
    Segments below ``fuzzy_threshold`` are still recorded (for near-miss reporting);
    the caller decides what counts as a "match".
    """
    lookup: dict[str, tuple[str | None, float]] = {}
    for expected in ground_truth.expected_texts:
        best_candidate: str | None = None
        best_sim = 0.0
        for seg in segments:
            # Time-window filter.
            if expected.start is not None and seg.end < expected.start:
                continue
            if expected.end is not None and seg.start > expected.end:
                continue
            sim = fuzz.ratio(seg.text, expected.text, processor=str.lower) / 100.0
            if sim > best_sim:
                best_sim = sim
                best_candidate = seg.text
        lookup[expected.text] = (best_candidate, best_sim)
    return lookup


def _count_matched_segments(
    segments: list[TranscriptSegment],
    ground_truth: GroundTruth,
    fuzzy_threshold: float,
) -> int:
    """Count output segments that fuzzy-match at least one GT text.

    Respects time windows: a segment only matches a GT text when
    it falls within the GT's start/end window (if specified).
    """
    matched = 0
    for seg in segments:
        for expected in ground_truth.expected_texts:
            # Time-window filter.
            if expected.start is not None and seg.end < expected.start:
                continue
            if expected.end is not None and seg.start > expected.end:
                continue
            sim = fuzz.ratio(seg.text, expected.text, processor=str.lower) / 100.0
            if sim >= fuzzy_threshold:
                matched += 1
                break
    return matched

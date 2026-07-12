"""Scoring function: compare OCR output against ground truth."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from omniscribe.eval.models import EvalResult, GroundTruth

if TYPE_CHECKING:
    from omniscribe.output import TranscriptSegment

_NEAR_MISS_LOWER: float = 0.50

# Maximum start-time span (in seconds) for pairing two segments to match a
# multi-line GT text. 0 s covers same-frame line pairs; 2.0 s covers a second
# line first OCR'd one sample-frame later at default 1 fps sampling.
_PAIR_MAX_SPAN_S: float = 2.0


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
    all_pair_indices: set[int] = set()

    for expected in ground_truth.expected_texts:
        entry = similarity_lookup.get(expected.text)
        if entry is not None:
            best_candidate, similarity, pair_indices = entry
            matched = similarity >= fuzzy_threshold
            if matched:
                all_pair_indices |= pair_indices
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
        individually_matched = _individually_matched_indices(
            segments, ground_truth, fuzzy_threshold
        )
        precision = len(individually_matched | all_pair_indices) / len(segments)

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


def _best_pair_match(
    gt_text: str,
    indexed_candidates: list[tuple[int, TranscriptSegment]],
    max_span: float,
) -> tuple[str | None, float, set[int]]:
    """Find the best pair of segments whose joined text matches the GT.

    Tries both join orders (``f"{a.text} {b.text}"`` and
    ``f"{b.text} {a.text}"``) with the same ``fuzz.ratio`` call pattern as
    single-segment matching.  Only pairs whose start-time difference is
    within ``max_span`` seconds are considered.

    Returns ``(joined_text, similarity, {i, j})`` or
    ``(None, 0.0, set())`` when no pairs exist within the span constraint.
    """
    best_joined: str | None = None
    best_sim = 0.0
    best_indices: set[int] = set()

    for i in range(len(indexed_candidates)):
        idx_i, seg_i = indexed_candidates[i]
        for j in range(i + 1, len(indexed_candidates)):
            idx_j, seg_j = indexed_candidates[j]
            if abs(seg_j.start - seg_i.start) > max_span:
                continue

            order1 = f"{seg_i.text} {seg_j.text}"
            order2 = f"{seg_j.text} {seg_i.text}"

            sim1 = fuzz.ratio(order1, gt_text, processor=str.lower) / 100.0
            sim2 = fuzz.ratio(order2, gt_text, processor=str.lower) / 100.0

            sim = max(sim1, sim2)
            if sim > best_sim:
                best_sim = sim
                best_joined = order1 if sim1 >= sim2 else order2
                best_indices = {idx_i, idx_j}

    if best_indices:
        return (best_joined, best_sim, best_indices)
    return (None, 0.0, set())


def _best_triple_extension(
    gt_text: str,
    indexed_candidates: list[tuple[int, TranscriptSegment]],
    pair_indices: set[int],
    max_span: float,
) -> tuple[str | None, float, set[int]]:
    """Extend the best pair to a triple match -- greedy extend-best-pair.

    NOT full C(n,3). The target case is a 3-line title where all lines
    coexist; the best-scoring pair is two of the three true lines, so
    extending it finds the third at O(n) instead of O(n\N{SUPERSCRIPT THREE}).
    Full-search escalation is a documented fallback if measurement still
    misses.

    For each remaining candidate (index not in *pair_indices*), form the
    3-segment set; span-gate via ``max(starts) - min(starts) <= max_span``;
    try all 6 join orders via ``itertools.permutations``.

    Returns ``(joined, sim, {i, j, k})`` or ``(None, 0.0, set())``.
    """
    cand_by_idx = dict(indexed_candidates)

    # Look up the pair's segments.
    pair_segs: dict[int, TranscriptSegment] = {}
    for idx in pair_indices:
        if idx in cand_by_idx:
            pair_segs[idx] = cand_by_idx[idx]

    best_joined: str | None = None
    best_sim = 0.0
    best_indices: set[int] = set()

    for idx, seg in indexed_candidates:
        if idx in pair_indices:
            continue
        # Form the 3-segment set and check span gate.
        triple = {idx: seg, **pair_segs}
        starts = [s.start for s in triple.values()]
        if max(starts) - min(starts) > max_span:
            continue
        # Try all 6 join orders.
        texts = [s.text for s in triple.values()]
        for perm in itertools.permutations(texts, 3):
            joined = " ".join(perm)
            sim = fuzz.ratio(joined, gt_text, processor=str.lower) / 100.0
            if sim > best_sim:
                best_sim = sim
                best_joined = joined
                best_indices = set(triple.keys())

    if best_indices:
        return (best_joined, best_sim, best_indices)
    return (None, 0.0, set())


def _build_similarity_lookup(
    segments: list[TranscriptSegment],
    ground_truth: GroundTruth,
    fuzzy_threshold: float,
) -> dict[str, tuple[str | None, float, set[int]]]:
    """For each GT text, find the best-matching output segment(s).

    Returns a dict keyed by ``expected.text``, with
    ``(best_candidate, similarity, pair_indices)``.  ``pair_indices`` is empty
    when a single segment wins.  Segments outside the time window (when the GT
    specifies one) are excluded.
    """
    lookup: dict[str, tuple[str | None, float, set[int]]] = {}
    for expected in ground_truth.expected_texts:
        best_candidate: str | None = None
        best_sim = 0.0
        pair_indices: set[int] = set()

        # Pass 1: single segments (existing logic — unchanged).
        indexed_candidates: list[tuple[int, TranscriptSegment]] = []
        for idx, seg in enumerate(segments):
            # Time-window filter.
            if expected.start is not None and seg.end < expected.start:
                continue
            if expected.end is not None and seg.start > expected.end:
                continue
            indexed_candidates.append((idx, seg))
            sim = fuzz.ratio(seg.text, expected.text, processor=str.lower) / 100.0
            if sim > best_sim:
                best_sim = sim
                best_candidate = seg.text
                pair_indices = set()

        # Pass 2: pairwise matching (Sprint 9.2).
        if len(indexed_candidates) >= 2:
            joined_text, pair_sim, p_indices = _best_pair_match(
                expected.text, indexed_candidates, _PAIR_MAX_SPAN_S
            )
            if pair_sim > best_sim:
                best_sim = pair_sim
                best_candidate = joined_text
                pair_indices = p_indices

        # Pass 3: greedy triple extension (Sprint 9.3).
        # Gated: run only when singles+pairs still below threshold, at least
        # 3 candidates exist, and a non-empty pair was found in pass 2.
        if best_sim < fuzzy_threshold and len(indexed_candidates) >= 3 and pair_indices:
            joined_text, triple_sim, t_indices = _best_triple_extension(
                expected.text, indexed_candidates, pair_indices, _PAIR_MAX_SPAN_S
            )
            if triple_sim > best_sim:
                best_sim = triple_sim
                best_candidate = joined_text
                pair_indices = t_indices

        lookup[expected.text] = (best_candidate, best_sim, pair_indices)
    return lookup


def _individually_matched_indices(
    segments: list[TranscriptSegment],
    ground_truth: GroundTruth,
    fuzzy_threshold: float,
) -> set[int]:
    """Return indices of output segments that fuzzy-match at least one GT text.

    Respects time windows: a segment only matches a GT text when
    it falls within the GT's start/end window (if specified).
    """
    matched: set[int] = set()
    for idx, seg in enumerate(segments):
        for expected in ground_truth.expected_texts:
            # Time-window filter.
            if expected.start is not None and seg.end < expected.start:
                continue
            if expected.end is not None and seg.start > expected.end:
                continue
            sim = fuzz.ratio(seg.text, expected.text, processor=str.lower) / 100.0
            if sim >= fuzzy_threshold:
                matched.add(idx)
                break
    return matched

"""Cross-frame OCR deduplicator.

Collapses near-duplicate ON-SCREEN segments (same overlay held across multiple
sampled frames) into a single segment spanning ``[first.start, last.end]``.
SPEECH segments pass through unchanged.

Design
------

* **Pure function.** No config access, no I/O, no logging. Callers pass in the
  similarity threshold, minimum duration, and gap tolerance directly.
* **Gap tolerance is a caller concern.** Computed upstream from
  ``config.ocr_sample_fps`` (``2.0 / ocr_sample_fps`` in the CLI wiring) so
  this module is independent of sampling rate. This keeps the function purer
  than passing the whole ``OmniScribeConfig`` in.
* **Similarity metric.** ``rapidfuzz.fuzz.ratio / 100`` in ``[0.0, 1.0]``.
  Compared against the *tail* (most recent) member of the active cluster, not
  the first — keeps the cluster anchored to its current textual form if OCR
  mis-reads a character mid-run.
* **Grouping.** ON-SCREEN segments are first partitioned out of the input,
  then bucketed by a canonical text key (case-folded, stripped). Clustering
  walks each bucket in time order so same-text occurrences across non-
  consecutive input positions still collapse — handling the multi-region-per-
  frame case (top pill / mid pill / bottom caption interleaved in receipt
  order).
* **Ordering.** Output is sorted by ``start``. On equal-start ties, SPEECH
  precedes ON-SCREEN — this matches the downstream
  :func:`merge_channels` expectation.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from omniscribe.ocr._text_match import _canonical_key, _fuzzy_match

if TYPE_CHECKING:
    from omniscribe.output import TranscriptSegment

from omniscribe.output import TranscriptSegment as _TranscriptSegment


def _flush_cluster(
    cluster: list[TranscriptSegment],
    min_duration: float,
) -> TranscriptSegment | None:
    """Collapse a non-empty ON-SCREEN cluster into one segment, or drop if too short.

    Returns ``None`` when the cluster span ``end - start`` is strictly less
    than ``min_duration``.

    Assumes each segment satisfies ``seg.end >= seg.start`` and the cluster is
    in non-decreasing start order — caller responsibility. Inverted-time
    segments would pass the duration guard with a negative span and produce
    inverted output.
    """
    first = cluster[0]
    last = cluster[-1]
    duration = last.end - first.start
    if duration < min_duration:
        return None

    confidences = [s.confidence for s in cluster if s.confidence is not None]
    mean_conf = sum(confidences) / len(confidences) if confidences else None

    return _TranscriptSegment(
        start=first.start,
        end=last.end,
        text=first.text,
        source="ON-SCREEN",
        confidence=mean_conf,
        language=first.language,
    )


def dedup_segments(
    segments: list[TranscriptSegment],
    threshold: float,
    min_duration: float,
    gap_tolerance: float,
) -> list[TranscriptSegment]:
    """Collapse near-duplicate ON-SCREEN runs; pass SPEECH through unchanged.

    ON-SCREEN segments with the same canonical text (case-folded, stripped) are
    clustered across the full input — they need not be consecutive in the input
    list. This handles the multi-region-per-frame case where each sampled frame
    yields several text regions (top pill, mid pill, bottom caption), so same-
    text occurrences across frames are interleaved with sibling regions in
    receipt order.

    Output is sorted by ``start``. Input order is **not** preserved. On equal
    ``start`` values, SPEECH segments precede ON-SCREEN segments in output,
    matching :func:`merge_channels`' downstream expectation.

    Parameters
    ----------
    segments:
        Mixed input list containing ``source="SPEECH"`` and
        ``source="ON-SCREEN"`` segments. Input order is not required to be
        time-sorted; the output sort makes that contract explicit.
    threshold:
        Minimum similarity (``[0.0, 1.0]``) between two ON-SCREEN segments'
        texts in the same canonical-key bucket for them to extend the active
        cluster. Computed as ``rapidfuzz.fuzz.ratio(a, b) / 100`` with
        ``processor=str.lower`` so case-variation within a bucket does not
        depress the score.
    min_duration:
        Minimum duration (``last.end - first.start``) for a collapsed cluster
        to survive. Shorter clusters are dropped entirely (useful to suppress
        single-frame flicker false positives).
    gap_tolerance:
        Maximum allowed gap (``current.start - cluster.last.end``) between
        consecutive ON-SCREEN segments **within the same canonical-key
        bucket**. Typically ``2.0 / ocr_sample_fps`` so a single missing frame
        does not break a held overlay into two segments. Identical text far
        apart in time still splits — text grouping does not bypass this guard.

    Returns
    -------
    list[TranscriptSegment]
        Output segments sorted by ``start``, SPEECH-before-ON-SCREEN on ties.
    """
    speech: list[TranscriptSegment] = [s for s in segments if s.source == "SPEECH"]
    onscreen: list[TranscriptSegment] = [s for s in segments if s.source == "ON-SCREEN"]

    grouped: dict[str, list[TranscriptSegment]] = defaultdict(list)
    for seg in onscreen:
        key = _canonical_key(seg.text)
        if not key:
            # Skip blank/whitespace-only OCR noise: at min_duration=0.0 these
            # would otherwise survive as a multi-second whitespace cluster
            # whose text is whatever raw spaces/newlines the first noise
            # segment happened to carry.
            continue
        grouped[key].append(seg)

    clustered: list[TranscriptSegment] = []
    for group in grouped.values():
        group.sort(key=lambda s: s.start)
        cluster: list[TranscriptSegment] = []
        for seg in group:
            if not cluster:
                cluster.append(seg)
                continue
            tail = cluster[-1]
            gap = seg.start - tail.end
            if _fuzzy_match(seg.text, tail.text, threshold) and gap <= gap_tolerance:
                cluster.append(seg)
            else:
                flushed = _flush_cluster(cluster, min_duration)
                if flushed is not None:
                    clustered.append(flushed)
                cluster = [seg]
        if cluster:
            flushed = _flush_cluster(cluster, min_duration)
            if flushed is not None:
                clustered.append(flushed)

    # Concatenate speech-first so the stable sort below keeps SPEECH before
    # ON-SCREEN on equal starts — matches merge_channels' downstream contract
    # (output.py:304-305). Don't reorder these halves without revisiting that.
    result = speech + clustered
    result.sort(key=lambda s: s.start)
    return result

"""Cross-frame OCR deduplicator.

Collapses consecutive near-duplicate ON-SCREEN segments (same overlay held
across multiple sampled frames) into a single segment spanning
``[first.start, last.end]``. SPEECH segments pass through unchanged, in
receipt order.

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
* **Ordering.** SPEECH segments are emitted in input order, interleaved with
  collapsed ON-SCREEN segments in the positions they first appeared. Final
  time-based sort is the downstream :func:`merge_channels`' job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

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

    Parameters
    ----------
    segments:
        Mixed input list containing ``source="SPEECH"`` and ``source="ON-SCREEN"``
        segments. ON-SCREEN segments are assumed to be in non-decreasing start
        order (as produced by frame sampling). SPEECH segments may be in any
        order — they are emitted in receipt order.
    threshold:
        Minimum similarity (``[0.0, 1.0]``) between two consecutive ON-SCREEN
        segments' texts for them to be considered the same overlay. Computed as
        ``rapidfuzz.fuzz.ratio(a, b) / 100``.
    min_duration:
        Minimum duration (``last.end - first.start``) for a collapsed cluster
        to survive. Shorter clusters are dropped entirely (useful to suppress
        single-frame flicker false positives).
    gap_tolerance:
        Maximum allowed gap (``current.start - cluster.last.end``) between
        consecutive ON-SCREEN segments in the same cluster. Typically
        ``2.0 / ocr_sample_fps`` so that a single missing frame does not break
        a held overlay into two segments.

    Returns
    -------
    list[TranscriptSegment]
        Output segments in original receipt order: SPEECH positions preserved,
        ON-SCREEN clusters emitted at the position of their first member.
    """
    out: list[TranscriptSegment] = []
    cluster: list[TranscriptSegment] = []

    def _flush() -> None:
        if not cluster:
            return
        collapsed = _flush_cluster(cluster, min_duration)
        if collapsed is not None:
            out.append(collapsed)
        cluster.clear()

    for seg in segments:
        if seg.source != "ON-SCREEN":
            _flush()
            out.append(seg)
            continue

        if not cluster:
            cluster.append(seg)
            continue

        tail = cluster[-1]
        similarity = fuzz.ratio(seg.text, tail.text) / 100.0
        gap = seg.start - tail.end
        if similarity >= threshold and gap <= gap_tolerance:
            cluster.append(seg)
        else:
            _flush()
            cluster.append(seg)

    _flush()
    return out

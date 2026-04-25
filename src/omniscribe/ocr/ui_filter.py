"""Pure-function UI filters for OCR segments.

Three independent helpers make up the Sprint 3.2 UI-filter stage:

* :func:`mask_zones` — zero out rectangular regions of a grayscale frame
  (defensive copy, pixel-rect clamped to the frame bounds; a no-op when the
  zone tuple is empty).
* :func:`filter_by_patterns` — drop ON-SCREEN segments whose text matches
  any compiled regex. SPEECH segments pass through untouched.
* :func:`filter_by_frequency` — drop ON-SCREEN segments whose normalised text
  appears in at least ``threshold`` of sampled frames, with a fuzzy
  near-duplicate clustering pass to catch the ``"SUBSCRIBE!"`` /
  ``"Subscribe →"`` / ``"SUBSCRIBE"`` family. SPEECH segments pass
  through. ``frame_count == 0`` short-circuits to the input unchanged.

Normalisation divergence (intentional)
--------------------------------------
``filter_by_patterns`` preserves case — regex authors opt into case
insensitivity via ``re.IGNORECASE``. ``filter_by_frequency`` case-folds
internally via :func:`omniscribe.ocr._text_match._canonical_key` because
UI chrome often flickers between ``"SUBSCRIBE"`` and ``"Subscribe"``
across frames and the ratio calculation would otherwise miss those
counts. The same helper is reused by the deduplicator so the two
stages cannot drift on case-folding semantics.

Ordering (binding)
------------------
The CLI must run :func:`filter_by_patterns` and :func:`filter_by_frequency`
on the **raw pre-dedup** OCR segments, not on post-dedup output. The
frequency ratio ``count / frame_count`` is only meaningful against raw
per-frame detections — dedup collapses duplicates and would trivialise the
ratio.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import cv2

from omniscribe.ocr._text_match import _canonical_key, _fuzzy_match

if TYPE_CHECKING:
    import re

    import numpy as np

    from omniscribe.output import TranscriptSegment
    from omniscribe.platforms.base import RelativeRect


def mask_zones(
    gray: np.ndarray,
    zones: tuple[RelativeRect, ...],
) -> np.ndarray:
    """Return a copy of ``gray`` with each ``zone`` filled with black (0).

    ``gray`` must be a 2D ``uint8`` array of shape ``(H, W)`` (as produced by
    :func:`omniscribe.ocr.preprocessor.preprocess`). Each
    :class:`omniscribe.platforms.base.RelativeRect` is expressed in
    normalised ``[0.0, 1.0]`` frame coordinates and converted to pixel
    coordinates via ``int(zone.x * W)``, ``int(zone.y * H)`` etc. The
    bottom-right corner is clamped with ``min(..., W)`` / ``min(..., H)`` to
    guard against float rounding that would otherwise put the rect one pixel
    past the frame edge.

    An empty ``zones`` tuple short-circuits to the input array unchanged —
    no copy is made.
    """
    if not zones:
        return gray
    height, width = gray.shape[:2]
    masked = gray.copy()
    for zone in zones:
        x1 = int(zone.x * width)
        y1 = int(zone.y * height)
        x2 = min(int((zone.x + zone.w) * width), width)
        y2 = min(int((zone.y + zone.h) * height), height)
        cv2.rectangle(masked, (x1, y1), (x2, y2), color=0, thickness=cv2.FILLED)
    return masked


def filter_by_patterns(
    segments: list[TranscriptSegment],
    patterns: tuple[re.Pattern[str], ...],
) -> list[TranscriptSegment]:
    """Drop ON-SCREEN segments whose stripped text matches any ``pattern``.

    SPEECH segments pass through untouched even if their text would match
    (speech is never UI chrome). Input order is preserved. An empty
    ``patterns`` tuple returns the input list as-is.
    """
    if not patterns:
        return list(segments)
    kept: list[TranscriptSegment] = []
    for seg in segments:
        if seg.source != "ON-SCREEN":
            kept.append(seg)
            continue
        text = seg.text.strip()
        if any(pattern.search(text) for pattern in patterns):
            continue
        kept.append(seg)
    return kept


def filter_by_frequency(
    segments: list[TranscriptSegment],
    frame_count: int,
    threshold: float,
    *,
    fuzzy_threshold: float = 90.0,
) -> list[TranscriptSegment]:
    """Drop ON-SCREEN segments whose normalised text appears in too many frames.

    Normalisation uses :func:`omniscribe.ocr._text_match._canonical_key`
    (case-folded, edge-stripped) so that variations like ``"SUBSCRIBE"``
    and ``"Subscribe"`` share a bucket. Beyond exact-key buckets, a second
    pass clusters near-duplicate keys (e.g. ``"SUBSCRIBE!"`` /
    ``"Subscribe →"`` / ``"SUBSCRIBE"``) using a greedy single-link walk
    against :func:`omniscribe.ocr._text_match._fuzzy_match` — a key joins
    an existing cluster if it matches *any* member of that cluster at
    ``fuzzy_threshold`` (in 0-100, the rapidfuzz ``fuzz.ratio`` scale).

    A segment is dropped when its **cluster's** combined occurrence count
    divided by ``frame_count`` is greater than or equal to ``threshold``.
    The fuzzy clustering raises the *effective* recurrence count for any
    canonical text — see the "Behavior-change risk" section in
    ``docs/plans/sprint-7-1-ocr-noise-floor.md``.

    SPEECH segments pass through untouched. ``frame_count == 0`` returns
    the input unchanged — the ratio is undefined and we must not divide
    by zero.

    Parameters
    ----------
    segments:
        Mixed input list. SPEECH segments bypass filtering.
    frame_count:
        Total number of sampled frames the OCR ran over. Denominator
        for the recurrence ratio.
    threshold:
        Drop boundary (``[0.0, 1.0]``). A cluster is dropped when
        ``cluster_count / frame_count >= threshold``.
    fuzzy_threshold:
        Minimum ``fuzz.ratio`` (0-100) for two canonical keys to share a
        cluster. Default 90.0 — tight enough to keep legitimate captions
        apart, loose enough to collapse common SUBSCRIBE / handle / arrow
        variants.
    """
    if frame_count == 0:
        return list(segments)

    # Build per-canonical-key counts first (cheap exact-match pass).
    counts: Counter[str] = Counter(
        _canonical_key(seg.text) for seg in segments if seg.source == "ON-SCREEN"
    )

    # Greedy single-link clustering over the unique non-empty keys.
    # Iteration order is the input-Counter order (deterministic in
    # CPython 3.7+). For each key, walk existing clusters and join the
    # first whose any member matches; otherwise start a new cluster.
    fuzzy_ratio = fuzzy_threshold / 100.0
    clusters: list[list[str]] = []
    for key in counts:
        if not key:
            # Empty canonical keys cannot meaningfully cluster — drop
            # them out of the cluster set; the lookup loop below treats
            # them as their own (zero-count) bucket so behavior matches
            # the pre-existing exact-match pass.
            continue
        joined = False
        for cluster in clusters:
            if any(_fuzzy_match(key, member, fuzzy_ratio) for member in cluster):
                cluster.append(key)
                joined = True
                break
        if not joined:
            clusters.append([key])

    # Map each key to its cluster's combined count.
    cluster_counts: dict[str, int] = {}
    for cluster in clusters:
        total = sum(counts[member] for member in cluster)
        for member in cluster:
            cluster_counts[member] = total

    kept: list[TranscriptSegment] = []
    for seg in segments:
        if seg.source != "ON-SCREEN":
            kept.append(seg)
            continue
        key = _canonical_key(seg.text)
        cluster_count = cluster_counts.get(key, counts[key])
        if cluster_count / frame_count >= threshold:
            continue
        kept.append(seg)
    return kept

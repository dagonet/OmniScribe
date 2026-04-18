"""Pure-function UI filters for OCR segments.

Three independent helpers make up the Sprint 3.2 UI-filter stage:

* :func:`mask_zones` — zero out rectangular regions of a grayscale frame
  (defensive copy, pixel-rect clamped to the frame bounds; a no-op when the
  zone tuple is empty).
* :func:`filter_by_patterns` — drop ON-SCREEN segments whose text matches
  any compiled regex. SPEECH segments pass through untouched.
* :func:`filter_by_frequency` — drop ON-SCREEN segments whose normalised text
  appears in at least ``threshold`` of sampled frames. SPEECH segments pass
  through. ``frame_count == 0`` short-circuits to the input unchanged.

Normalisation divergence (intentional)
--------------------------------------
``filter_by_patterns`` preserves case — regex authors opt into case
insensitivity via ``re.IGNORECASE``. ``filter_by_frequency`` case-folds
internally via ``seg.text.strip().lower()`` because UI chrome often flickers
between ``"SUBSCRIBE"`` and ``"Subscribe"`` across frames and the ratio
calculation would otherwise miss those counts.

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
) -> list[TranscriptSegment]:
    """Drop ON-SCREEN segments whose normalised text appears in too many frames.

    Normalisation is ``seg.text.strip().lower()`` (see module docstring for
    why this diverges from :func:`filter_by_patterns`). A segment is dropped
    when its occurrence count divided by ``frame_count`` is greater than or
    equal to ``threshold``. SPEECH segments pass through untouched.
    ``frame_count == 0`` returns the input unchanged — the ratio is
    undefined and we must not divide by zero.
    """
    if frame_count == 0:
        return list(segments)
    counts: Counter[str] = Counter(
        seg.text.strip().lower() for seg in segments if seg.source == "ON-SCREEN"
    )
    kept: list[TranscriptSegment] = []
    for seg in segments:
        if seg.source != "ON-SCREEN":
            kept.append(seg)
            continue
        key = seg.text.strip().lower()
        if counts[key] / frame_count >= threshold:
            continue
        kept.append(seg)
    return kept

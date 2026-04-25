"""Per-frame bounding-box aggregation for RapidOCR results.

Groups same-line bboxes into a single canonical caption string per text
region per frame, *before* cross-frame deduplication runs. RapidOCR returns
text at the word/region level; without this stage a visible caption like
``KEINE KAMPFSPORTTECHNIK KEINE`` produces three separate point segments per
frame and downstream text-similarity dedup never clusters them.

Pure function — no side effects, no I/O, no logging. Caller is responsible
for confidence-threshold semantics (passed in as ``min_confidence``).

Algorithm (per frame):
    1. Drop bboxes whose individual ``score < min_confidence``.
    2. Drop intra-frame duplicate-text bboxes (overlapping detections of
       the same word) — keep first occurrence.
    3. Compute axis-aligned ``y_min``/``y_max``/``x_min``/``x_max`` from each
       polygon's four corners; derive ``y_center``, ``x_center``, ``box_height``.
    4. Compute frame-wide ``mean_height`` over surviving boxes; this is the
       tolerance baseline for line grouping (more robust than per-line running
       mean, which is fragile if the first bbox is a tiny icon).
    5. Sort by ``y_center``; walk the list joining boxes whose
       ``abs(y_center - line.y_center_running_mean) <= 0.5 * mean_height``
       to the current line, otherwise start a new line. ``<=`` is inclusive —
       a delta exactly equal to the tolerance joins the current line.
    6. Within each line, sort by ``x_center`` for left-to-right reading order.
    7. Emit one ``(joined_text, mean_confidence)`` tuple per line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def aggregate_frame_bboxes(
    boxes: Sequence[Sequence[tuple[float, float]]],
    texts: Sequence[str],
    scores: Sequence[float],
    *,
    min_confidence: float,
    y_tolerance_ratio: float = 0.5,
) -> list[tuple[str, float]]:
    """Group bboxes into reading-order lines.

    Parameters
    ----------
    boxes:
        Sequence of 4-corner polygons; each polygon is a sequence of four
        ``(x, y)`` tuples (RapidOCR's rotated-rectangle output, used here as
        an axis-aligned bounding rectangle for grouping).
    texts:
        Per-bbox text strings; ``len(texts) == len(boxes)``.
    scores:
        Per-bbox confidence scores in ``[0.0, 1.0]``; ``len(scores) == len(boxes)``.
    min_confidence:
        Bboxes with ``score < min_confidence`` are dropped *before* grouping
        (mirrors the per-bbox confidence semantics of the pre-aggregation
        loop). A line never inherits a low-confidence word.
    y_tolerance_ratio:
        Multiplier on ``frame_mean_height`` used as the y-delta tolerance
        for joining a bbox to the current line. Default ``0.5`` means a
        bbox joins if its ``y_center`` is within half the mean line height
        of the running line center.

    Returns
    -------
    list[tuple[str, float]]
        One ``(joined_text, mean_confidence)`` tuple per detected line, in
        top-to-bottom order. Within each line, words are joined by single
        space in left-to-right order. Empty input returns an empty list.

    Raises
    ------
    AssertionError
        If ``len(boxes) != len(texts)`` or ``len(boxes) != len(scores)`` —
        this is a RapidOCR contract violation; failing fast here is safer
        than silently truncating to the shortest sequence.
    """
    assert len(boxes) == len(texts) == len(scores), (
        f"length mismatch: boxes={len(boxes)}, texts={len(texts)}, scores={len(scores)}"
    )
    # Use ``len`` rather than truthiness — RapidOCR returns ``boxes`` as a
    # numpy array, where ``if not boxes:`` raises ``ValueError``.
    if len(boxes) == 0:
        return []

    # Step 1+2: confidence filter + intra-frame dedup, building survivor list.
    survivors: list[tuple[float, float, float, float, str, float]] = []
    seen_texts: set[str] = set()
    for box, text, score in zip(boxes, texts, scores, strict=True):
        if score < min_confidence:
            continue
        if text in seen_texts:
            continue
        seen_texts.add(text)
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        y_min, y_max = min(ys), max(ys)
        x_min, x_max = min(xs), max(xs)
        y_center = (y_min + y_max) / 2.0
        x_center = (x_min + x_max) / 2.0
        box_height = y_max - y_min
        survivors.append((y_center, x_center, box_height, score, text, float(score)))

    if not survivors:
        return []

    # Step 4: frame-wide mean height as the tolerance baseline.
    mean_height = sum(s[2] for s in survivors) / len(survivors)
    tolerance = y_tolerance_ratio * mean_height

    # Step 5: sort by y_center, walk into lines.
    survivors.sort(key=lambda s: s[0])
    lines: list[list[tuple[float, float, float, float, str, float]]] = []
    line_centers: list[float] = []  # running mean y_center per line
    for entry in survivors:
        y_center = entry[0]
        if not lines:
            lines.append([entry])
            line_centers.append(y_center)
            continue
        last_center = line_centers[-1]
        if abs(y_center - last_center) <= tolerance:
            lines[-1].append(entry)
            # Update running mean for the current line.
            n = len(lines[-1])
            line_centers[-1] = ((last_center * (n - 1)) + y_center) / n
        else:
            lines.append([entry])
            line_centers.append(y_center)

    # Steps 6+7: within each line sort by x_center, emit (text, mean_conf).
    out: list[tuple[str, float]] = []
    for line in lines:
        line.sort(key=lambda s: s[1])
        joined_text = " ".join(s[4] for s in line)
        mean_conf = sum(s[5] for s in line) / len(line)
        out.append((joined_text, mean_conf))
    return out

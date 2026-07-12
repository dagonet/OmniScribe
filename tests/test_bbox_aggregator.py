"""Unit tests for omniscribe.ocr.bbox_aggregator.

``aggregate_frame_bboxes`` groups same-line RapidOCR bounding boxes into one
canonical caption per detected text region, in reading order. Pure helper —
no mocks required.
"""

from __future__ import annotations

import pytest

from omniscribe.ocr.bbox_aggregator import aggregate_frame_bboxes


def _box(x_min: float, y_min: float, x_max: float, y_max: float) -> list[tuple[float, float]]:
    """Build a 4-corner polygon in (x, y) order (axis-aligned rectangle)."""
    return [
        (x_min, y_min),
        (x_max, y_min),
        (x_max, y_max),
        (x_min, y_max),
    ]


def test_empty_input_returns_empty_output() -> None:
    assert aggregate_frame_bboxes([], [], [], min_confidence=0.6) == []


def test_single_bbox_returns_single_segment() -> None:
    boxes = [_box(0.0, 0.0, 100.0, 30.0)]
    out = aggregate_frame_bboxes(boxes, ["hello"], [0.9], min_confidence=0.6)

    assert len(out) == 1
    text, conf = out[0]
    assert text == "hello"
    assert conf == pytest.approx(0.9)


def test_two_bboxes_same_line_joined_left_to_right() -> None:
    # Both boxes on the same y-line (y_center = 15), x-adjacent.
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # left
        _box(60.0, 0.0, 110.0, 30.0),  # right
    ]
    out = aggregate_frame_bboxes(boxes, ["left", "right"], [0.8, 0.9], min_confidence=0.6)

    assert len(out) == 1
    text, conf = out[0]
    assert text == "left right"
    assert conf == pytest.approx(0.85)


def test_two_bboxes_different_lines_yield_two_segments() -> None:
    # Top line y_center = 15, bottom line y_center = 100. Heights ~30 each →
    # tolerance = 15. Δ = 85 > 15 → separate lines.
    boxes = [
        _box(0.0, 0.0, 100.0, 30.0),  # top
        _box(0.0, 85.0, 100.0, 115.0),  # bottom
    ]
    out = aggregate_frame_bboxes(boxes, ["top", "bottom"], [0.9, 0.9], min_confidence=0.6)

    assert [t for t, _ in out] == ["top", "bottom"]


def test_three_bboxes_one_line_out_of_x_order_sorts_by_x() -> None:
    # All on same y-line (y_center = 15). Provided in middle/right/left order;
    # output must be left → middle → right.
    boxes = [
        _box(60.0, 0.0, 100.0, 30.0),  # middle (x_center = 80)
        _box(120.0, 0.0, 160.0, 30.0),  # right  (x_center = 140)
        _box(0.0, 0.0, 40.0, 30.0),  # left   (x_center = 20)
    ]
    out = aggregate_frame_bboxes(
        boxes, ["middle", "right", "left"], [0.9, 0.9, 0.9], min_confidence=0.6
    )

    assert len(out) == 1
    assert out[0][0] == "left middle right"


def test_below_min_confidence_dropped_high_emits_alone() -> None:
    # Two bboxes on different y-lines. Below-threshold one disappears entirely.
    boxes = [
        _box(0.0, 0.0, 100.0, 30.0),  # keep
        _box(0.0, 100.0, 100.0, 130.0),  # drop (low score)
    ]
    out = aggregate_frame_bboxes(boxes, ["keep", "drop"], [0.9, 0.4], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "keep"


def test_mean_confidence_aggregation_on_one_line() -> None:
    # Three same-line bboxes; mean of (0.6, 0.9, 0.9) = 0.8.
    boxes = [
        _box(0.0, 0.0, 30.0, 30.0),
        _box(40.0, 0.0, 70.0, 30.0),
        _box(80.0, 0.0, 110.0, 30.0),
    ]
    out = aggregate_frame_bboxes(boxes, ["a", "b", "c"], [0.6, 0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    _, conf = out[0]
    assert conf == pytest.approx(0.8)


def test_tolerance_boundary_inclusive_merges() -> None:
    # mean_height = 30, tolerance = 15. Place second box's y_center at exactly
    # +15 from the first (delta == tolerance) — must merge by inclusive ``<=``.
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # y_center = 15
        _box(60.0, 15.0, 110.0, 45.0),  # y_center = 30 → Δ = 15 (boundary)
    ]
    out = aggregate_frame_bboxes(boxes, ["a", "b"], [0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "a b"


def test_intra_frame_duplicate_text_emitted_once() -> None:
    # Same word detected twice (e.g. overlapping detections of "TRUE"); the
    # aggregator keeps the first and drops the second so the joined line
    # doesn't read "TRUE TRUE".
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # first "TRUE"
        _box(2.0, 1.0, 52.0, 31.0),  # near-duplicate detection of "TRUE"
    ]
    out = aggregate_frame_bboxes(boxes, ["TRUE", "TRUE"], [0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "TRUE"


def test_length_mismatch_input_raises_assertion() -> None:
    boxes = [_box(0.0, 0.0, 50.0, 30.0)]
    texts: list[str] = ["a", "b"]
    scores: list[float] = [0.9]

    with pytest.raises(AssertionError, match="length mismatch"):
        aggregate_frame_bboxes(boxes, texts, scores, min_confidence=0.6)


def test_all_below_confidence_returns_empty() -> None:
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),
        _box(60.0, 0.0, 110.0, 30.0),
    ]
    out = aggregate_frame_bboxes(boxes, ["a", "b"], [0.3, 0.4], min_confidence=0.6)

    assert out == []


def test_three_separate_lines_emit_three_segments() -> None:
    # Three boxes stacked vertically, each its own line.
    boxes = [
        _box(0.0, 0.0, 100.0, 30.0),  # y_center = 15
        _box(0.0, 100.0, 100.0, 130.0),  # y_center = 115
        _box(0.0, 200.0, 100.0, 230.0),  # y_center = 215
    ]
    out = aggregate_frame_bboxes(
        boxes, ["top", "middle", "bottom"], [0.9, 0.9, 0.9], min_confidence=0.6
    )

    assert [t for t, _ in out] == ["top", "middle", "bottom"]


def test_mixed_confidence_within_line_keeps_only_high_bbox() -> None:
    # Two bboxes on the same y-line; one below threshold. The line's joined
    # text and mean confidence reflect the surviving bbox only.
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # below threshold
        _box(60.0, 0.0, 110.0, 30.0),  # above threshold
    ]
    out = aggregate_frame_bboxes(boxes, ["lo", "hi"], [0.5, 0.8], min_confidence=0.6)

    assert len(out) == 1
    text, conf = out[0]
    assert text == "hi"
    assert conf == pytest.approx(0.8)


# ── Sprint 9.3: Column-aware line splitting ────────────────────────────────


def test_column_gap_splits_line_into_two_segments() -> None:
    """Same y-line, gap 100 px with mean height 30 (ratio 3.3) -> 2 segments."""
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # left column
        _box(150.0, 0.0, 200.0, 30.0),  # right column, gap=100, ratio≈3.3
    ]
    out = aggregate_frame_bboxes(boxes, ["Eier", "Nudeln"], [0.9, 0.8], min_confidence=0.6)

    assert len(out) == 2
    assert out[0][0] == "Eier"
    assert out[1][0] == "Nudeln"


def test_small_gap_stays_joined() -> None:
    """Same y-line, gap 10 px (0.33 x mean height) -> 1 joined segment."""
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # left
        _box(60.0, 0.0, 110.0, 30.0),  # right, gap=10, ratio≈0.33
    ]
    out = aggregate_frame_bboxes(boxes, ["Hello", "World"], [0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "Hello World"


def test_boundary_gap_at_threshold_stays_joined() -> None:
    """Gap exactly 2.0 x mean_height stays joined (strict >)."""
    # gap = 110 - 50 = 60, mean_height = 30, ratio = 2.0
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),
        _box(110.0, 0.0, 160.0, 30.0),
    ]
    out = aggregate_frame_bboxes(boxes, ["left", "right"], [0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "left right"


def test_negative_gap_overlap_never_splits() -> None:
    """Overlapping x-ranges (negative gap) -> 1 segment."""
    boxes = [
        _box(0.0, 0.0, 100.0, 30.0),
        _box(50.0, 0.0, 150.0, 30.0),  # overlap: gap=50-100=-50
    ]
    out = aggregate_frame_bboxes(boxes, ["overlap", "text"], [0.9, 0.9], min_confidence=0.6)

    assert len(out) == 1
    assert out[0][0] == "overlap text"


def test_two_by_two_grid_emits_four_segments() -> None:
    """2 rows x 2 columns with gutter gaps -> 4 segments in row-major order."""
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # row 1, col 1
        _box(150.0, 0.0, 200.0, 30.0),  # row 1, col 2
        _box(0.0, 100.0, 50.0, 130.0),  # row 2, col 1
        _box(150.0, 100.0, 200.0, 130.0),  # row 2, col 2
    ]
    out = aggregate_frame_bboxes(
        boxes,
        ["r1c1", "r1c2", "r2c1", "r2c2"],
        [0.9, 0.9, 0.9, 0.9],
        min_confidence=0.6,
    )

    assert [t for t, _ in out] == ["r1c1", "r1c2", "r2c1", "r2c2"]


def test_split_chunks_have_independent_mean_confidence() -> None:
    """2-column line: each chunk's confidence is the mean of that column's boxes only."""
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # col 1, score 0.6
        _box(60.0, 0.0, 110.0, 30.0),  # col 1, score 0.9
        _box(200.0, 0.0, 250.0, 30.0),  # col 2, score 0.7
        # gap between idx 1 and idx 2: 200-110=90, mean_height=30, ratio=3.0 > 2.0 -> split
    ]
    out = aggregate_frame_bboxes(boxes, ["A", "B", "C"], [0.6, 0.9, 0.7], min_confidence=0.6)

    assert len(out) == 2
    # col 1: "A B", mean (0.6+0.9)/2 = 0.75
    assert out[0][0] == "A B"
    assert out[0][1] == pytest.approx(0.75)
    # col 2: "C", confidence 0.7
    assert out[1][0] == "C"
    assert out[1][1] == pytest.approx(0.7)


def test_x_gap_tolerance_ratio_passthrough() -> None:
    """x_gap_tolerance_ratio=10.0 keeps a 3.3x gap joined."""
    boxes = [
        _box(0.0, 0.0, 50.0, 30.0),  # left
        _box(150.0, 0.0, 200.0, 30.0),  # right, gap=100, ratio≈3.3
    ]
    out = aggregate_frame_bboxes(
        boxes,
        ["left", "right"],
        [0.9, 0.9],
        min_confidence=0.6,
        x_gap_tolerance_ratio=10.0,
    )

    assert len(out) == 1
    assert out[0][0] == "left right"

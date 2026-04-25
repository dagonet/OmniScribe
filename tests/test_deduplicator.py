"""Unit tests for omniscribe.ocr.deduplicator.

``dedup_segments`` collapses consecutive near-duplicate ON-SCREEN segments into
a single segment spanning ``[first.start, last.end]``. SPEECH segments pass
through unchanged, in receipt order.
"""

from __future__ import annotations

import pytest

from omniscribe.ocr.deduplicator import dedup_segments
from omniscribe.output import TranscriptSegment


def _on_screen(start: float, text: str, confidence: float = 0.9) -> TranscriptSegment:
    return TranscriptSegment(
        start=start,
        end=start,
        text=text,
        source="ON-SCREEN",
        confidence=confidence,
        language="en",
    )


def _speech(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        start=start,
        end=end,
        text=text,
        source="SPEECH",
        confidence=None,
        language="en",
    )


# gap_tolerance for 1.0 fps sampling = 2 * 1.0 = 2.0 seconds.
GAP_1FPS = 2.0


def test_empty_input_returns_empty_output() -> None:
    assert dedup_segments([], threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS) == []


def test_three_identical_on_screen_frames_collapse_to_single_segment() -> None:
    segments = [
        _on_screen(0.0, "Breaking News", 0.90),
        _on_screen(1.0, "Breaking News", 0.80),
        _on_screen(2.0, "Breaking News", 0.70),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 1
    collapsed = result[0]
    assert collapsed.text == "Breaking News"
    assert collapsed.start == 0.0
    assert collapsed.end == 2.0
    assert collapsed.source == "ON-SCREEN"
    assert collapsed.confidence == pytest.approx((0.90 + 0.80 + 0.70) / 3)


def test_similarity_below_threshold_splits_into_separate_segments() -> None:
    segments = [
        _on_screen(0.0, "hello world", 0.9),
        _on_screen(1.0, "goodbye moon", 0.9),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert [s.text for s in result] == ["hello world", "goodbye moon"]
    assert [s.start for s in result] == [0.0, 1.0]


def test_min_duration_drops_sub_threshold_blip() -> None:
    segments = [
        _on_screen(0.0, "flash", 0.9),
        _on_screen(0.2, "flash", 0.9),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.5, gap_tolerance=GAP_1FPS)

    assert result == []


def test_speech_segments_pass_through_in_order_unchanged() -> None:
    segments = [
        _speech(0.0, 1.0, "first"),
        _speech(2.0, 3.0, "second"),
        _speech(4.0, 5.0, "third"),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert [s.text for s in result] == ["first", "second", "third"]
    assert all(s.source == "SPEECH" for s in result)
    # Exact same objects — not copies — since SPEECH is untouched.
    assert result[0] is segments[0]


def test_interleaved_speech_and_on_screen_preserves_input_order() -> None:
    """SPEECH stays in receipt order; ON-SCREEN runs are collapsed in place."""
    segments = [
        _speech(0.0, 1.0, "speech-1"),
        _on_screen(0.5, "overlay", 0.9),
        _on_screen(1.5, "overlay", 0.9),
        _speech(2.5, 3.5, "speech-2"),
        _on_screen(4.0, "other", 0.9),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    texts = [s.text for s in result]
    sources = [s.source for s in result]

    # Expected: both SPEECH in receipt order, one collapsed 'overlay' ON-SCREEN,
    # one standalone 'other' ON-SCREEN.
    assert "speech-1" in texts
    assert "speech-2" in texts
    assert texts.index("speech-1") < texts.index("speech-2")

    assert sources.count("SPEECH") == 2
    assert sources.count("ON-SCREEN") == 2

    overlay = next(s for s in result if s.text == "overlay")
    assert overlay.start == 0.5
    assert overlay.end == 1.5

    other = next(s for s in result if s.text == "other")
    assert other.start == 4.0
    assert other.end == 4.0


def test_dedup_keeps_single_frame_caption() -> None:
    """Sprint OCR-Recall — a single-frame caption survives at ``min_duration=0.0``.

    Guards against accidentally re-introducing a positive ``min_duration``
    floor: the new aggregator emits one segment per detected line per frame,
    and a sub-second caption that only spans one sampled frame must still
    reach output. The 0.5s floor (pre-aggregation default) would have
    silently dropped this case.
    """
    seg = _on_screen(1.0, "KEINE KAMPFSPORTTECHNIK KEINE", 0.9)
    result = dedup_segments([seg], threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 1
    assert result[0].text == "KEINE KAMPFSPORTTECHNIK KEINE"
    assert result[0].start == 1.0
    assert result[0].end == 1.0


def test_gap_tolerance_breaks_cluster_when_exceeded() -> None:
    """Identical text but separated by a gap > 2 * 1/fps -> two clusters."""
    segments = [
        _on_screen(0.0, "same text", 0.9),
        _on_screen(5.0, "same text", 0.9),  # gap = 5s > 2.0s tolerance
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 2
    assert [s.start for s in result] == [0.0, 5.0]
    assert [s.end for s in result] == [0.0, 5.0]


def test_dedup_clusters_non_consecutive_same_text_across_frames() -> None:
    """Sprint OCR-Recall Part 2 — the multi-region-per-frame regression case.

    Each sampled frame produces multiple text regions (e.g. top pill, mid pill,
    bottom caption). In time-ordered receipt order, same-text occurrences are
    NOT consecutive — they are interleaved with sibling regions from the same
    frame. The pre-fix single-pass walk emitted 6 point segments here because
    text-mismatches broke the cluster on every step. Post-fix: the partition
    + group-by-canonical-text pass yields 3 multi-second clusters.
    """
    segments = [
        _on_screen(12.0, "TOP_PILL"),
        _on_screen(12.0, "MID_PILL"),
        _on_screen(12.0, "BOTTOM"),
        _on_screen(13.0, "TOP_PILL"),
        _on_screen(13.0, "MID_PILL"),
        _on_screen(13.0, "BOTTOM"),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 3
    by_text = {s.text: s for s in result}
    assert set(by_text) == {"TOP_PILL", "MID_PILL", "BOTTOM"}
    for seg in result:
        assert seg.start == 12.0
        assert seg.end == 13.0
        assert seg.source == "ON-SCREEN"


def test_dedup_groups_by_canonical_text_case_folded() -> None:
    """Case-folded canonical key clusters case-variants of the same text.

    Mirrors the merge_channels case-folding fix from the prior sprint: even if
    OCR varies between upper- and lower-case across frames, a single overlay
    must still resolve to one cluster.
    """
    segments = [
        _on_screen(12.0, "KEINE"),
        _on_screen(13.0, "keine"),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 1
    assert result[0].start == 12.0
    assert result[0].end == 13.0
    assert result[0].source == "ON-SCREEN"


def test_dedup_far_apart_identical_text_does_not_overcluster() -> None:
    """Grouping by text must not bypass the within-group gap-tolerance check.

    Two identical strings 40 seconds apart with gap_tolerance=2.0 must remain
    two separate clusters — the group-by-text optimisation cannot smuggle
    far-apart occurrences past the time-gap guard.
    """
    segments = [
        _on_screen(10.0, "X"),
        _on_screen(50.0, "X"),
    ]
    result = dedup_segments(segments, threshold=0.85, min_duration=0.0, gap_tolerance=GAP_1FPS)

    assert len(result) == 2
    assert [s.start for s in result] == [10.0, 50.0]
    assert [s.end for s in result] == [10.0, 50.0]

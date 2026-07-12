"""Unit tests for omniscribe.eval.scoring.

CI-safe: no GPU, no external dependencies beyond Python + rapidfuzz.
"""

from __future__ import annotations

import pytest

from omniscribe.eval.models import ExpectedText, GroundTruth
from omniscribe.eval.scoring import score_video
from omniscribe.output import TranscriptSegment


def _seg(text: str, start: float = 0.0, end: float | None = None) -> TranscriptSegment:
    """Convenience: build an ON-SCREEN TranscriptSegment."""
    return TranscriptSegment(
        start=start,
        end=end if end is not None else start,
        text=text,
        source="ON-SCREEN",
    )


def _gt(
    texts: list[tuple[str, bool, float | None, float | None]],
    language: str = "en",
) -> GroundTruth:
    """Convenience: build GroundTruth from tuple list (text, required, start, end)."""
    expected = [ExpectedText(text=t, required=r, start=s, end=e) for t, r, s, e in texts]
    return GroundTruth(language=language, expected_texts=expected)


class TestScoreVideo:
    """Score OCR output against ground truth."""

    def test_perfect_recall_and_precision(self) -> None:
        """Exact text match � everything hits."""
        segments = [_seg("Hello World", 1.0), _seg("Goodbye", 3.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
                ("Goodbye", True, 2.0, 6.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        assert result.precision == 1.0
        assert result.mean_match_similarity is not None
        assert result.mean_match_similarity >= 0.85

    def test_partial_recall(self) -> None:
        """One GT text matches, one is absent."""
        segments = [_seg("Hello World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
                ("MissingText", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.5
        assert result.mean_match_similarity is not None

    def test_precision_below_one(self) -> None:
        """Noise output segments that don't match any GT text."""
        segments = [_seg("Hello World", 1.0), _seg("Noise Text", 2.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        # "Noise Text" doesn't match, so 1/2 = 0.5
        assert result.precision == 0.5

    def test_near_miss_flagging(self) -> None:
        """Similarity between 0.50 and 0.85 flags as near-miss."""
        # "Hell0 W0rld" (2 substitutions) vs "Hello World" should score ~0.82
        segments = [_seg("Hell0 W0rld", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.0  # below 0.85 threshold
        per_text = result.per_text_results[0]
        assert per_text["near_miss"] is True
        assert per_text["matched"] is False
        assert per_text["similarity"] is not None
        assert 0.50 <= per_text["similarity"] < 0.85

    def test_time_window_filtering(self) -> None:
        """Segment outside the GT time window is excluded."""
        segments = [_seg("Hello World", 10.0)]  # Outside GT window [0, 5]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.0  # No match within window
        # The segment still counts as output for precision though
        assert result.precision == 0.0

    def test_empty_segments(self) -> None:
        """Zero output segments: recall 0, precision 1.0."""
        segments: list[TranscriptSegment] = []
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.0
        assert result.precision == 1.0  # no false positives

    def test_non_required_excluded_from_recall(self) -> None:
        """required=False texts don't affect recall denominator."""
        segments = [_seg("Hello World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
                ("SUBSCRIBE", False, None, None),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        # Only 1 required text, 1 match -> recall 1.0
        assert result.recall == 1.0
        # Non-required still reported in per_text_results
        assert len(result.per_text_results) == 2
        assert result.per_text_results[1]["expected_text"] == "SUBSCRIBE"
        assert result.per_text_results[1]["matched"] is False  # no segment for it

    def test_mean_match_similarity_none_when_no_matches(self) -> None:
        """No matches at all -> mean_match_similarity is None."""
        segments = [_seg("SomethingElse", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.0
        assert result.mean_match_similarity is None

    def test_precision_drops_with_noise(self) -> None:
        """Multiple output segments, some matching, some noise."""
        segments = [
            _seg("Hello World", 1.0),
            _seg("UIFollowerCount", 2.0),
            _seg("SubscribeNow", 3.0),
        ]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        # recall: 1 required, 1 match -> 1.0
        assert result.recall == 1.0
        # precision: 1 matching out of 3 -> ~0.333
        assert result.precision == pytest.approx(1.0 / 3.0)

    def test_per_text_results_structure(self) -> None:
        """per_text_results contains the expected keys."""
        segments = [_seg("Hello World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert len(result.per_text_results) == 1
        entry = result.per_text_results[0]
        assert "expected_text" in entry
        assert "matched" in entry
        assert "best_candidate" in entry
        assert "similarity" in entry
        assert "near_miss" in entry
        assert entry["expected_text"] == "Hello World"
        assert entry["matched"] is True
        assert entry["best_candidate"] == "Hello World"

    def test_fuzzy_match_via_rapidfuzz(self) -> None:
        """Fuzzy match: 'SUBSCRIBE!' vs 'SUBSCRIBE' should match."""
        segments = [_seg("SUBSCRIBE!", 1.0)]
        gt = _gt(
            [
                ("SUBSCRIBE", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        # "SUBSCRIBE!" vs "SUBSCRIBE" with str.lower should be ~97+%
        assert result.recall == 1.0
        assert result.per_text_results[0]["matched"] is True

    # ── Sprint 9.2: Pairwise multi-line matching ─────────────────────────

    def test_two_segments_pair_to_match_multiline_gt(self) -> None:
        """Two same-timestamp segments whose join matches a multi-line GT."""
        segments = [_seg("Hello", 1.0), _seg("World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        assert result.per_text_results[0]["best_candidate"] == "Hello World"

    def test_pair_join_order_independent(self) -> None:
        """Segments supplied in reverse reading order still match (both join orders tried)."""
        segments = [_seg("World", 1.0), _seg("Hello", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0

    def test_interleaved_noise_segment_does_not_block_pairing(self) -> None:
        """An unrelated same-timestamp segment that sorts between the pair does NOT block the match."""
        segments = [_seg("Hello", 1.0), _seg("UNRELATED", 1.0), _seg("World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0

    def test_segments_beyond_max_span_not_paired(self) -> None:
        """Two segments with starts > 2.0s apart do not pair (GT stays unmatched)."""
        segments = [_seg("Hello", 0.0), _seg("World", 5.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 10.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 0.0

    def test_pair_match_counts_both_segments_in_precision(self) -> None:
        """A pair match puts BOTH segment indices in the precision numerator."""
        segments = [_seg("Hello", 1.0), _seg("World", 1.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        assert result.precision == 1.0

    def test_individually_matched_segment_still_counts_in_precision(self) -> None:
        """A segment that matches some GT >= threshold still counts in precision
        even when it is not that GT's best candidate (multiple segments match
        the same GT but only one is labelled 'best')."""
        segments = [_seg("Hello World", 1.0), _seg("HELLO WORLD", 2.0)]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        # Both match "Hello World" at 1.0 (case-insensitive via str.lower).
        # Index 1 is NOT the best_candidate but still individually matched.
        assert result.precision == 1.0

    def test_mixed_single_and_pair_matches(self) -> None:
        """One GT matched by single segment + one GT matched by a pair."""
        segments = [
            _seg("Hello World", 1.0),
            _seg("Line 1", 2.0),
            _seg("Line 2", 2.0),
        ]
        gt = _gt(
            [
                ("Hello World", True, 0.0, 5.0),
                ("Line 1 Line 2", True, 0.0, 5.0),
            ]
        )
        result = score_video(segments, gt, fuzzy_threshold=0.85)
        assert result.recall == 1.0
        assert result.precision == 1.0

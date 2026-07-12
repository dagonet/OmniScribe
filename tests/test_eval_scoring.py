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

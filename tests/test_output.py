"""Unit tests for omniscribe.output — data model + JSON writer + merge_channels."""

from __future__ import annotations

from pathlib import Path

from omniscribe.output import Transcript, TranscriptSegment, merge_channels, write_json

# Default threshold mirrors ``OmniScribeConfig.merge_similarity_threshold`` so
# the test cases track the intended production cutoff.
_T: float = 0.85


def _speech(start: float, end: float, text: str, **kw: object) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, source="SPEECH", **kw)  # type: ignore[arg-type]


def _ocr(start: float, end: float, text: str, **kw: object) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, source="ON-SCREEN", **kw)  # type: ignore[arg-type]


def test_transcript_segment_defaults() -> None:
    seg = TranscriptSegment(start=0.0, end=1.0, text="hello")
    assert seg.source == "SPEECH"
    assert seg.confidence is None
    assert seg.language is None


def test_write_json_round_trip(tmp_path: Path) -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                start=0.0,
                end=1.5,
                text="hello world",
                confidence=-0.12,
                language="en",
            ),
            TranscriptSegment(start=1.5, end=3.0, text="second segment", language="en"),
        ],
        language="en",
    )
    out = tmp_path / "nested" / "transcript.json"

    write_json(transcript, out)

    assert out.is_file()
    restored = Transcript.model_validate_json(out.read_text(encoding="utf-8"))
    assert restored == transcript


def test_write_json_empty_segments_round_trip(tmp_path: Path) -> None:
    transcript = Transcript(segments=[], language="en")
    out = tmp_path / "empty.json"

    write_json(transcript, out)

    restored = Transcript.model_validate_json(out.read_text(encoding="utf-8"))
    assert restored.segments == []
    assert restored.language == "en"


def test_write_json_creates_parent_dirs(tmp_path: Path) -> None:
    transcript = Transcript(segments=[], language="en")
    out = tmp_path / "a" / "b" / "c" / "t.json"

    write_json(transcript, out)

    assert out.is_file()


# ── merge_channels: ordering / passthrough ─────────────────────────────────


def test_merge_channels_empty_speech_returns_ocr() -> None:
    ocr = [
        _ocr(0.0, 0.0, "title"),
        _ocr(2.0, 2.0, "credits"),
    ]

    merged = merge_channels([], ocr, threshold=_T)

    assert merged == ocr


def test_merge_channels_empty_ocr_returns_speech() -> None:
    speech = [_speech(0.0, 1.0, "hello"), _speech(2.0, 3.0, "world")]

    merged = merge_channels(speech, [], threshold=_T)

    assert merged == speech


def test_merge_channels_both_empty_returns_empty() -> None:
    assert merge_channels([], [], threshold=_T) == []


def test_merge_channels_speech_wins_equal_start_ties() -> None:
    # Non-overlapping (OCR is zero-duration at same start as speech start) so
    # no collapse fires; we verify stable-sort ordering keeps SPEECH first.
    speech = [_speech(1.0, 2.0, "spoken")]
    ocr = [_ocr(1.0, 1.0, "overlay")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert [s.source for s in merged] == ["SPEECH", "ON-SCREEN"]


def test_merge_channels_interleaves_by_start() -> None:
    speech = [
        _speech(0.0, 1.0, "a"),
        _speech(3.0, 4.0, "c"),
    ]
    ocr = [
        _ocr(1.5, 1.5, "b"),
        _ocr(5.0, 5.0, "d"),
    ]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert [s.text for s in merged] == ["a", "b", "c", "d"]


# ── merge_channels: cross-source dedup (collapse to BOTH) ──────────────────


def test_merge_channels_collapses_exact_match_to_both() -> None:
    speech = [_speech(1.0, 5.0, "hello world", confidence=-0.1, language="en")]
    ocr = [_ocr(2.0, 4.0, "hello world", confidence=0.95, language="en")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 1
    both = merged[0]
    assert both.source == "BOTH"
    assert both.text == "hello world"
    assert both.start == 1.0
    assert both.end == 5.0
    # confidence anchor is speech, not OCR pixel confidence.
    assert both.confidence == -0.1
    assert both.language == "en"


def test_merge_channels_below_threshold_keeps_both_separate() -> None:
    # Wildly different texts — WRatio far below 85.
    speech = [_speech(1.0, 5.0, "the quarterly revenue forecast")]
    ocr = [_ocr(2.0, 4.0, "xyzzy plugh")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 2
    assert {s.source for s in merged} == {"SPEECH", "ON-SCREEN"}


def test_merge_channels_non_overlap_keeps_both_even_if_text_matches() -> None:
    speech = [_speech(0.0, 1.0, "hello world")]
    ocr = [_ocr(10.0, 11.0, "hello world")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 2
    assert {s.source for s in merged} == {"SPEECH", "ON-SCREEN"}


def test_merge_channels_strict_boundary_no_overlap() -> None:
    # Touching boundaries (speech.end == ocr.start) must NOT overlap.
    speech = [_speech(0.0, 5.0, "hello world")]
    ocr = [_ocr(5.0, 10.0, "hello world")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 2
    sources = [s.source for s in merged]
    assert "BOTH" not in sources


def test_merge_channels_multiple_ocr_picks_highest_similarity() -> None:
    speech = [_speech(0.0, 10.0, "deploy the service to production")]
    # Two overlapping OCR: low-match and high-match. Only the high-match
    # should collapse; the other stays ON-SCREEN.
    ocr = [
        _ocr(1.0, 2.0, "deploy the service to production"),  # high WRatio
        _ocr(3.0, 4.0, "unrelated side banner text"),  # low WRatio
    ]

    merged = merge_channels(speech, ocr, threshold=_T)

    sources = sorted(s.source for s in merged)
    assert sources == ["BOTH", "ON-SCREEN"]
    both = next(s for s in merged if s.source == "BOTH")
    assert both.text == "deploy the service to production"


def test_merge_channels_multi_match_keeps_nonwinning_ocr_onscreen() -> None:
    # Two overlapping OCR both meeting threshold; only the top scorer consumes.
    speech = [_speech(0.0, 10.0, "hello world")]
    ocr = [
        _ocr(1.0, 2.0, "hello world"),  # exact — WRatio 100
        _ocr(3.0, 4.0, "hello worlds"),  # near-exact — slightly lower
    ]

    merged = merge_channels(speech, ocr, threshold=_T)

    both = [s for s in merged if s.source == "BOTH"]
    on_screen = [s for s in merged if s.source == "ON-SCREEN"]
    assert len(both) == 1
    assert len(on_screen) == 1
    assert both[0].text == "hello world"
    # The losing candidate stays intact.
    assert on_screen[0].text == "hello worlds"


def test_merge_channels_lossy_text_keeps_speech_text() -> None:
    # When OCR holds richer detail, merged text is still speech.text.
    # WRatio on these partial/token-set cases meets threshold.
    speech = [_speech(0.0, 5.0, "AcmeCloud Enterprise v4.2")]
    ocr = [_ocr(1.0, 4.0, "AcmeCloud Enterprise v4.2 — production console")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 1
    assert merged[0].source == "BOTH"
    # Lossy-on-collapse: richer OCR detail is dropped; speech text wins.
    assert merged[0].text == "AcmeCloud Enterprise v4.2"


def test_merge_channels_cue_normalization_collapses_whitespace() -> None:
    # Speech text carries a newline; merged BOTH cue must be single-spaced.
    speech = [_speech(0.0, 5.0, "hello\nworld")]
    ocr = [_ocr(1.0, 4.0, "hello world")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 1
    assert merged[0].source == "BOTH"
    assert merged[0].text == "hello world"


def test_merge_channels_collapsed_segment_spans_union_of_times() -> None:
    speech = [_speech(2.0, 6.0, "hello world")]
    ocr = [_ocr(1.0, 4.0, "hello world")]

    merged = merge_channels(speech, ocr, threshold=_T)

    assert len(merged) == 1
    both = merged[0]
    assert both.start == 1.0
    assert both.end == 6.0


def test_merge_channels_tie_break_at_zero_start() -> None:
    """Two OCR segments both match at identical WRatio and both start at 0.0.

    Regression guard for the tie-break sentinel bug: with
    ``best_start = 0.0`` as the initial sentinel, a second same-score
    candidate at ``oc.start == 0.0`` would fail the ``oc.start < best_start``
    predicate and the first-seen would win by iteration order rather than
    by the documented "earliest start wins" rule. With ``best_start = None``
    the first candidate sets the real start, and subsequent same-score
    candidates compare against a real prior winner.
    """
    speech = [_speech(0.0, 5.0, "hello world")]
    ocr = [
        _ocr(0.0, 3.0, "hello world"),
        _ocr(0.0, 3.0, "hello world"),
    ]

    merged = merge_channels(speech, ocr, threshold=_T)

    both = [s for s in merged if s.source == "BOTH"]
    on_screen = [s for s in merged if s.source == "ON-SCREEN"]
    assert len(both) == 1
    assert len(on_screen) == 1


def test_merge_channels_tie_break_earliest_start_wins() -> None:
    """When two OCR segments tie on WRatio with different starts, earliest wins.

    The earlier-start OCR is consumed into BOTH; the later-start survives
    as ON-SCREEN — proves the tie-break predicate fires correctly once a
    real prior winner is in play.
    """
    speech = [_speech(5.0, 10.0, "hello")]
    ocr = [
        _ocr(8.0, 9.0, "hello"),  # later
        _ocr(5.5, 6.5, "hello"),  # earlier
    ]

    merged = merge_channels(speech, ocr, threshold=_T)

    both = [s for s in merged if s.source == "BOTH"]
    on_screen = [s for s in merged if s.source == "ON-SCREEN"]
    assert len(both) == 1
    # The earlier-start OCR (5.5) was consumed; the later-start (8.0) survives.
    assert on_screen[0].start == 8.0


def test_merge_channels_each_ocr_consumed_at_most_once() -> None:
    # Two speech segments both overlap and match one OCR segment — only the
    # first speech consumes it; the second must emit as bare SPEECH.
    speech = [
        _speech(0.0, 10.0, "shared overlay text"),
        _speech(1.0, 9.0, "shared overlay text"),
    ]
    ocr = [_ocr(2.0, 8.0, "shared overlay text")]

    merged = merge_channels(speech, ocr, threshold=_T)

    sources = sorted(s.source for s in merged)
    assert sources == ["BOTH", "SPEECH"]


# ── Regression: --no-ocr path still serializes cleanly ─────────────────────


def test_transcript_serializes_with_only_speech_segments(tmp_path: Path) -> None:
    """Regression guard for the Literal-tightening of ``source``."""
    transcript = Transcript(
        segments=[_speech(0.0, 1.0, "hello")],
        language="en",
    )
    out = tmp_path / "no_ocr.json"

    write_json(transcript, out)

    restored = Transcript.model_validate_json(out.read_text(encoding="utf-8"))
    assert restored == transcript


def test_write_json_is_pretty_printed(tmp_path: Path) -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(start=0.0, end=1.0, text="hi")],
        language="en",
    )
    out = tmp_path / "pretty.json"

    write_json(transcript, out)

    text = out.read_text(encoding="utf-8")
    assert "\n  " in text  # 2-space indent marker

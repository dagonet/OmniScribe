"""Unit tests for omniscribe.output — data model + JSON writer."""

from __future__ import annotations

from pathlib import Path

from omniscribe.output import Transcript, TranscriptSegment, write_json


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


def test_write_json_is_pretty_printed(tmp_path: Path) -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(start=0.0, end=1.0, text="hi")],
        language="en",
    )
    out = tmp_path / "pretty.json"

    write_json(transcript, out)

    text = out.read_text(encoding="utf-8")
    assert "\n  " in text  # 2-space indent marker

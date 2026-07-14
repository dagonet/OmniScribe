"""Tests for OcrEngine protocol and write_transcript dispatcher.

* ``OcrEngine`` is a structural (duck) protocol — any class with the right
  methods and attributes satisfies ``isinstance(engine, OcrEngine)``.
* ``write_transcript`` dispatches over the four format writers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ── OcrEngine protocol tests ──────────────────────────────────────────


def test_rapid_ocr_satisfies_protocol() -> None:
    """RapidOCREngine with a mocked constructor must satisfy OcrEngine."""
    from omniscribe.config import OmniScribeConfig
    from omniscribe.ocr.protocol import OcrEngine

    with patch("omniscribe.ocr.rapid_ocr.RapidOCR"):
        from omniscribe.ocr.rapid_ocr import RapidOCREngine

        engine = RapidOCREngine(OmniScribeConfig())
        assert isinstance(engine, OcrEngine)


def test_fake_with_full_surface_satisfies_protocol() -> None:
    """A minimal class with the right method signatures passes isinstance."""
    from omniscribe.ocr.protocol import OcrEngine

    class _FakeEngine:
        last_frame_count: int = 0

        def extract(
            self,
            video_path: Path,
            *,
            detected_language: str | None = None,
            funnel=None,
        ) -> list:
            return []

        def extract_images(
            self,
            image_paths,
            *,
            detected_language: str | None = None,
            funnel=None,
            timestamps=None,
        ) -> list:
            return []

    assert isinstance(_FakeEngine(), OcrEngine)


def test_fake_missing_method_fails_isinstance() -> None:
    """A class missing ``extract_images`` must NOT pass isinstance."""
    from omniscribe.ocr.protocol import OcrEngine

    class _MissingExtractImages:
        last_frame_count: int = 0

        def extract(
            self,
            video_path: Path,
            *,
            detected_language: str | None = None,
            funnel=None,
        ) -> list:
            return []

    assert not isinstance(_MissingExtractImages(), OcrEngine)


def test_fake_missing_attribute_fails_isinstance() -> None:
    """A class missing ``last_frame_count`` must NOT pass isinstance."""
    from omniscribe.ocr.protocol import OcrEngine

    class _MissingFrameCount:
        def extract(
            self,
            video_path: Path,
            *,
            detected_language: str | None = None,
            funnel=None,
        ) -> list:
            return []

        def extract_images(
            self,
            image_paths,
            *,
            detected_language: str | None = None,
            funnel=None,
            timestamps=None,
        ) -> list:
            return []

    assert not isinstance(_MissingFrameCount(), OcrEngine)


# ── write_transcript dispatcher tests ────────────────────────────────


def test_write_transcript_dispatches_json(tmp_path: Path) -> None:
    """fmt='json' must call write_json."""
    from omniscribe.output import Transcript, write_transcript

    transcript = Transcript(segments=[], language="en")
    path = tmp_path / "out.json"

    with patch("omniscribe.output.write_json") as mock_write:
        write_transcript(transcript, path, "json")

    mock_write.assert_called_once_with(transcript, path)


def test_write_transcript_dispatches_txt(tmp_path: Path) -> None:
    """fmt='txt' must call write_txt."""
    from omniscribe.output import Transcript, write_transcript

    transcript = Transcript(segments=[], language="en")
    path = tmp_path / "out.txt"

    with patch("omniscribe.output.write_txt") as mock_write:
        write_transcript(transcript, path, "txt")

    mock_write.assert_called_once_with(transcript, path)


def test_write_transcript_dispatches_srt(tmp_path: Path) -> None:
    """fmt='srt' must call write_srt."""
    from omniscribe.output import Transcript, write_transcript

    transcript = Transcript(segments=[], language="en")
    path = tmp_path / "out.srt"

    with patch("omniscribe.output.write_srt") as mock_write:
        write_transcript(transcript, path, "srt")

    mock_write.assert_called_once_with(transcript, path)


def test_write_transcript_dispatches_md(tmp_path: Path) -> None:
    """fmt='md' must call write_markdown."""
    from omniscribe.output import Transcript, write_transcript

    transcript = Transcript(segments=[], language="en")
    path = tmp_path / "out.md"

    with patch("omniscribe.output.write_markdown") as mock_write:
        write_transcript(transcript, path, "md")

    mock_write.assert_called_once_with(transcript, path)


def test_write_transcript_raises_on_unknown_format() -> None:
    """Unknown fmt must raise OmniScribeError."""
    from omniscribe.errors import OmniScribeError
    from omniscribe.output import Transcript, write_transcript

    transcript = Transcript(segments=[], language="en")
    path = Path("/dev/null/out.foo")

    with pytest.raises(OmniScribeError, match="Unknown output format"):
        write_transcript(transcript, path, "foo")

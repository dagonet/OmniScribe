"""Transcript data models and JSON writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path


class TranscriptSegment(BaseModel):
    """A single transcript segment (speech or on-screen text)."""

    start: float
    end: float
    text: str
    source: str = "SPEECH"
    confidence: float | None = None
    language: str | None = None


class Transcript(BaseModel):
    """Full transcript: ordered segments plus detected language."""

    segments: list[TranscriptSegment]
    language: str


def write_json(transcript: Transcript, path: Path) -> None:
    """Write ``transcript`` as pretty JSON to ``path`` (UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

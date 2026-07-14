"""OCR engine protocol — structural subtyping for swappable backends.

Uses :class:`typing.Protocol` (not ABC) so a future vision-LLM backend
satisfies the interface by having the right methods and attributes, without
needing to inherit from a base class.  Marked ``@runtime_checkable`` so
``isinstance(engine, OcrEngine)`` works at runtime.

The method signatures mirror :class:`omniscribe.ocr.rapid_ocr.RapidOCREngine`
exactly — every backend must expose:
* ``extract(video_path, *, detected_language, funnel)``
* ``extract_images(image_paths, *, detected_language, funnel, timestamps)``
* ``last_frame_count`` (``int`` attribute, updated after each call)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from omniscribe.eval.funnel import FunnelCounts
    from omniscribe.output import TranscriptSegment


@runtime_checkable
class OcrEngine(Protocol):
    """Structural protocol for OCR backends.

    Any object whose attributes and methods match this protocol — whether
    via inheritance, duck typing, or explicit implementation — is accepted
    as an ``OcrEngine`` at runtime via ``isinstance(engine, OcrEngine)``.
    """

    last_frame_count: int
    """Number of frames processed by the most recent ``extract`` or
    ``extract_images`` call — used by the CLI for status logging."""

    def extract(
        self,
        video_path: Path,
        *,
        detected_language: str | None = None,
        funnel: FunnelCounts | None = None,
    ) -> list[TranscriptSegment]:
        """Sample frames from ``video_path`` and return on-screen text segments.

        Each yielded RapidOCR result contributes zero or more
        :class:`TranscriptSegment` instances — one per aggregated text line
        per frame.
        """
        ...

    def extract_images(
        self,
        image_paths: Sequence[Path],
        *,
        detected_language: str | None = None,
        funnel: FunnelCounts | None = None,
        timestamps: Sequence[tuple[float, float]] | None = None,
    ) -> list[TranscriptSegment]:
        """OCR each image in ``image_paths`` and return text segments.

        Designed for native photo-post processing.  ``timestamps`` maps
        each image to a ``(start, end)`` pair; when ``None``, defaults to
        ``(i, i + 1.0)`` for image *i*.
        """
        ...

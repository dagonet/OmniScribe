"""User-facing error types for OmniScribe.

Intended error hierarchy (to be implemented as part of Phase 6 API hardening)::

    OmniScribeError
    ├── AcquireError      — download / network / file-access failures
    ├── TranscriptionError — ASR / Whisper failures
    └── OcrError          — OCR / RapidOCR failures

See ``docs/architecture.md`` for the full error-handling design.
"""


class OmniScribeError(Exception):
    """Raised for user-visible failures (download, ffmpeg, ASR, etc.).

    CLI catches this and prints a clean single-line message — never a traceback.
    """

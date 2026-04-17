"""User-facing error types for OmniScribe."""


class OmniScribeError(Exception):
    """Raised for user-visible failures (download, ffmpeg, ASR, etc.).

    CLI catches this and prints a clean single-line message — never a traceback.
    """

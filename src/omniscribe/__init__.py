"""OmniScribe — transcribe any video with speech + on-screen text."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("omniscribe")
except PackageNotFoundError:  # editable install without metadata
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]

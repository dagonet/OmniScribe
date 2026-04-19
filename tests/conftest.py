"""Shared pytest fixtures.

Patch targets live at the *import site*, e.g. ``omniscribe.acquire.downloader.YoutubeDL``,
never at the library's own module (``yt_dlp.YoutubeDL``). Patching at the import site
captures the bound name inside the module under test; patching at the library path leaves
the already-bound alias untouched and the real class still runs.
"""

from __future__ import annotations

import logging
import os
import wave
from collections.abc import Iterator
from pathlib import Path

# Neutralize Rich's TTY-dependent styling before Typer imports. On GitHub Actions
# (``CI=true``/``GITHUB_ACTIONS=true``) Rich emits bold/dim escape codes *inside*
# quoted flag names, which breaks substring assertions in CLI tests (e.g.
# ``"--output"``, ``"Invalid value for '--platform'"``). ``NO_COLOR`` only
# disables color per no-color.org — bold/dim persist — so the reliable switch is
# ``TERM=dumb``, which Rich treats as a non-styling terminal. ``COLUMNS=200``
# stops panel borders from wrapping long flag names across lines. Must run at
# module import (before ``from omniscribe.cli import app``) because Rich caches
# terminal detection when the Typer app is first constructed.
os.environ["TERM"] = "dumb"
os.environ["COLUMNS"] = "200"

import pytest

from omniscribe.config import OmniScribeConfig


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OmniScribeConfig:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni-tmp"))
    monkeypatch.delenv("OMNI_WHISPER_LANGUAGE", raising=False)
    return OmniScribeConfig()


@pytest.fixture
def silence_wav_path(tmp_path: Path) -> Path:
    """1s 16 kHz mono PCM silence.

    Mock-only fixture — this file is byte-valid WAV but not exercised by real decoders
    in unit tests. Sprint 1.2 tests that exercise ffmpeg/faster-whisper must mock those
    boundaries, not decode this file.
    """
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return path


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Clear root logger handlers + level around each test (CliRunner isolation)."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved_handlers)
    root.level = saved_level

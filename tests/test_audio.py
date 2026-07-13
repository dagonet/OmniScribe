"""Unit tests for omniscribe.audio (all subprocess boundaries mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from omniscribe.audio import extract_audio
from omniscribe.errors import OmniScribeError


def test_extract_audio_builds_correct_ffmpeg_argv(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "out" / "audio.wav"

    with (
        patch("omniscribe.audio._FFMPEG", "/usr/bin/ffmpeg"),
        patch("omniscribe.audio.subprocess.run") as mock_run,
    ):
        result = extract_audio(video, out)

    assert result == out
    assert out.parent.is_dir()
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == [
        "/usr/bin/ffmpeg",
        "-i",
        str(video),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-vn",
        "-f",
        "wav",
        str(out),
        "-y",
    ]
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["shell"] is False


def test_extract_audio_raises_when_ffmpeg_missing(tmp_path: Path) -> None:
    with (
        patch("omniscribe.audio._FFMPEG", None),
        pytest.raises(OmniScribeError, match="ffmpeg not found"),
    ):
        extract_audio(tmp_path / "a.mp4", tmp_path / "b.wav")


def test_extract_audio_wraps_called_process_error(tmp_path: Path) -> None:
    err = subprocess.CalledProcessError(
        returncode=1,
        cmd=["ffmpeg"],
        stderr=b"line one\nline two: Invalid data found\n",
    )
    with (
        patch("omniscribe.audio._FFMPEG", "/usr/bin/ffmpeg"),
        patch("omniscribe.audio.subprocess.run", side_effect=err),
        pytest.raises(OmniScribeError, match="Invalid data found"),
    ):
        extract_audio(tmp_path / "a.mp4", tmp_path / "b.wav")


# -- get_duration -------------------------------------------------------------


def test_get_duration_parses_ffprobe(tmp_path: Path) -> None:
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake")
    mock_stdout = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"13.35\n", stderr=b"")

    with patch("omniscribe.audio.subprocess.run", return_value=mock_stdout):
        from omniscribe.audio import get_duration

        result = get_duration(audio)

    assert result == pytest.approx(13.35)


def test_get_duration_failure_returns_none(tmp_path: Path) -> None:
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake")

    with (
        patch("omniscribe.audio.subprocess.run", side_effect=FileNotFoundError),
        patch("omniscribe.audio.shutil.which", return_value="/usr/bin/ffprobe"),
    ):
        from omniscribe.audio import get_duration

        result = get_duration(audio)

    assert result is None

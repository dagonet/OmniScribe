"""Smoke tests for the OmniScribe CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from omniscribe import __version__
from omniscribe.cli import app
from omniscribe.errors import OmniScribeError
from omniscribe.output import Transcript, TranscriptSegment


def test_version_flag_prints_version_and_exits() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0  # Typer exits with the help banner
    assert "Transcribe videos" in result.output


def test_transcribe_help() -> None:
    result = CliRunner().invoke(app, ["transcribe", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.output
    assert "--language" in result.output


def _patched_pipeline(
    tmp_path: Path,
    segments: list[TranscriptSegment] | None = None,
    detected_language: str = "en",
):
    """Return a patch context that mocks every external boundary."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"fake")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake")

    download_patch = patch(
        "omniscribe.cli.download_video",
        return_value=video_path,
    )
    extract_patch = patch(
        "omniscribe.cli.extract_audio",
        return_value=audio_path,
    )
    whisper_patch = patch("omniscribe.cli.WhisperTranscriber")
    return download_patch, extract_patch, whisper_patch, video_path, audio_path


def test_transcribe_writes_json_with_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    segments = [
        TranscriptSegment(start=0.0, end=1.0, text="hello", language="en"),
    ]

    dl, ex, wh, _, _ = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls:
        mock_whisper_cls.return_value.transcribe.return_value = (segments, "en")
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert output.is_file()
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    assert len(restored.segments) == 1
    assert restored.language == "en"


def test_transcribe_silent_video_produces_zero_segment_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "silent.json"

    dl, ex, wh, _, _ = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    assert restored.segments == []
    assert restored.language == "en"


def test_transcribe_cleans_temp_dir_by_default(tmp_path: Path, monkeypatch) -> None:
    temp_dir = tmp_path / "omni"
    monkeypatch.setenv("OMNI_TEMP_DIR", str(temp_dir))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, _, _ = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        # Seed the temp dir so the cleanup branch has something to remove.
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "leftover.bin").write_bytes(b"x")

        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert not temp_dir.exists()


def test_transcribe_keeps_temp_dir_when_configured(tmp_path: Path, monkeypatch) -> None:
    temp_dir = tmp_path / "omni"
    monkeypatch.setenv("OMNI_TEMP_DIR", str(temp_dir))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, _, _ = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "leftover.bin").write_bytes(b"x")

        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert temp_dir.exists()
    assert (temp_dir / "leftover.bin").is_file()


def test_transcribe_omniscribe_error_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    with patch(
        "omniscribe.cli.download_video",
        side_effect=OmniScribeError("ffmpeg not found on PATH"),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output)],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    # The error is printed on stderr via typer.secho(..., err=True); CliRunner
    # captures both streams together in `output` by default.
    assert "ffmpeg not found on PATH" in result.output or "ffmpeg not found on PATH" in (
        result.stderr if hasattr(result, "stderr") else ""
    )


def test_transcribe_language_override_threads_into_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, _, _ = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "fr")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--language", "fr"],
        )

    assert result.exit_code == 0, result.output
    (config_arg,), _ = mock_whisper_cls.call_args
    assert config_arg.whisper_language == "fr"

"""Smoke tests for the OmniScribe CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from omniscribe import __version__
from omniscribe.cli import app
from omniscribe.errors import OmniScribeError
from omniscribe.output import Transcript, TranscriptSegment


def _ocr_seg(start: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=start, text=text, source="ON-SCREEN", language="en")


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
    assert "--ocr" in result.output
    assert "--no-ocr" in result.output
    assert "--ocr-language" in result.output
    assert "--platform" in result.output


def _patched_pipeline(tmp_path: Path):
    """Return patch context managers that mock every external boundary.

    Includes ``RapidOCREngine`` because ``config.ocr_enabled`` defaults to
    ``True`` — any test that does not explicitly disable OCR would otherwise
    hit a real ``RapidOCR(params=...)`` init.
    """
    download_patch = patch("omniscribe.cli.download_video", return_value=tmp_path / "video.mp4")
    extract_patch = patch("omniscribe.cli.extract_audio", return_value=tmp_path / "audio.wav")
    whisper_patch = patch("omniscribe.cli.WhisperTranscriber")
    ocr_patch = patch("omniscribe.cli.RapidOCREngine")
    return download_patch, extract_patch, whisper_patch, ocr_patch


def test_transcribe_writes_json_with_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    segments = [
        TranscriptSegment(start=0.0, end=1.0, text="hello", language="en"),
    ]

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_ocr_cls.return_value.extract.return_value = []
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

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_ocr_cls.return_value.extract.return_value = []
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

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_ocr_cls.return_value.extract.return_value = []
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

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_ocr_cls.return_value.extract.return_value = []
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
    # CliRunner merges stderr into `result.output` by default.
    assert "ffmpeg not found on PATH" in result.output


def test_transcribe_language_override_threads_into_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_ocr_cls.return_value.extract.return_value = []
        mock_whisper_cls.return_value.transcribe.return_value = ([], "fr")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--language", "fr"],
        )

    assert result.exit_code == 0, result.output
    (config_arg,), _ = mock_whisper_cls.call_args
    assert config_arg.whisper_language == "fr"


def test_transcribe_ocr_flag_interleaves_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    # Sprint 2.2 dedup defaults drop zero-duration segments; disable the floor
    # here so this test can continue to assert interleaving behaviour.
    monkeypatch.setenv("OMNI_DEDUP_MIN_DURATION", "0")
    output = tmp_path / "out.json"

    speech = [TranscriptSegment(start=0.0, end=1.0, text="hello", language="en")]
    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = (speech, "en")
        mock_ocr_cls.return_value.extract.return_value = [
            _ocr_seg(0.5, "first overlay text"),
            _ocr_seg(2.0, "completely different caption"),
        ]
        # Sprint 3.2 frequency filter divides by last_frame_count; set enough
        # frames that 1/N is below the 0.95 threshold so neither overlay is dropped.
        mock_ocr_cls.return_value.last_frame_count = 10
        result = CliRunner().invoke(
            app,
            [
                "transcribe",
                "fake.mp4",
                "--output",
                str(output),
                "--language",
                "en",
                "--ocr",
                "--ocr-language",
                "ch",
            ],
        )

    assert result.exit_code == 0, result.output

    # Whisper received the --language override.
    (wh_cfg,), _ = mock_whisper_cls.call_args
    assert wh_cfg.whisper_language == "en"

    # RapidOCREngine received the --ocr-language override.
    (ocr_cfg,), _ = mock_ocr_cls.call_args
    assert ocr_cfg.ocr_language == "ch"

    # RapidOCREngine.extract() was actually invoked (not just constructed).
    mock_ocr_cls.return_value.extract.assert_called_once()

    # Output is interleaved by start.
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    assert [s.source for s in restored.segments] == [
        "SPEECH",
        "ON-SCREEN",
        "ON-SCREEN",
    ]
    assert [s.start for s in restored.segments] == [0.0, 0.5, 2.0]


def test_transcribe_no_ocr_flag_skips_engine(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_OCR_ENABLED", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--no-ocr"],
        )

    assert result.exit_code == 0, result.output
    mock_ocr_cls.assert_not_called()


def test_transcribe_env_ocr_disabled_without_flag_skips_engine(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_OCR_ENABLED", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_ocr_cls.assert_not_called()


def test_transcribe_cli_ocr_flag_overrides_env_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_OCR_ENABLED", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app, ["transcribe", "fake.mp4", "--output", str(output), "--ocr"]
        )

    assert result.exit_code == 0, result.output
    mock_ocr_cls.assert_called_once()


def test_transcribe_ocr_dedup_collapses_duplicate_overlays(tmp_path: Path, monkeypatch) -> None:
    """Three identical ON-SCREEN segments + 1 SPEECH → 1 collapsed + 1 SPEECH."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_DEDUP_MIN_DURATION", "0")
    output = tmp_path / "out.json"

    speech = [TranscriptSegment(start=0.0, end=1.0, text="hello", language="en")]
    ocr_segments = [
        _ocr_seg(0.5, "Breaking News"),
        _ocr_seg(1.5, "Breaking News"),
        _ocr_seg(2.5, "Breaking News"),
    ]

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = (speech, "en")
        mock_ocr_cls.return_value.extract.return_value = ocr_segments
        # 100 frames → 3/100 ratio per text, below 0.95 freq threshold.
        mock_ocr_cls.return_value.last_frame_count = 100
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--ocr"],
        )

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))

    # One collapsed ON-SCREEN + one SPEECH.
    assert len(restored.segments) == 2
    assert [s.source for s in restored.segments] == ["SPEECH", "ON-SCREEN"]
    assert [s.start for s in restored.segments] == [0.0, 0.5]

    collapsed = restored.segments[1]
    assert collapsed.text == "Breaking News"
    assert collapsed.start == 0.5
    assert collapsed.end == 2.5


def test_transcribe_zero_speech_zero_ocr_produces_empty_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app, ["transcribe", "fake.mp4", "--output", str(output), "--ocr"]
        )

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    assert restored.segments == []
    assert restored.language == "en"


def test_transcribe_platform_flag_overrides_config(tmp_path: Path, monkeypatch) -> None:
    """--platform tiktok on a non-TikTok source should override platform_profile."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--platform", "tiktok"],
        )

    assert result.exit_code == 0, result.output
    # Sprint 3.1 plumbs --platform into config.platform_profile only; profile
    # resolution + OCR wire-in land in Sprint 3.2. WhisperTranscriber is the
    # first downstream consumer of the merged config, so inspect it.
    (wh_cfg,), _ = mock_whisper_cls.call_args
    assert wh_cfg.platform_profile == "tiktok"


def test_transcribe_invalid_platform_flag_exits_nonzero(tmp_path: Path) -> None:
    """--platform bogus should fail via click.Choice, not pydantic traceback."""
    output = tmp_path / "out.json"
    result = CliRunner().invoke(
        app,
        ["transcribe", "fake.mp4", "--output", str(output), "--platform", "bogus"],
    )
    assert result.exit_code != 0
    assert "Invalid value for '--platform'" in result.output


def test_invalid_platform_profile_env_raises_validation_error(monkeypatch) -> None:
    """OMNI_PLATFORM_PROFILE=bogus bypasses click.Choice but hits the pydantic validator."""
    from pydantic import ValidationError

    from omniscribe.config import OmniScribeConfig

    monkeypatch.setenv("OMNI_PLATFORM_PROFILE", "bogus")
    with pytest.raises(ValidationError):
        OmniScribeConfig()


def test_platform_profile_env_threads_into_config(monkeypatch) -> None:
    """OMNI_PLATFORM_PROFILE=instagram (valid) should land in config."""
    from omniscribe.config import OmniScribeConfig

    monkeypatch.setenv("OMNI_PLATFORM_PROFILE", "instagram")
    cfg = OmniScribeConfig()
    assert cfg.platform_profile == "instagram"


def test_ui_filter_tiktok_drops_sidebar_handle(tmp_path: Path, monkeypatch) -> None:
    """--platform tiktok --ocr with a sidebar handle + body text:
    the handle must be dropped by the pattern filter; the body survives."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_DEDUP_MIN_DURATION", "0")
    output = tmp_path / "out.json"

    ocr_segments = [
        _ocr_seg(0.5, "@creator"),
        _ocr_seg(0.5, "hello world"),
    ]

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        engine_instance = mock_ocr_cls.return_value
        engine_instance.extract.return_value = ocr_segments
        # 10 frames so "hello world"'s ratio is 1/10 < 0.95 (kept).
        engine_instance.last_frame_count = 10
        result = CliRunner().invoke(
            app,
            [
                "transcribe",
                "fake.mp4",
                "--output",
                str(output),
                "--platform",
                "tiktok",
                "--ocr",
            ],
        )

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    texts = [s.text for s in restored.segments]
    assert "hello world" in texts
    assert "@creator" not in texts


def test_no_ui_filter_flag_keeps_sidebar_handle(tmp_path: Path, monkeypatch) -> None:
    """With --no-ui-filter, the sidebar handle must NOT be dropped."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_DEDUP_MIN_DURATION", "0")
    output = tmp_path / "out.json"

    ocr_segments = [
        _ocr_seg(0.5, "@creator"),
        _ocr_seg(0.5, "hello world"),
    ]

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        engine_instance = mock_ocr_cls.return_value
        engine_instance.extract.return_value = ocr_segments
        engine_instance.last_frame_count = 1
        result = CliRunner().invoke(
            app,
            [
                "transcribe",
                "fake.mp4",
                "--output",
                str(output),
                "--platform",
                "tiktok",
                "--ocr",
                "--no-ui-filter",
            ],
        )

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    texts = [s.text for s in restored.segments]
    assert "hello world" in texts
    assert "@creator" in texts


def test_ui_filter_env_disabled_without_flag_keeps_handle(tmp_path: Path, monkeypatch) -> None:
    """OMNI_UI_FILTER_ENABLED=false + no CLI flag must let sidebar chrome through."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_DEDUP_MIN_DURATION", "0")
    monkeypatch.setenv("OMNI_UI_FILTER_ENABLED", "false")
    output = tmp_path / "out.json"

    ocr_segments = [
        _ocr_seg(0.5, "@creator"),
        _ocr_seg(0.5, "hello world"),
    ]

    dl, ex, wh, oc = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        engine_instance = mock_ocr_cls.return_value
        engine_instance.extract.return_value = ocr_segments
        engine_instance.last_frame_count = 1
        result = CliRunner().invoke(
            app,
            [
                "transcribe",
                "fake.mp4",
                "--output",
                str(output),
                "--platform",
                "tiktok",
                "--ocr",
            ],
        )

    assert result.exit_code == 0, result.output
    restored = Transcript.model_validate_json(output.read_text(encoding="utf-8"))
    texts = [s.text for s in restored.segments]
    assert "hello world" in texts
    assert "@creator" in texts

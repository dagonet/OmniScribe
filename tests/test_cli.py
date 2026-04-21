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
    assert "--format" in result.output
    assert "--llm-cleanup" in result.output
    assert "--no-llm-cleanup" in result.output
    assert "--asr-cleanup" in result.output
    assert "--no-asr-cleanup" in result.output


def _patched_pipeline(tmp_path: Path):
    """Return patch context managers that mock every external boundary.

    Includes ``RapidOCREngine`` because ``config.ocr_enabled`` defaults to
    ``True`` — any test that does not explicitly disable OCR would otherwise
    hit a real ``RapidOCR(params=...)`` init.

    Sprint 6.1: also patches ``cleanup_ocr_segments`` with a pass-through so
    tests that don't exercise LLM cleanup never reach the real Ollama client.
    Sprint 6.2: additionally patches ``cleanup_speech_segments``. The 6-tuple
    return is ``(download, extract, whisper, ocr, llm_cleanup, asr_cleanup)``.
    """
    download_patch = patch("omniscribe.cli.download_video", return_value=tmp_path / "video.mp4")
    extract_patch = patch("omniscribe.cli.extract_audio", return_value=tmp_path / "audio.wav")
    whisper_patch = patch("omniscribe.cli.WhisperTranscriber")
    ocr_patch = patch("omniscribe.cli.RapidOCREngine")
    llm_cleanup_patch = patch(
        "omniscribe.cli.cleanup_ocr_segments",
        side_effect=lambda segs, cfg: segs,
    )
    asr_cleanup_patch = patch(
        "omniscribe.cli.cleanup_speech_segments",
        side_effect=lambda segs, cfg: segs,
    )
    return (
        download_patch,
        extract_patch,
        whisper_patch,
        ocr_patch,
        llm_cleanup_patch,
        asr_cleanup_patch,
    )


def test_transcribe_writes_json_with_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    segments = [
        TranscriptSegment(start=0.0, end=1.0, text="hello", language="en"),
    ]

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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
    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_ocr_cls.assert_not_called()


def test_transcribe_cli_ocr_flag_overrides_env_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_OCR_ENABLED", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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


def test_scene_change_flag_merges_into_config_true(tmp_path: Path, monkeypatch) -> None:
    """--scene-change merges scene_change_enabled=True into the OCR engine's config."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_SCENE_CHANGE_ENABLED", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--ocr", "--scene-change"],
        )

    assert result.exit_code == 0, result.output
    (ocr_cfg,), _ = mock_ocr_cls.call_args
    assert ocr_cfg.scene_change_enabled is True


def test_no_scene_change_flag_merges_into_config_false(tmp_path: Path, monkeypatch) -> None:
    """--no-scene-change merges scene_change_enabled=False into the OCR engine's config."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--ocr", "--no-scene-change"],
        )

    assert result.exit_code == 0, result.output
    (ocr_cfg,), _ = mock_ocr_cls.call_args
    assert ocr_cfg.scene_change_enabled is False


def test_scene_change_env_disabled_without_flag(tmp_path: Path, monkeypatch) -> None:
    """OMNI_SCENE_CHANGE_ENABLED=false + no CLI flag → config has False."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_SCENE_CHANGE_ENABLED", "false")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--ocr"],
        )

    assert result.exit_code == 0, result.output
    (ocr_cfg,), _ = mock_ocr_cls.call_args
    assert ocr_cfg.scene_change_enabled is False


# ── Sprint 4.2: --format flag + precedence ────────────────────────────────


def _invoke_with_format(
    tmp_path: Path,
    monkeypatch,
    *,
    output_path: Path,
    extra_args: list[str],
) -> str:
    """Run the CLI with a single speech segment and return the resulting output text.

    Returns the UTF-8 text of ``output_path`` after the run.
    """
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")

    speech = [TranscriptSegment(start=0.0, end=1.0, text="hello", language="en")]
    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_ocr_cls.return_value.extract.return_value = []
        mock_whisper_cls.return_value.transcribe.return_value = (speech, "en")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output_path), *extra_args],
        )
    assert result.exit_code == 0, result.output
    return output_path.read_text(encoding="utf-8")


def test_format_json_writes_json(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out.json"
    text = _invoke_with_format(
        tmp_path, monkeypatch, output_path=out, extra_args=["--format", "json"]
    )
    # JSON round-trip confirms shape.
    restored = Transcript.model_validate_json(text)
    assert len(restored.segments) == 1


def test_format_txt_writes_txt(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out.any"
    text = _invoke_with_format(
        tmp_path, monkeypatch, output_path=out, extra_args=["--format", "txt"]
    )
    # First line is just the segment text — no annotations, no JSON brace.
    assert text.splitlines()[0] == "hello"


def test_format_srt_writes_srt(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out.any"
    text = _invoke_with_format(
        tmp_path, monkeypatch, output_path=out, extra_args=["--format", "srt"]
    )
    # SRT cue index first line.
    assert text.startswith("1\n")
    assert "00:00:00,000 --> 00:00:01,000" in text


def test_format_md_writes_markdown(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out.any"
    text = _invoke_with_format(
        tmp_path, monkeypatch, output_path=out, extra_args=["--format", "md"]
    )
    # Markdown writer emits the [SPEECH] source annotation.
    assert "[SPEECH]" in text


def test_format_flag_beats_extension(tmp_path: Path, monkeypatch) -> None:
    """--format srt with -o out.txt: CLI flag wins over extension."""
    out = tmp_path / "out.txt"
    text = _invoke_with_format(
        tmp_path, monkeypatch, output_path=out, extra_args=["--format", "srt"]
    )
    assert text.startswith("1\n")


def test_env_format_beats_extension(tmp_path: Path, monkeypatch) -> None:
    """OMNI_OUTPUT_FORMAT=srt + -o out.txt (no --format): env wins over extension."""
    monkeypatch.setenv("OMNI_OUTPUT_FORMAT", "srt")
    out = tmp_path / "out.txt"
    text = _invoke_with_format(tmp_path, monkeypatch, output_path=out, extra_args=[])
    assert text.startswith("1\n")


def test_extension_beats_default(tmp_path: Path, monkeypatch) -> None:
    """No --format, no env, -o out.srt: extension inference routes to SRT."""
    out = tmp_path / "out.srt"
    text = _invoke_with_format(tmp_path, monkeypatch, output_path=out, extra_args=[])
    assert text.startswith("1\n")


def test_default_when_extension_unknown(tmp_path: Path, monkeypatch) -> None:
    """No --format, no env, -o out.bin: falls through to default 'json'."""
    out = tmp_path / "out.bin"
    text = _invoke_with_format(tmp_path, monkeypatch, output_path=out, extra_args=[])
    restored = Transcript.model_validate_json(text)
    assert len(restored.segments) == 1


def test_invalid_format_flag_exits_nonzero(tmp_path: Path) -> None:
    """--format bogus should fail via click.Choice with a helpful error."""
    output = tmp_path / "out.any"
    result = CliRunner().invoke(
        app,
        ["transcribe", "fake.mp4", "--output", str(output), "--format", "bogus"],
    )
    assert result.exit_code != 0
    assert "Invalid value for '--format'" in result.output


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

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
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


# --- _resolve_output_format unit tests -------------------------------------


class TestResolveOutputFormat:
    """Unit-level coverage for the precedence resolver.

    CLI smoke tests already exercise the full flag/env/extension matrix
    through the patched pipeline, but those obscure which branch fired.
    These tests pin the precedence contract independently so a refactor
    can't silently break semantics.
    """

    def test_flag_overrides_everything(self) -> None:
        from omniscribe.cli import _resolve_output_format

        assert (
            _resolve_output_format(
                flag="srt",
                env_value="txt",
                output_path=Path("out.md"),
                config_value="txt",
            )
            == "srt"
        )

    def test_env_set_wins_over_extension(self) -> None:
        from omniscribe.cli import _resolve_output_format

        assert (
            _resolve_output_format(
                flag=None,
                env_value="srt",
                output_path=Path("out.md"),
                config_value="srt",
            )
            == "srt"
        )

    def test_env_explicitly_json_still_wins_over_extension(self) -> None:
        """OMNI_OUTPUT_FORMAT=json with out.srt should write JSON (env > ext).

        Regression guard for the "env equals hard default" ambiguity:
        presence is the trigger, not value-differs-from-default.
        """
        from omniscribe.cli import _resolve_output_format

        assert (
            _resolve_output_format(
                flag=None,
                env_value="json",
                output_path=Path("out.srt"),
                config_value="json",
            )
            == "json"
        )

    def test_empty_env_value_ignored(self) -> None:
        from omniscribe.cli import _resolve_output_format

        assert (
            _resolve_output_format(
                flag=None,
                env_value="",
                output_path=Path("out.srt"),
                config_value="json",
            )
            == "srt"
        )

    def test_extension_inference_when_env_unset(self) -> None:
        from omniscribe.cli import _resolve_output_format

        for suffix, expected in (
            (".json", "json"),
            (".txt", "txt"),
            (".srt", "srt"),
            (".md", "md"),
            (".MD", "md"),
        ):
            assert (
                _resolve_output_format(
                    flag=None,
                    env_value=None,
                    output_path=Path(f"out{suffix}"),
                    config_value="json",
                )
                == expected
            ), suffix

    def test_unknown_extension_falls_to_default(self) -> None:
        from omniscribe.cli import _resolve_output_format

        assert (
            _resolve_output_format(
                flag=None,
                env_value=None,
                output_path=Path("out.bin"),
                config_value="json",
            )
            == "json"
        )


# ── Sprint 6.1: --llm-cleanup flag + env + error path ─────────────────────


def test_llm_cleanup_flag_invokes_cleanup(tmp_path: Path, monkeypatch) -> None:
    """--llm-cleanup → cleanup_ocr_segments called once with merged config."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app, ["transcribe", "fake.mp4", "--output", str(output), "--llm-cleanup"]
        )

    assert result.exit_code == 0, result.output
    mock_cleanup.assert_called_once()
    (_segs, cfg), _ = mock_cleanup.call_args
    assert cfg.llm_cleanup_enabled is True


def test_llm_cleanup_default_off(tmp_path: Path, monkeypatch) -> None:
    """No flag, no env → cleanup NOT called (opt-in default)."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.delenv("OMNI_LLM_CLEANUP_ENABLED", raising=False)
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_cleanup.assert_not_called()


def test_llm_cleanup_env_enabled_without_flag(tmp_path: Path, monkeypatch) -> None:
    """OMNI_LLM_CLEANUP_ENABLED=true + no flag → cleanup IS called."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_LLM_CLEANUP_ENABLED", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_cleanup.assert_called_once()


def test_no_llm_cleanup_flag_overrides_env(tmp_path: Path, monkeypatch) -> None:
    """--no-llm-cleanup with OMNI_LLM_CLEANUP_ENABLED=true → cleanup NOT called."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_LLM_CLEANUP_ENABLED", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--no-llm-cleanup", "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    mock_cleanup.assert_not_called()


def test_llm_cleanup_error_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    """OmniScribeError from cleanup → exit 1 + message on stderr."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        mock_cleanup.side_effect = OmniScribeError("Ollama not reachable at http://localhost:11434")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--llm-cleanup"],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    assert "Ollama not reachable" in result.output


def test_llm_cleanup_with_no_ocr_runs_on_speech_only(tmp_path: Path, monkeypatch) -> None:
    """--llm-cleanup --no-ocr → cleanup still invoked; SPEECH-only input is fine."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    speech = [TranscriptSegment(start=0.0, end=1.0, text="hello", language="en")]

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc as mock_cleanup, ac:
        mock_whisper_cls.return_value.transcribe.return_value = (speech, "en")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--llm-cleanup", "--no-ocr"],
        )

    assert result.exit_code == 0, result.output
    # RapidOCREngine must not be constructed; cleanup IS called (with SPEECH-only
    # segments — the no-op short-circuit lives inside cleanup_ocr_segments itself).
    mock_ocr_cls.assert_not_called()
    mock_cleanup.assert_called_once()
    (segs, _cfg), _ = mock_cleanup.call_args
    assert all(s.source == "SPEECH" for s in segs)


# ── Sprint 6.2: --asr-cleanup flag + env + negation + ordering ────────────


def test_asr_cleanup_flag_invokes_cleanup(tmp_path: Path, monkeypatch) -> None:
    """--asr-cleanup → cleanup_speech_segments called once with merged config."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac as mock_asr_cleanup:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app, ["transcribe", "fake.mp4", "--output", str(output), "--asr-cleanup"]
        )

    assert result.exit_code == 0, result.output
    mock_asr_cleanup.assert_called_once()
    (_segs, cfg), _ = mock_asr_cleanup.call_args
    assert cfg.llm_asr_cleanup_enabled is True


def test_asr_cleanup_default_off(tmp_path: Path, monkeypatch) -> None:
    """No flag, no env → ASR cleanup NOT called (opt-in default)."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.delenv("OMNI_LLM_ASR_CLEANUP_ENABLED", raising=False)
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac as mock_asr_cleanup:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_asr_cleanup.assert_not_called()


def test_asr_cleanup_env_enabled_without_flag(tmp_path: Path, monkeypatch) -> None:
    """OMNI_LLM_ASR_CLEANUP_ENABLED=true + no flag → ASR cleanup IS called."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_LLM_ASR_CLEANUP_ENABLED", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac as mock_asr_cleanup:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(app, ["transcribe", "fake.mp4", "--output", str(output)])

    assert result.exit_code == 0, result.output
    mock_asr_cleanup.assert_called_once()


def test_no_asr_cleanup_flag_overrides_env(tmp_path: Path, monkeypatch) -> None:
    """--no-asr-cleanup with OMNI_LLM_ASR_CLEANUP_ENABLED=true → cleanup NOT called.

    Mirrors the 6.1 review-gap fix: negation-overrides-env must be tested for
    each boolean flag with env binding.
    """
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    monkeypatch.setenv("OMNI_LLM_ASR_CLEANUP_ENABLED", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac as mock_asr_cleanup:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--no-asr-cleanup", "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    mock_asr_cleanup.assert_not_called()


def test_both_cleanup_flags_invoke_both_in_order(tmp_path: Path, monkeypatch) -> None:
    """--llm-cleanup --asr-cleanup → OCR cleanup called first, then ASR cleanup."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with (
        dl,
        ex,
        wh as mock_whisper_cls,
        oc as mock_ocr_cls,
        lc as mock_cleanup,
        ac as mock_asr_cleanup,
    ):
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        # Track call ordering via an external list so we can assert OCR first.
        call_order: list[str] = []
        mock_cleanup.side_effect = lambda segs, cfg: call_order.append("ocr") or segs
        mock_asr_cleanup.side_effect = lambda segs, cfg: call_order.append("asr") or segs

        result = CliRunner().invoke(
            app,
            [
                "transcribe",
                "fake.mp4",
                "--output",
                str(output),
                "--llm-cleanup",
                "--asr-cleanup",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_cleanup.assert_called_once()
    mock_asr_cleanup.assert_called_once()
    assert call_order == ["ocr", "asr"]


def test_asr_cleanup_error_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    """OmniScribeError from ASR cleanup → exit 1 + message on stderr."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac as mock_asr_cleanup:
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        mock_ocr_cls.return_value.extract.return_value = []
        mock_asr_cleanup.side_effect = OmniScribeError(
            "Ollama not reachable at http://localhost:11434"
        )
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--asr-cleanup"],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    assert "Ollama not reachable" in result.output

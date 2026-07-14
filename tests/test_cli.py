"""Smoke tests for the OmniScribe CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
import typer
from typer.testing import CliRunner

from omniscribe import __version__
from omniscribe.cli import app
from omniscribe.errors import OmniScribeError
from omniscribe.output import Transcript, TranscriptSegment

# ── Sprint 9.11: shared CLI option parity (issue #52) ────────────────────────
# These nine options are declared by both ``transcribe`` and ``transcribe-many``;
# the parity test below fails if one command diverges from the other.
_COMMON_CLI_OPTIONS = {
    "language",
    "ocr",
    "ocr_language",
    "platform",
    "ui_filter",
    "scene_change",
    "llm_cleanup",
    "asr_cleanup",
    "translate",
}


def test_cli_option_parity_between_transcribe_and_transcribe_many() -> None:
    """Guards issue #52: every common option must be identical on both commands.

    Compares typer's registered click parameters: flag names, secondary opts,
    and help text. A dev adding an option to only one command will trip this.
    """
    click_cmd = typer.main.get_command(app)
    t_cmd = click_cmd.get_command(None, "transcribe")
    tm_cmd = click_cmd.get_command(None, "transcribe-many")
    assert t_cmd is not None
    assert tm_cmd is not None

    t_opts: dict[str, click.Option] = {
        p.name: p
        for p in t_cmd.params
        if isinstance(p, click.Option) and p.name in _COMMON_CLI_OPTIONS  # type: ignore[type-var]
    }
    tm_opts: dict[str, click.Option] = {
        p.name: p
        for p in tm_cmd.params
        if isinstance(p, click.Option) and p.name in _COMMON_CLI_OPTIONS  # type: ignore[type-var]
    }

    for name in sorted(_COMMON_CLI_OPTIONS):
        assert name in t_opts, f"transcribe missing common option {name!r}"
        assert name in tm_opts, f"transcribe-many missing common option {name!r}"
        t, tm = t_opts[name], tm_opts[name]
        assert t.opts == tm.opts, f"{name}: opts differ ({t.opts} vs {tm.opts})"
        assert t.secondary_opts == tm.secondary_opts, (
            f"{name}: secondary_opts differ ({t.secondary_opts} vs {tm.secondary_opts})"
        )
        assert t.help == tm.help, f"{name}: help text differs"


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
    download_patch = patch(
        "omniscribe.pipeline.download_video", return_value=tmp_path / "video.mp4"
    )
    extract_patch = patch("omniscribe.pipeline.extract_audio", return_value=tmp_path / "audio.wav")
    whisper_patch = patch("omniscribe.pipeline.WhisperTranscriber")
    ocr_patch = patch("omniscribe.pipeline.RapidOCREngine")
    llm_cleanup_patch = patch(
        "omniscribe.pipeline.cleanup_ocr_segments",
        side_effect=lambda segs, cfg: segs,
    )
    asr_cleanup_patch = patch(
        "omniscribe.pipeline.cleanup_speech_segments",
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        "omniscribe.pipeline.download_video",
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
        mock_ocr_cls.return_value.last_frame_count = 0
        mock_whisper_cls.return_value.transcribe.return_value = ([], "fr")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--language", "fr"],
        )

    assert result.exit_code == 0, result.output
    (config_arg,), _ = mock_whisper_cls.call_args
    assert config_arg.whisper_language == "fr"


def test_transcribe_passes_detected_language_to_ocr_engine(tmp_path: Path, monkeypatch) -> None:
    """ASR-detected language is forwarded to OCR engine's extract()."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with dl, ex, wh as mock_whisper_cls, oc as mock_ocr_cls, lc, ac:
        mock_ocr_cls.return_value.extract.return_value = []
        mock_ocr_cls.return_value.last_frame_count = 0
        mock_whisper_cls.return_value.transcribe.return_value = ([], "de")
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    mock_ocr_cls.return_value.extract.assert_called_once()
    _, kwargs = mock_ocr_cls.return_value.extract.call_args
    assert kwargs.get("detected_language") == "de"


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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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
        mock_ocr_cls.return_value.last_frame_count = 0
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


# ── Sprint 5.4: transcribe-many (batch) ───────────────────────────────────


def _write_urls(tmp_path: Path, urls: list[str]) -> Path:
    p = tmp_path / "urls.txt"
    p.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    return p


def test_transcribe_many_empty_file(tmp_path: Path, monkeypatch) -> None:
    """Empty URL list → exit 0, no items processed, no state file written."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls_file = _write_urls(tmp_path, [])
    out_dir = tmp_path / "out"

    with patch("omniscribe.cli.process_single_video") as mock_proc:
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
            ],
        )

    assert result.exit_code == 0, result.output
    mock_proc.assert_not_called()
    state_file = out_dir / ".omniscribe-batch-state.json"
    assert not state_file.exists()


def test_transcribe_many_unwritable_output_dir_fails_fast(tmp_path: Path, monkeypatch) -> None:
    """Read-only --output-dir → exit 1, process_single_video never called."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls_file = _write_urls(tmp_path, ["https://a/1"])
    out_dir = tmp_path / "out"

    # Make the probe-write fail (cross-platform: monkeypatch Path.write_bytes
    # on the probe path).
    real_write_bytes = Path.write_bytes

    def _maybe_deny(self: Path, data: bytes) -> int:
        if self.name == ".omniscribe-write-probe":
            raise PermissionError("read-only")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _maybe_deny)

    with patch("omniscribe.cli.process_single_video") as mock_proc:
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    assert "not writable" in result.output
    mock_proc.assert_not_called()


def test_transcribe_many_all_succeed(tmp_path: Path, monkeypatch) -> None:
    """Three URLs, all succeed → state shows three ``done``."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls = ["https://a/1", "https://a/2", "https://a/3"]
    urls_file = _write_urls(tmp_path, urls)
    out_dir = tmp_path / "out"

    # Make compute_output_path deterministic: derive stems from the URL tail.
    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    assert mock_proc.call_count == 3
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    assert [it["status"] for it in state["items"]] == ["done", "done", "done"]
    assert [it["source"] for it in state["items"]] == urls


def test_transcribe_many_mixed_valid_invalid(tmp_path: Path, monkeypatch) -> None:
    """Two succeed, one raises OmniScribeError → exit 0, failed item recorded."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls = ["https://a/1", "https://a/2", "https://a/3"]
    urls_file = _write_urls(tmp_path, urls)
    out_dir = tmp_path / "out"

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    def _fake_proc(source, *_a, **_kw):
        if source == "https://a/2":
            raise OmniScribeError("Video unavailable: this video is private")

    with (
        patch("omniscribe.cli.process_single_video", side_effect=_fake_proc),
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    # At least one item succeeded → exit 0.
    assert result.exit_code == 0, result.output
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    statuses = {it["source"]: it["status"] for it in state["items"]}
    assert statuses == {"https://a/1": "done", "https://a/2": "failed", "https://a/3": "done"}
    failed = next(it for it in state["items"] if it["source"] == "https://a/2")
    assert "Video unavailable" in failed["error"]


def test_transcribe_many_resume_skips_done(tmp_path: Path, monkeypatch) -> None:
    """Pre-populated state with one ``done`` item → that item is skipped."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls = ["https://a/1", "https://a/2"]
    urls_file = _write_urls(tmp_path, urls)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / ".omniscribe-batch-state.json"
    state_path.write_text(
        "{\n"
        '  "version": 1,\n'
        '  "started_at": "2026-04-30T12:00:00+00:00",\n'
        f'  "input_file": "{urls_file.as_posix()}",\n'
        f'  "output_dir": "{out_dir.as_posix()}",\n'
        '  "format": "md",\n'
        '  "items": [\n'
        f'    {{"source": "https://a/1", "status": "done", "output_path": "{(out_dir / "1.md").as_posix()}", "error": null}},\n'
        '    {"source": "https://a/2", "status": "pending", "output_path": null, "error": null}\n'
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    # Only the pending item should have been processed.
    sources_called = [c.args[0] for c in mock_proc.call_args_list]
    assert sources_called == ["https://a/2"]


def test_transcribe_many_resume_retries_failed_and_pending(tmp_path: Path, monkeypatch) -> None:
    """Pre-populated state with one ``failed`` + one ``pending`` → both retried."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls = ["https://a/1", "https://a/2"]
    urls_file = _write_urls(tmp_path, urls)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / ".omniscribe-batch-state.json"
    state_path.write_text(
        "{\n"
        '  "version": 1,\n'
        '  "started_at": "2026-04-30T12:00:00+00:00",\n'
        f'  "input_file": "{urls_file.as_posix()}",\n'
        f'  "output_dir": "{out_dir.as_posix()}",\n'
        '  "format": "md",\n'
        '  "items": [\n'
        '    {"source": "https://a/1", "status": "failed", "output_path": null, "error": "old"},\n'
        '    {"source": "https://a/2", "status": "pending", "output_path": null, "error": null}\n'
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    sources_called = sorted(c.args[0] for c in mock_proc.call_args_list)
    assert sources_called == ["https://a/1", "https://a/2"]


def test_transcribe_many_resume_against_edited_list_drops_orphans_and_appends_new(
    tmp_path: Path, monkeypatch
) -> None:
    """Drop orphan A, skip done B, retry failed C, append new D as pending."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate state with A done, B done, C failed.
    state_path = out_dir / ".omniscribe-batch-state.json"
    state_path.write_text(
        "{\n"
        '  "version": 1,\n'
        '  "started_at": "2026-04-30T12:00:00+00:00",\n'
        f'  "input_file": "{(tmp_path / "old.txt").as_posix()}",\n'
        f'  "output_dir": "{out_dir.as_posix()}",\n'
        '  "format": "md",\n'
        '  "items": [\n'
        f'    {{"source": "A", "status": "done", "output_path": "{(out_dir / "A.md").as_posix()}", "error": null}},\n'
        f'    {{"source": "B", "status": "done", "output_path": "{(out_dir / "B.md").as_posix()}", "error": null}},\n'
        '    {"source": "C", "status": "failed", "output_path": null, "error": "x"}\n'
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    # New URL list: B, C, D (A dropped, D added).
    urls_file = _write_urls(tmp_path, ["B", "C", "D"])

    def _fake_compute(source, output_dir, ext, taken):
        return output_dir / f"{source}{ext}"

    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    sources_called = sorted(c.args[0] for c in mock_proc.call_args_list)
    # B is done → skipped. C (failed) and D (new pending) processed.
    assert sources_called == ["C", "D"]

    state = _json.loads(state_path.read_text(encoding="utf-8"))
    sources = [it["source"] for it in state["items"]]
    assert sources == ["B", "C", "D"]  # A dropped, order matches list.


def test_transcribe_many_ctrl_c_mid_item_keeps_state_valid(tmp_path: Path, monkeypatch) -> None:
    """KeyboardInterrupt mid-batch → state file valid, in-flight item ``pending``."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls = ["https://a/1", "https://a/2", "https://a/3"]
    urls_file = _write_urls(tmp_path, urls)
    out_dir = tmp_path / "out"

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    def _fake_proc(source, *_a, **_kw):
        if source == "https://a/2":
            raise KeyboardInterrupt

    with (
        patch("omniscribe.cli.process_single_video", side_effect=_fake_proc),
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
            catch_exceptions=False,
        )

    # Non-zero exit on Ctrl+C.
    assert result.exit_code != 0
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    statuses = {it["source"]: it["status"] for it in state["items"]}
    # First item completed; second was persisted as pending before the call;
    # third never started.
    assert statuses == {
        "https://a/1": "done",
        "https://a/2": "pending",
        "https://a/3": "pending",
    }


# ── Sprint 8.1: playlist / channel auto-expansion ─────────────────────────


def test_transcribe_many_expands_playlist_url(tmp_path: Path, monkeypatch) -> None:
    """Playlist URL in urls.txt is expanded into per-video items in feed order."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls_file = _write_urls(tmp_path, ["https://www.youtube.com/playlist?list=PLx"])
    out_dir = tmp_path / "out"

    expanded = ["https://example.com/v1", "https://example.com/v2", "https://example.com/v3"]

    def _fake_expand(urls: list[str]) -> list[str]:
        return expanded

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.expand_url_list", side_effect=_fake_expand),
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    sources_called = [c.args[0] for c in mock_proc.call_args_list]
    assert sources_called == expanded
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    assert [it["source"] for it in state["items"]] == expanded
    assert [it["status"] for it in state["items"]] == ["done", "done", "done"]


def test_transcribe_many_mixed_playlist_and_singles(tmp_path: Path, monkeypatch) -> None:
    """Mixed list [single-A, playlist-X, single-B] expands to 4 calls in order."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    inputs = [
        "https://example.com/single-A",
        "https://www.youtube.com/playlist?list=PLx",
        "https://example.com/single-B",
    ]
    urls_file = _write_urls(tmp_path, inputs)
    out_dir = tmp_path / "out"

    final = [
        "https://example.com/single-A",
        "https://example.com/v1",
        "https://example.com/v2",
        "https://example.com/single-B",
    ]

    def _fake_expand(urls: list[str]) -> list[str]:
        return final

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.expand_url_list", side_effect=_fake_expand),
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    sources_called = [c.args[0] for c in mock_proc.call_args_list]
    assert sources_called == final


def test_transcribe_many_playlist_url_not_in_state(tmp_path: Path, monkeypatch) -> None:
    """After expansion, the playlist URL itself never appears in state.items."""
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    playlist_url = "https://www.youtube.com/playlist?list=PLx"
    urls_file = _write_urls(tmp_path, [playlist_url])
    out_dir = tmp_path / "out"

    expanded = ["https://example.com/v1", "https://example.com/v2"]

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.expand_url_list", side_effect=lambda urls: expanded),
        patch("omniscribe.cli.process_single_video"),
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    sources = [it["source"] for it in state["items"]]
    assert playlist_url not in sources
    assert sources == expanded


def test_transcribe_many_playlist_resume_skips_done(tmp_path: Path, monkeypatch) -> None:
    """Pre-existing state with one expanded video done → only the rest are processed."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    playlist_url = "https://www.youtube.com/playlist?list=PLx"
    urls_file = _write_urls(tmp_path, [playlist_url])
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    expanded = [
        "https://example.com/v1",
        "https://example.com/v2",
        "https://example.com/v3",
    ]

    state_path = out_dir / ".omniscribe-batch-state.json"
    state_path.write_text(
        "{\n"
        '  "version": 1,\n'
        '  "started_at": "2026-04-30T12:00:00+00:00",\n'
        f'  "input_file": "{urls_file.as_posix()}",\n'
        f'  "output_dir": "{out_dir.as_posix()}",\n'
        '  "format": "md",\n'
        '  "items": [\n'
        f'    {{"source": "https://example.com/v1", "status": "done", "output_path": "{(out_dir / "v1.md").as_posix()}", "error": null}},\n'
        '    {"source": "https://example.com/v2", "status": "pending", "output_path": null, "error": null},\n'
        '    {"source": "https://example.com/v3", "status": "pending", "output_path": null, "error": null}\n'
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    with (
        patch("omniscribe.cli.expand_url_list", side_effect=lambda urls: expanded),
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    sources_called = [c.args[0] for c in mock_proc.call_args_list]
    assert sources_called == ["https://example.com/v2", "https://example.com/v3"]


def test_transcribe_many_playlist_extraction_failure_keeps_url(tmp_path: Path, monkeypatch) -> None:
    """When expand_url_list returns the URL unchanged (extraction failed),
    the URL is processed verbatim; per-video failure is recorded as ``failed``.
    """
    import json as _json

    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    playlist_url = "https://www.youtube.com/playlist?list=BROKEN"
    urls_file = _write_urls(tmp_path, [playlist_url])
    out_dir = tmp_path / "out"

    def _fake_compute(source, output_dir, ext, taken):
        stem = source.rstrip("/").rsplit("/", 1)[-1]
        return output_dir / f"{stem}{ext}"

    def _fake_proc(source, *_a, **_kw):
        raise OmniScribeError("Unsupported URL")

    with (
        # extraction "failed" → expand_url_list returns the URL unchanged.
        patch("omniscribe.cli.expand_url_list", side_effect=lambda urls: list(urls)),
        patch("omniscribe.cli.process_single_video", side_effect=_fake_proc),
        patch("omniscribe.cli.compute_output_path", side_effect=_fake_compute),
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    # All items failed → exit 1.
    assert result.exit_code == 1
    state = _json.loads((out_dir / ".omniscribe-batch-state.json").read_text(encoding="utf-8"))
    assert [it["source"] for it in state["items"]] == [playlist_url]
    assert state["items"][0]["status"] == "failed"
    assert "Unsupported URL" in state["items"][0]["error"]


def test_transcribe_many_all_playlists_empty(tmp_path: Path, monkeypatch) -> None:
    """When every playlist URL expands to nothing, exit 0 without processing."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    urls_file = _write_urls(tmp_path, ["https://www.youtube.com/playlist?list=PL-empty"])
    out_dir = tmp_path / "out"

    with (
        patch("omniscribe.cli.expand_url_list", return_value=[]),
        patch("omniscribe.cli.process_single_video") as mock_proc,
    ):
        result = CliRunner().invoke(
            app,
            ["transcribe-many", str(urls_file), "--output-dir", str(out_dir), "--format", "md"],
        )

    assert result.exit_code == 0, result.output
    mock_proc.assert_not_called()
    assert not (out_dir / ".omniscribe-batch-state.json").exists()


# -- Sprint 9.6: photo-mode routing ------------------------------------------


def test_transcribe_photo_url_routes_to_photo_path(tmp_path: Path, monkeypatch) -> None:
    """Photo URL is routed to download_photo_post + extract_images."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    photo_post = MagicMock()
    photo_post.image_paths = []
    photo_post.audio_path = None

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with (
        dl,
        ex,
        wh as mock_whisper_cls,
        oc as mock_ocr_cls,
        lc,
        ac,
        patch("omniscribe.pipeline.is_photo_post", return_value=True),
        patch("omniscribe.pipeline.download_photo_post", return_value=photo_post),
        patch("omniscribe.pipeline.get_duration", return_value=None),
    ):
        mock_ocr_cls.return_value.extract_images.return_value = []
        mock_ocr_cls.return_value.last_frame_count = 0
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(
            app,
            ["transcribe", "https://www.tiktok.com/@u/photo/123", "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    # extract_images must be called instead of extract (photo route).
    mock_ocr_cls.return_value.extract_images.assert_called_once()
    mock_ocr_cls.return_value.extract.assert_not_called()


def test_transcribe_local_dir_routes_to_photo_path(tmp_path: Path, monkeypatch) -> None:
    """Local directory with images triggers scan_photo_dir -> extract_images."""
    monkeypatch.setenv("OMNI_TEMP_DIR", str(tmp_path / "omni"))
    monkeypatch.setenv("OMNI_KEEP_TEMP_FILES", "true")
    output = tmp_path / "out.json"

    # Create a directory with images.
    photo_dir = tmp_path / "slides"
    photo_dir.mkdir()
    (photo_dir / "img1.jpg").write_bytes(b"fake")
    (photo_dir / "img2.jpg").write_bytes(b"fake")

    dl, ex, wh, oc, lc, ac = _patched_pipeline(tmp_path)
    with (
        dl,
        ex,
        wh as mock_whisper_cls,
        oc as mock_ocr_cls,
        lc,
        ac,
        patch("omniscribe.pipeline.get_duration", return_value=None),
    ):
        mock_ocr_cls.return_value.extract_images.return_value = []
        mock_ocr_cls.return_value.last_frame_count = 0
        mock_whisper_cls.return_value.transcribe.return_value = ([], "en")
        result = CliRunner().invoke(
            app,
            ["transcribe", str(photo_dir), "--output", str(output)],
        )

    assert result.exit_code == 0, result.output
    mock_ocr_cls.return_value.extract_images.assert_called_once()
    mock_ocr_cls.return_value.extract.assert_not_called()


# ── Sprint 9.9: whisper translate flag ─────────────────────────────────────


def test_translate_flag_sets_whisper_task(tmp_path: Path) -> None:
    """``--translate`` on ``transcribe`` sets whisper_task='translate' on config."""
    output = tmp_path / "out.json"
    with patch("omniscribe.cli.process_single_video") as mock_proc:
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--translate"],
        )
    assert result.exit_code == 0, result.output
    config = mock_proc.call_args[0][1]
    assert config.whisper_task == "translate"


def test_translate_no_translate_resets_to_transcribe(tmp_path: Path) -> None:
    """``--no-translate`` on ``transcribe`` sets whisper_task='transcribe'."""
    output = tmp_path / "out.json"
    with patch("omniscribe.cli.process_single_video") as mock_proc:
        result = CliRunner().invoke(
            app,
            ["transcribe", "fake.mp4", "--output", str(output), "--no-translate"],
        )
    assert result.exit_code == 0, result.output
    config = mock_proc.call_args[0][1]
    assert config.whisper_task == "transcribe"


def test_transcribe_many_translate_flag_sets_whisper_task(tmp_path: Path) -> None:
    """``--translate`` on ``transcribe-many`` sets whisper_task='translate' on each item."""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/1\nhttps://example.com/2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch(
            "omniscribe.cli.compute_output_path",
            side_effect=lambda s, d, e, t: d / f"{s.split('/')[-1]}{e}",
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
                "--translate",
            ],
        )
    assert result.exit_code == 0, result.output
    for call in mock_proc.call_args_list:
        assert call.args[1].whisper_task == "translate"


# ── Sprint 9.10: serve command ─────────────────────────────────────────────


def test_transcribe_many_ocr_language_flag(tmp_path: Path) -> None:
    """``--ocr-language de`` on ``transcribe-many`` sets ocr_language on each item."""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/1\nhttps://example.com/2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch(
            "omniscribe.cli.compute_output_path",
            side_effect=lambda s, d, e, t: d / f"{s.split('/')[-1]}{e}",
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
                "--ocr-language",
                "de",
            ],
        )
    assert result.exit_code == 0, result.output
    for call in mock_proc.call_args_list:
        assert call.args[1].ocr_language == "de"


def test_transcribe_many_ui_filter_flag(tmp_path: Path) -> None:
    """``--no-ui-filter`` on ``transcribe-many`` sets ui_filter_enabled False."""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch(
            "omniscribe.cli.compute_output_path",
            side_effect=lambda s, d, e, t: d / f"{s.split('/')[-1]}{e}",
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
                "--no-ui-filter",
            ],
        )
    assert result.exit_code == 0, result.output
    for call in mock_proc.call_args_list:
        assert call.args[1].ui_filter_enabled is False


def test_transcribe_many_scene_change_flag(tmp_path: Path) -> None:
    """``--no-scene-change`` on ``transcribe-many`` sets scene_change_enabled False."""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://example.com/1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    with (
        patch("omniscribe.cli.process_single_video") as mock_proc,
        patch(
            "omniscribe.cli.compute_output_path",
            side_effect=lambda s, d, e, t: d / f"{s.split('/')[-1]}{e}",
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "transcribe-many",
                str(urls_file),
                "--output-dir",
                str(out_dir),
                "--format",
                "md",
                "--no-scene-change",
            ],
        )
    assert result.exit_code == 0, result.output
    for call in mock_proc.call_args_list:
        assert call.args[1].scene_change_enabled is False


def test_serve_import_error_raises_omniscribe_error(monkeypatch) -> None:
    """Missing [api] extra raises OmniScribeError with a helpful message."""
    import builtins
    import sys

    # Clear module cache so __import__ is called (not cached import).
    for mod in ("uvicorn", "omniscribe.api.server"):
        sys.modules.pop(mod, None)

    orig_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError(f"No module named {name!r}")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)

    result = CliRunner().invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8000"])

    assert result.exception is not None
    assert "requires the [api] extra" in str(result.exception)


def test_serve_calls_uvicorn_run(monkeypatch) -> None:
    """Happy path: serve calls uvicorn.run with the configured host/port."""
    import uvicorn as uvicorn_mod

    captured: list[dict] = []

    def _fake_run(app, **kwargs):
        captured.append({"app": app, **kwargs})

    monkeypatch.setattr(uvicorn_mod, "run", _fake_run)

    result = CliRunner().invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--port", "9000"],
    )

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0]["host"] == "0.0.0.0"
    assert captured[0]["port"] == 9000

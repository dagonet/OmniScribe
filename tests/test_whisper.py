"""Unit tests for omniscribe.asr.whisper — all external boundaries mocked."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from omniscribe.asr.whisper import WhisperTranscriber
from omniscribe.config import OmniScribeConfig


def _make_config() -> OmniScribeConfig:
    return OmniScribeConfig(
        whisper_model="tiny",
        whisper_device="cpu",
        whisper_compute_type="int8",
        whisper_batch_size=4,
        whisper_language=None,
    )


def _fake_segment(
    start: float, end: float, text: str, avg_logprob: float = -0.2
) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=avg_logprob)


def test_constructor_does_not_load_model() -> None:
    with (
        patch("omniscribe.asr.whisper.WhisperModel") as mock_model_cls,
        patch("omniscribe.asr.whisper.BatchedInferencePipeline") as mock_pipe_cls,
    ):
        WhisperTranscriber(_make_config())

        mock_model_cls.assert_not_called()
        mock_pipe_cls.assert_not_called()


def test_transcribe_lazy_loads_and_wraps_model(tmp_path: Path) -> None:
    config = _make_config()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"riff-fake")

    fake_model = MagicMock(name="WhisperModel")
    fake_pipeline = MagicMock(name="BatchedInferencePipeline")
    fake_pipeline.transcribe.return_value = (
        iter([]),
        SimpleNamespace(language="en"),
    )

    with (
        patch("omniscribe.asr.whisper.WhisperModel", return_value=fake_model) as mock_model_cls,
        patch(
            "omniscribe.asr.whisper.BatchedInferencePipeline",
            return_value=fake_pipeline,
        ) as mock_pipe_cls,
    ):
        transcriber = WhisperTranscriber(config)
        segments, language = transcriber.transcribe(audio)

    mock_model_cls.assert_called_once_with(
        model_size_or_path="tiny",
        device="cpu",
        compute_type="int8",
    )
    mock_pipe_cls.assert_called_once_with(fake_model)
    fake_pipeline.transcribe.assert_called_once_with(
        str(audio),
        language=None,
        batch_size=4,
        vad_filter=True,
        word_timestamps=False,
    )
    assert segments == []
    assert language == "en"


def test_transcribe_consumes_generator_into_segments(tmp_path: Path) -> None:
    config = _make_config()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"riff-fake")

    fake_pipeline = MagicMock()
    fake_pipeline.transcribe.return_value = (
        iter(
            [
                _fake_segment(0.0, 1.2, "  hello  ", avg_logprob=-0.1),
                _fake_segment(1.2, 2.5, "world", avg_logprob=-0.3),
            ]
        ),
        SimpleNamespace(language="de"),
    )

    with (
        patch("omniscribe.asr.whisper.WhisperModel"),
        patch(
            "omniscribe.asr.whisper.BatchedInferencePipeline",
            return_value=fake_pipeline,
        ),
    ):
        segments, language = WhisperTranscriber(config).transcribe(audio)

    assert language == "de"
    assert [s.text for s in segments] == ["hello", "world"]
    assert [s.start for s in segments] == [0.0, 1.2]
    assert [s.end for s in segments] == [1.2, 2.5]
    assert segments[0].confidence == -0.1
    assert segments[1].confidence == -0.3
    assert all(s.language == "de" for s in segments)
    assert all(s.source == "SPEECH" for s in segments)


def test_transcribe_passes_explicit_language(tmp_path: Path) -> None:
    config = _make_config().model_copy(update={"whisper_language": "fr"})
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"riff-fake")

    fake_pipeline = MagicMock()
    fake_pipeline.transcribe.return_value = (iter([]), SimpleNamespace(language="fr"))

    with (
        patch("omniscribe.asr.whisper.WhisperModel"),
        patch(
            "omniscribe.asr.whisper.BatchedInferencePipeline",
            return_value=fake_pipeline,
        ),
    ):
        WhisperTranscriber(config).transcribe(audio)

    _, kwargs = fake_pipeline.transcribe.call_args
    assert kwargs["language"] == "fr"


def test_transcribe_logs_info_before_model_init(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"riff-fake")

    fake_pipeline = MagicMock()
    fake_pipeline.transcribe.return_value = (iter([]), SimpleNamespace(language="en"))

    with (
        patch("omniscribe.asr.whisper.WhisperModel") as mock_model_cls,
        patch(
            "omniscribe.asr.whisper.BatchedInferencePipeline",
            return_value=fake_pipeline,
        ),
        caplog.at_level(logging.INFO, logger="omniscribe.asr.whisper"),
    ):
        WhisperTranscriber(_make_config()).transcribe(audio)

    mock_model_cls.assert_called_once()
    info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("Loading Whisper model" in m for m in info_messages)


def test_transcribe_reuses_pipeline_across_calls(tmp_path: Path) -> None:
    config = _make_config()
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"riff-fake")

    fake_pipeline = MagicMock()
    fake_pipeline.transcribe.return_value = (iter([]), SimpleNamespace(language="en"))

    with (
        patch("omniscribe.asr.whisper.WhisperModel") as mock_model_cls,
        patch(
            "omniscribe.asr.whisper.BatchedInferencePipeline",
            return_value=fake_pipeline,
        ) as mock_pipe_cls,
    ):
        transcriber = WhisperTranscriber(config)
        transcriber.transcribe(audio)
        transcriber.transcribe(audio)

    assert mock_model_cls.call_count == 1
    assert mock_pipe_cls.call_count == 1
    assert fake_pipeline.transcribe.call_count == 2

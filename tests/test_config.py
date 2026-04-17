"""Tests for OmniScribeConfig — env loading, defaults, empty-string coercion."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from omniscribe.config import OmniScribeConfig


def _strip_omni_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in os.environ if k.startswith("OMNI_")]:
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env overrides, config uses documented defaults."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.whisper_model == "large-v3"
    assert cfg.whisper_device == "cuda"
    assert cfg.whisper_compute_type == "float16"
    assert cfg.whisper_batch_size == 16
    assert cfg.whisper_language is None
    assert cfg.output_format == "json"
    assert cfg.log_level == "INFO"
    assert cfg.temp_dir == Path(tempfile.gettempdir()) / "omniscribe"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNI_* env vars override defaults."""
    monkeypatch.setenv("OMNI_WHISPER_MODEL", "small")
    monkeypatch.setenv("OMNI_WHISPER_BATCH_SIZE", "4")

    cfg = OmniScribeConfig()

    assert cfg.whisper_model == "small"
    assert cfg.whisper_batch_size == 4


def test_empty_string_coerced_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty-string env values for optional fields become None."""
    monkeypatch.setenv("OMNI_WHISPER_LANGUAGE", "")
    monkeypatch.setenv("OMNI_LLM_API_KEY", "")

    cfg = OmniScribeConfig()

    assert cfg.whisper_language is None
    assert cfg.llm_api_key is None


def test_temp_dir_is_path_under_platform_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default temp_dir is a Path rooted in the platform tempdir."""
    monkeypatch.delenv("OMNI_TEMP_DIR", raising=False)

    cfg = OmniScribeConfig()

    assert isinstance(cfg.temp_dir, Path)
    assert str(cfg.temp_dir).startswith(tempfile.gettempdir())

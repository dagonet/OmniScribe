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

    assert cfg.whisper_model == "large-v3-turbo"
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

    cfg = OmniScribeConfig()

    assert cfg.whisper_language is None


def test_temp_dir_is_path_under_platform_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default temp_dir is a Path rooted in the platform tempdir."""
    monkeypatch.delenv("OMNI_TEMP_DIR", raising=False)

    cfg = OmniScribeConfig()

    assert isinstance(cfg.temp_dir, Path)
    assert str(cfg.temp_dir).startswith(tempfile.gettempdir())


def test_scene_change_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 2.5 — documented defaults for scene-change fields."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.scene_change_enabled is True
    assert cfg.scene_change_threshold == 0.02


@pytest.mark.parametrize("bad", [0.0, 1.5, -0.1])
def test_scene_change_threshold_out_of_range_raises(
    monkeypatch: pytest.MonkeyPatch, bad: float
) -> None:
    """scene_change_threshold must be in (0.0, 1.0]; boundaries and negatives reject."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(scene_change_threshold=bad)


def test_scene_change_threshold_upper_boundary_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upper boundary value 1.0 is accepted (closed interval)."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(scene_change_threshold=1.0)

    assert cfg.scene_change_threshold == 1.0


def test_scene_change_enabled_env_false_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNI_SCENE_CHANGE_ENABLED=false round-trips to scene_change_enabled=False."""
    _strip_omni_env(monkeypatch)
    monkeypatch.setenv("OMNI_SCENE_CHANGE_ENABLED", "false")

    cfg = OmniScribeConfig()

    assert cfg.scene_change_enabled is False


def test_default_dedup_min_duration_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint OCR-Recall — dedup_min_duration default lowered from 0.5 to 0.0.

    With per-frame bbox aggregation in
    :mod:`omniscribe.ocr.bbox_aggregator`, the 0.5s floor is harmful: it
    drops legitimate single-frame captions whose held-overlay version was
    not visible long enough to span two sampled frames. Pinning the new
    default here guards against accidental reversion.
    """
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.dedup_min_duration == 0.0


@pytest.mark.parametrize("bad", [-0.1, -1.0, -30.0])
def test_dedup_min_duration_negative_rejects(monkeypatch: pytest.MonkeyPatch, bad: float) -> None:
    """Sprint OCR-Recall — negative ``dedup_min_duration`` raises ``ValidationError``.

    Without the validator, ``OMNI_DEDUP_MIN_DURATION=-1.0`` would be silently
    accepted and the floor would be effectively disabled (every cluster
    duration ``>= 0`` clears a negative threshold). Failing fast at config
    construction is the right behaviour.
    """
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(dedup_min_duration=bad)


def test_merge_similarity_threshold_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 4.1 — cross-source merge threshold defaults to 0.85."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.merge_similarity_threshold == 0.85


@pytest.mark.parametrize("bad", [-0.1, 1.01, 2.0, -1.0])
def test_merge_similarity_threshold_out_of_range_raises(
    monkeypatch: pytest.MonkeyPatch, bad: float
) -> None:
    """merge_similarity_threshold must be in ``[0.0, 1.0]``; out-of-range rejects."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(merge_similarity_threshold=bad)


@pytest.mark.parametrize("ok", [0.0, 0.5, 1.0])
def test_merge_similarity_threshold_boundaries_accepted(
    monkeypatch: pytest.MonkeyPatch, ok: float
) -> None:
    """Closed-interval boundaries ``0.0`` and ``1.0`` are accepted."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(merge_similarity_threshold=ok)

    assert cfg.merge_similarity_threshold == ok


# ── ocr_language validator ──────────────────────────────────────────


def test_ocr_language_default_is_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ocr_language is 'auto' — resolved at runtime via ASR detections."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.ocr_language == "auto"


def test_ocr_language_accepts_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """ocr_language='auto' is accepted (resolved at runtime via ASR detections)."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(ocr_language="auto")

    assert cfg.ocr_language == "auto"


@pytest.mark.parametrize("lang", ["en", "latin", "ch", "arabic", "eslav", "devanagari"])
def test_ocr_language_accepts_valid_langrec_values(
    monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    """All valid LangRec enum values are accepted."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(ocr_language=lang)

    assert cfg.ocr_language == lang


@pytest.mark.parametrize("iso", ["de", "fr", "ru", "zh", "ja", "ar"])
def test_ocr_language_accepts_mapped_iso_codes(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    """Mapped ISO 639-1 codes are accepted."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(ocr_language=iso)

    assert cfg.ocr_language == iso


@pytest.mark.parametrize("bad", ["xx", "garbage", "zz"])
def test_ocr_language_rejects_unmapped_values(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Unmapped / unknown values are rejected at config construction."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError, match="ocr_language"):
        OmniScribeConfig(ocr_language=bad)


# ── ocr_mask_auto_captions ──────────────────────────────────────────


def test_ocr_mask_auto_captions_default_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default preserves current behavior (auto-caption band masked)."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.ocr_mask_auto_captions is True


def test_ocr_mask_auto_captions_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNI_OCR_MASK_AUTO_CAPTIONS=false disables caption masking."""
    _strip_omni_env(monkeypatch)
    monkeypatch.setenv("OMNI_OCR_MASK_AUTO_CAPTIONS", "false")

    cfg = OmniScribeConfig()

    assert cfg.ocr_mask_auto_captions is False


# ── output_format ──────────────────────────────────────────────────────────


def test_output_format_default_is_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 4.2 — default output_format is 'json'."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.output_format == "json"


@pytest.mark.parametrize("ok", ["json", "txt", "srt", "md"])
def test_output_format_allowed_values(monkeypatch: pytest.MonkeyPatch, ok: str) -> None:
    """All four allowed values are accepted at construction time."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(output_format=ok)  # type: ignore[arg-type]

    assert cfg.output_format == ok


@pytest.mark.parametrize("bad", ["pdf", "vtt", "JSON", ""])
def test_output_format_invalid_rejects(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Unknown output formats raise ValidationError with a helpful message."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(output_format=bad)  # type: ignore[arg-type]


# ── llm_cleanup_* (Sprint 6.1) ─────────────────────────────────────────────


def test_llm_cleanup_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 6.1 documented defaults: disabled, llama3.2:3b, localhost, 30s."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.llm_cleanup_enabled is False
    assert cfg.llm_cleanup_model == "llama3.2:3b"
    assert cfg.llm_cleanup_host == "http://localhost:11434"
    assert cfg.llm_cleanup_timeout_s == 30.0


@pytest.mark.parametrize("bad", [0.0, -0.1, -30.0])
def test_llm_cleanup_timeout_non_positive_rejects(
    monkeypatch: pytest.MonkeyPatch, bad: float
) -> None:
    """llm_cleanup_timeout_s must be strictly positive; zero and negatives reject."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(llm_cleanup_timeout_s=bad)


def test_llm_cleanup_timeout_small_positive_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub-millisecond positive timeout is accepted (lower edge of the validator)."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig(llm_cleanup_timeout_s=0.001)

    assert cfg.llm_cleanup_timeout_s == 0.001


def test_llm_cleanup_enabled_env_true_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNI_LLM_CLEANUP_ENABLED=true round-trips to llm_cleanup_enabled=True."""
    _strip_omni_env(monkeypatch)
    monkeypatch.setenv("OMNI_LLM_CLEANUP_ENABLED", "true")

    cfg = OmniScribeConfig()

    assert cfg.llm_cleanup_enabled is True


# ── llm_asr_cleanup_enabled (Sprint 6.2) ───────────────────────────────────


def test_llm_asr_cleanup_enabled_default_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sprint 6.2 — strict opt-in default: ASR cleanup disabled."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.llm_asr_cleanup_enabled is False


def test_llm_asr_cleanup_enabled_env_true_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """OMNI_LLM_ASR_CLEANUP_ENABLED=true round-trips to True."""
    _strip_omni_env(monkeypatch)
    monkeypatch.setenv("OMNI_LLM_ASR_CLEANUP_ENABLED", "true")

    cfg = OmniScribeConfig()

    assert cfg.llm_asr_cleanup_enabled is True


# ── ocr_frequency_min_frame_count (Sprint 9.2) ─────────────────────────


def test_ocr_frequency_min_frame_count_default_is_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 9.2 — default min_frame_count is 10 (photo-slideshow guard)."""
    _strip_omni_env(monkeypatch)

    cfg = OmniScribeConfig()

    assert cfg.ocr_frequency_min_frame_count == 10


def test_ocr_frequency_min_frame_count_rejects_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative min_frame_count raises ValidationError (pydantic ge=0)."""
    from pydantic import ValidationError

    _strip_omni_env(monkeypatch)

    with pytest.raises(ValidationError):
        OmniScribeConfig(ocr_frequency_min_frame_count=-1)


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1"])
def test_llm_asr_cleanup_enabled_env_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    """Pydantic's bool env parser accepts case variants and ``1`` alike."""
    _strip_omni_env(monkeypatch)
    monkeypatch.setenv("OMNI_LLM_ASR_CLEANUP_ENABLED", truthy)

    cfg = OmniScribeConfig()

    assert cfg.llm_asr_cleanup_enabled is True

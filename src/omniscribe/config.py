"""Runtime configuration loaded from environment / .env file."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from omniscribe.acquire.platform import Platform

# Whitelist of values accepted by the ``platform_profile`` field. Includes the
# ``"unknown"`` enum value because env-var round-trips may surface it from an
# auto-detect fallback; user-facing CLI choices exclude it (see cli.py).
_VALID_PLATFORM_PROFILES: frozenset[str] = frozenset({"auto"} | {p.value for p in Platform})

# Whitelist of values accepted by the ``output_format`` field. Mirrors the
# CLI's ``click.Choice`` set (see cli.py) so env and flag paths stay in sync.
_VALID_OUTPUT_FORMATS: frozenset[str] = frozenset({"json", "txt", "srt", "md"})


class OmniScribeConfig(BaseSettings):
    """OmniScribe runtime configuration.

    All fields map 1:1 to ``OMNI_``-prefixed environment variables. Fields
    with an empty string value are coerced to ``None`` where appropriate
    so that blank entries in ``.env`` do not override sensible defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="OMNI_",
        env_file=".env",
        extra="ignore",
    )

    # ── ASR ──────────────────────────────────────────────
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_batch_size: int = 16
    whisper_language: str | None = None

    # ── OCR ──────────────────────────────────────────────
    ocr_enabled: bool = True
    ocr_language: str = "en"
    ocr_sample_fps: float = 1.0
    ocr_min_confidence: float = 0.6
    ocr_device: str = "cuda"
    scene_change_enabled: bool = True
    scene_change_threshold: float = 0.02

    # ── LLM cleanup ──────────────────────────────────────
    # Opt-in per-segment OCR-artefact cleanup via a local Ollama model.
    # Applies to ON-SCREEN and BOTH segments only; SPEECH is handled by the
    # Sprint 6.2 ASR cleanup pass below. Default disabled — strict opt-in.
    llm_cleanup_enabled: bool = False
    llm_cleanup_model: str = "llama3.2:3b"
    llm_cleanup_host: str = "http://localhost:11434"
    llm_cleanup_timeout_s: float = 30.0
    # Sprint 6.2 — opt-in per-segment punctuation + capitalization cleanup on
    # SPEECH segments. Reuses llm_cleanup_model / _host / _timeout_s; only the
    # enable flag is separate so users can toggle OCR vs ASR independently.
    # The env var keeps the OMNI_LLM_CLEANUP_* namespace for discoverability.
    llm_asr_cleanup_enabled: bool = False

    # ── Platform ─────────────────────────────────────────
    platform_profile: str = "auto"
    ui_filter_enabled: bool = True

    # ── Dedup ────────────────────────────────────────────
    dedup_similarity_threshold: float = 0.85
    dedup_min_duration: float = 0.5

    # ── Merge (cross-source speech↔OCR) ──────────────────
    # Separate from ``dedup_similarity_threshold`` (same-source OCR dedup):
    # cross-source may tolerate a lower bar as tuning data accumulates, and
    # decoupling now is cheaper than renaming later.
    merge_similarity_threshold: float = 0.85

    # ── Output ───────────────────────────────────────────
    # ``Literal`` already rejects invalid env values automatically; the
    # explicit validator below is belt-and-suspenders — it produces a more
    # user-friendly error message listing the allowed values.
    output_format: Literal["json", "txt", "srt", "md"] = "json"

    # ── General ──────────────────────────────────────────
    temp_dir: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / "omniscribe")
    keep_temp_files: bool = False
    log_level: str = "INFO"

    @field_validator("whisper_language", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        """Coerce empty-string env values to ``None`` for optional fields."""
        return None if v == "" else v

    @field_validator("platform_profile", mode="after")
    @classmethod
    def _validate_platform_profile(cls, v: str) -> str:
        """Reject unknown platform profile names early at config construction."""
        if v not in _VALID_PLATFORM_PROFILES:
            allowed = ", ".join(sorted(_VALID_PLATFORM_PROFILES))
            raise ValueError(f"platform_profile must be one of: {allowed}; got {v!r}")
        return v

    @field_validator("scene_change_threshold", mode="after")
    @classmethod
    def _validate_scene_change_threshold(cls, v: float) -> float:
        """Reject thresholds outside (0.0, 1.0].

        ``0.0`` means "every frame passes" (defeats the feature); values above
        ``1.0`` are impossible for a mean-absdiff normalized into ``[0.0, 1.0]``.
        """
        if not (0.0 < v <= 1.0):
            raise ValueError(f"scene_change_threshold must be in (0.0, 1.0]; got {v!r}")
        return v

    @field_validator("llm_cleanup_timeout_s", mode="after")
    @classmethod
    def _validate_llm_cleanup_timeout_s(cls, v: float) -> float:
        """Reject non-positive LLM cleanup timeouts.

        A zero or negative timeout defeats the availability gate — the
        ``ollama`` client treats ``0`` as "no timeout" on some transports and
        negative values raise opaque errors far from the config layer. Keep
        the error message here so misconfiguration fails fast at startup.
        """
        if v <= 0:
            raise ValueError(f"llm_cleanup_timeout_s must be > 0; got {v!r}")
        return v

    @field_validator("output_format", mode="before")
    @classmethod
    def _validate_output_format(cls, v: object) -> object:
        """Reject unknown ``output_format`` values with a friendly message.

        Runs ``mode="before"`` so the pydantic ``Literal`` rejection never
        fires on a string input; users always see the listed allowed values
        rather than the stock literal-union error.
        """
        if isinstance(v, str) and v not in _VALID_OUTPUT_FORMATS:
            allowed = ", ".join(sorted(_VALID_OUTPUT_FORMATS))
            raise ValueError(f"output_format must be one of: {allowed}; got {v!r}")
        return v

    @field_validator("merge_similarity_threshold", mode="after")
    @classmethod
    def _validate_merge_similarity_threshold(cls, v: float) -> float:
        """Reject cross-source merge thresholds outside ``[0.0, 1.0]``.

        The value is a similarity floor compared against
        ``rapidfuzz.fuzz.WRatio`` output scaled by 100 in
        :func:`omniscribe.output.merge_channels`.
        """
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"merge_similarity_threshold must be in [0.0, 1.0]; got {v!r}")
        return v

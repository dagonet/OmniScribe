"""Runtime configuration loaded from environment / .env file."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── Platform ─────────────────────────────────────────
    platform_profile: str = "auto"

    # ── Dedup ────────────────────────────────────────────
    dedup_similarity_threshold: float = 0.85
    dedup_min_duration: float = 0.5

    # ── LLM (optional) ───────────────────────────────────
    llm_enabled: bool = False
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None

    # ── Output ───────────────────────────────────────────
    output_format: str = "json"

    # ── General ──────────────────────────────────────────
    temp_dir: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / "omniscribe")
    keep_temp_files: bool = False
    log_level: str = "INFO"

    @field_validator("whisper_language", "llm_api_key", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        """Coerce empty-string env values to ``None`` for optional fields."""
        return None if v == "" else v

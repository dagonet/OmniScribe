"""Pydantic models for the OCR evaluation harness."""

from __future__ import annotations

from pydantic import BaseModel


class ExpectedText(BaseModel):
    """A single expected text string from the ground truth."""

    text: str
    start: float | None = None
    end: float | None = None
    required: bool = True


class GroundTruth(BaseModel):
    """Ground truth data for one video."""

    language: str
    expected_texts: list[ExpectedText]


class EvalResult(BaseModel):
    """Result of scoring OCR output against ground truth."""

    recall: float
    precision: float
    mean_match_similarity: float | None = None
    per_text_results: list[dict] = []
    funnel: dict | None = None

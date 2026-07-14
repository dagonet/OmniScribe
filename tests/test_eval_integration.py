"""Opt-in eval integration tests — run with ``pytest -m eval``.

Each sample fixture tests the full OCR pipeline against a known-good ground
truth (baseline: recall >= 1.0).  Tests ``pytest.skip`` when the fixture files
or ground truth are absent, so they are safe to invoke on any machine.

Marked ``eval`` — excluded from the default test run.  Run explicitly:
    uv run pytest -m eval -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omniscribe.config import OmniScribeConfig
from omniscribe.eval.models import GroundTruth
from omniscribe.eval.scoring import score_video
from omniscribe.ocr.rapid_ocr import RapidOCREngine
from omniscribe.platforms.registry import resolve_profile

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval"

SAMPLES: list[dict] = [
    {
        "id": 1,
        "type": "photo",
        "slides_dir": _FIXTURES / "slides" / "sample-1",
        "gt_path": _FIXTURES / "gt-sample-1.json",
        "language": "de",
        "baseline_recall": 1.0,
    },
    {
        "id": 2,
        "type": "photo",
        "slides_dir": _FIXTURES / "slides" / "sample-2",
        "gt_path": _FIXTURES / "gt-sample-2.json",
        "language": "de",
        "baseline_recall": 1.0,
    },
    {
        "id": 3,
        "type": "video",
        "video_path": _FIXTURES / "videos" / "sample-3.mp4",
        "gt_path": _FIXTURES / "gt-sample-3.json",
        "language": "de",
        "baseline_recall": 1.0,
    },
]

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})


@pytest.mark.eval
@pytest.mark.parametrize(
    "sample",
    SAMPLES,
    ids=[f"sample-{s['id']}" for s in SAMPLES],
)
def test_eval_sample_baseline(sample: dict) -> None:
    """Full OCR pipeline against the sample fixture scores >= baseline recall."""
    gt_path: Path = sample["gt_path"]
    if not gt_path.exists():
        pytest.skip(f"Ground truth not found: {gt_path}")

    gt = GroundTruth.model_validate_json(gt_path.read_text(encoding="utf-8"))
    config = OmniScribeConfig(ocr_language=sample["language"])
    profile = resolve_profile(config, str(sample.get("video_path") or sample.get("slides_dir")))

    ocr_engine = RapidOCREngine(config, profile=profile)

    if sample["type"] == "photo":
        slides_dir: Path = sample["slides_dir"]
        if not slides_dir.is_dir():
            pytest.skip(f"Slides directory not found: {slides_dir}")
        image_paths = sorted(
            p for p in slides_dir.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
        if not image_paths:
            pytest.skip(f"No slide images in {slides_dir}")
        ocr_segments = ocr_engine.extract_images(image_paths, timestamps=None)
    else:
        video_path: Path = sample["video_path"]
        if not video_path.is_file():
            pytest.skip(f"Video not found: {video_path}")
        ocr_segments = ocr_engine.extract(video_path)

    result = score_video(ocr_segments, gt)
    assert result.recall >= sample["baseline_recall"], (
        f"Sample {sample['id']}: recall {result.recall:.3f} < baseline {sample['baseline_recall']}"
    )
    print(f"Sample {sample['id']}: recall={result.recall:.3f} precision={result.precision:.3f}")

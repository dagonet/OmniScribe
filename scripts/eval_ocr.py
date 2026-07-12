#!/usr/bin/env python3
"""OCR-only evaluation harness for OmniScribe.

Usage
-----
    python scripts/eval_ocr.py VIDEO GROUND_TRUTH [--ocr-language LANG]
        [--funnel] [--output OUTPUT]

Runs the OCR pipeline (frame sampling -> preprocessing -> UI masking ->
RapidOCR -> aggregation -> pattern filter -> frequency filter -> dedup)
against a video file and scores the result against a ground-truth JSON file.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from omniscribe.config import OmniScribeConfig
from omniscribe.eval.funnel import FunnelCounts
from omniscribe.eval.models import GroundTruth
from omniscribe.eval.scoring import score_video
from omniscribe.ocr.deduplicator import dedup_segments
from omniscribe.ocr.rapid_ocr import RapidOCREngine
from omniscribe.ocr.ui_filter import filter_by_frequency, filter_by_patterns
from omniscribe.platforms.registry import resolve_profile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate OCR output against ground truth.",
    )
    parser.add_argument("video", type=str, help="Path to the video file.")
    parser.add_argument(
        "ground_truth",
        type=str,
        help="Path to the ground-truth JSON file.",
    )
    parser.add_argument(
        "--ocr-language",
        default=None,
        help=(
            "RapidOCR LangRec value (e.g. 'en', 'latin'). "
            "Default: from ground-truth JSON 'language' field."
        ),
    )
    parser.add_argument(
        "--funnel",
        action="store_true",
        help="Collect and print funnel diagnostics.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write EvalResult JSON to this path.",
    )
    return parser


def _load_ground_truth(path: str) -> GroundTruth:
    raw = Path(path).read_text(encoding="utf-8")
    return GroundTruth.model_validate_json(raw)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Load ground truth.
    gt = _load_ground_truth(args.ground_truth)
    ocr_language = args.ocr_language or gt.language

    # Build config overridden for evaluation.
    config = OmniScribeConfig()
    config = config.model_copy(update={"ocr_language": ocr_language})

    # Resolve platform profile.
    profile = resolve_profile(config, args.video)

    # OCR pipeline (no ASR, no merge).
    ocr_engine = RapidOCREngine(config, profile=profile)
    funnel = FunnelCounts() if args.funnel else None
    ocr_segments = ocr_engine.extract(Path(args.video), funnel=funnel)

    # UI filters -- same order as cli.py process_single_video.
    if config.ui_filter_enabled and profile is not None:
        ocr_segments = filter_by_patterns(ocr_segments, profile.ui_text_patterns)
        if funnel is not None:
            funnel.post_pattern_filter = len(ocr_segments)

        ocr_segments = filter_by_frequency(
            ocr_segments,
            ocr_engine.last_frame_count,
            profile.frequency_threshold,
        )
        if funnel is not None:
            funnel.post_frequency_filter = len(ocr_segments)

    # Dedup.
    deduped = dedup_segments(
        ocr_segments,
        threshold=config.dedup_similarity_threshold,
        min_duration=config.dedup_min_duration,
        gap_tolerance=2.0 / config.ocr_sample_fps,
    )
    if funnel is not None:
        funnel.post_dedup = len(deduped)

    # After merge-like step: final on-screen + both count.
    on_screen = sum(1 for s in deduped if s.source in ("ON-SCREEN", "BOTH"))
    if funnel is not None:
        funnel.final_on_screen_both = on_screen

    # Score.
    result = score_video(deduped, gt, fuzzy_threshold=config.dedup_similarity_threshold)

    # Attach funnel data if collected.
    if funnel is not None:
        result.funnel = asdict(funnel)

    # Console output.
    funnel_str = ""
    if args.funnel and funnel is not None:
        funnel_str = chr(10) * 2 + funnel.report()
    print(
        result.model_dump_json(indent=2) + funnel_str,
    )

    # File output.
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(chr(10) + "Wrote evaluation result to " + str(out_path), file=sys.stderr)

    # Exit code: non-zero if recall < 1.0 or precision < 1.0.
    if result.recall < 1.0 or result.precision < 1.0:
        sys.exit(1)


if __name__ == "__main__":
    main()

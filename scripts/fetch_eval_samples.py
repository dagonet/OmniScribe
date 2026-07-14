#!/usr/bin/env python3
"""Fetch eval fixture samples from their source URLs (idempotent).

Usage
-----
    python scripts/fetch_eval_samples.py          # fetch all three samples
    python scripts/fetch_eval_samples.py --sample 1   # fetch sample 1 only
    python scripts/fetch_eval_samples.py --sample 3   # fetch sample 3 only

Samples 1 and 2 are TikTok PHOTO posts (image slides + optional audio),
downloaded via gallery-dl (requires the ``[photo]`` extra).
Sample 3 is a TikTok VIDEO, downloaded via yt-dlp.

The script skips any sample whose target files already exist.  Data is placed
at ``tests/fixtures/eval/{slides,samples,gt-*.json}`` — see the README manifest
at ``tests/fixtures/eval/README.md`` for the full layout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "eval"

SAMPLES: list[dict] = [
    {
        "id": 1,
        "url": "https://www.tiktok.com/@roadauravibes/photo/7658949262654410017",
        "type": "photo",
        "target_dir": "slides/sample-1",
        "expected_files": lambda d: list(d.rglob("*")),
    },
    {
        "id": 2,
        "url": "https://www.tiktok.com/@a.blackmirror/photo/7658362360523918625",
        "type": "photo",
        "target_dir": "slides/sample-2",
        "expected_files": lambda d: list(d.rglob("*")),
    },
    {
        "id": 3,
        "url": "https://www.tiktok.com/@antriebscode/video/7651478445557320993",
        "type": "video",
        "target_dir": "videos/sample-3.mp4",
        "expected_files": lambda d: [d] if d.exists() else [],
    },
]


def _already_downloaded(sample: dict) -> bool:
    target = _FIXTURES_DIR / sample["target_dir"]
    return len(sample["expected_files"](target)) > 0


def _download_photo(sample: dict) -> None:
    """Download a PHOTO post via gallery-dl."""
    try:
        from omniscribe.acquire.photo import download_photo_post
    except ImportError:
        print(
            "The '[photo]' extra is required for samples 1 and 2. "
            "Install with: uv sync --extra photo",
            file=sys.stderr,
        )
        sys.exit(1)

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        post = download_photo_post(sample["url"], Path(tmpdir))
        dest = _FIXTURES_DIR / sample["target_dir"]
        dest.mkdir(parents=True, exist_ok=True)
        for p in post.image_paths:
            p.rename(dest / p.name)
        if post.audio_path is not None:
            post.audio_path.rename(dest / post.audio_path.name)
    print(f"Sample {sample['id']}: downloaded {len(post.image_paths)} slides to {dest}")


def _download_video(sample: dict) -> None:
    """Download a VIDEO via yt-dlp."""
    from omniscribe.acquire.downloader import download_video

    dest = _FIXTURES_DIR / sample["target_dir"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    download_video(sample["url"], dest.parent)
    print(f"Sample {sample['id']}: downloaded to {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch eval fixture samples.")
    parser.add_argument(
        "--sample",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="Sample ID to fetch (default: all three).",
    )
    args = parser.parse_args()

    selected = [s for s in SAMPLES if args.sample is None or s["id"] == args.sample]

    for sample in selected:
        if _already_downloaded(sample):
            print(f"Sample {sample['id']}: already present, skipping")
            continue
        if sample["type"] == "photo":
            _download_photo(sample)
        else:
            _download_video(sample)


if __name__ == "__main__":
    main()

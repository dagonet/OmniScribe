"""Pipeline orchestration — extracted from :mod:`omniscribe.cli`.

Extracted to decouple the orchestration logic from the CLI layer.  The API
server now imports from :mod:`omniscribe.pipeline` instead of
:mod:`omniscribe.cli`, breaking the layering violation where the API
depended on the CLI module.

``cli.py`` retains a re-export shim so existing test patches targeting
``omniscribe.cli.*`` names continue to work.
"""

from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from omniscribe.acquire.downloader import download_video
from omniscribe.acquire.photo import download_photo_post, is_photo_post, scan_photo_dir
from omniscribe.asr.whisper import WhisperTranscriber
from omniscribe.audio import extract_audio, get_duration
from omniscribe.eval.funnel import FunnelCounts
from omniscribe.merge.llm_cleanup import cleanup_ocr_segments, cleanup_speech_segments
from omniscribe.ocr.deduplicator import dedup_segments
from omniscribe.ocr.rapid_ocr import RapidOCREngine
from omniscribe.ocr.ui_filter import filter_by_frequency, filter_by_patterns
from omniscribe.output import Transcript, merge_channels, write_transcript
from omniscribe.platforms.registry import resolve_profile

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console

    from omniscribe.config import OmniScribeConfig

logger = logging.getLogger(__name__)

# Maps lowercased output-path suffixes to the format key used by the writers.
_EXTENSION_TO_FORMAT: dict[str, str] = {
    ".json": "json",
    ".txt": "txt",
    ".srt": "srt",
    ".md": "md",
}


def _resolve_output_format(
    flag: str | None,
    *,
    env_value: str | None,
    output_path: Path,
    config_value: str,
) -> str:
    """Resolve the output format per documented precedence.

    Order (highest priority first):

    1. CLI ``--format`` flag.
    2. ``OMNI_OUTPUT_FORMAT`` env var (if ``env_value`` is non-empty). Presence
       is determined by the caller — we pass ``os.environ.get(...)`` which
       yields ``None`` when unset, so this branch is gated on "env var exists"
       and not on "value differs from default". Concretely,
       ``OMNI_OUTPUT_FORMAT=json`` explicitly set still wins over an ``.srt``
       extension. Invalid values have already been rejected by
       :class:`~omniscribe.config.OmniScribeConfig`, so ``config_value`` (the
       validated pydantic-resolved value) is authoritative here.
    3. Output-path suffix (``.json`` / ``.txt`` / ``.srt`` / ``.md``).
    4. Hard default ``"json"``.

    Parameters
    ----------
    flag:
        Value from ``--format`` (``None`` when omitted).
    env_value:
        Raw ``OMNI_OUTPUT_FORMAT`` env-var value; ``None`` when unset. Treat
        presence as the env-branch trigger — a literal ``""`` is ignored.
    output_path:
        The output file the user passed via ``-o``; its suffix drives
        extension inference when neither flag nor env is set.
    config_value:
        The pydantic-resolved ``OmniScribeConfig.output_format`` value. When
        ``env_value`` is set, this reflects the validated env value; when
        unset, this reflects the pydantic field default. Only read when
        ``env_value`` is present.
    """
    if flag is not None:
        return flag
    if env_value is not None and env_value != "":
        # Config validator already rejected invalid values; reflect the
        # validated value (not the raw string) so casing etc. is normalised.
        return config_value
    suffix_format = _EXTENSION_TO_FORMAT.get(output_path.suffix.lower())
    if suffix_format is not None:
        return suffix_format
    return "json"


def process_single_video(
    source: str,
    config: OmniScribeConfig,
    output_path: Path,
    *,
    ocr_active: bool,
    output_format: str,
    console: Console | None = None,
) -> None:
    """Run the full single-video pipeline and write the transcript.

    Behavior is byte-identical to the previous inline path inside
    :func:`transcribe`. Extracted so :func:`transcribe_many` can reuse the
    orchestration loop.

    Imports stay at module scope (do NOT move them in here): existing tests
    patch ``omniscribe.cli.WhisperTranscriber`` etc. and rely on those names
    resolving via this module.
    """
    temp_dir = config.temp_dir
    try:
        # ---- Photo-mode routing (before download_video) ----
        photo_post = None
        if Path(source).is_dir():
            photo_post = scan_photo_dir(Path(source))
        elif is_photo_post(source):
            photo_post = download_photo_post(source, temp_dir)

        if photo_post is not None:
            # Photo branch: audio, ASR, and timestamp computation.
            if photo_post.audio_path:
                audio_path = extract_audio(photo_post.audio_path, temp_dir / "audio.wav")
                speech_segments, detected_language = WhisperTranscriber(config).transcribe(
                    audio_path
                )
            else:
                speech_segments, detected_language = [], "en"

            audio_duration = get_duration(photo_post.audio_path) if photo_post.audio_path else None
            if audio_duration is not None and photo_post.image_paths:
                n = len(photo_post.image_paths)
                photo_timestamps = tuple(
                    (i * audio_duration / n, (i + 1) * audio_duration / n) for i in range(n)
                )
            else:
                photo_timestamps = None

            if ocr_active:
                profile = resolve_profile(config, source)
                ocr_engine = RapidOCREngine(config, profile=profile)
                funnel = FunnelCounts()
                ocr_segments = ocr_engine.extract_images(
                    photo_post.image_paths,
                    detected_language=detected_language,
                    funnel=funnel,
                    timestamps=photo_timestamps,
                )
                logger.info(
                    "OCR: %d segments from %d images",
                    len(ocr_segments),
                    ocr_engine.last_frame_count,
                )
        else:
            # ---- Existing video path (byte-identical) ----
            video_path = download_video(source, temp_dir)
            audio_path = extract_audio(video_path, temp_dir / "audio.wav")
            speech_segments, detected_language = WhisperTranscriber(config).transcribe(audio_path)

            if ocr_active:
                profile = resolve_profile(config, source)
                ocr_engine = RapidOCREngine(config, profile=profile)
                funnel = FunnelCounts()
                ocr_segments = ocr_engine.extract(
                    video_path, detected_language=detected_language, funnel=funnel
                )
                logger.info(
                    "OCR: %d segments from %d frames",
                    len(ocr_segments),
                    ocr_engine.last_frame_count,
                )

        # ---- Shared filter/dedup/merge pipeline (photo + video) ----
        if ocr_active:
            if config.ui_filter_enabled:
                pre_pattern = len(ocr_segments)
                ocr_segments = filter_by_patterns(ocr_segments, profile.ui_text_patterns)
                funnel.post_pattern_filter = len(ocr_segments)
                post_pattern = len(ocr_segments)
                # frame_count is yielded frames (may be scene-change-reduced from nominal fps * duration)
                ocr_segments = filter_by_frequency(
                    ocr_segments,
                    ocr_engine.last_frame_count,
                    profile.frequency_threshold,
                    min_frame_count=config.ocr_frequency_min_frame_count,
                )
                funnel.post_frequency_filter = len(ocr_segments)
                post_freq = len(ocr_segments)
                logger.info(
                    "UI filter: dropped %d pattern-matches, %d frequency-hits",
                    pre_pattern - post_pattern,
                    post_pattern - post_freq,
                )
            deduped_ocr_segments = dedup_segments(
                ocr_segments,
                threshold=config.dedup_similarity_threshold,
                min_duration=config.dedup_min_duration,
                gap_tolerance=2.0 / config.ocr_sample_fps,
            )
            funnel.post_dedup = len(deduped_ocr_segments)
            segments = merge_channels(
                speech_segments,
                deduped_ocr_segments,
                threshold=config.merge_similarity_threshold,
            )
            funnel.final_on_screen_both = sum(
                1 for s in segments if s.source in ("ON-SCREEN", "BOTH")
            )
            logger.debug("OCR pipeline funnel:\n%s", funnel.report())
        else:
            segments = speech_segments

        if config.llm_cleanup_enabled:
            segments = cleanup_ocr_segments(segments, config)
        if config.llm_asr_cleanup_enabled:
            segments = cleanup_speech_segments(segments, config)

        transcript = Transcript(segments=segments, language=detected_language)
        write_transcript(transcript, output_path, output_format)
        if console is not None:
            console.print(f"[green]Wrote {len(segments)} segment(s) to {output_path}[/green]")
    finally:
        if not config.keep_temp_files and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


@contextmanager
def _quiet_pipeline_logging() -> Iterator[None]:
    """Demote per-pipeline-stage INFO logs to keep the progress bar legible.

    The single-video pipeline emits INFO records per stage (download, ASR, OCR,
    merge). Across many items these drown out the progress bar; raise the
    ``omniscribe`` logger level to WARNING for the duration of the batch and
    restore on exit. WARNING / ERROR records still surface.
    """
    pkg_logger = logging.getLogger("omniscribe")
    prior_level = pkg_logger.level
    pkg_logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        pkg_logger.setLevel(prior_level)

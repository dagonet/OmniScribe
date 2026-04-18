"""OmniScribe command-line interface."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import click
import typer
from rich.console import Console
from rich.logging import RichHandler

from omniscribe import __version__
from omniscribe.acquire.downloader import download_video
from omniscribe.acquire.platform import Platform
from omniscribe.asr.whisper import WhisperTranscriber
from omniscribe.audio import extract_audio
from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError
from omniscribe.ocr.deduplicator import dedup_segments
from omniscribe.ocr.rapid_ocr import RapidOCREngine
from omniscribe.ocr.ui_filter import filter_by_frequency, filter_by_patterns
from omniscribe.output import (
    Transcript,
    merge_channels,
    write_json,
    write_markdown,
    write_srt,
    write_txt,
)
from omniscribe.platforms.registry import resolve_profile

# Output-format choices mirror ``config._VALID_OUTPUT_FORMATS`` / the
# ``output.write_*`` writers. Kept as a module-level constant so the flag
# help and resolution logic can't drift.
_OUTPUT_FORMAT_CHOICES: list[str] = ["json", "txt", "srt", "md"]
# Maps lowercased output-path suffixes to the format key used by the writers.
_EXTENSION_TO_FORMAT: dict[str, str] = {
    ".json": "json",
    ".txt": "txt",
    ".srt": "srt",
    ".md": "md",
}

# User-facing ``--platform`` choices: derived from ``Platform`` enum values plus
# ``"auto"``. Excludes ``"unknown"`` — that's an internal auto-detect sentinel,
# not a selectable profile. Config-level validator still accepts it so env-var
# round-trips don't break.
_PLATFORM_CHOICES = sorted(({"auto"} | {p.value for p in Platform}) - {"unknown"})

app = typer.Typer(
    name="omniscribe",
    help="Transcribe videos with speech (ASR) and on-screen text (OCR).",
    no_args_is_help=True,
)

_console = Console()
logger = logging.getLogger(__name__)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"omniscribe {__version__}")
        raise typer.Exit()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_console, rich_tracebacks=False, show_path=False)],
    )


def _resolve_output_format(
    *,
    flag: str | None,
    env_value: str | None,
    output_path: Path,
    config_default: str,
) -> str:
    """Resolve the output format per documented precedence.

    Order (highest priority first):

    1. CLI ``--format`` flag.
    2. ``OMNI_OUTPUT_FORMAT`` env var (if set to any non-empty value). Invalid
       values have already been rejected by
       :class:`~omniscribe.config.OmniScribeConfig`; if the env var survived
       that, it matches ``config_default`` and is therefore authoritative.
    3. Output-path suffix (``.json`` / ``.txt`` / ``.srt`` / ``.md``).
    4. Hard default ``"json"``.

    Parameters
    ----------
    flag:
        Value from ``--format`` (``None`` when omitted).
    env_value:
        Raw ``OMNI_OUTPUT_FORMAT`` env-var value; ``None`` when unset.
    output_path:
        The output file the user passed via ``-o``; its suffix drives
        extension inference when neither flag nor env is set.
    config_default:
        The validated ``OmniScribeConfig.output_format`` value; used as the
        env-carried value (pydantic has already validated it by the time we
        see it here).
    """
    if flag is not None:
        return flag
    if env_value is not None and env_value != "":
        # Config validator already rejected invalid values; reflect the
        # validated value (not the raw string) so casing etc. is normalised.
        return config_default
    suffix_format = _EXTENSION_TO_FORMAT.get(output_path.suffix.lower())
    if suffix_format is not None:
        return suffix_format
    return "json"


@app.callback()
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """OmniScribe — video transcription CLI."""
    config = OmniScribeConfig()
    _setup_logging(config.log_level)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config


@app.command()
def transcribe(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Local video file or http(s) URL."),
    output: Path = typer.Option(
        Path("transcript.json"),
        "--output",
        "-o",
        help="Destination JSON path.",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Force source language (e.g. 'en'); auto-detect when omitted.",
    ),
    ocr: bool | None = typer.Option(
        None,
        "--ocr/--no-ocr",
        help="Enable or disable on-screen-text OCR (overrides OMNI_OCR_ENABLED).",
    ),
    ocr_language: str | None = typer.Option(
        None,
        "--ocr-language",
        help="RapidOCR LangRec value (e.g. 'en', 'ch', 'japan'); overrides OMNI_OCR_LANGUAGE.",
    ),
    platform: str | None = typer.Option(
        None,
        "--platform",
        click_type=click.Choice(_PLATFORM_CHOICES),
        help="Override OMNI_PLATFORM_PROFILE for this run.",
    ),
    ui_filter: bool | None = typer.Option(
        None,
        "--ui-filter/--no-ui-filter",
        help=(
            "Enable or disable UI filtering (zone masking + pattern + frequency); "
            "overrides OMNI_UI_FILTER_ENABLED."
        ),
    ),
    scene_change: bool | None = typer.Option(
        None,
        "--scene-change/--no-scene-change",
        help=(
            "Enable or disable scene-change detection in the OCR frame sampler; "
            "overrides OMNI_SCENE_CHANGE_ENABLED."
        ),
    ),
    output_format: str | None = typer.Option(
        None,
        "--format",
        click_type=click.Choice(_OUTPUT_FORMAT_CHOICES),
        help=(
            "Output format. Precedence: --format > OMNI_OUTPUT_FORMAT > "
            "output-path extension (.json/.txt/.srt/.md) > default 'json'. "
            "Note: since v0.4 the output-path suffix routes the writer — "
            "-o foo.txt without --format now writes TXT (previously JSON)."
        ),
    ),
) -> None:
    """Download (if URL), extract audio, transcribe, and write the transcript.

    Output format is resolved in this order: ``--format`` flag, then
    ``OMNI_OUTPUT_FORMAT`` env var, then the output-path extension
    (``.json`` / ``.txt`` / ``.srt`` / ``.md``), then the default ``"json"``.
    """
    config: OmniScribeConfig = ctx.obj["config"]
    if language is not None:
        config = config.model_copy(update={"whisper_language": language})
    if ocr_language is not None:
        config = config.model_copy(update={"ocr_language": ocr_language})
    if platform is not None:
        config = config.model_copy(update={"platform_profile": platform})
    if ui_filter is not None:
        config = config.model_copy(update={"ui_filter_enabled": ui_filter})
    if scene_change is not None:
        config = config.model_copy(update={"scene_change_enabled": scene_change})

    resolved_format = _resolve_output_format(
        flag=output_format,
        env_value=os.environ.get("OMNI_OUTPUT_FORMAT"),
        output_path=output,
        config_default=config.output_format,
    )

    ocr_active = ocr if ocr is not None else config.ocr_enabled

    temp_dir = config.temp_dir
    try:
        video_path = download_video(source, temp_dir)
        audio_path = extract_audio(video_path, temp_dir / "audio.wav")
        speech_segments, detected_language = WhisperTranscriber(config).transcribe(audio_path)

        if ocr_active:
            profile = resolve_profile(config, source)
            ocr_engine = RapidOCREngine(config, profile=profile)
            ocr_segments = ocr_engine.extract(video_path)
            logger.info(
                "OCR: %d segments from %d frames",
                len(ocr_segments),
                ocr_engine.last_frame_count,
            )
            if config.ui_filter_enabled:
                pre_pattern = len(ocr_segments)
                ocr_segments = filter_by_patterns(ocr_segments, profile.ui_text_patterns)
                post_pattern = len(ocr_segments)
                # frame_count is yielded frames (may be scene-change-reduced from nominal fps * duration)
                ocr_segments = filter_by_frequency(
                    ocr_segments,
                    ocr_engine.last_frame_count,
                    profile.frequency_threshold,
                )
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
            segments = merge_channels(
                speech_segments,
                deduped_ocr_segments,
                threshold=config.merge_similarity_threshold,
            )
        else:
            segments = speech_segments

        transcript = Transcript(segments=segments, language=detected_language)
        match resolved_format:
            case "json":
                write_json(transcript, output)
            case "txt":
                write_txt(transcript, output)
            case "srt":
                write_srt(transcript, output)
            case "md":
                write_markdown(transcript, output)
            case _:  # pragma: no cover — exhaustive by Choice + resolver
                raise OmniScribeError(f"unknown output format: {resolved_format!r}")
        _console.print(f"[green]Wrote {len(segments)} segment(s) to {output}[/green]")
    except OmniScribeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    finally:
        if not config.keep_temp_files and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

"""OmniScribe command-line interface."""

from __future__ import annotations

import logging
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
from omniscribe.output import Transcript, merge_channels, write_json

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
) -> None:
    """Download (if URL), extract audio, transcribe, and write JSON."""
    config: OmniScribeConfig = ctx.obj["config"]
    if language is not None:
        config = config.model_copy(update={"whisper_language": language})
    if ocr_language is not None:
        config = config.model_copy(update={"ocr_language": ocr_language})
    if platform is not None:
        config = config.model_copy(update={"platform_profile": platform})

    ocr_active = ocr if ocr is not None else config.ocr_enabled

    temp_dir = config.temp_dir
    try:
        video_path = download_video(source, temp_dir)
        audio_path = extract_audio(video_path, temp_dir / "audio.wav")
        speech_segments, detected_language = WhisperTranscriber(config).transcribe(audio_path)

        if ocr_active:
            ocr_engine = RapidOCREngine(config)
            ocr_segments = ocr_engine.extract(video_path)
            logger.info(
                "OCR: %d segments from %d frames",
                len(ocr_segments),
                ocr_engine.last_frame_count,
            )
            deduped_ocr_segments = dedup_segments(
                ocr_segments,
                threshold=config.dedup_similarity_threshold,
                min_duration=config.dedup_min_duration,
                gap_tolerance=2.0 / config.ocr_sample_fps,
            )
            segments = merge_channels(speech_segments, deduped_ocr_segments)
        else:
            segments = speech_segments

        transcript = Transcript(segments=segments, language=detected_language)
        write_json(transcript, output)
        _console.print(f"[green]Wrote {len(segments)} segment(s) to {output}[/green]")
    except OmniScribeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    finally:
        if not config.keep_temp_files and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

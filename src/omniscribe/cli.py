"""OmniScribe command-line interface."""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from omniscribe import __version__
from omniscribe.acquire.downloader import download_video
from omniscribe.acquire.platform import Platform
from omniscribe.asr.whisper import WhisperTranscriber
from omniscribe.audio import extract_audio
from omniscribe.batch import (
    BatchState,
    compute_output_path,
    load_state,
    parse_url_list,
    reconcile,
    save_state,
)
from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError
from omniscribe.merge.llm_cleanup import cleanup_ocr_segments, cleanup_speech_segments
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

if TYPE_CHECKING:
    from collections.abc import Iterator

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
        help="Destination path. Extension infers format when --format and OMNI_OUTPUT_FORMAT are both unset.",
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
    llm_cleanup: bool | None = typer.Option(
        None,
        "--llm-cleanup/--no-llm-cleanup",
        help=(
            "Enable Ollama-backed OCR-artefact cleanup on [ON-SCREEN] and [BOTH] "
            "segments; overrides OMNI_LLM_CLEANUP_ENABLED. Requires: uv sync --extra llm."
        ),
    ),
    asr_cleanup: bool | None = typer.Option(
        None,
        "--asr-cleanup/--no-asr-cleanup",
        help=(
            "Enable Ollama-backed punctuation + capitalization cleanup on [SPEECH] "
            "segments; overrides OMNI_LLM_ASR_CLEANUP_ENABLED. Requires: uv sync --extra llm."
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
    if llm_cleanup is not None:
        config = config.model_copy(update={"llm_cleanup_enabled": llm_cleanup})
    if asr_cleanup is not None:
        config = config.model_copy(update={"llm_asr_cleanup_enabled": asr_cleanup})

    resolved_format = _resolve_output_format(
        flag=output_format,
        env_value=os.environ.get("OMNI_OUTPUT_FORMAT"),
        output_path=output,
        config_value=config.output_format,
    )

    ocr_active = ocr if ocr is not None else config.ocr_enabled

    try:
        process_single_video(
            source,
            config,
            output,
            ocr_active=ocr_active,
            output_format=resolved_format,
        )
    except OmniScribeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None


def process_single_video(
    source: str,
    config: OmniScribeConfig,
    output_path: Path,
    *,
    ocr_active: bool,
    output_format: str,
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

        if config.llm_cleanup_enabled:
            segments = cleanup_ocr_segments(segments, config)
        if config.llm_asr_cleanup_enabled:
            segments = cleanup_speech_segments(segments, config)

        transcript = Transcript(segments=segments, language=detected_language)
        match output_format:
            case "json":
                write_json(transcript, output_path)
            case "txt":
                write_txt(transcript, output_path)
            case "srt":
                write_srt(transcript, output_path)
            case "md":
                write_markdown(transcript, output_path)
            case _:  # pragma: no cover — exhaustive by Choice + resolver
                raise OmniScribeError(f"unknown output format: {output_format!r}")
        _console.print(f"[green]Wrote {len(segments)} segment(s) to {output_path}[/green]")
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


_BATCH_STATE_FILENAME = ".omniscribe-batch-state.json"


def _probe_writable(directory: Path) -> None:
    """Confirm ``directory`` accepts file writes; raise OmniScribeError if not.

    Called up-front before any download, so a read-only ``--output-dir`` fails
    fast without spending bandwidth.
    """
    probe = directory / ".omniscribe-write-probe"
    try:
        probe.write_bytes(b"")
    except OSError as e:
        raise OmniScribeError(f"Output directory is not writable: {directory} ({e})") from None
    finally:
        # Probe-cleanup failure isn't fatal — the up-front write succeeded.
        with suppress(OSError):
            probe.unlink(missing_ok=True)


@app.command("transcribe-many")
def transcribe_many(
    ctx: typer.Context,
    urls_file: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a UTF-8 file with one URL or local file path per line.",
    ),
    output_dir: Path = typer.Option(
        ...,
        "--output-dir",
        "-o",
        help="Directory for per-input transcripts and the resume state file.",
    ),
    output_format: str = typer.Option(
        "md",
        "--format",
        click_type=click.Choice(_OUTPUT_FORMAT_CHOICES),
        help="Output format applied to every item in the batch.",
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
    platform: str | None = typer.Option(
        None,
        "--platform",
        click_type=click.Choice(_PLATFORM_CHOICES),
        help="Override OMNI_PLATFORM_PROFILE for this run.",
    ),
    llm_cleanup: bool | None = typer.Option(
        None,
        "--llm-cleanup/--no-llm-cleanup",
        help="Enable Ollama-backed OCR-artefact cleanup; overrides OMNI_LLM_CLEANUP_ENABLED.",
    ),
    asr_cleanup: bool | None = typer.Option(
        None,
        "--asr-cleanup/--no-asr-cleanup",
        help=(
            "Enable Ollama-backed punctuation/capitalization cleanup on speech segments; "
            "overrides OMNI_LLM_ASR_CLEANUP_ENABLED."
        ),
    ),
) -> None:
    """Process a list of URLs (or local files), one per line, with resume-on-failure.

    For each line in ``urls_file``: download (if URL), transcribe, and write
    ``{output_dir}/{stem}.{ext}``. Failures are recorded in
    ``{output_dir}/.omniscribe-batch-state.json``; re-running the same command
    resumes from the state file (already-``done`` items are skipped;
    ``pending`` and ``failed`` items are re-attempted).
    """
    config: OmniScribeConfig = ctx.obj["config"]
    if language is not None:
        config = config.model_copy(update={"whisper_language": language})
    if platform is not None:
        config = config.model_copy(update={"platform_profile": platform})
    if llm_cleanup is not None:
        config = config.model_copy(update={"llm_cleanup_enabled": llm_cleanup})
    if asr_cleanup is not None:
        config = config.model_copy(update={"llm_asr_cleanup_enabled": asr_cleanup})

    ocr_active = ocr if ocr is not None else config.ocr_enabled

    # Up-front guards.
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _probe_writable(output_dir)
    except OmniScribeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    state_path = output_dir / _BATCH_STATE_FILENAME

    try:
        urls = parse_url_list(urls_file)
    except OSError as e:
        typer.secho(f"Failed to read URL list: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    if not urls:
        # Empty list — nothing to do, no state file written.
        return

    prior_state = load_state(state_path)
    if prior_state is not None:
        n_pending = sum(1 for it in prior_state.items if it.status == "pending")
        n_failed = sum(1 for it in prior_state.items if it.status == "failed")
        n_done = sum(1 for it in prior_state.items if it.status == "done")
        logger.info(
            "Resuming batch started %s from %s; %d pending, %d failed, %d done",
            prior_state.started_at.isoformat(),
            prior_state.input_file,
            n_pending,
            n_failed,
            n_done,
        )

    state = reconcile(prior_state, urls)
    # If reconcile produced a fresh state, ensure metadata reflects this run.
    if prior_state is None:
        state = BatchState(
            version=1,
            started_at=datetime.now(UTC),
            input_file=urls_file.resolve(),
            output_dir=output_dir.resolve(),
            format=output_format,
            items=state.items,
        )

    ext = f".{output_format}"
    # Build the set of taken paths from items that already carry output_path.
    taken: set[Path] = {item.output_path for item in state.items if item.output_path is not None}
    # Pre-compute output paths for every pending/failed-without-path item so
    # collision suffixes are stable across the whole run.
    for item in state.items:
        if item.output_path is None:
            item.output_path = compute_output_path(item.source, output_dir, ext, taken)
            taken.add(item.output_path)

    # Persist the reconciled / freshly-computed state before any work starts.
    save_state(state, state_path)

    pending_indices = [
        i for i, item in enumerate(state.items) if item.status in {"pending", "failed"}
    ]
    total = len(state.items)

    any_success = False
    any_attempt = False
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=_console,
        transient=False,
    )
    try:
        with progress, _quiet_pipeline_logging():
            task_id = progress.add_task(f"0/{total} starting", total=len(pending_indices) or None)
            for done_so_far, idx in enumerate(pending_indices, start=1):
                item = state.items[idx]
                truncated = item.source if len(item.source) <= 60 else item.source[:57] + "..."
                progress.update(
                    task_id,
                    description=f"{done_so_far}/{len(pending_indices)} {truncated}",
                )

                # Persist pending status BEFORE the call so a Ctrl+C / crash
                # leaves a recoverable state file.
                item.status = "pending"
                item.error = None
                save_state(state, state_path)

                try:
                    any_attempt = True
                    assert item.output_path is not None  # set above
                    process_single_video(
                        item.source,
                        config,
                        item.output_path,
                        ocr_active=ocr_active,
                        output_format=output_format,
                    )
                except OmniScribeError as e:
                    item.status = "failed"
                    item.error = str(e)
                    save_state(state, state_path)
                except KeyboardInterrupt:
                    # Leave item in `pending` (already persisted); re-raise.
                    raise
                else:
                    item.status = "done"
                    item.error = None
                    any_success = True
                    save_state(state, state_path)
                    progress.advance(task_id)
    except KeyboardInterrupt:
        typer.secho(
            "Interrupted; state file preserved for resume.", fg=typer.colors.YELLOW, err=True
        )
        raise typer.Exit(code=130) from None

    # Exit code: 1 if work was attempted but nothing succeeded; 0 otherwise.
    if any_attempt and not any_success:
        raise typer.Exit(code=1)

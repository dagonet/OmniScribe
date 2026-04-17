"""OmniScribe command-line interface."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.logging import RichHandler

from omniscribe import __version__
from omniscribe.config import OmniScribeConfig

app = typer.Typer(
    name="omniscribe",
    help="Transcribe videos with speech (ASR) and on-screen text (OCR).",
    no_args_is_help=True,
)

_console = Console()


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

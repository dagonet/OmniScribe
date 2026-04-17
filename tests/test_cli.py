"""Smoke tests for the OmniScribe CLI entry point (Sprint 1.1 scope)."""

from __future__ import annotations

from typer.testing import CliRunner

from omniscribe import __version__
from omniscribe.cli import app


def test_version_flag_prints_version_and_exits() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0  # Typer exits with the help banner
    assert "Transcribe videos" in result.output

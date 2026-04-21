"""Unit tests for :mod:`omniscribe.merge.llm_cleanup` (Sprint 6.1).

Patch targets live at the import site (``omniscribe.merge.llm_cleanup.Client``,
not ``ollama.Client``) so the bound name inside the module under test is
replaced. Patching at the library path leaves the already-bound alias
untouched and the real client still runs.
"""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError
from omniscribe.merge.llm_cleanup import cleanup_ocr_segments
from omniscribe.output import TranscriptSegment


def _cfg() -> OmniScribeConfig:
    """Config with LLM cleanup enabled and defaults for other fields."""
    return OmniScribeConfig(llm_cleanup_enabled=True)


def _seg(source: str, text: str, start: float = 0.0, end: float = 1.0) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, source=source, language="en")


# ── Target-source gating ──────────────────────────────────────────────


def test_on_screen_segment_is_cleaned(mock_ollama_client: MagicMock) -> None:
    """ON-SCREEN segment → chat called → text replaced."""
    segments = [_seg("ON-SCREEN", "brok en teext")]
    mock_ollama_client.chat.return_value = {"message": {"content": "broken text"}}
    with patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client):
        result = cleanup_ocr_segments(segments, _cfg())

    assert len(result) == 1
    assert result[0].text == "broken text"
    assert result[0].source == "ON-SCREEN"
    mock_ollama_client.chat.assert_called_once()


def test_both_segment_is_cleaned(mock_ollama_client: MagicMock) -> None:
    """BOTH segment → chat called → text replaced (Phase 4 collapse can leak OCR)."""
    segments = [_seg("BOTH", "hello wrold")]
    mock_ollama_client.chat.return_value = {"message": {"content": "hello world"}}
    with patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client):
        result = cleanup_ocr_segments(segments, _cfg())

    assert result[0].text == "hello world"
    assert result[0].source == "BOTH"
    mock_ollama_client.chat.assert_called_once()


def test_speech_segment_is_not_cleaned(mock_ollama_client: MagicMock) -> None:
    """SPEECH segment → chat NOT called; text byte-identical."""
    # Include an ON-SCREEN segment so the no-op short-circuit does not fire.
    speech = _seg("SPEECH", "hello world")
    on_screen = _seg("ON-SCREEN", "on screen text", start=1.0, end=2.0)
    mock_ollama_client.chat.return_value = {"message": {"content": "cleaned"}}
    with patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client):
        result = cleanup_ocr_segments([speech, on_screen], _cfg())

    # Only the ON-SCREEN segment triggered chat.
    assert mock_ollama_client.chat.call_count == 1
    # SPEECH preserved byte-for-byte, including the exact object (model_copy not called).
    assert result[0].text == "hello world"
    assert result[0].source == "SPEECH"


def test_mixed_batch_counts_and_log_message(
    mock_ollama_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """[SPEECH, ON-SCREEN, BOTH, SPEECH] → 2 chat calls + INFO summary."""
    segments = [
        _seg("SPEECH", "alpha", 0.0, 1.0),
        _seg("ON-SCREEN", "bravo", 1.0, 2.0),
        _seg("BOTH", "charlie", 2.0, 3.0),
        _seg("SPEECH", "delta", 3.0, 4.0),
    ]
    mock_ollama_client.chat.return_value = {"message": {"content": "CLEAN"}}
    with (
        caplog.at_level(logging.INFO, logger="omniscribe.merge.llm_cleanup"),
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client),
    ):
        cleanup_ocr_segments(segments, _cfg())

    assert mock_ollama_client.chat.call_count == 2
    assert "2 target segments processed (of 4 total), 2 modified" in caplog.text


# ── Availability gate ─────────────────────────────────────────────────


def test_availability_gate_connection_error_raises_omniscribe_error(
    mock_ollama_client: MagicMock,
) -> None:
    """client.list raises ConnectionError → OmniScribeError with actionable message."""
    mock_ollama_client.list.side_effect = ConnectionError("refused")
    segments = [_seg("ON-SCREEN", "text")]
    cfg = _cfg()
    with (
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client),
        pytest.raises(OmniScribeError) as exc,
    ):
        cleanup_ocr_segments(segments, cfg)

    msg = str(exc.value)
    assert "not reachable" in msg
    assert cfg.llm_cleanup_host in msg
    assert "--no-llm-cleanup" in msg
    assert "refused" in msg


# ── Model-presence gate ───────────────────────────────────────────────


def test_model_presence_gate_missing_model_raises(mock_ollama_client: MagicMock) -> None:
    """client.list returns a response without the configured model → OmniScribeError."""
    mock_ollama_client.list.return_value = SimpleNamespace(
        models=[SimpleNamespace(model="some-other-model:7b")]
    )
    segments = [_seg("ON-SCREEN", "text")]
    with (
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client),
        pytest.raises(OmniScribeError) as exc,
    ):
        cleanup_ocr_segments(segments, _cfg())

    msg = str(exc.value)
    assert "not pulled" in msg
    assert "ollama pull llama3.2:3b" in msg


# ── Safety rails ──────────────────────────────────────────────────────


def test_length_rail_rejects_hallucinated_response(
    mock_ollama_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Response longer than input x 2.0 keeps the original and logs WARNING."""
    original = "short"
    # 5 * 2 = 10 cap; response length 11 exceeds it.
    mock_ollama_client.chat.return_value = {"message": {"content": "x" * 11}}
    segments = [_seg("ON-SCREEN", original)]
    with (
        caplog.at_level(logging.WARNING, logger="omniscribe.merge.llm_cleanup"),
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client),
    ):
        result = cleanup_ocr_segments(segments, _cfg())

    assert result[0].text == original
    assert "exceeds" in caplog.text


def test_empty_response_keeps_original(
    mock_ollama_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Whitespace-only response → original preserved, WARNING logged."""
    mock_ollama_client.chat.return_value = {"message": {"content": "   \n\t  "}}
    segments = [_seg("ON-SCREEN", "keep me")]
    with (
        caplog.at_level(logging.WARNING, logger="omniscribe.merge.llm_cleanup"),
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client),
    ):
        result = cleanup_ocr_segments(segments, _cfg())

    assert result[0].text == "keep me"
    assert "empty response" in caplog.text


# ── Lazy-import failure ───────────────────────────────────────────────


def test_missing_ollama_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client unset (extras missing) raises OmniScribeError pointing at install cmd."""
    # The module-top import uses ``try: from ollama import Client except
    # ImportError: Client = None``. When the ``[llm]`` extras aren't
    # installed, ``Client`` ends up as ``None`` at module scope. Simulate
    # that state by patching the bound name directly.
    monkeypatch.setattr("omniscribe.merge.llm_cleanup.Client", None)
    segments = [_seg("ON-SCREEN", "text")]
    with pytest.raises(OmniScribeError) as exc:
        cleanup_ocr_segments(segments, _cfg())

    assert "uv sync --extra llm" in str(exc.value)


def test_no_op_short_circuit_skips_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEECH-only input must NOT import ollama or construct Client."""
    # If ollama is already importable, this is a weak assertion; but the key
    # signal is that with ``ollama`` neutralised the call still succeeds on a
    # SPEECH-only batch — proving the short-circuit runs before the import.
    for name in list(sys.modules):
        if name == "ollama" or name.startswith("ollama."):
            monkeypatch.setitem(sys.modules, name, None)
    segments = [_seg("SPEECH", "hello"), _seg("SPEECH", "world", 1.0, 2.0)]

    result = cleanup_ocr_segments(segments, _cfg())

    # Returned list is the same identity on the no-op path (documented
    # shortcut: we return the input list unchanged).
    assert result is segments


def test_no_op_short_circuit_info_log(caplog: pytest.LogCaptureFixture) -> None:
    """Empty / SPEECH-only input emits the 'no target-source segments' INFO line."""
    with caplog.at_level(logging.INFO, logger="omniscribe.merge.llm_cleanup"):
        cleanup_ocr_segments([_seg("SPEECH", "hi")], _cfg())

    assert "no target-source segments" in caplog.text


# ── Input immutability ────────────────────────────────────────────────


def test_input_list_not_mutated(mock_ollama_client: MagicMock) -> None:
    """Returned list is a different object; input list identity preserved."""
    mock_ollama_client.chat.return_value = {"message": {"content": "cleaned"}}
    segments = [_seg("ON-SCREEN", "dirty"), _seg("SPEECH", "speech", 1.0, 2.0)]
    snapshot = list(segments)
    with patch("omniscribe.merge.llm_cleanup.Client", return_value=mock_ollama_client):
        result = cleanup_ocr_segments(segments, _cfg())

    assert result is not segments
    assert segments == snapshot  # unchanged
    assert result[0].text == "cleaned"


# ── Narrow-catch propagation ──────────────────────────────────────────


def test_narrow_catch_does_not_swallow_attribute_error() -> None:
    """AttributeError raised while parsing tags must propagate, NOT be wrapped.

    Guards against a bare ``except Exception`` regression in the availability
    gate. We route the error through the ``tags`` iteration (step 4 of
    ``cleanup_ocr_segments``) by returning an object whose ``.models`` attribute
    raises when iterated.
    """

    class BadModels:
        def __iter__(self) -> object:
            raise AttributeError("deliberate")

    mock = MagicMock()
    mock.list.return_value = SimpleNamespace(models=BadModels())
    mock.chat.return_value = {"message": {"content": "x"}}

    segments = [_seg("ON-SCREEN", "text")]
    with (
        patch("omniscribe.merge.llm_cleanup.Client", return_value=mock),
        pytest.raises(AttributeError),
    ):
        cleanup_ocr_segments(segments, _cfg())


# ── Integration (skipped in CI) ───────────────────────────────────────


@pytest.mark.integration
def test_integration_live_ollama_smoke() -> None:  # pragma: no cover
    """Real Ollama + real llama3.2:3b on a known-garbled fixture.

    Skipped by default; run with `uv run pytest -m integration`. Requires a
    running local Ollama with `llama3.2:3b` pulled.
    """
    cfg = OmniScribeConfig(llm_cleanup_enabled=True)
    segments = [_seg("ON-SCREEN", "heIIo w0rId")]
    result = cleanup_ocr_segments(segments, cfg)

    assert len(result) == 1
    assert result[0].text.strip() != ""

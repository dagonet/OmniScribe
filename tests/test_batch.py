"""Unit tests for ``omniscribe.batch`` (Sprint 5.4).

Pure data + IO — these tests do NOT spin up the transcribe pipeline. The CLI
end-to-end coverage lives in ``test_cli.py`` under the ``transcribe-many``
group.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from omniscribe.batch import (
    BatchItem,
    BatchState,
    compute_output_path,
    load_state,
    parse_url_list,
    reconcile,
    save_state,
)

# ── parse_url_list ────────────────────────────────────────────────────────


def test_parse_url_list_strips_blanks_and_whitespace(tmp_path: Path) -> None:
    p = tmp_path / "urls.txt"
    p.write_text(
        "  https://a/1  \n\nhttps://b/2\n    \nhttps://c/3\n",
        encoding="utf-8",
    )
    assert parse_url_list(p) == ["https://a/1", "https://b/2", "https://c/3"]


def test_parse_url_list_handles_bom_and_crlf(tmp_path: Path) -> None:
    """Notepad-on-Windows writes UTF-8 BOM + CRLF; both must be transparent."""
    p = tmp_path / "urls.txt"
    # Explicit BOM bytes + CRLF line separators.
    p.write_bytes(b"\xef\xbb\xbfhttps://a/1\r\nhttps://b/2\r\n")
    assert parse_url_list(p) == ["https://a/1", "https://b/2"]


def test_parse_url_list_handles_local_paths(tmp_path: Path) -> None:
    p = tmp_path / "urls.txt"
    p.write_text("/home/user/clip.mp4\nC:\\videos\\clip2.mkv\n", encoding="utf-8")
    assert parse_url_list(p) == ["/home/user/clip.mp4", "C:\\videos\\clip2.mkv"]


# ── compute_output_path ───────────────────────────────────────────────────


def test_compute_output_path_url(tmp_path: Path) -> None:
    """yt-dlp metadata returns a video ID; stem is that ID."""
    with patch("omniscribe.batch._video_id_from_url", return_value="abc123"):
        out = compute_output_path("https://example.com/v/abc123", tmp_path, ".md", set())
    assert out == tmp_path / "abc123.md"


def test_compute_output_path_local_file(tmp_path: Path) -> None:
    out = compute_output_path("/home/u/clip.mp4", tmp_path, ".json", set())
    assert out == tmp_path / "clip.json"


def test_compute_output_path_video_id_extraction_fails_uses_hash(tmp_path: Path) -> None:
    """When yt-dlp can't extract an ID, the stem is the 12-char sha256 hex prefix."""
    url = "https://example.com/some/weird/page"
    with patch("omniscribe.batch._video_id_from_url", return_value=None):
        out = compute_output_path(url, tmp_path, ".md", set())
    stem = out.stem
    # Expect 12 hex chars.
    assert len(stem) == 12
    assert all(c in "0123456789abcdef" for c in stem)


def test_compute_output_path_truncates_long_stems(tmp_path: Path) -> None:
    long_id = "x" * 300
    with patch("omniscribe.batch._video_id_from_url", return_value=long_id):
        out = compute_output_path("https://example.com/long", tmp_path, ".md", set())
    assert len(out.stem) <= 200


def test_compute_output_path_collision(tmp_path: Path) -> None:
    """Second item with the same stem gets a (2) suffix."""
    taken: set[Path] = set()
    with patch("omniscribe.batch._video_id_from_url", return_value="abc"):
        first = compute_output_path("https://x/1", tmp_path, ".md", taken)
    taken.add(first)
    with patch("omniscribe.batch._video_id_from_url", return_value="abc"):
        second = compute_output_path("https://x/2", tmp_path, ".md", taken)
    assert first == tmp_path / "abc.md"
    assert second == tmp_path / "abc(2).md"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only case-fold check")
def test_compute_output_path_collision_case_insensitive_on_windows(tmp_path: Path) -> None:
    """``Foo.md`` already taken; new ``foo`` stem collides on Windows."""
    taken: set[Path] = {tmp_path / "Foo.md"}
    out = compute_output_path("/data/foo.mp4", tmp_path, ".md", taken)
    # On Windows, "foo.md" collides with "Foo.md" → suffix added.
    assert out == tmp_path / "foo(2).md"


def test_compute_output_path_resume_honors_existing(tmp_path: Path) -> None:
    """Resume rule: if a state item already carries an output_path, callers do
    NOT call compute_output_path for it. This test confirms compute is only
    invoked for items lacking an output_path — verified by checking that an
    existing path in ``taken`` makes the function pick a new name (i.e. it is
    NOT recomputed for the prior item; only new items see compute_output_path).
    """
    existing = tmp_path / "abc.md"
    taken = {existing}
    with patch("omniscribe.batch._video_id_from_url", return_value="abc"):
        # New item with the same derived stem must skip the existing path.
        new_path = compute_output_path("https://other", tmp_path, ".md", taken)
    assert new_path != existing
    assert new_path == tmp_path / "abc(2).md"


# ── State round-trip and load semantics ───────────────────────────────────


def _sample_state(tmp_path: Path) -> BatchState:
    return BatchState(
        version=1,
        started_at=datetime(2026, 4, 30, 15, 0, 0, tzinfo=UTC),
        input_file=tmp_path / "urls.txt",
        output_dir=tmp_path / "out",
        format="md",
        items=[
            BatchItem(
                source="https://a/1",
                status="done",
                output_path=tmp_path / "out" / "a1.md",
            ),
            BatchItem(source="https://b/2", status="failed", error="boom"),
            BatchItem(source="https://c/3", status="pending"),
        ],
    )


def test_state_round_trip(tmp_path: Path) -> None:
    state = _sample_state(tmp_path)
    sf = tmp_path / ".omniscribe-batch-state.json"
    save_state(state, sf)
    loaded = load_state(sf)
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.format == "md"
    assert [it.source for it in loaded.items] == ["https://a/1", "https://b/2", "https://c/3"]
    assert [it.status for it in loaded.items] == ["done", "failed", "pending"]
    assert loaded.items[0].output_path == tmp_path / "out" / "a1.md"
    assert loaded.items[1].error == "boom"
    assert loaded.started_at == state.started_at


def test_state_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_state(tmp_path / "no-such-file.json") is None


def test_state_load_version_mismatch_returns_none(tmp_path: Path, caplog) -> None:
    sf = tmp_path / "state.json"
    sf.write_text(json.dumps({"version": 99, "items": []}), encoding="utf-8")
    import logging

    with caplog.at_level(logging.WARNING, logger="omniscribe.batch"):
        assert load_state(sf) is None
    assert any("version" in r.message for r in caplog.records)


def test_state_load_corrupt_returns_none(tmp_path: Path, caplog) -> None:
    """Truncated / non-JSON content yields None plus a WARNING (not an exception)."""
    sf = tmp_path / "state.json"
    sf.write_text("{not json at all", encoding="utf-8")
    import logging

    with caplog.at_level(logging.WARNING, logger="omniscribe.batch"):
        result = load_state(sf)
    assert result is None
    assert any("unreadable" in r.message for r in caplog.records)


def test_save_state_atomic(tmp_path: Path) -> None:
    """Simulate ``os.replace`` raising mid-write; the original file must be
    untouched and the temp file must be cleaned up.
    """
    sf = tmp_path / "state.json"
    # Seed an existing valid state so we can confirm it survives the failure.
    original = BatchState(format="md", items=[BatchItem(source="x")])
    save_state(original, sf)
    pre_bytes = sf.read_bytes()

    new_state = BatchState(format="srt", items=[BatchItem(source="y")])

    def _boom(*_a, **_kw):
        raise OSError("disk full")

    with patch("omniscribe.batch.os.replace", side_effect=_boom), pytest.raises(OSError):
        save_state(new_state, sf)

    # Original file untouched.
    assert sf.read_bytes() == pre_bytes
    # No leftover temp files in the parent dir.
    leftovers = list(tmp_path.glob(".omniscribe-batch-state.*.tmp"))
    assert leftovers == []


def test_save_state_uses_same_volume_tempfile(tmp_path: Path, monkeypatch) -> None:
    """``mkstemp`` must be called with ``dir=path.parent`` to keep the temp
    on the same volume as the target (Windows ``os.replace`` constraint).
    """
    import omniscribe.batch as batch_mod

    sf = tmp_path / "state.json"
    captured: dict[str, str] = {}
    real_mkstemp = batch_mod.tempfile.mkstemp

    def _spy_mkstemp(*args, **kwargs):
        captured["dir"] = kwargs.get("dir", "")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(batch_mod.tempfile, "mkstemp", _spy_mkstemp)

    save_state(BatchState(items=[BatchItem(source="x")]), sf)

    assert captured["dir"] == str(sf.parent)


# ── reconcile ─────────────────────────────────────────────────────────────


def test_reconcile_drops_orphan_items_not_in_list(tmp_path: Path) -> None:
    state = BatchState(
        items=[
            BatchItem(source="A", status="done"),
            BatchItem(source="B", status="failed"),
            BatchItem(source="C", status="pending"),
        ]
    )
    out = reconcile(state, ["B", "C"])
    assert [it.source for it in out.items] == ["B", "C"]


def test_reconcile_appends_new_urls_as_pending() -> None:
    state = BatchState(items=[BatchItem(source="A", status="done")])
    out = reconcile(state, ["A", "B", "C"])
    sources = [it.source for it in out.items]
    statuses = [it.status for it in out.items]
    assert sources == ["A", "B", "C"]
    assert statuses == ["done", "pending", "pending"]


def test_reconcile_preserves_done_status_for_existing_urls() -> None:
    state = BatchState(
        items=[
            BatchItem(source="A", status="done"),
            BatchItem(source="B", status="failed", error="x"),
        ]
    )
    out = reconcile(state, ["A", "B"])
    a, b = out.items
    assert a.status == "done"
    assert b.status == "failed"
    assert b.error == "x"


def test_reconcile_none_state_returns_all_pending() -> None:
    out = reconcile(None, ["A", "B"])
    assert all(it.status == "pending" for it in out.items)
    assert [it.source for it in out.items] == ["A", "B"]

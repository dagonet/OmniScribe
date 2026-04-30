"""Batch transcription state + helpers (Sprint 5.4).

Pure data + IO. No transcribe pipeline calls live in this module — that lives
in :mod:`omniscribe.cli`. Keeping the split clean lets ``test_batch.py``
exercise state / parsing / collision logic without spinning up the full
pipeline mocks.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Schema version. Bump if BatchState fields change shape; loaders see older or
# newer values and treat the file as unrecognised → return None → fresh start.
_STATE_VERSION: int = 1

# 200 chars stays well under Windows MAX_PATH (260) once an output_dir prefix
# and 4-char extension are added.
_STEM_MAX_CHARS: int = 200

# Windows-reserved characters in filenames + path separators. Replaced with
# underscore in the local-file stem path. URLs go through yt-dlp / sha256
# fallback so this set is irrelevant for them.
_INVALID_STEM_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


Status = Literal["pending", "done", "failed"]


@dataclass
class BatchItem:
    """One URL/file in the batch with its run status."""

    source: str
    status: Status = "pending"
    output_path: Path | None = None
    error: str | None = None


@dataclass
class BatchState:
    """Persisted state for a single ``transcribe-many`` invocation.

    ``started_at`` and ``input_file`` are logged on resume to help the user
    identify which prior run is being continued; they are NOT used as drift
    detectors or checksums.
    """

    version: int = _STATE_VERSION
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    input_file: Path = field(default_factory=lambda: Path("."))
    output_dir: Path = field(default_factory=lambda: Path("."))
    format: str = "md"
    items: list[BatchItem] = field(default_factory=list)


# ── State file IO ─────────────────────────────────────────────────────────


def _state_to_jsonable(state: BatchState) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": state.version,
        "started_at": state.started_at.isoformat(),
        "input_file": str(state.input_file),
        "output_dir": str(state.output_dir),
        "format": state.format,
        "items": [],
    }
    for item in state.items:
        d = asdict(item)
        if d.get("output_path") is not None:
            d["output_path"] = str(d["output_path"])
        payload["items"].append(d)
    return payload


def _state_from_jsonable(data: dict[str, Any]) -> BatchState:
    items_raw = data.get("items", [])
    items: list[BatchItem] = []
    for entry in items_raw:
        op = entry.get("output_path")
        items.append(
            BatchItem(
                source=entry["source"],
                status=entry.get("status", "pending"),
                output_path=Path(op) if op is not None else None,
                error=entry.get("error"),
            )
        )
    return BatchState(
        version=int(data["version"]),
        started_at=datetime.fromisoformat(data["started_at"]),
        input_file=Path(data["input_file"]),
        output_dir=Path(data["output_dir"]),
        format=data.get("format", "md"),
        items=items,
    )


def load_state(path: Path) -> BatchState | None:
    """Load a previously-persisted batch state.

    Returns ``None`` if the file is missing, has a version mismatch, or fails
    to JSON-decode (corrupt / truncated / hand-edited gone wrong). Corrupt
    files emit a WARNING and the run starts fresh — callers must NOT crash on
    a malformed state file.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Batch state file %s is unreadable (%s); starting fresh.", path, e)
        return None
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        logger.warning("Batch state file %s has non-integer version; starting fresh.", path)
        return None
    if version != _STATE_VERSION:
        logger.warning(
            "Batch state file %s has version %d (expected %d); starting fresh.",
            path,
            version,
            _STATE_VERSION,
        )
        return None
    try:
        return _state_from_jsonable(data)
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Batch state file %s has unexpected shape (%s); starting fresh.", path, e)
        return None


def save_state(state: BatchState, path: Path) -> None:
    """Atomically persist ``state`` to ``path``.

    Windows-safe atomic write: the temp file MUST live on the same volume as
    the target, otherwise ``os.replace`` raises ``WinError 17`` ("The system
    cannot move the file to a different disk drive."). We call
    ``tempfile.mkstemp(dir=path.parent, ...)`` for that guarantee. On any
    write error, the temp is unlinked.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".omniscribe-batch-state.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_state_to_jsonable(state), f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        # Best-effort cleanup; original file remains untouched.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


# ── URL list parsing ──────────────────────────────────────────────────────


def parse_url_list(path: Path) -> list[str]:
    """Read a URL/file-path list, one per line.

    Uses ``encoding='utf-8-sig'`` so a UTF-8 BOM (Notepad-on-Windows default)
    is transparently stripped. Lines are ``.strip()``-ed (handles CRLF and
    trailing whitespace) and empty lines are dropped. No comment-prefix
    support — pre-process with ``grep -v '^#'`` if needed.
    """
    raw = path.read_text(encoding="utf-8-sig")
    return [line.strip() for line in raw.splitlines() if line.strip()]


# ── Output path computation ───────────────────────────────────────────────


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _video_id_from_url(url: str) -> str | None:
    """Best-effort yt-dlp metadata-only ID extraction.

    Returns ``None`` on any failure (network down, generic URL, ambiguous, or
    yt-dlp raising). Callers fall back to the sha256 hash.
    """
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError:  # pragma: no cover — yt-dlp is a hard dep
        return None
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
        if info is None:
            return None
        vid = info.get("id")
        if isinstance(vid, str) and vid:
            return vid
    except DownloadError:
        return None
    except Exception as e:
        logger.debug("yt-dlp video-id extraction failed for %s: %s", url, e)
        return None
    return None


def _sanitize_local_stem(source: str) -> str:
    stem = Path(source).stem
    return _INVALID_STEM_CHARS.sub("_", stem) or "file"


def _hash_stem(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def _truncate(stem: str) -> str:
    if len(stem) <= _STEM_MAX_CHARS:
        return stem
    return stem[:_STEM_MAX_CHARS]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _normalize_for_compare(p: Path) -> str:
    """Case-fold on Windows so ``Foo.md`` and ``foo.md`` are treated equal."""
    s = str(p)
    return s.lower() if _is_windows() else s


def compute_output_path(
    source: str,
    output_dir: Path,
    ext: str,
    taken: set[Path],
) -> Path:
    """Derive a unique output path for ``source`` under ``output_dir``.

    - For URLs, attempt yt-dlp video-ID extraction; fall back to a 12-char
      sha256 prefix on any extraction failure.
    - For local files, use the input's sanitized ``Path(source).stem``.
    - Truncate the stem to 200 chars before the extension/suffix.
    - On collision against ``taken``, append ``(2)``, ``(3)``, … (up to 999).
      Comparison is case-insensitive on Windows.

    ``taken`` MUST already contain output_paths from items the caller has
    already placed in the run, because two pending items can derive identical
    stems before either output file exists on disk.
    """
    if _is_url(source):
        vid = _video_id_from_url(source)
        stem = vid or _hash_stem(source)
        # Even yt-dlp IDs can in theory be unusual; sanitize defensively.
        stem = _INVALID_STEM_CHARS.sub("_", stem)
    else:
        stem = _sanitize_local_stem(source)

    stem = _truncate(stem)

    # Normalise case-fold lookup of taken paths once per call.
    taken_keys = {_normalize_for_compare(p) for p in taken}

    candidate = output_dir / f"{stem}{ext}"
    if _normalize_for_compare(candidate) not in taken_keys:
        return candidate

    for n in range(2, 1000):
        candidate = output_dir / f"{stem}({n}){ext}"
        if _normalize_for_compare(candidate) not in taken_keys:
            return candidate
    raise AssertionError(f"More than 999 collisions for stem {stem!r} in {output_dir}; aborting.")


# ── Reconcile loaded state vs. fresh URL list ─────────────────────────────


def reconcile(state: BatchState | None, urls: list[str]) -> BatchState:
    """Merge a loaded state with the current URL list.

    URL list is the source of truth: items in state but missing from the list
    are dropped silently; items in the list but missing from state are
    appended as new ``pending``. Existing items keep their status (``done``
    items aren't re-run; ``pending`` and ``failed`` items are re-attempted).

    If ``state`` is ``None``, a brand-new state is returned with one pending
    item per URL.
    """
    if state is None:
        items = [BatchItem(source=u, status="pending") for u in urls]
        return BatchState(items=items)

    existing_by_source: dict[str, BatchItem] = {}
    for item in state.items:
        # If the URL list contains duplicates, dedup-by-first-occurrence is
        # implicit because compute_output_path is called per state item later.
        existing_by_source.setdefault(item.source, item)

    new_items: list[BatchItem] = []
    for url in urls:
        if url in existing_by_source:
            new_items.append(existing_by_source[url])
        else:
            new_items.append(BatchItem(source=url, status="pending"))

    return BatchState(
        version=state.version,
        started_at=state.started_at,
        input_file=state.input_file,
        output_dir=state.output_dir,
        format=state.format,
        items=new_items,
    )

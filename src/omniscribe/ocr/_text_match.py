"""Private text-matching primitives shared across OCR pipeline stages.

Both :mod:`omniscribe.ocr.deduplicator` (cross-frame clustering) and
:mod:`omniscribe.ocr.ui_filter` (frequency-filter clustering) need the
same notion of "are these two OCR strings the same overlay?". A single
shared helper keeps the two stages from drifting on whitespace /
case-folding / similarity-threshold semantics — a kind of subtle
inconsistency that bites users when noise gets through.

The module is private (``_``-prefixed) — these helpers are an
implementation detail of the OCR pipeline, not a public API.

Both helpers are pure functions; no I/O, no logging, no global state.
"""

from __future__ import annotations

from rapidfuzz import fuzz


def _canonical_key(text: str) -> str:
    """Bucket key for OCR text grouping: case-folded, edge-stripped.

    Preserves inner whitespace — only leading and trailing whitespace
    (spaces, tabs, newlines) is removed. ``str.casefold`` is preferred
    over ``str.lower`` for full Unicode-aware case folding (matters for
    locales we may yet encounter).
    """
    return text.casefold().strip()


def _fuzzy_match(a: str, b: str, threshold: float) -> bool:
    """Return ``True`` when ``a`` and ``b`` clear a similarity threshold.

    Similarity is ``rapidfuzz.fuzz.ratio(a, b, processor=str.lower) / 100``
    in ``[0.0, 1.0]``. The ``str.lower`` processor keeps case variation
    inside a bucket from depressing the score. ``threshold`` is the
    inclusive lower bound — ``ratio >= threshold`` matches.

    Two empty strings score 100 in rapidfuzz and therefore match at any
    threshold ``<= 1.0``; callers that want to exclude empty strings
    must filter them upstream (the deduplicator does this via
    :func:`_canonical_key` returning ``""``).
    """
    similarity = fuzz.ratio(a, b, processor=str.lower) / 100.0
    return similarity >= threshold

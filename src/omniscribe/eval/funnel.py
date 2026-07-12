"""Funnel diagnostics: per-stage OCR pipeline drop-off counters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FunnelCounts:
    """Counts at each stage of the OCR pipeline.

    Used to diagnose where text detections are being lost: frame-level bbox
    detection -> per-frame aggregation -> per-segment extraction -> pattern
    filter -> frequency filter -> dedup -> final on-screen / both.
    """

    raw_bboxes: int = 0
    post_aggregation: int = 0
    post_extract: int = 0
    post_pattern_filter: int = 0
    post_frequency_filter: int = 0
    post_dedup: int = 0
    final_on_screen_both: int = 0

    def report(self) -> str:
        """Return a formatted table string showing stage -> count -> drop_%."""
        rows = [
            ("Raw bboxes (frame-level)", self.raw_bboxes),
            ("Post aggregation (per-frame)", self.post_aggregation),
            ("Post extract (segments)", self.post_extract),
            ("Post pattern filter", self.post_pattern_filter),
            ("Post frequency filter", self.post_frequency_filter),
            ("Post dedup", self.post_dedup),
            ("Final on-screen / both", self.final_on_screen_both),
        ]
        header = f"{'Stage':<35} {'Count':>6}  {'Drop %':>7}"
        sep = "-" * 50
        out = [header, sep]
        prev = None
        for label, count in rows:
            count_val = count if count is not None else 0
            if prev is not None and prev > 0:
                drop_pct = (1.0 - count_val / prev) * 100.0
                drop_str = f"{drop_pct:6.1f}%"
            else:
                drop_str = "    —"
            out.append(f"{label:<35} {count_val:>6}  {drop_str:>7}")
            prev = count_val
        return chr(10).join(out)

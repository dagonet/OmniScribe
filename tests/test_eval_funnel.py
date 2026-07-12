"""Unit tests for omniscribe.eval.funnel."""

from __future__ import annotations

from omniscribe.eval.funnel import FunnelCounts


class TestFunnelCounts:
    """FunnelCounts dataclass: defaults, report formatting, increment pattern."""

    def test_defaults_all_zero(self) -> None:
        funnel = FunnelCounts()
        assert funnel.raw_bboxes == 0
        assert funnel.post_aggregation == 0
        assert funnel.post_extract == 0
        assert funnel.post_pattern_filter == 0
        assert funnel.post_frequency_filter == 0
        assert funnel.post_dedup == 0
        assert funnel.final_on_screen_both == 0

    def test_report_returns_non_empty_string(self) -> None:
        funnel = FunnelCounts()
        report = funnel.report()
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_contains_stage_names(self) -> None:
        funnel = FunnelCounts()
        report = funnel.report()
        assert "Raw bboxes" in report
        assert "Post aggregation" in report
        assert "Post extract" in report
        assert "Post pattern filter" in report
        assert "Post frequency filter" in report
        assert "Post dedup" in report
        assert "Final on-screen" in report

    def test_report_shows_drop_percentages(self) -> None:
        funnel = FunnelCounts(
            raw_bboxes=1000,
            post_aggregation=500,
            post_extract=200,
            post_pattern_filter=180,
            post_frequency_filter=100,
            post_dedup=80,
            final_on_screen_both=80,
        )
        report = funnel.report()
        assert "50.0%" in report
        assert "60.0%" in report

    def test_increment_pattern_produces_correct_drop_off(self) -> None:
        funnel = FunnelCounts()
        funnel.raw_bboxes = 5000
        funnel.post_aggregation = 800
        funnel.post_extract = 350
        funnel.post_pattern_filter = 300
        funnel.post_frequency_filter = 100
        funnel.post_dedup = 95
        funnel.final_on_screen_both = 90

        assert funnel.raw_bboxes == 5000
        assert funnel.post_aggregation == 800
        assert funnel.post_extract == 350
        assert funnel.post_pattern_filter == 300
        assert funnel.post_frequency_filter == 100
        assert funnel.post_dedup == 95
        assert funnel.final_on_screen_both == 90

    def test_report_with_zero_counts_shows_dash(self) -> None:
        funnel = FunnelCounts()
        report = funnel.report()
        # Unicode — is the em-dash shown when drop cannot be computed
        assert "—" in report

    def test_report_first_row_has_dash_drop(self) -> None:
        funnel = FunnelCounts(raw_bboxes=100)
        report = funnel.report()
        lines = report.split("\n")
        assert len(lines) >= 3
        assert "—" in lines[2]

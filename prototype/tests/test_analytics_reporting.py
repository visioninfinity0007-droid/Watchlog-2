#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

import analytics_reporting as ar  # noqa: E402
import daily_report  # noqa: E402


class AnalyticsReportingTests(unittest.TestCase):
    def test_no_measurements_keeps_base_unchanged(self):
        report = {"analytics": {"measurements": 0}}
        self.assertEqual("base", ar.compose(lambda _r: "base", report))
        self.assertEqual("<html>base</html>",
                         ar.render_html(lambda _r, _u: "<html>base</html>", report, "#"))

    def test_plain_text_business_metrics(self):
        report = {"analytics": {
            "measurements": 20, "visitor_in": 8, "visitor_out": 6,
            "vehicles_in": 3, "vehicles_out": 2, "zone_entries": 1,
            "after_hours": 2, "checkout_peak": 5,
        }}
        out = ar.compose(lambda _r: "WatchLog report", report)
        self.assertIn("Site intelligence", out)
        self.assertIn("Visitor flow: 8 in, 6 out.", out)
        self.assertIn("Vehicle flow: 3 in, 2 out.", out)
        self.assertIn("Checkout activity: peak 5 people", out)
        self.assertNotIn("transactions", out.lower())

    def test_html_inserts_before_cta(self):
        report = {"analytics": {"measurements": 4, "visitor_in": 4}}
        base = '<table><tr><td style="padding:24px;">CTA</td></tr></table></td></tr></table></body></html>'
        out = ar.render_html(lambda _r, _u: base, report, "#")
        self.assertIn("Site intelligence", out)
        self.assertLess(out.index("Site intelligence"), out.index("CTA"))

    def test_canonical_daily_report_includes_intelligence(self):
        report = {
            "site": "Korangi Warehouse",
            "date": "2026-09-01",
            "timezone": "Asia/Karachi",
            "total_events": 0,
            "analytics": {
                "measurements": 12,
                "visitor_in": 5,
                "visitor_out": 4,
                "vehicles_in": 2,
                "vehicles_out": 1,
                "zone_entries": 0,
                "after_hours": 0,
                "checkout_peak": 0,
            },
        }
        out = daily_report.compose(report)
        self.assertIn("Nothing to report. No events.", out)
        self.assertIn("Site intelligence", out)
        self.assertIn("Visitor flow: 5 in, 4 out.", out)
        self.assertIn("Vehicle flow: 2 in, 1 out.", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

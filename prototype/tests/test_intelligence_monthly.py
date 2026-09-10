#!/usr/bin/env python3
"""Monthly reporting render + org aggregation (item 13) — from the canonical rollup model."""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

import intelligence_monthly as im  # noqa: E402


def rollup(site="HQ", month="2026-06", incidents=5, crit=1, det=1200, busiest=("2026-06-14", 90)):
    return {
        "schema": "monthly_rollup.v1",
        "meta": {"site": site, "site_id": f"id-{site}", "month": month, "timezone": "Asia/Karachi",
                 "days_with_activity": 22},
        "incidents": {"total": incidents, "critical": crit, "warning": incidents - crit, "info": 0,
                      "by_type": {"after_hours_armory": crit, "restricted_area_access": incidents - crit}},
        "activity": {"total_detections": det, "after_hours_detections": 30, "restricted_access_windows": 12,
                     "busiest_day": {"date": busiest[0], "detections": busiest[1]},
                     "daily": [{"date": f"2026-06-{d:02d}", "detections": d * 3, "after_hours": 0} for d in range(1, 23)]},
        "coverage": {"coverage_ratio": 0.96},
        "honesty": ["Counts are camera detections aggregated over the month, not distinct people."],
    }


class MonthlyRender(unittest.TestCase):
    def test_standalone_document(self):
        h = im.render_monthly_html(rollup())
        self.assertTrue(h.strip().lower().startswith("<!doctype html>"))
        self.assertIn("@page", h)
        self.assertIn("Monthly Site Intelligence", h)

    def test_key_figures_present(self):
        h = im.render_monthly_html(rollup(incidents=5, det=1200))
        self.assertIn("HQ", h)
        self.assertIn("2026-06", h)
        self.assertIn("1200", h)                 # total detections
        self.assertIn("2026-06-14", h)           # busiest day
        self.assertIn("96%", h)                  # coverage
        self.assertIn("after hours armory", h.lower())  # by_type humanised

    def test_trend_bars_rendered(self):
        h = im.render_monthly_html(rollup())
        self.assertIn('class="bars"', h)
        self.assertEqual(h.count('class="bar"'), 22)   # one bar per active day

    def test_honesty_and_pdf_fallback(self):
        r = rollup()
        self.assertIn("not distinct people", im.render_monthly_html(r))
        out = im.render_monthly_pdf(r)
        self.assertTrue(out is None or out[:4] == b"%PDF")


class OrgAggregation(unittest.TestCase):
    def test_org_totals_and_busiest(self):
        org = im.aggregate_org([rollup("HQ", incidents=5, crit=1, det=1200),
                                rollup("Depot", incidents=3, crit=2, det=4000, busiest=("2026-06-02", 200)),
                                rollup("Kiosk", incidents=0, crit=0, det=100)])
        self.assertEqual(org["sites"], 3)
        self.assertEqual(org["totals"]["incidents"], 8)
        self.assertEqual(org["totals"]["critical"], 3)
        self.assertEqual(org["totals"]["detections"], 5300)
        self.assertEqual(org["busiest_site"], "Depot")             # most detections
        self.assertEqual(org["per_site"][0]["site"], "HQ")         # ranked by incidents
        self.assertFalse(org["month_mismatch"])
        self.assertEqual(org["month"], "2026-06")

    def test_month_mismatch_flagged_not_merged(self):
        org = im.aggregate_org([rollup("HQ", month="2026-06"), rollup("Depot", month="2026-07")])
        self.assertTrue(org["month_mismatch"])
        self.assertIsNone(org["month"])

    def test_empty_org(self):
        org = im.aggregate_org([])
        self.assertEqual(org["sites"], 0)
        self.assertIsNone(org["busiest_site"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

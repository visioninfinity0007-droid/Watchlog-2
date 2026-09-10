#!/usr/bin/env python3
"""Contract for the Daily Office Intelligence brief (0060 / office_reporting).

Guards the render layer and, crucially, the honesty wording: detections are
never called a headcount, a partial day is labelled partial, and access
"windows" (not "people") are what a restricted area reports.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

import office_reporting as orp  # noqa: E402
import daily_report  # noqa: E402


def _office(full_day=False, after_hours=0, coverage_ratio=1.0, gaps=None):
    return {
        "coverage": {"first": "14:18", "last": "15:40",
                     "person_events": 231, "full_day": full_day},
        "peak_hour": {"hour": 14, "count": 130},
        "by_area": [
            {"camera": "Director's Office", "events": 75, "episodes": 1,
             "first": "14:18", "last": "15:40"},
            {"camera": "Armory Gate", "events": 31, "episodes": 2,
             "first": "14:19", "last": "15:38"},
        ],
        "restricted": [
            {"camera": "Armory Gate", "episodes": 2,
             "after_hours": after_hours, "last": "15:38"},
        ],
        "after_hours_total": after_hours,
        "agent": {"last_seen": "2026-09-10T10:40:00+00:00", "online": True},
        "monitoring_coverage": {"coverage_ratio": coverage_ratio,
                                "monitored_seconds": 0, "unverified_seconds": 0,
                                "gaps": gaps or []},
    }


class OfficeReportingTests(unittest.TestCase):
    def test_no_office_keeps_base_unchanged(self):
        self.assertEqual("base", orp.compose_append("base", {}))
        self.assertEqual("base", orp.compose_append("base", {"office": {"coverage": {"person_events": 0}}}))
        self.assertEqual("<html>base</html>",
                         orp.insert_html("<html>base</html>", {}))

    def test_plain_text_office_chapter(self):
        out = orp.compose_append("SECURITY", {"office": _office()})
        self.assertIn("*Office activity*", out)
        self.assertIn("Active 14:18–15:40.", out)
        self.assertIn("Busiest around 14:00 (130 detections).", out)
        self.assertIn("Armory Gate 31 (2 windows)", out)
        self.assertIn("Armory Gate: 2 windows of access, last 15:38, no after-hours access.", out)

    def test_honesty_wording(self):
        out = orp.compose_append("SECURITY", {"office": _office(full_day=False)})
        # partial day must be stated, never dressed up
        self.assertIn("Partial day", out)
        # detections are not a headcount, and we never claim named/unique people
        self.assertIn("Figures are activity detections, not a unique headcount.", out)
        self.assertNotIn("headcount of", out.lower())
        self.assertNotIn("employees present", out.lower())
        # a full day drops the partial-day caveat
        full = orp.compose_append("SECURITY", {"office": _office(full_day=True)})
        self.assertNotIn("Partial day", full)

    def test_after_hours_called_out(self):
        out = orp.compose_append("SECURITY", {"office": _office(after_hours=3)})
        self.assertIn("After-hours activity: 3 detections outside 08:00–19:00.", out)
        self.assertIn("3 after-hours", out)  # on the restricted line too

    def test_html_inserts_before_cta(self):
        base = '<table><tr><td style="padding:24px;">CTA</td></tr></table></td></tr></table></body></html>'
        out = orp.insert_html(base, {"office": _office()})
        self.assertIn("Office activity", out)
        self.assertLess(out.index("Office activity"), out.index("CTA"))
        self.assertIn("not a unique headcount", out)

    def test_monitoring_coverage_full_is_stated(self):
        out = orp.compose_append("SECURITY", {"office": _office()})
        self.assertIn("Monitoring coverage: 100% of the reporting period.", out)

    def test_monitoring_coverage_partial_shows_cause_in_local_time(self):
        gaps = [{"start": "2026-09-10T21:21:00+00:00",
                 "end": "2026-09-11T01:28:00+00:00", "cause": "observation_gap"}]
        report = {"office": _office(coverage_ratio=0.83, gaps=gaps),
                  "timezone": "Asia/Karachi"}
        out = orp.compose_append("SECURITY", report)
        self.assertIn("Monitoring coverage: 83% of the reporting period.", out)
        # honest cause: never claims a specific OS-level reason (no "asleep")
        self.assertIn("site not monitored (agent not running)", out)
        self.assertNotIn("asleep", out.lower())
        self.assertIn("02:21-06:28", out)          # 21:21/01:28 UTC -> +05:00 PKT
        self.assertIn("Not monitored:", out)

    def test_canonical_report_orders_office_before_site_intelligence(self):
        report = {
            "site": "Main site", "date": "2026-09-10", "timezone": "Asia/Karachi",
            "total_events": 232, "by_camera": [{"camera": "Reception", "count": 75}],
            "office": _office(),
            "analytics": {"measurements": 12, "visitor_in": 5, "visitor_out": 4},
        }
        out = daily_report.compose(report)
        self.assertIn("*Office activity*", out)
        self.assertIn("*Site intelligence*", out)
        self.assertLess(out.index("*Office activity*"), out.index("*Site intelligence*"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

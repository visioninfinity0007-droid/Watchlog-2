#!/usr/bin/env python3
"""Daily-intelligence WhatsApp renderer (item 18) — third surface of the canonical dataset."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))
sys.path.insert(0, str(ROOT / "tests"))

import intelligence_whatsapp as iw  # noqa: E402
from test_intelligence_pdf import sample  # noqa: E402 — reuse the canonical sample JSON


class RenderWhatsApp(unittest.TestCase):
    def test_header_and_bold(self):
        m = iw.render_whatsapp(sample())
        self.assertIn("*WatchLog — Al-Khalid HQ*", m)
        self.assertIn("2026-06-01", m)

    def test_incidents_listed_severity_first(self):
        m = iw.render_whatsapp(sample())
        self.assertIn("2 incidents need attention", m)
        # critical must appear before warning in the message
        self.assertLess(m.index("CRITICAL"), m.index("WARNING"))
        self.assertIn("after hours armory", m.lower())

    def test_quiet_day(self):
        m = iw.render_whatsapp(sample(incidents=False))
        self.assertIn("*No incidents*", m)
        self.assertNotIn("need attention", m)

    def test_partial_day_note(self):
        self.assertNotIn("Partial day", iw.render_whatsapp(sample(partial=False)))
        self.assertIn("Partial day", iw.render_whatsapp(sample(partial=True)))

    def test_activity_and_afterhours(self):
        m = iw.render_whatsapp(sample())
        self.assertIn("Active 09:12–17:40", m)
        self.assertIn("after-hours", m)

    def test_coverage_honest(self):
        self.assertIn("Monitoring coverage: 100%", iw.render_whatsapp(sample(coverage_ratio=1.0)))
        m = iw.render_whatsapp(sample(coverage_ratio=0.8))
        self.assertIn("80%", m)
        self.assertIn("unobserved", m)

    def test_headcount_caveat(self):
        self.assertIn("not a unique headcount", iw.render_whatsapp(sample()))

    def test_length_bounded(self):
        r = sample()
        r["incidents"] = [{"type": f"t_{n}", "severity": "warning", "camera": "C" * 50,
                           "time": "10:00", "status": "open"} for n in range(400)]
        r["attention"] = {"incidents_total": 400, "critical": 0, "warning": 400, "info": 0}
        m = iw.render_whatsapp(r)
        self.assertLessEqual(len(m), iw.MAX_CHARS)


if __name__ == "__main__":
    unittest.main(verbosity=2)

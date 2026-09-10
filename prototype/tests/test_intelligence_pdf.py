#!/usr/bin/env python3
"""Professional daily-intelligence report renderer (item 17).

Renders the canonical wl_daily_intelligence JSON to a print-ready A4 document. Guards the
things a client-facing report must get right: the attention hero, incident severities, the
honesty caveats, partial-day labelling, the coverage-gap warning, HTML escaping, and the
honest 'no PDF engine -> None, never a fake file' fallback.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

import intelligence_pdf as ip  # noqa: E402


def sample(partial=False, incidents=True, coverage_ratio=1.0):
    return {
        "schema": "daily_intelligence.v1",
        "meta": {"site": "Al-Khalid HQ", "date": "2026-06-01", "timezone": "Asia/Karachi",
                 "generated_at": "2026-06-01T18:00:00+05:00", "partial_day": partial},
        "office": {"coverage": {"first": "09:12", "last": "17:40", "person_events": 210, "full_day": True},
                   "peak_hour": {"hour": 14, "count": 61},
                   "by_area": [{"camera": "Reception", "events": 120}, {"camera": "Armory Gate", "events": 40}],
                   "after_hours_total": 3},
        "coverage": {"coverage_ratio": coverage_ratio,
                     "gaps": ([] if coverage_ratio >= 0.999 else [{"start": "a", "end": "b", "cause": "observation_gap"}])},
        "access_windows": [
            {"camera": "Armory Gate", "purpose": "armory", "type": "presence",
             "start": "22:00", "end": "22:03", "dwell_seconds": 190, "detections": 2}],
        "incidents": ([
            {"type": "after_hours_armory", "severity": "critical", "camera": "Armory Gate",
             "time": "22:00", "status": "open"},
            {"type": "restricted_area_access", "severity": "warning", "camera": "Armory Gate",
             "time": "10:10", "status": "open"}] if incidents else []),
        "attention": ({"incidents_total": 2, "critical": 1, "warning": 1, "info": 0}
                      if incidents else {"incidents_total": 0, "critical": 0, "warning": 0, "info": 0}),
        "honesty": ["Counts are camera detections, not a headcount of distinct people."],
    }


class RenderHtml(unittest.TestCase):
    def test_is_standalone_document(self):
        h = ip.render_html(sample())
        self.assertTrue(h.strip().lower().startswith("<!doctype html>"))
        self.assertIn("</html>", h)
        self.assertIn("@page", h)                       # print CSS present

    def test_header_and_brand(self):
        h = ip.render_html(sample())
        self.assertIn("Al-Khalid HQ", h)
        self.assertIn("2026-06-01", h)
        self.assertIn("Vision Infinity", h)
        self.assertIn("Watch", h)

    def test_incidents_and_severity(self):
        h = ip.render_html(sample())
        self.assertIn("2", h)                            # attention count
        self.assertIn("critical", h.lower())
        self.assertIn("after hours armory", h.lower())   # underscores humanised
        self.assertIn("What needs attention", h)

    def test_access_windows_and_dwell(self):
        h = ip.render_html(sample())
        self.assertIn("Access windows", h)
        self.assertIn("22:00", h)
        self.assertIn("3m 10s", h)                       # 190s dwell formatted

    def test_quiet_day_has_no_incident_table(self):
        h = ip.render_html(sample(incidents=False))
        self.assertIn("quiet day", h)
        self.assertNotIn("What needs attention", h)

    def test_partial_day_badge(self):
        self.assertNotIn("PARTIAL DAY", ip.render_html(sample(partial=False)))
        self.assertIn("PARTIAL DAY", ip.render_html(sample(partial=True)))

    def test_coverage_gap_warning(self):
        self.assertNotIn("not continuous", ip.render_html(sample(coverage_ratio=1.0)))
        self.assertIn("not continuous", ip.render_html(sample(coverage_ratio=0.82)))
        self.assertIn("82%", ip.render_html(sample(coverage_ratio=0.82)))

    def test_honesty_caveat_rendered(self):
        self.assertIn("not a headcount", ip.render_html(sample()))

    def test_html_escaping(self):
        r = sample()
        r["meta"]["site"] = "<script>alert(1)</script>"
        h = ip.render_html(r)
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;", h)


class PdfEngine(unittest.TestCase):
    def test_render_pdf_is_bytes_or_honest_none(self):
        out = ip.render_pdf(sample())
        # Never a fake placeholder: either real PDF bytes (engine present) or None.
        self.assertTrue(out is None or (isinstance(out, bytes) and out[:4] == b"%PDF"))
        self.assertEqual(out is not None, ip.pdf_engine_available())


class Duration(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ip._dur(45), "45s")
        self.assertEqual(ip._dur(190), "3m 10s")
        self.assertEqual(ip._dur(3720), "1h 02m")
        self.assertEqual(ip._dur(None), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Canonical report unification + operational PDF (items 1 & 2).

One frozen payload -> identical headline metrics in portal read / WhatsApp / PDF (no renderer
recomputes), and a REAL PDF is produced by the packaged engine. Also: empty sections don't
crash, the coverage warning renders, the incident/restricted sections render, and the
visitor/staff uncertainty wording is retained.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))

import intelligence_report as ir           # noqa: E402
import report_metrics as rm                # noqa: E402
import intelligence_pdf as ipdf            # noqa: E402


def alkhalid_payload(incidents=True, coverage_ratio=0.82, partial=False):
    return {
        "schema": "daily_intelligence.v3",
        "meta": {"site": "Al-Khalid HQ", "date": "2026-06-01", "timezone": "Asia/Karachi",
                 "generated_at": "2026-06-01T18:00:00+05:00", "partial_day": partial},
        "day_boundaries": {"model": "state_machine", "opening_at": "09:00", "closing_at": "17:30",
                           "confidence": 0.82, "opening_basis": "entrance arrival then sustained internal activity"},
        "office": {"coverage": {"first": "09:12", "last": "17:40", "person_events": 231, "full_day": True}},
        "coverage": {"coverage_ratio": coverage_ratio,
                     "gaps": ([] if coverage_ratio >= 0.999 else [{"start": "a", "end": "b", "cause": "observation_gap"}])},
        "restricted": [{"camera": "Armory Gate", "purpose": "armory", "episodes": 2, "last": "22:03"}],
        "after_hours": {"verified": True, "count": 3, "reason": None},
        "incidents": ([
            {"type": "after_hours_armory", "severity": "critical", "camera": "Armory Gate", "time": "22:00", "status": "open"},
            {"type": "restricted_area_access", "severity": "warning", "camera": "Armory Gate", "time": "10:10", "status": "open"}]
            if incidents else []),
        "attention": ({"incidents_total": 2, "critical": 1, "warning": 1, "info": 0}
                      if incidents else {"incidents_total": 0, "critical": 0, "warning": 0, "info": 0}),
        "people": {"method": "estimated behavioral classification", "calibration_state": "default_uncalibrated",
                   "summary": {"probable_visitor": 4, "probable_regular_staff": 2, "unclassified": 1}},
        "honesty": ["Counts are camera detections, not a headcount of distinct people.",
                    "Visitor/staff labels are estimated behavioral classifications from movement patterns, not identities."],
    }


class OneDatasetThreeSurfaces(unittest.TestCase):
    def test_identical_headline_metrics_across_surfaces(self):
        p = alkhalid_payload()
        out = ir.render_report(p)
        h = out["headline"]
        # portal read == headline_metrics(payload) by definition
        self.assertEqual(h, rm.headline_metrics(p))
        # incident count identical in WhatsApp and PDF
        self.assertIn(str(h["incidents_total"]), out["whatsapp"])
        self.assertIn(str(h["incidents_total"]), out["pdf_html"])
        # opening identical in WhatsApp and PDF
        self.assertIn(h["opening"], out["whatsapp"])
        self.assertIn(h["opening"], out["pdf_html"])
        # coverage identical in WhatsApp and PDF
        self.assertIn(f"{h['coverage_pct']}%", out["whatsapp"])
        self.assertIn(f"{h['coverage_pct']}%", out["pdf_html"])

    def test_pdf_is_real_bytes(self):
        out = ir.render_report(alkhalid_payload())
        self.assertTrue(ipdf.pdf_engine_available(), "a production PDF engine must be packaged")
        self.assertIsInstance(out["pdf_bytes"], bytes)
        self.assertTrue(out["pdf_bytes"].startswith(b"%PDF"))
        self.assertGreater(len(out["pdf_bytes"]), 800)

    def test_empty_sections_do_not_crash(self):
        out = ir.render_report(alkhalid_payload(incidents=False, coverage_ratio=1.0))
        self.assertTrue(out["pdf_bytes"].startswith(b"%PDF"))
        self.assertIn("quiet day", out["pdf_html"].lower())

    def test_coverage_warning_rendered(self):
        self.assertIn("not continuous", ipdf.render_pdf_html(alkhalid_payload(coverage_ratio=0.82)))
        self.assertNotIn("not continuous", ipdf.render_pdf_html(alkhalid_payload(coverage_ratio=1.0)))

    def test_incident_and_restricted_sections_rendered(self):
        html = ipdf.render_pdf_html(alkhalid_payload())
        self.assertIn("What needs attention", html)
        self.assertIn("after hours armory", html.lower())
        self.assertIn("Restricted-area access", html)
        self.assertIn("armory", html.lower())

    def test_visitor_staff_uncertainty_retained(self):
        out = ir.render_report(alkhalid_payload())
        self.assertIn("estimated behavioral classifications", out["pdf_html"])
        self.assertIn("not a unique headcount", out["whatsapp"])

    def test_minimal_payload_is_safe(self):
        out = ir.render_report({})     # totally empty
        self.assertTrue(out["pdf_bytes"] is None or out["pdf_bytes"].startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

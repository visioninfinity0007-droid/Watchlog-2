#!/usr/bin/env python3
"""AI Site Configuration Skill (item 2) — deterministic, evidence-respecting advisor.

Tests the four required archetypes and the load-bearing behavior: a recorder lacking native
line crossing yields a WatchLog software-analytics recommendation (NOT an assumption that the
recorder supports tripwire), a recorder write is proposed ONLY when field-verified + safe, and
UNKNOWN never reads as supported.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "advisor"))

import site_config_advisor as sca  # noqa: E402


def cap(verdict, ev, safety="na"):
    return {"verdict": verdict, "evidence_class": ev, "ai_location": None, "safety_class": safety}


ALKHALID = {"vendor": "Dahua", "model": "DH-XVR1B08-I", "capabilities": {
    "human_vehicle_classification": cap("supported", "FIELD_VERIFIED", "safe_write"),
    "line_crossing": cap("unsupported", "FIELD_VERIFIED"),
    "intrusion": cap("unsupported", "FIELD_VERIFIED"),
}}
RETAIL = {"vendor": "Dahua", "model": "DH-XVR5108H-I3", "capabilities": {
    "human_vehicle_classification": cap("supported", "OFFICIAL_DOCUMENTED", "safe_write"),
    "line_crossing": cap("unsupported", "OFFICIAL_DOCUMENTED"),
    "people_counting": cap("unsupported", "OFFICIAL_DOCUMENTED"),
}}
FACTORY = {"vendor": "Hikvision", "model": "iDS-7208HQHI-M1/S", "capabilities": {
    "line_crossing": cap("supported", "OFFICIAL_DOCUMENTED", "safe_write"),
    "intrusion": cap("supported", "OFFICIAL_DOCUMENTED", "safe_write"),
}}
RESTAURANT = {"vendor": "Dahua", "model": "DHI-NVR4104HS-P-4KS2", "capabilities": {
    "human_vehicle_classification": cap("by_camera", "OFFICIAL_DOCUMENTED"),
    # people_counting, dwell absent -> unknown
}}


def dmap(result):
    return {r["analytic"]: r["decision"] for r in result["recommendations"]}


class Archetypes(unittest.TestCase):
    def test_alkhalid_office_no_ivs_recommends_software_line(self):
        r = sca.advise(ALKHALID, {"site_type": "office"})
        d = dmap(r)
        # THE behavior: no native line crossing -> WatchLog software, never "assume tripwire"
        self.assertEqual(d["line_crossing"], "watchlog_software")
        self.assertIn("line_crossing", r["software_analytics"])
        # field-verified SMD -> a real safe-write proposal
        self.assertEqual(d["human_vehicle_classification"], "recorder_configure")
        self.assertTrue(any(p["analytic"] == "human_vehicle_classification" for p in r["site_control_proposals"]))
        self.assertEqual(r["version"], sca.SKILL_VERSION)

    def test_retail_official_support_needs_verification_not_blind_write(self):
        r = sca.advise(RETAIL, {"site_type": "retail"})
        d = dmap(r)
        self.assertEqual(d["human_vehicle_classification"], "recorder_needs_verification")
        # OFFICIAL (not field-verified) must NOT produce a write proposal
        self.assertFalse(r["site_control_proposals"])
        self.assertEqual(d["line_crossing"], "watchlog_software")
        # people counting is honestly not available (not a WatchLog software capability)
        self.assertEqual(d["people_counting"], "not_available")
        self.assertTrue(any(u["analytic"] == "people_counting" for u in r["unsupported"]))

    def test_factory_official_perimeter_is_verify_first(self):
        r = sca.advise(FACTORY, {"site_type": "factory"})
        d = dmap(r)
        # supported per datasheet but not field-verified -> verify first, no blind write
        self.assertEqual(d["line_crossing"], "recorder_needs_verification")
        self.assertEqual(d["intrusion"], "recorder_needs_verification")
        self.assertFalse(r["site_control_proposals"])
        # restricted area (not a recorder cap here) -> WatchLog software
        self.assertEqual(d["restricted_area"], "watchlog_software")

    def test_restaurant_by_camera_and_unknown(self):
        r = sca.advise(RESTAURANT, {"site_type": "restaurant"})
        d = dmap(r)
        self.assertEqual(d["human_vehicle_classification"], "by_camera")
        self.assertEqual(d["people_counting"], "not_available")     # unknown + not software
        self.assertEqual(d["dwell"], "watchlog_software")
        # an UNKNOWN capability prompts a verify question, never an assumption of support
        self.assertTrue(any("unknown" in q.lower() for q in r["human_questions"]))


class Guards(unittest.TestCase):
    def test_proposals_are_only_field_verified_safe_writes(self):
        for prof in (ALKHALID, RETAIL, FACTORY, RESTAURANT):
            r = sca.advise(prof, {"site_type": "office"})
            for p in r["site_control_proposals"]:
                self.assertEqual(p["evidence_class"], "FIELD_VERIFIED")
                self.assertEqual(p["safety_class"], "safe_write")

    def test_human_questions_for_unmapped_context(self):
        r = sca.advise(ALKHALID, {"site_type": "office", "operating_hours_known": False,
                                  "restricted_cameras_mapped": False, "entrance_cameras_mapped": False})
        joined = " ".join(r["human_questions"]).lower()
        self.assertIn("operating hours", joined)
        self.assertIn("restricted", joined)
        self.assertIn("line or zone", joined)

    def test_context_goals_override_site_type(self):
        r = sca.advise(ALKHALID, {"site_type": "office", "goals": ["human_vehicle_classification"]})
        self.assertEqual(len(r["recommendations"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

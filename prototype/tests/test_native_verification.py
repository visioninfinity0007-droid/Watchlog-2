#!/usr/bin/env python3
"""Native-AI secondary verification (item 8).

The load-bearing case is the field regression: an indoor camera fires a recorder-
native "Vehicle" classification, but the local model on the same still sees a person
and no vehicle. That must resolve to CONFLICT and must NOT promote as a verified
Vehicle incident — while never discarding the recorder's original event.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import native_verification as nv  # noqa: E402


class VerifyStates(unittest.TestCase):
    def test_person_confirmed_by_local_person(self):
        self.assertEqual(nv.verify("person", ["person"]), nv.VERIFIED_HUMAN)

    def test_vehicle_confirmed_by_local_car(self):
        self.assertEqual(nv.verify("vehicle", ["car"]), nv.VERIFIED_VEHICLE)
        self.assertEqual(nv.verify("vehicle", ["motorcycle"]), nv.VERIFIED_VEHICLE)

    def test_indoor_vehicle_false_positive_is_conflict(self):
        # THE regression: native Vehicle, local sees only a person -> conflict.
        self.assertEqual(nv.verify("vehicle", ["person"]), nv.CONFLICT)
        # And it must not be promoted as any class.
        self.assertIsNone(nv.promoted_class("vehicle", nv.CONFLICT))

    def test_person_but_local_only_vehicle_is_conflict(self):
        self.assertEqual(nv.verify("person", ["car"]), nv.CONFLICT)
        self.assertIsNone(nv.promoted_class("person", nv.CONFLICT))

    def test_no_local_evidence_stays_unverified(self):
        # A dropped/empty snapshot must NOT invalidate the recorder's event.
        self.assertEqual(nv.verify("vehicle", None), nv.UNVERIFIED)
        self.assertEqual(nv.verify("vehicle", []), nv.UNVERIFIED)
        self.assertEqual(nv.verify("person", []), nv.UNVERIFIED)

    def test_empty_local_frame_does_not_conflict(self):
        # Local model sees nothing recognisable (object left frame): unverified, not conflict.
        self.assertEqual(nv.verify("vehicle", []), nv.UNVERIFIED)

    def test_both_present_confirms_native(self):
        # Mixed frame: native call is satisfied if its own class is present.
        self.assertEqual(nv.verify("vehicle", ["person", "car"]), nv.VERIFIED_VEHICLE)
        self.assertEqual(nv.verify("person", ["person", "car"]), nv.VERIFIED_HUMAN)

    def test_case_insensitive_and_synonyms(self):
        self.assertEqual(nv.verify("vehicle", ["Truck"]), nv.VERIFIED_VEHICLE)
        self.assertEqual(nv.verify("person", ["Human"]), nv.VERIFIED_HUMAN)

    def test_non_classified_native_is_unverifiable(self):
        # Line crossing / intrusion / dwell are geometry/temporal — a single later
        # still cannot confirm them, so they are never secondarily verified.
        self.assertFalse(nv.is_verifiable("line_crossing"))
        self.assertFalse(nv.is_verifiable("intrusion"))
        self.assertTrue(nv.is_verifiable("person"))
        self.assertTrue(nv.is_verifiable("vehicle"))
        # verify() on a non-object native type is a no-op UNVERIFIED even with a local hit.
        self.assertEqual(nv.verify("line_crossing", ["person"]), nv.UNVERIFIED)


class PromotedClass(unittest.TestCase):
    def test_verified_maps_to_class(self):
        self.assertEqual(nv.promoted_class("vehicle", nv.VERIFIED_VEHICLE), "vehicle")
        self.assertEqual(nv.promoted_class("person", nv.VERIFIED_HUMAN), "person")

    def test_conflict_is_not_promoted(self):
        self.assertIsNone(nv.promoted_class("vehicle", nv.CONFLICT))

    def test_unverified_keeps_native_class(self):
        # Kept and reported as the native class, but flagged (state travels with it).
        self.assertEqual(nv.promoted_class("vehicle", nv.UNVERIFIED), "vehicle")
        self.assertEqual(nv.promoted_class("person", nv.UNVERIFIED), "person")


class AnnotateEventWiring(unittest.TestCase):
    """Behavioral tests of the collector's wiring decision (item 15 semantic change).

    The invariant: the recorder's native event is authoritative and is only ANNOTATED —
    never dropped, never gated, and geometry events are never second-guessed from one still.
    """
    def _native(self, event_type):
        # a native event payload as the collector holds it, with its recorder provenance
        return {"native_ai": True, "native_code": "SmartMotion", "source": "recorder_native_ai"}

    def test_raw_event_is_preserved_when_verified(self):
        p = self._native("person")
        state = nv.annotate_event(p, "person", ["person"])
        self.assertEqual(state, nv.VERIFIED_HUMAN)
        # original recorder provenance untouched; verification ADDED alongside
        self.assertTrue(p["native_ai"] and p["native_code"] == "SmartMotion")
        self.assertEqual(p["source"], "recorder_native_ai")
        self.assertEqual(p["native_verification"], nv.VERIFIED_HUMAN)
        self.assertEqual(p["verified_local_classes"], ["person"])

    def test_indoor_vehicle_fp_conflicts_and_is_not_promoted(self):
        p = self._native("vehicle")
        state = nv.annotate_event(p, "vehicle", ["person"])   # native Vehicle, local sees a person
        self.assertEqual(state, nv.CONFLICT)
        self.assertEqual(p["native_verification"], nv.CONFLICT)
        self.assertIsNone(nv.promoted_class("vehicle", state))  # not promoted as a verified Vehicle
        self.assertTrue(p["native_ai"])                          # but the raw event is still there

    def test_geometry_event_is_not_second_guessed(self):
        p = self._native("line_crossing")
        state = nv.annotate_event(p, "line_crossing", [])       # empty still
        self.assertIsNone(state)                                 # not verifiable
        self.assertNotIn("native_verification", p)              # payload untouched
        self.assertTrue(p["native_ai"])                          # event kept intact

    def test_no_local_evidence_is_unverified_not_dropped(self):
        p = self._native("vehicle")
        state = nv.annotate_event(p, "vehicle", None)
        self.assertEqual(state, nv.UNVERIFIED)
        self.assertEqual(p["native_verification"], nv.UNVERIFIED)
        self.assertEqual(nv.promoted_class("vehicle", state), "vehicle")  # kept as native, flagged


if __name__ == "__main__":
    unittest.main(verbosity=2)

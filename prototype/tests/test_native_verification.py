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


if __name__ == "__main__":
    unittest.main(verbosity=2)

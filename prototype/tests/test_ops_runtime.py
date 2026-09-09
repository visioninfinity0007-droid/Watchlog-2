#!/usr/bin/env python3
"""Deterministic + temporal tests for the generic Operations-Intelligence primitives
added to the local analytics engine (zone_exit, zone_presence/absence, queue_wait,
vehicle_activity) and confidence threading. Absence/queue are STATE over monitored
time — never a fabricated one-frame event."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import analytics  # noqa: E402

BUSINESS = {
    "id": "business", "name": "Business hours", "timezone": "Asia/Karachi", "enabled": True,
    "schedule": {"days": {d: [["08:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}},
}
POLY = [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]


class D:
    def __init__(self, label, x, y, confidence=None, w=20, h=20):
        self.label = label
        self.confidence = confidence
        self.box = (x - w // 2, y - h // 2, x + w // 2, y + h // 2)


def configured(rule):
    cfg = {"timezone": "Asia/Karachi", "schedules": [BUSINESS],
           "cameras": [{"id": "cam-1", "channel": "1", "analytics_enabled": True, "rules": [rule]}]}
    eng = analytics.AnalyticsEngine(log=lambda _m: None)
    eng.configure(cfg)
    return eng


def rule(**kw):
    base = {"id": "r", "object_classes": ["person"], "enabled": True, "sample_seconds": 1,
            "geometry": {"type": "polygon", "points": POLY}, "direction": {}, "schedule_id": None}
    base.update(kw)
    return base


class OpsPrimitives(unittest.TestCase):
    def frame(self, eng, dets, t):
        return eng.process("1", dets, (1000, 1000), t)

    # ---- zone_exit -------------------------------------------------------
    def test_zone_exit_on_leaving(self):
        # steps stay within the tracker match radius (0.16) so it is ONE track leaving,
        # not two separate detections; exit requires inside->outside on the same track.
        eng = configured(rule(id="ex", rule_type="zone_exit"))
        t = datetime.now(timezone.utc)
        self.frame(eng, [D("person", 300, 500)], t)                          # inside (0.30)
        ev = self.frame(eng, [D("person", 150, 500)], t + timedelta(seconds=1))  # left to 0.15
        self.assertEqual(1, len(ev))
        self.assertEqual("zone_exit", ev[0]["event_type"])

    # ---- zone_absence (empty too long) -----------------------------------
    def test_zone_absence_fires_after_threshold_and_resets(self):
        eng = configured(rule(id="ab", rule_type="zone_absence", dwell_seconds=5))
        t = datetime.now(timezone.utc)
        self.assertEqual([], self.frame(eng, [], t))                       # clock starts
        self.assertEqual([], self.frame(eng, [], t + timedelta(seconds=4)))
        ev = self.frame(eng, [], t + timedelta(seconds=6))
        self.assertEqual(1, len(ev))
        self.assertEqual("zone_empty", ev[0]["event_type"])
        self.assertEqual([], self.frame(eng, [], t + timedelta(seconds=8)))  # no re-fire while empty
        # re-occupied resets; empty again can fire again
        self.frame(eng, [D("person", 500, 500)], t + timedelta(seconds=9))
        self.frame(eng, [], t + timedelta(seconds=10))
        ev2 = self.frame(eng, [], t + timedelta(seconds=16))
        self.assertEqual(1, len(ev2))
        self.assertEqual("zone_empty", ev2[0]["event_type"])

    def test_absence_does_not_fire_on_single_frame_miss(self):
        eng = configured(rule(id="ab2", rule_type="zone_absence", dwell_seconds=5))
        t = datetime.now(timezone.utc)
        self.frame(eng, [D("person", 500, 500)], t)                        # present
        self.frame(eng, [], t + timedelta(seconds=1))                      # one-frame flicker
        self.frame(eng, [D("person", 500, 500)], t + timedelta(seconds=2)) # present again
        self.assertEqual([], self.frame(eng, [D("person", 500, 500)], t + timedelta(seconds=8)))

    # ---- zone_presence (expected present during schedule) ----------------
    def test_expected_absent_only_inside_schedule(self):
        r = rule(id="pr", rule_type="zone_presence", schedule_id="business", dwell_seconds=5)
        # inside business hours: Mon 10:00 PKT == 2026-08-31 05:00Z
        eng = configured(r)
        t = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
        self.frame(eng, [], t)
        ev = self.frame(eng, [], t + timedelta(seconds=6))
        self.assertEqual(1, len(ev))
        self.assertEqual("expected_absent", ev[0]["event_type"])
        # outside business hours: Tue 01:00 PKT == 2026-08-31 20:00Z -> silent
        eng2 = configured(rule(id="pr", rule_type="zone_presence", schedule_id="business", dwell_seconds=5))
        t2 = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)
        self.frame(eng2, [], t2)
        self.assertEqual([], self.frame(eng2, [], t2 + timedelta(seconds=60)))

    # ---- queue / wait ----------------------------------------------------
    def test_queue_wait_sustained_then_reset(self):
        eng = configured(rule(id="q", rule_type="queue_wait", occupancy_min=3, dwell_seconds=5))
        t = datetime.now(timezone.utc)
        three = [D("person", 300, 300), D("person", 500, 500), D("person", 700, 700)]
        self.frame(eng, three, t)                                          # count 3 -> over_since=t
        self.assertEqual([], self.frame(eng, three, t + timedelta(seconds=4)))
        ev = self.frame(eng, three, t + timedelta(seconds=6))
        self.assertEqual(1, len(ev))
        self.assertEqual("queue_wait", ev[0]["event_type"])
        self.assertEqual(3, ev[0]["metadata"]["count"])
        self.assertEqual([], self.frame(eng, three, t + timedelta(seconds=8)))   # no re-fire
        self.frame(eng, [D("person", 300, 300)], t + timedelta(seconds=9))       # drop below -> reset
        self.assertEqual([], self.frame(eng, [D("person", 300, 300)], t + timedelta(seconds=20)))

    # ---- vehicle_activity ------------------------------------------------
    def test_vehicle_activity_class_filtered(self):
        eng = configured(rule(id="v", rule_type="vehicle_activity", object_classes=["car", "motorcycle"]))
        t = datetime.now(timezone.utc)
        self.frame(eng, [D("person", 100, 100)], t)                        # person ignored
        self.assertEqual([], self.frame(eng, [D("person", 500, 500)], t + timedelta(seconds=1)))
        self.frame(eng, [D("car", 100, 100)], t + timedelta(seconds=2))    # car outside
        ev = self.frame(eng, [D("car", 500, 500)], t + timedelta(seconds=3))  # car enters
        self.assertEqual(1, len(ev))
        self.assertEqual("vehicle_activity", ev[0]["event_type"])
        self.assertEqual("car", ev[0]["object_class"])

    # ---- confidence threading -------------------------------------------
    def test_confidence_rides_in_metadata(self):
        eng = configured(rule(id="ze", rule_type="zone_entry"))
        t = datetime.now(timezone.utc)
        self.frame(eng, [D("person", 100, 100, confidence=0.87)], t)
        ev = self.frame(eng, [D("person", 500, 500, confidence=0.87)], t + timedelta(seconds=1))
        self.assertEqual(1, len(ev))
        self.assertEqual(0.87, ev[0]["metadata"]["confidence"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

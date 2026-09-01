#!/usr/bin/env python3
"""Deterministic tests for WatchLog Analytics Studio local rule engine."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import analytics  # noqa: E402


class D:
    def __init__(self, label, x, y, w=20, h=20):
        self.label = label
        self.box = (x - w // 2, y - h // 2, x + w // 2, y + h // 2)


SITE = {
    "site_id": "site-1",
    "site_type": "warehouse_logistics",
    "timezone": "Asia/Karachi",
    "version": 2,
    "schedules": [{
        "id": "business", "name": "Business hours", "timezone": "Asia/Karachi", "enabled": True,
        "schedule": {"days": {
            "mon": [["08:00", "18:00"]], "tue": [["08:00", "18:00"]],
            "wed": [["08:00", "18:00"]], "thu": [["08:00", "18:00"]],
            "fri": [["08:00", "18:00"]], "sat": [], "sun": []
        }}
    }],
    "cameras": [{"id": "cam-1", "channel": "1", "name": "Main Gate",
                 "purpose": "main_gate", "analytics_enabled": True, "rules": []}]
}


def configured(*rules):
    cfg = {**SITE, "cameras": [{**SITE["cameras"][0], "rules": list(rules)}]}
    engine = analytics.AnalyticsEngine(log=lambda _m: None)
    engine.configure(cfg)
    return engine


class GeometryTests(unittest.TestCase):
    def test_polygon(self):
        poly = [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]
        self.assertTrue(analytics.point_in_polygon((.5, .5), poly))
        self.assertFalse(analytics.point_in_polygon((.9, .5), poly))

    def test_schedule(self):
        schedule = SITE["schedules"][0]["schedule"]
        inside = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)   # Mon 09:00 PKT
        outside = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc) # Tue 01:00 PKT
        self.assertTrue(analytics.schedule_contains(schedule, inside, "Asia/Karachi"))
        self.assertFalse(analytics.schedule_contains(schedule, outside, "Asia/Karachi"))

    def test_overnight_schedule_carries_into_next_day(self):
        night = {"days": {"mon": [["22:00", "06:00"]], "tue": [], "wed": [],
                           "thu": [], "fri": [], "sat": [], "sun": []}}
        mon_2300_pkt = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
        tue_0200_pkt = datetime(2026, 8, 31, 21, 0, tzinfo=timezone.utc)
        tue_0700_pkt = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
        self.assertTrue(analytics.schedule_contains(night, mon_2300_pkt, "Asia/Karachi"))
        self.assertTrue(analytics.schedule_contains(night, tue_0200_pkt, "Asia/Karachi"))
        self.assertFalse(analytics.schedule_contains(night, tue_0700_pkt, "Asia/Karachi"))

    def test_finite_segments(self):
        self.assertTrue(analytics.segments_intersect((.4,.5),(.6,.5),(.5,.1),(.5,.9)))
        self.assertFalse(analytics.segments_intersect((.4,.95),(.6,.95),(.5,.1),(.5,.9)))


class RuleTests(unittest.TestCase):
    def line_rule(self):
        return {"id":"r-line","name":"Visitor Flow","rule_type":"line_crossing",
                "object_classes":["person"],"enabled":True,"sample_seconds":1,
                "geometry":{"type":"line","points":[[.5,.1],[.5,.9]]},
                "direction":{"positive_to_negative":"in","negative_to_positive":"out"},
                "schedule_id":None}

    def test_line_crossing_direction_and_no_double_count(self):
        eng = configured(self.line_rule())
        t = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
        # 0.43 -> 0.55 stays within the short-lived track match radius.
        self.assertEqual([], eng.process("1", [D("person", 430, 500)], (1000, 1000), t))
        events = eng.process("1", [D("person", 550, 500)], (1000, 1000), t + timedelta(seconds=1))
        self.assertEqual(1, len(events))
        self.assertEqual("line_crossing", events[0]["event_type"])
        self.assertIn(events[0]["direction"], ("in", "out"))
        self.assertEqual([], eng.process("1", [D("person", 600, 500)], (1000, 1000), t + timedelta(seconds=2)))

    def test_crossing_infinite_extension_does_not_count(self):
        eng = configured(self.line_rule())
        t = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
        # Same left-to-right movement, but y=.95 is outside the line segment ending at .9.
        eng.process("1", [D("person", 430, 950)], (1000,1000), t)
        self.assertEqual([], eng.process("1", [D("person", 550, 950)], (1000,1000), t+timedelta(seconds=1)))

    def test_zone_entry(self):
        rule = {"id":"r-zone","name":"Restricted area","rule_type":"zone_entry",
                "object_classes":["person"],"enabled":True,"sample_seconds":1,
                "geometry":{"type":"polygon","points":[[.4,.4],[.8,.4],[.8,.8],[.4,.8]]},
                "direction":{},"schedule_id":None}
        eng = configured(rule); t = datetime.now(timezone.utc)
        eng.process("1", [D("person", 380, 500)], (1000, 1000), t)
        ev = eng.process("1", [D("person", 500, 500)], (1000, 1000), t + timedelta(seconds=1))
        self.assertEqual(1, len(ev)); self.assertEqual("zone_entry", ev[0]["event_type"])

    def test_dwell_once(self):
        rule = {"id":"r-dwell","name":"Dwell","rule_type":"zone_dwell",
                "object_classes":["person"],"enabled":True,"sample_seconds":1,
                "geometry":{"type":"polygon","points":[[.1,.1],[.9,.1],[.9,.9],[.1,.9]]},
                "direction":{},"schedule_id":None,"dwell_seconds":5}
        eng = configured(rule); t = datetime.now(timezone.utc)
        eng.process("1", [D("person",500,500)], (1000,1000), t)
        self.assertEqual([], eng.process("1", [D("person",510,500)], (1000,1000), t+timedelta(seconds=4)))
        ev=eng.process("1", [D("person",520,500)], (1000,1000), t+timedelta(seconds=6))
        self.assertEqual(1,len(ev));self.assertEqual("dwell_completed",ev[0]["event_type"]);self.assertGreaterEqual(ev[0]["duration_seconds"],5)
        self.assertEqual([], eng.process("1",[D("person",530,500)],(1000,1000),t+timedelta(seconds=7)))

    def test_after_hours_schedule(self):
        rule={"id":"r-after","name":"After hours","rule_type":"schedule_activity",
              "object_classes":["person"],"enabled":True,"sample_seconds":1,"geometry":{},
              "direction":{"schedule_mode":"outside"},"schedule_id":"business"}
        eng=configured(rule);t=datetime(2026,8,31,16,0,tzinfo=timezone.utc) # Mon 21:00 PKT
        ev=eng.process("1",[D("person",500,500)],(1000,1000),t)
        self.assertEqual(1,len(ev));self.assertEqual("schedule_activity",ev[0]["event_type"])
        self.assertEqual([],eng.process("1",[D("person",510,500)],(1000,1000),t+timedelta(seconds=1)))

    def test_occupancy_changes_only(self):
        rule={"id":"r-occ","name":"Checkout Activity","rule_type":"occupancy",
              "object_classes":["person"],"enabled":True,"sample_seconds":1,
              "geometry":{"type":"polygon","points":[[.2,.2],[.8,.2],[.8,.8],[.2,.8]]},"direction":{},"schedule_id":None}
        eng=configured(rule);t=datetime.now(timezone.utc)
        first=eng.process("1",[D("person",500,500)],(1000,1000),t)
        self.assertEqual(1,len(first));self.assertEqual(1,first[0]["metadata"]["count"])
        self.assertEqual([],eng.process("1",[D("person",510,500)],(1000,1000),t+timedelta(seconds=1)))
        empty=eng.process("1",[],(1000,1000),t+timedelta(seconds=2))
        self.assertEqual(1,len(empty));self.assertEqual(0,empty[0]["metadata"]["count"])

    def test_class_filter(self):
        rule={"id":"r-vehicle","name":"Vehicle Flow","rule_type":"line_crossing",
              "object_classes":["car","motorcycle"],"enabled":True,"sample_seconds":1,
              "geometry":{"type":"line","points":[[.5,.1],[.5,.9]]},"direction":{},"schedule_id":None}
        eng=configured(rule);t=datetime.now(timezone.utc)
        eng.process("1",[D("person",430,500)],(1000,1000),t)
        self.assertEqual([],eng.process("1",[D("person",550,500)],(1000,1000),t+timedelta(seconds=1)))


if __name__ == "__main__": unittest.main(verbosity=2)

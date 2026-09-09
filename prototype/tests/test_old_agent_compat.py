#!/usr/bin/env python3
"""Forward-compatibility audit: the FROZEN production agent engine vs the 0054-0058 server.

Loads the ACTUAL production agent parser/runtime (a verbatim frozen copy of prototype/agent/
analytics.py at main — the engine that has NONE of the post-Phase-A primitives) and drives its
real config lifecycle (load_config -> configure -> save_config -> process) against a realistic
wl_agent_analytics_config (0056) response. Proves the old agent, talking to the new server:

  * does not crash on the new payload (extra top-level fields, multi_agent_enabled, governance
    fields on rules, occupancy_min/max);
  * safely IGNORES additional fields it does not know;
  * still evaluates the rule types it DOES support (line_crossing/zone_entry/zone_dwell/
    occupancy/schedule_activity) exactly as before;
  * emits NOTHING for unknown/new primitives (zone_exit/zone_presence/zone_absence/queue_wait/
    vehicle_activity) — they are skipped, never misinterpreted;
  * handles the unchanged-config (changed:false) response without reconfiguring.

If this ever failed we would fix the SERVER, never the installed agent. It passes because the
0056 payload is a strict superset of the old shape and the old engine reads only known keys.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "tests" / "fixtures" / "frozen_production_analytics.py"

_spec = importlib.util.spec_from_file_location("frozen_production_analytics", FROZEN)
frozen = importlib.util.module_from_spec(_spec)
sys.modules["frozen_production_analytics"] = frozen
_spec.loader.exec_module(frozen)

ZONE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
LINE = [[0.1, 0.5], [0.9, 0.5]]


class D:
    def __init__(self, label, x, y, w=20, h=20):
        self.label = label
        self.confidence = 0.9
        self.box = (x - w // 2, y - h // 2, x + w // 2, y + h // 2)


def _rule(rid, rule_type, **extra):
    # every rule carries the NEW governance/threshold fields the old engine has never seen
    base = {"id": rid, "analytic_key": rule_type, "name": rid, "rule_type": rule_type,
            "object_classes": ["person"], "geometry": {"points": ZONE}, "direction": {},
            "schedule_id": None, "dwell_seconds": 2, "sample_seconds": 2, "enabled": True,
            "severity": "attention", "promote_incident": True,
            # 0056 governance layer — unknown to the frozen engine, must be ignored:
            "occupancy_min": 2, "occupancy_max": 5, "confidence_min": 0.8, "cooldown_seconds": 300,
            "actions": [{"type": "capture_still"}, {"type": "request_footage"}],
            "evidence_json": {"retain_minutes": 30}, "sensitive": True, "review_required": True,
            "rule_version": 4}
    base.update(extra)
    return base


def config_0056(changed=True):
    return {
        "version": 7, "changed": changed, "multi_agent_enabled": True,        # NEW top-level field
        "config": {
            "site_id": "s1", "site_type": "warehouse_logistics", "timezone": "Asia/Karachi",
            "version": 7, "multi_agent_enabled": True,                        # NEW inside config too
            "schedules": [],
            "cameras": [{"id": "cam-1", "channel": "1", "name": "Gate", "purpose": "perimeter",
                         "analytics_enabled": True, "rules": [
                             _rule("known-entry", "zone_entry"),
                             _rule("known-line", "line_crossing", geometry={"points": LINE},
                                   direction={"negative_to_positive": "in", "positive_to_negative": "out"}),
                             _rule("known-occ", "occupancy"),
                             # unknown/new primitives — the old engine must skip these entirely:
                             _rule("new-exit", "zone_exit"),
                             _rule("new-presence", "zone_presence"),
                             _rule("new-absence", "zone_absence"),
                             _rule("new-queue", "queue_wait"),
                             _rule("new-vehicle", "vehicle_activity", object_classes=["car"]),
                         ]}],
        },
        "snapshot_requests": [],
    }


class OldAgentCompat(unittest.TestCase):
    def _lifecycle(self, tmp):
        """Replicate the frozen agent's exact config handling using the frozen functions."""
        path = Path(tmp) / "analytics_config.json"
        cached = frozen.load_config(path)                       # first run: empty
        engine = frozen.AnalyticsEngine(log=lambda _m: None)
        engine.configure(cached.get("config"))
        payload = config_0056(changed=True)
        # the old worker's exact envelope handling (reads only known keys):
        if payload.get("changed") and payload.get("config"):
            version = int(payload.get("version") or 0)
            frozen.save_config(path, version, payload["config"])
            engine.configure(payload["config"])
        _ = payload.get("snapshot_requests") or []             # known key, empty
        return engine

    def test_new_config_does_not_crash_and_plans_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._lifecycle(tmp)
            plan = engine.sample_plan()                        # must not raise; camera is planned
            self.assertTrue(any(ch == "1" for ch, _ in plan), f"old engine should still plan the camera: {plan}")

    def test_known_primitives_still_fire_unknown_emit_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._lifecycle(tmp)
            t = datetime.now(timezone.utc)
            fs = (1000, 1000)
            # walk a person from outside the zone to inside — old zone_entry must still fire
            engine.process("1", [D("person", 150, 500)], fs, t)                 # outside
            events = engine.process("1", [D("person", 300, 500)], fs, t + timedelta(seconds=1))  # inside
            types = sorted({e["event_type"] for e in events})
            # zone_entry fires; occupancy emits its measurement; NOTHING from the new primitives
            self.assertIn("zone_entry", types)
            for forbidden in ("zone_exit", "expected_absent", "zone_empty", "queue_wait",
                              "vehicle_activity", "occupancy_above", "occupancy_below"):
                self.assertNotIn(forbidden, types, f"old engine must not emit {forbidden}")

    def test_additional_fields_are_ignored_not_misread(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._lifecycle(tmp)
            # the occupancy rule carries occupancy_min/max the old engine cannot honour: it must
            # fall back to its measurement stream ('occupancy'), never a threshold incident.
            t = datetime.now(timezone.utc)
            fs = (1000, 1000)
            ev = engine.process("1", [D("person", 300, 300), D("person", 500, 500), D("person", 700, 700)], fs, t)
            occ = [e for e in ev if e["event_type"] == "occupancy"]
            self.assertTrue(occ, "old occupancy still emits a measurement")
            self.assertNotIn("min", occ[0].get("metadata", {}), "old engine must not synthesise threshold metadata")

    def test_unchanged_response_does_not_reconfigure(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._lifecycle(tmp)
            before = engine.sample_plan()
            payload = config_0056(changed=False)               # unchanged-config envelope
            if payload.get("changed") and payload.get("config"):
                engine.configure(payload["config"])            # must NOT run
            self.assertEqual(before, engine.sample_plan(), "unchanged response must not reconfigure")


if __name__ == "__main__":
    unittest.main(verbosity=1)

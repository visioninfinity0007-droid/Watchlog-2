#!/usr/bin/env python3
"""Recorder config drift engine (item 9).

Anchored on the Al-Khalid baseline: indoor SMD Human-only (Vehicle OFF), NTP on, DST off.
The engine must (a) stay silent when the recorder still matches, (b) raise HIGH when indoor
Vehicle is turned back on or Human is disabled, (c) keep UNREADABLE distinct from drift, and
(d) never fabricate a value the recorder didn't return.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import config_drift as cd  # noqa: E402


DESIRED = {
    "channels": {
        "1": {"smd_enable": True, "smd_human": True, "smd_vehicle": False, "title": "Reception & Entrance"},
        "3": {"smd_enable": True, "smd_human": True, "smd_vehicle": False, "smd_sensitivity": "3", "title": "Armory Gate"},
    },
    "time": {"ntp_enabled": True, "dst_enabled": False},
}


class FakeDriver:
    """Minimal driver exposing the read surface config_drift.observe() uses."""
    def __init__(self, smd=None, titles=None, clock=None, raise_smd=False):
        self._smd = smd or {}
        self._titles = titles or {}
        self._clock = clock
        self._raise_smd = raise_smd

    def get_smd(self, ch):
        if self._raise_smd:
            raise RuntimeError("configManager unreachable")
        return self._smd.get(str(ch), {})

    def get_channel_title(self, ch):
        return self._titles.get(str(ch))

    def get_clock(self):
        return self._clock if self._clock is not None else {"supported": False}


class DiffLogic(unittest.TestCase):
    def test_in_sync_is_silent(self):
        observed = {
            "channels": {
                "1": {"smd_enable": True, "smd_human": True, "smd_vehicle": False, "title": "Reception & Entrance"},
                "3": {"smd_enable": True, "smd_human": True, "smd_vehicle": False, "smd_sensitivity": "3", "title": "Armory Gate"},
            },
            "time": {"ntp_enabled": True, "dst_enabled": False},
        }
        s = cd.summarize(cd.diff(DESIRED, observed))
        self.assertFalse(s["in_drift"])
        self.assertEqual(s["unreadable"], 0)

    def test_indoor_vehicle_reenabled_is_high(self):
        observed = {"channels": {"3": {"smd_enable": True, "smd_human": True, "smd_vehicle": True}}}
        drifts = cd.diff(DESIRED, observed)
        veh = [d for d in drifts if d.key == "smd_vehicle" and d.channel == "3"]
        self.assertEqual(len(veh), 1)
        self.assertEqual(veh[0].severity, cd.HIGH)
        self.assertTrue(veh[0].readable)

    def test_human_disabled_is_high(self):
        observed = {"channels": {"1": {"smd_human": False}}}
        drifts = [d for d in cd.diff(DESIRED, observed) if d.key == "smd_human"]
        self.assertEqual(drifts[0].severity, cd.HIGH)

    def test_ntp_off_is_medium(self):
        observed = {"time": {"ntp_enabled": False}}
        drifts = [d for d in cd.diff(DESIRED, observed) if d.key == "ntp_enabled"]
        self.assertEqual(drifts[0].severity, cd.MEDIUM)

    def test_dst_on_is_medium(self):
        observed = {"time": {"dst_enabled": True}}
        drifts = [d for d in cd.diff(DESIRED, observed) if d.key == "dst_enabled"]
        self.assertEqual(drifts[0].severity, cd.MEDIUM)

    def test_title_change_is_low(self):
        observed = {"channels": {"1": {"title": "CAM1"}}}
        drifts = [d for d in cd.diff(DESIRED, observed) if d.key == "title"]
        self.assertEqual(drifts[0].severity, cd.LOW)

    def test_unreadable_is_not_drift(self):
        # Observed returns nothing for ch3 -> every managed key UNREADABLE, in_drift stays False.
        observed = {"channels": {}, "time": {}}
        s = cd.summarize(cd.diff({"channels": {"3": {"smd_vehicle": False}}, "time": {}}, observed))
        self.assertFalse(s["in_drift"])
        self.assertEqual(s["unreadable"], 1)
        self.assertEqual(s["findings"][0]["severity"], cd.UNKNOWN)
        self.assertFalse(s["findings"][0]["readable"])

    def test_normalization_no_false_alarm(self):
        # 'true' vs True and '3' vs 3 must not register as drift.
        observed = {"channels": {"3": {"smd_human": "true", "smd_vehicle": "false", "smd_sensitivity": 3}}}
        drifts = [d for d in cd.diff({"channels": {"3": {"smd_human": True, "smd_vehicle": False, "smd_sensitivity": "3"}}}, observed) if d.readable]
        self.assertEqual(drifts, [])

    def test_sorted_most_severe_first(self):
        observed = {
            "channels": {"1": {"title": "CAM1"}, "3": {"smd_vehicle": True}},
            "time": {"ntp_enabled": False},
        }
        sevs = [d.severity for d in cd.diff(DESIRED, observed) if d.readable]
        self.assertEqual(sevs, sorted(sevs, key=lambda s: cd._RANK[s]))
        self.assertEqual(sevs[0], cd.HIGH)


class ObserveReader(unittest.TestCase):
    def test_observe_assembles_shape(self):
        drv = FakeDriver(
            smd={"3": {"enable": True, "human": True, "vehicle": True, "sensitivity": "3"}},
            titles={"3": "Armory Gate"},
            clock={"supported": True, "ntp_enabled": False, "dst_enabled": False},
        )
        obs = cd.observe(drv, ["3"])
        self.assertEqual(obs["channels"]["3"]["smd_vehicle"], True)
        self.assertEqual(obs["channels"]["3"]["title"], "Armory Gate")
        self.assertEqual(obs["time"]["ntp_enabled"], False)

    def test_observe_failsafe_on_getter_error(self):
        # get_smd raising must NOT fabricate a value or crash — the channel is simply absent.
        drv = FakeDriver(raise_smd=True, titles={"3": "Armory Gate"})
        obs = cd.observe(drv, ["3"])
        self.assertNotIn("smd_vehicle", obs["channels"].get("3", {}))
        self.assertEqual(obs["channels"]["3"]["title"], "Armory Gate")   # title still read

    def test_check_end_to_end_flags_reenabled_vehicle(self):
        drv = FakeDriver(
            smd={"3": {"enable": True, "human": True, "vehicle": True}},
            clock={"supported": True, "ntp_enabled": True, "dst_enabled": False},
        )
        result = cd.check(drv, {"channels": {"3": {"smd_vehicle": False, "smd_human": True}},
                                "time": {"ntp_enabled": True}})
        self.assertTrue(result["in_drift"])
        self.assertEqual(result["counts"]["high"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

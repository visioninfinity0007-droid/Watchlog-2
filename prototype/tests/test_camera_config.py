#!/usr/bin/env python3
"""0.4.4 P4 — camera Monitor/Ignore + naming merged into the sync payload (no hardware).

Proves setup_backend.merge_camera_config folds the operator's per-channel Monitor/Ignore + name
choices into what wl_sync_cameras receives: monitored -> is_configured true, Ignore -> false, a
discovered channel with no explicit profile stays monitored, and edited names win.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import setup_backend as sb  # noqa: E402


class MergeCameraConfig(unittest.TestCase):
    def test_monitor_and_ignore(self):
        channels = [{"channel": "1", "name": "Cam1"}, {"channel": "2", "name": "Cam2"}]
        profiles = [{"channel": "1", "name": "Reception Main Entrance", "monitored": True},
                    {"channel": "2", "name": "Spare", "monitored": False}]
        out = {c["channel"]: c for c in sb.merge_camera_config(channels, profiles)}
        self.assertTrue(out["1"]["is_configured"])
        self.assertEqual(out["1"]["name"], "Reception Main Entrance")   # edited name wins
        self.assertFalse(out["2"]["is_configured"])                     # Ignore -> unconfigured

    def test_channel_without_profile_defaults_monitored(self):
        out = sb.merge_camera_config([{"channel": "3", "name": "Armory"}], [])
        self.assertTrue(out[0]["is_configured"])
        self.assertEqual(out[0]["name"], "Armory")

    def test_blank_channels_dropped(self):
        out = sb.merge_camera_config([{"channel": "", "name": "x"}, {"channel": "4", "name": "y"}], [])
        self.assertEqual([c["channel"] for c in out], ["4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

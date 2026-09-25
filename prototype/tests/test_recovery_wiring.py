#!/usr/bin/env python3
"""Automatic recovery wiring contract (0.4.4 §1/§2) — the Agent runs recovery itself.

Source contract over watchlog_agent.py: the recovery worker is started as a thread, detects the
outage from persisted last-live on startup, opens a recovery interval, backfills with live
priority + throttle, is gated by recovery_enabled, and is strictly READ-ONLY (never a recorder
write). Persistence of last-live is on the heartbeat cadence.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")


class RecoveryWiring(unittest.TestCase):
    def test_worker_defined_and_started(self):
        self.assertIn("def recovery_worker(", SRC)
        self.assertIn("target=recovery_worker", SRC)
        self.assertIn("recov.join(", SRC)                     # joined on shutdown

    def test_outage_detection_and_report(self):
        self.assertIn("read_last_live", SRC)
        self.assertIn("detect_outage", SRC)
        self.assertIn("wl_open_recovery_interval", SRC)

    def test_last_live_requires_fresh_recorder_transport(self):
        self.assertIn("Persist RECORDER observation", SRC)
        self.assertIn('holder.get("live_driver")', SRC)
        self.assertIn("last_activity_monotonic", SRC)
        self.assertIn("persist_last_live", SRC)

    def test_hikvision_and_dahua_archive_recovery_are_wired(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        self.assertIn("dahua_archive.install()", worker)
        self.assertIn("hikvision_archive.install()", worker)
        self.assertNotIn("Hikvision archive recovery disabled", worker)

    def test_spool_overflow_becomes_recorder_archive_recovery(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        self.assertIn("pending_recovery_gap", worker)
        self.assertIn("clear_recovery_gap", worker)
        self.assertIn("spool overflow", worker)

    def test_gated_and_live_priority_and_throttled(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        self.assertIn("cfg.recovery_enabled", worker)
        self.assertIn("live_pending=lambda: spool.count()", worker)
        self.assertIn("throttle_seconds=cfg.recovery_throttle_seconds", worker)

    def test_recovery_is_read_only(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        for banned in ("execute_write", "set_smd", "set_channel_title", "set_time_config",
                       "wl_site_command", "propose_write"):
            self.assertNotIn(banned, worker, f"recovery worker must be read-only (found {banned})")

    def test_default_on_but_disable_flag_exists(self):
        self.assertIn('get("recovery_enabled") or "true"', SRC)   # ON by default, disable-able


if __name__ == "__main__":
    unittest.main(verbosity=2)

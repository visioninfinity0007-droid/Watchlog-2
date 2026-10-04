#!/usr/bin/env python3
"""Automatic recovery wiring contract (0.4.4 §1/§2) — the Agent runs recovery itself.

Source contract over watchlog_agent.py: the recovery worker is started as a thread, detects the
outage from persisted last-live on startup, opens a recovery interval, backfills with live
priority + throttle, is gated by recovery_enabled, and is strictly READ-ONLY (never a recorder
write). Persistence of last-live is on the heartbeat cadence only when recorder transport is fresh.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
ANALYTICS = (ROOT / "agent" / "analytics_agent.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "agent" / "native_event_collector.py").read_text(encoding="utf-8")
INCIDENT = (ROOT / "agent" / "incident_evidence.py").read_text(encoding="utf-8")
HIK_ARCHIVE = (ROOT / "agent" / "hikvision_archive.py").read_text(encoding="utf-8")


class RecoveryWiring(unittest.TestCase):
    def test_worker_defined_and_started(self):
        self.assertIn("def recovery_worker(", SRC)
        self.assertIn("target=recovery_worker", SRC)
        self.assertIn("recov.join(", SRC)
        # The shipped release replaces core.cmd_run with analytics_agent.enhanced_cmd_run.
        # Recovery must therefore be composed there too, not only in the dormant core loop.
        self.assertIn("target=core.recovery_worker", ANALYTICS)
        self.assertIn("recovery.start()", ANALYTICS)
        self.assertIn("recovery.join(", ANALYTICS)

    def test_real_collector_publishes_recorder_transport_truth(self):
        collector = SRC.split("def collector(", 1)[1].split("def upload_once(", 1)[0]
        for source in (collector, NATIVE):
            self.assertIn('holder["live_driver"] = driver', source)
            self.assertIn('holder["recorder_live_at"] = time.monotonic()', source)
            self.assertIn('holder.pop("live_driver", None)', source)

    def test_recovery_accepts_startup_channel_dictionaries(self):
        helper = SRC.split("def _recovery_camera_ids(", 1)[1].split("def recovery_worker(", 1)[0]
        self.assertIn("isinstance(c, dict)", helper)
        self.assertIn('c.get("channel")', helper)

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
        archive_open = SRC.split("def open_archive_driver(", 1)[1].split("# --- enrollment", 1)[0]
        self.assertIn("open_archive_driver(cfg)", worker)
        self.assertIn("dahua_archive.install()", archive_open)
        self.assertIn("hikvision_archive.install()", archive_open)
        self.assertIn('"dahua-cgi"', archive_open)
        self.assertIn('"hikvision-isapi"', archive_open)

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

    def test_visual_gap_backfill_is_always_wired(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        self.assertIn("snapshot_interval_seconds=cfg.recovery_snapshot_seconds", worker)
        self.assertIn("RecoveryRunner(", worker)

    def test_recovery_is_read_only(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        for banned in ("execute_write", "set_smd", "set_channel_title", "set_time_config",
                       "wl_site_command", "propose_write"):
            self.assertNotIn(banned, worker, f"recovery worker must be read-only (found {banned})")

    def test_packaged_archive_scan_is_real_not_placeholder(self):
        self.assertNotIn("retrieve_frames=None, analyze=None", ANALYTICS)
        self.assertIn("core.open_archive_driver(cfg)", ANALYTICS)
        self.assertIn("recovery_ai.recovered_frame", ANALYTICS)

    def test_incident_footage_uses_vendor_archive_transport(self):
        self.assertIn("core.open_archive_driver(cfg)", INCIDENT)

    def test_runtime_heartbeat_does_not_fake_recorder_seen(self):
        self.assertIn("recorder_live=recorder_live", ANALYTICS)
        self.assertIn("recorder_is_live", SRC)

    def test_hikvision_clip_path_has_total_time_budget(self):
        self.assertIn("CLIP_TOTAL_SECONDS = 90", HIK_ARCHIVE)
        self.assertIn("MAX_DOWNLOAD_CANDIDATES = 2", HIK_ARCHIVE)
        self.assertIn("deadline = time.monotonic() + CLIP_TOTAL_SECONDS", HIK_ARCHIVE)

    def test_default_on_but_disable_flag_exists(self):
        self.assertIn('get("recovery_enabled") or "true"', SRC)   # ON by default, disable-able


if __name__ == "__main__":
    unittest.main(verbosity=2)

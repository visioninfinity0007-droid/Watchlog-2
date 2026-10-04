#!/usr/bin/env python3
"""Automatic recovery wiring contract (0.4.4 §1/§2) — the Agent runs recovery itself.

Source contract over watchlog_agent.py: the recovery worker is started as a thread, detects the
outage from persisted last-live on startup, opens a recovery interval, backfills with live
priority + throttle, is gated by recovery_enabled, and is strictly READ-ONLY (never a recorder
write). Persistence of last-live is on the heartbeat cadence only when recorder transport is fresh.

The packaged Agent runs analytics_agent.enhanced_cmd_run (release_agent -> analytics_agent.main
replaces core.cmd_run), so the worker composition is checked in THAT loop; watchlog_agent.cmd_run
is the dormant core loop. Behaviour is covered by test_recovery_rpc_contract.py and
test_worker_exception_survival.py.
"""
from __future__ import annotations

import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
ANALYTICS = (ROOT / "agent" / "analytics_agent.py").read_text(encoding="utf-8")
SHIPPED = ANALYTICS.split("def enhanced_cmd_run(", 1)[1].split("\ndef ", 1)[0]
NATIVE = (ROOT / "agent" / "native_event_collector.py").read_text(encoding="utf-8")
INCIDENT = (ROOT / "agent" / "incident_evidence.py").read_text(encoding="utf-8")
HIK_ARCHIVE = (ROOT / "agent" / "hikvision_archive.py").read_text(encoding="utf-8")


class RecoveryWiring(unittest.TestCase):
    def test_worker_defined_and_started(self):
        self.assertIn("def recovery_worker(", SRC)
        self.assertIn("target=recovery_worker", SRC)
        self.assertIn("recov.join(", SRC)

    def test_shipped_run_loop_starts_recovery_and_site_control(self):
        # The shipped release replaces core.cmd_run with analytics_agent.enhanced_cmd_run.
        # Recovery and Site Control must therefore be composed there, not only in the dormant loop.
        self.assertIn("target=core.recovery_worker", SHIPPED)
        # The startup channel inventory reaches the worker (camera mapping + re-enumeration).
        self.assertIn("args=(cfg, state, cloud, stop, spool, channels, holder)", SHIPPED)
        self.assertIn("recovery.start()", SHIPPED)
        self.assertIn("recovery.join(", SHIPPED)
        self.assertIn("target=core.command_worker", SHIPPED)
        self.assertIn("sitectl.start()", SHIPPED)

    def test_recovery_intervals_use_camera_uuids_not_recorder_channels(self):
        worker = SRC.split("def recovery_worker(", 1)[1].split("def cmd_run(", 1)[0]
        self.assertIn("_recovery_camera_ids(", worker)
        self.assertIn("camera_channels=", worker)
        self.assertNotIn("p_cameras=cams or []", worker,
                         "an empty or channel-number camera list must never be sent")

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

#!/usr/bin/env python3
"""Automatic recovery wiring contract (0.4.4 §1/§2) — the Agent runs recovery itself.

Source contract over watchlog_agent.py: the recovery worker is started as a thread, detects the
outage from persisted last-live on startup, opens a recovery interval, backfills with live
priority + throttle, is gated by recovery_enabled, and is strictly READ-ONLY (never a recorder
write). Persistence of last-live is on the heartbeat cadence only when recorder transport is fresh.

The packaged Agent runs analytics_agent.enhanced_cmd_run (release_agent -> analytics_agent.main
replaces core.cmd_run), so the worker composition is checked by running THAT loop with its workers
recorded; watchlog_agent.cmd_run is the dormant core loop. The archive scan and incident footage are
checked by behaviour too: each reads the archive of the recorder the job names (its routed job_cfg),
through the vendor archive transport. Worker behaviour is covered by test_recovery_rpc_contract.py
and test_worker_exception_survival.py.
"""
from __future__ import annotations

import sys, tempfile, threading, unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
SRC = (ROOT / "agent" / "watchlog_agent.py").read_text(encoding="utf-8")
# Config, the cloud client and the open path moved to agent_core.py (shared with Setup).
CORE_SRC = (ROOT / "agent" / "agent_core.py").read_text(encoding="utf-8")
ANALYTICS = (ROOT / "agent" / "analytics_agent.py").read_text(encoding="utf-8")
SHIPPED = ANALYTICS.split("def enhanced_cmd_run(", 1)[1].split("\ndef ", 1)[0]
NATIVE = (ROOT / "agent" / "native_event_collector.py").read_text(encoding="utf-8")
INCIDENT = (ROOT / "agent" / "incident_evidence.py").read_text(encoding="utf-8")
HIK_ARCHIVE = (ROOT / "agent" / "hikvision_archive.py").read_text(encoding="utf-8")

import analytics_agent  # noqa: E402
import incident_evidence  # noqa: E402
import recorder_runtime  # noqa: E402
import recovery_ai  # noqa: E402
import watchlog_agent as core  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key", "site_id": "site", "tenant_id": "tenant"}
RECORDER = "5e1f0000-0000-4000-8000-000000000001"     # the camera's cloud recorder
CAMERA = "ca3e0000-0000-4000-8000-000000000003"
T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
MP4 = bytes([0, 0, 0, 0x18]) + b"ftypmp42" + b"clip"


def _patched(*patches):
    stack = ExitStack()
    for patch in patches:
        stack.enter_context(patch)
    return stack


class _FakeSpool:
    def __init__(self, *args, **kwargs):
        pass

    def count(self):
        return 0

    def close(self):
        pass


def _run_shipped_loop(tmp: Path, channels, *, prepared=None):
    """Run the real analytics_agent.enhanced_cmd_run until every worker it starts has run once.

    Each worker body is replaced by a recorder of the arguments the shipped loop handed it, so
    this checks what the packaged Agent wires together, not the source text."""
    names = ("collector", "recovery_worker", "command_worker", "health_worker")
    started, ran = {}, threading.Event()

    def recorder(name):
        def worker(*args):
            started[name] = args
            if all(n in started for n in names):
                ran.set()
        return worker

    sleeps = {"n": 0}

    def sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] == 1:
            ran.wait(5)
        else:
            raise KeyboardInterrupt

    cfg = SimpleNamespace(
        state_path=tmp / "agent_state.json", spool_path=tmp / "spool.sqlite", spool_max_rows=0,
        health_batch=4, health_concurrency=2, upload_seconds=15, heartbeat_seconds=3600,
        health_seconds=300, recovery_enabled=True, last_live_path=tmp / "last_live.json",
        recovery_threshold_seconds=180)
    patches = [
        mock.patch.object(analytics_agent.recorder_registry, "data_dir", lambda: tmp),
        mock.patch.object(core.vision, "build", lambda _cfg, _log: None),
        *(mock.patch.object(core, name, recorder(name)) for name in names),
        mock.patch.object(core, "health_cycle", lambda *a, **k: None),
        mock.patch.object(core, "upload_once", lambda *a, **k: 0),
        mock.patch.object(core, "heartbeat", lambda *a, **k: None),
        mock.patch.object(analytics_agent, "analytics_worker", lambda *a, **k: None),
        mock.patch.object(analytics_agent, "archive_worker", lambda *a, **k: None),
        mock.patch.object(analytics_agent, "Spool", _FakeSpool),
        mock.patch.object(analytics_agent.time, "sleep", sleep),
    ]
    if prepared is not None:
        patches += [
            mock.patch.object(analytics_agent.recorder_registry, "recorders",
                              lambda: [{"local_id": "local-1", "is_configured": True}]),
            mock.patch.object(analytics_agent.multi_recorder_orchestrator, "prepare_recorders",
                              lambda *a, **k: [prepared]),
        ]
    with _patched(*patches):
        analytics_agent.enhanced_cmd_run(cfg, STATE, object(), once=False, device=None,
                                         channels=channels)
    return cfg, started


def _bound_recorder(tmp: Path):
    """startup preflight's result for one configured recorder bound to RECORDER."""
    bound = SimpleNamespace(
        nvr_url="http://192.0.2.10", nvr_driver="hikvision", nvr_username="registry-user",
        nvr_password="registry-pw", recorder_local_id="local-1", recorder_cloud_id=RECORDER,
        recorder_display_name="Primary Recorder", recorder_state_dir=tmp,
        spool_path=tmp / "spool.sqlite", health_store_path=tmp / "health.sqlite",
        last_live_path=tmp / "last_live.json")
    return SimpleNamespace(
        context=SimpleNamespace(config=bound, cloud_recorder_id=RECORDER,
                                display_name="Primary Recorder", is_primary=True),
        device=None, channels=[{"channel": "3", "name": "Gate"}],
        camera_mapping={"3": CAMERA}, capabilities=None, error=None)


def _route(job_cfg, routed):
    """config_for_cloud_recorder stand-in: RECORDER's jobs run on job_cfg, nothing else does."""
    def resolve(cfg, recorder_id):
        routed.append(recorder_id)
        if recorder_id != RECORDER:
            raise ValueError("cloud recorder target is not mapped to exactly one local recorder")
        return job_cfg
    return resolve


def _no_live_driver(_cfg):
    raise AssertionError("recorded media must be read through the archive transport")

class RecoveryWiring(unittest.TestCase):
    def test_worker_defined_and_started(self):
        self.assertIn("def recovery_worker(", SRC)
        self.assertIn("target=recovery_worker", SRC)
        self.assertIn("recov.join(", SRC)

    def test_shipped_run_loop_starts_recovery_and_site_control(self):
        # The shipped release replaces core.cmd_run with analytics_agent.enhanced_cmd_run.
        # Recovery and Site Control must therefore be composed there, not only in the dormant loop.
        channels = [{"channel": "3", "name": "Gate"}]
        with tempfile.TemporaryDirectory() as tmp:
            cfg, started = _run_shipped_loop(Path(tmp), channels)
        self.assertIn("recovery_worker", started, "the shipped loop never started recovery")
        self.assertIn("command_worker", started, "the shipped loop never started Site Control")
        run_cfg, state, _cloud, _stop, spool, worker_channels, holder = started["recovery_worker"]
        self.assertIs(run_cfg, cfg)
        self.assertIs(state, STATE)
        # The startup channel inventory reaches the worker (camera mapping + re-enumeration).
        self.assertIs(worker_channels, channels)
        # Recorder liveness and the live backlog come from the collector's own holder and spool.
        _cfg, collector_spool, _stop, collector_holder = started["collector"]
        self.assertIs(holder, collector_holder)
        self.assertIs(spool, collector_spool)
        self.assertIs(started["command_worker"][0], cfg)

    def test_bound_single_recorder_recovery_follows_the_synced_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, started = _run_shipped_loop(Path(tmp), [], prepared=_bound_recorder(Path(tmp)))
        self.assertEqual(cfg.recorder_cloud_id, RECORDER)
        self.assertIs(started["recovery_worker"][0], cfg)
        provider, holder = started["recovery_worker"][5], started["recovery_worker"][6]
        # A bound recorder never guesses channels: the worker reads the inventory the cloud
        # mapped, and follows it when the health cycle re-syncs after a reconnect.
        self.assertTrue(callable(provider))
        self.assertEqual(provider(), [{"channel": "3", "name": "Gate", "camera_id": CAMERA}])
        holder["synced_channels"] = [{"channel": "5", "camera_id": "cam-5"}]
        self.assertEqual(provider(), [{"channel": "5", "camera_id": "cam-5"}])

    def test_packaged_archive_scan_reads_the_cameras_recorder_archive(self):
        self.assertNotIn("retrieve_frames=None, analyze=None", ANALYTICS)
        process_cfg = SimpleNamespace(analytics_config_path=Path("unused.json"),
                                      recovery_ai_max_frames=4)
        job_cfg = SimpleNamespace(nvr_url="http://192.0.2.20")
        opened, routed, decoded = [], [], []

        class Archive:
            name = "hikvision-isapi"
            closed = False

            def enumerate_historical_events(self, channel, start, end, cursor=None, limit=100):
                return {"status": "supported", "next_cursor": None,
                        "events": [{"ts": T0.isoformat(), "segment": {"start": T0.isoformat()}}]}

            def close(self):
                Archive.closed = True

        def recovered_frame(driver, channel, ts, *args, **kwargs):
            decoded.append((driver.name, channel))
            return b"frame"

        config = {"config": {"cameras": [{"id": CAMERA, "recorder_id": RECORDER,
                                          "channel": "3", "rules": []}]}}
        with _patched(
                mock.patch.object(analytics_agent.analytics, "load_config", lambda _p: config),
                mock.patch.object(recorder_runtime, "config_for_cloud_recorder",
                                  _route(job_cfg, routed)),
                mock.patch.object(core, "open_archive_driver",
                                  lambda cfg: opened.append(cfg) or (Archive(), None)),
                mock.patch.object(core, "open_driver", _no_live_driver),
                mock.patch.object(recovery_ai, "recovered_frame", recovered_frame),
                mock.patch.object(core, "log", lambda *_a, **_k: None)):
            retrieve, _analyze = analytics_agent._archive_scan_handlers(
                process_cfg, None, threading.Event())
            frames = retrieve(CAMERA, T0, T0 + timedelta(minutes=10))
        # The camera's own recorder archive, read through the vendor archive transport.
        self.assertEqual(routed, [RECORDER])
        self.assertEqual(opened, [job_cfg])
        self.assertEqual(decoded, [("hikvision-isapi", "3")])
        self.assertEqual([frame for frame, _ts in frames], [b"frame"])
        self.assertTrue(Archive.closed)

    def test_incident_footage_reads_the_requested_recorders_archive(self):
        stop = threading.Event()
        calls, opened, routed = [], [], []
        process_cfg = SimpleNamespace(supabase_url="https://example.invalid",
                                      publishable_key="test", nvr_url="http://192.0.2.10")
        job_cfg = SimpleNamespace(nvr_url="http://192.0.2.20")

        class Cloud:
            def __init__(self, *_args):
                self.claims = 0

            def call(self, name, **kwargs):
                calls.append((name, kwargs))
                if name == "wl_agent_claim_clip_requests":
                    self.claims += 1
                    if self.claims > 1:
                        stop.set()
                        return []
                    return [{"request_id": "clip-1", "channel": "3", "recorder_id": RECORDER,
                             "start_at": T0.isoformat(),
                             "end_at": (T0 + timedelta(seconds=30)).isoformat()}]
                return {"ok": True}

        class Archive:
            name = "hikvision-isapi"

            def get_clip(self, channel, start, end):
                return MP4

            def close(self):
                pass

        with _patched(
                mock.patch.object(core, "Cloud", Cloud),
                mock.patch.object(recorder_runtime, "config_for_cloud_recorder",
                                  _route(job_cfg, routed)),
                mock.patch.object(core, "open_archive_driver",
                                  lambda cfg: opened.append(cfg) or (Archive(), None)),
                mock.patch.object(core, "open_driver", _no_live_driver),
                mock.patch.object(core, "log", lambda *_a, **_k: None)):
            incident_evidence.footage_worker(process_cfg, STATE, stop)
        # The clip comes from the requested recorder's archive transport, never another recorder.
        self.assertEqual(routed, [RECORDER])
        self.assertEqual(opened, [job_cfg])
        names = [name for name, _kwargs in calls]
        self.assertIn("wl_agent_complete_clip", names)
        self.assertNotIn("wl_agent_fail_clip", names)

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

    def test_runtime_heartbeat_does_not_fake_recorder_seen(self):
        self.assertIn("recorder_live=recorder_live", ANALYTICS)
        self.assertIn("recorder_is_live", SRC)

    def test_hikvision_clip_path_has_total_time_budget(self):
        self.assertIn("CLIP_TOTAL_SECONDS = 90", HIK_ARCHIVE)
        self.assertIn("MAX_DOWNLOAD_CANDIDATES = 2", HIK_ARCHIVE)
        self.assertIn("deadline = time.monotonic() + CLIP_TOTAL_SECONDS", HIK_ARCHIVE)

    def test_default_on_but_disable_flag_exists(self):
        self.assertIn('get("recovery_enabled") or "true"', CORE_SRC)   # ON by default, disable-able


if __name__ == "__main__":
    unittest.main(verbosity=2)

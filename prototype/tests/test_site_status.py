#!/usr/bin/env python3
"""0.4.4 P1 — Site Status model (the panel's business logic, Qt-free).

Proves the honest classification the customer panel depends on: an unused channel is never a camera
failure, a metric the recorder can't expose renders as "Not available on this recorder" (never a
guess), archive/recovery states fold correctly, and summaries read right.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import io  # noqa: E402
import json  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import site_status as ss  # noqa: E402
import watchlog_agent as wa  # noqa: E402


class CameraView(unittest.TestCase):
    def test_unused_channel_is_not_a_failure(self):
        channels = [{"channel": "1", "name": "Reception"}, {"channel": "5", "name": "Armory"},
                    {"channel": "2", "name": "Spare"}]
        configured = {"1": True, "5": True, "2": False}          # ch2 intentionally unused
        health = {"1": {"health_state": "operational", "recording_state": "recording"},
                  "5": {"health_state": "offline", "recording_state": "unknown"}}
        v = ss.camera_view(channels, configured=configured, health=health)
        by = {c["channel"]: c for c in v["cameras"]}
        self.assertEqual(by["2"]["status"], "unused")
        self.assertEqual(v["summary"]["offline"], 1)             # only the armory, NOT the spare
        self.assertEqual(v["summary"]["unused"], 1)
        self.assertEqual(v["summary"]["monitored"], 1)
        self.assertEqual(v["summary"]["configured_total"], 2)
        self.assertEqual(v["summary"]["headline"], "1/2 monitored")

    def test_missing_configured_map_defaults_monitored(self):
        v = ss.camera_view([{"channel": "1", "name": "Cam"}], health={"1": {"health_state": "operational"}})
        self.assertEqual(v["cameras"][0]["status"], "monitored")


class RecordingView(unittest.TestCase):
    def test_only_configured_and_counts(self):
        cams = [{"channel": "1", "name": "A", "configured": True},
                {"channel": "2", "name": "B", "configured": False},
                {"channel": "3", "name": "C", "configured": True}]
        v = ss.recording_view(cams, recording={"1": "verified", "3": "warning"})
        self.assertEqual(len(v["rows"]), 2)                      # ch2 (unused) excluded
        self.assertEqual(v["summary"], {"verified": 1, "warning": 1, "unknown": 0})


class ArchiveView(unittest.TestCase):
    def test_verified_available(self):
        v = ss.archive_view(proof_status="verified", frame_decoded=True, last_proof_at="t")
        self.assertEqual(v["archive_access"], "verified")
        self.assertEqual(v["recovery"], "available")

    def test_verified_but_frame_not_decoded(self):
        v = ss.archive_view(proof_status="verified", frame_decoded=False)
        self.assertEqual(v["archive_access"], "available_frame_unverified")

    def test_empty_unsupported_unknown_failed(self):
        self.assertEqual(ss.archive_view(proof_status="empty")["archive_access"], "empty")
        self.assertEqual(ss.archive_view(proof_status="unsupported")["recovery"], "unsupported")
        self.assertEqual(ss.archive_view(proof_status="unknown")["archive_access"], "unknown")
        self.assertEqual(ss.archive_view(proof_status="error")["archive_access"], "failed")

    def test_backlog_becomes_pending(self):
        v = ss.archive_view(proof_status="verified", recovery_backlog=3)
        self.assertEqual(v["recovery"], "pending")
        self.assertEqual(v["recovery_backlog"], 3)


class StorageView(unittest.TestCase):
    def test_missing_fields_are_not_available(self):
        v = ss.storage_view({"health": "healthy", "total_gb": 2000})
        self.assertEqual(v["health"], "healthy")
        self.assertEqual(v["total_gb"], 2000)
        self.assertEqual(v["free_gb"], ss.NA)
        self.assertEqual(v["retention_days"], ss.NA)
        self.assertEqual(v["oldest_recording"], ss.NA)

    def test_no_storage_is_unavailable(self):
        v = ss.storage_view(None)
        self.assertEqual(v["health"], "unavailable")
        self.assertEqual(v["oldest_recording"], ss.NA)


class AgentAndRecorderView(unittest.TestCase):
    def test_agent_view(self):
        v = ss.agent_view(build_meta={"version": "0.4.4", "build_sha": "abc1234"}, channel="pilot",
                          state={"agent_id": "a1"}, running=True, cloud_ok=True,
                          spool_backlog=7, recovery_backlog=2)
        self.assertEqual(v["running"], "running")
        self.assertEqual(v["enrollment"], "enrolled")
        self.assertEqual(v["cloud"], "connected")
        self.assertEqual(v["spool_backlog"], 7)
        self.assertEqual(v["channel"], "pilot")

    def test_agent_view_unknowns(self):
        v = ss.agent_view(build_meta={"version": "0.4.4", "build_sha": ""}, state={})
        self.assertEqual(v["running"], "unknown")
        self.assertEqual(v["enrollment"], "not_enrolled")
        self.assertIsNone(v["build_sha"])

    def test_recorder_view_na(self):
        v = ss.recorder_view(reachable=True, auth_ok=True,
                             info={"vendor": "Dahua", "model": "XVR", "driver": "dahua"},
                             capability={"segments": "supported"})
        self.assertEqual(v["connection"], "connected")
        self.assertEqual(v["archive_capability"], "supported")
        self.assertEqual(v["time_offset_seconds"], ss.NA)       # not probed here


class BuildSnapshot(unittest.TestCase):
    def test_assembles_all_sections(self):
        snap = ss.build_snapshot(agent={"a": 1}, recorder={"r": 1}, camera={"c": 1},
                                 recording={"rec": 1}, archive={"ar": 1}, storage={"s": 1},
                                 generated_at="2026-09-14T00:00:00Z")
        self.assertEqual(snap["schema"], ss.SCHEMA)
        for k in ("agent", "recorder", "cameras", "recording", "archive", "storage"):
            self.assertIn(k, snap)


class _FakeSpool:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n

    def close(self):
        pass


class _FakeDriver:
    name = "dahua"

    def __init__(self, channels=(("1", "Reception"), ("2", "Spare"))):
        self._channels = channels

    def list_channels(self):
        return [SimpleNamespace(channel=c, name=n) for c, n in self._channels]

    def historical_capability(self):
        return {"segments": "supported", "events": "supported", "snapshots": "unsupported"}

    def close(self):
        pass


def _status_cfg(**over):
    cfg = dict(state_path=Path("/nonexistent/s.json"), spool_path=Path("/nonexistent/spool.db"),
               spool_max_rows=0, supabase_url="https://x.supabase.co", publishable_key="pk",
               nvr_driver="dahua", update_channel="pilot",
               camera_profiles=[{"channel": "1", "name": "Reception Main Entrance", "monitored": True},
                                {"channel": "2", "name": "Spare", "monitored": False}])
    cfg.update(over)
    return SimpleNamespace(**cfg)


def _run_status(cfg, **over):
    kwargs = dict(
        _state={"agent_id": "a1", "site_id": "s1", "agent_key": "k1"},
        _open_driver=lambda c: (_FakeDriver(), SimpleNamespace(vendor="Dahua", model="XVR-Test")),
        _cloud_factory=lambda: object(),
        _heartbeat=lambda c, s, d: None,
        _spool_factory=lambda: _FakeSpool(3),
        _archive=lambda driver, channel: {"status": "verified"},
    )
    kwargs.update(over)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = wa.cmd_status_json(cfg, **kwargs)
    out = buf.getvalue()
    snap = json.loads(out.split("STATUS_JSON ", 1)[1].splitlines()[0])
    return code, snap


class CmdStatusJson(unittest.TestCase):
    def test_healthy_snapshot(self):
        code, snap = _run_status(_status_cfg())
        self.assertEqual(code, 0)
        self.assertEqual(snap["schema"], ss.SCHEMA)
        self.assertEqual(snap["agent"]["enrollment"], "enrolled")
        self.assertEqual(snap["agent"]["cloud"], "connected")
        self.assertEqual(snap["agent"]["channel"], "pilot")
        self.assertEqual(snap["agent"]["spool_backlog"], 3)
        self.assertEqual(snap["recorder"]["connection"], "connected")
        self.assertEqual(snap["recorder"]["archive_capability"], "supported")
        self.assertEqual(snap["archive"]["archive_access"], "verified")
        by = {c["channel"]: c for c in snap["cameras"]["cameras"]}
        self.assertEqual(by["2"]["status"], "unused")            # local Ignore classification honored
        self.assertTrue(by["1"]["configured"])
        self.assertEqual(by["1"]["name"], "Reception Main Entrance")

    def test_recorder_down_is_honest(self):
        def boom(cfg):
            raise RuntimeError("connection refused")
        code, snap = _run_status(_status_cfg(), _open_driver=boom)
        self.assertEqual(code, 0)
        self.assertEqual(snap["recorder"]["connection"], "disconnected")
        self.assertEqual(snap["archive"]["archive_access"], "unknown")
        self.assertEqual(snap["storage"]["oldest_recording"], ss.NA)

    def test_not_enrolled_no_cloud_probe(self):
        code, snap = _run_status(_status_cfg(), _state={})
        self.assertEqual(snap["agent"]["enrollment"], "not_enrolled")
        self.assertEqual(snap["agent"]["cloud"], "unknown")     # never probed without identity

    def test_storage_na_by_default(self):
        _code, snap = _run_status(_status_cfg())
        self.assertEqual(snap["storage"]["retention_days"], ss.NA)    # fast path: not probed
        self.assertEqual(snap["storage"]["oldest_recording"], ss.NA)

    def test_retention_surfaced_when_measured(self):
        ret = {"status": "measured", "retention_days": 10.2, "oldest_recording": "2026-09-04T11:12:00+00:00"}
        _code, snap = _run_status(_status_cfg(), _retention=ret)
        self.assertEqual(snap["storage"]["retention_days"], 10.2)
        self.assertEqual(snap["storage"]["oldest_recording"], "2026-09-04T11:12:00+00:00")


class CmdRecheckArchive(unittest.TestCase):
    def _run(self, **over):
        kwargs = dict(
            _open_driver=lambda c: (_FakeDriver(), SimpleNamespace(vendor="Dahua", model="XVR")),
            _archive=lambda driver, ch: {"status": "verified",
                                         "sample": [{"start": "2026-09-14T22:00:00+00:00"}]},
            _inspect=lambda driver, ch, ts: (b"JPEG", {"kind": "dav", "media_size": 44, "decoded": True}),
        )
        kwargs.update(over)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = wa.cmd_recheck_archive_json(_status_cfg(), **kwargs)
        rep = json.loads(buf.getvalue().split("ARCHIVE_JSON ", 1)[1].splitlines()[0])
        return code, rep

    def test_verified_with_decoded_frame(self):
        _c, rep = self._run()
        self.assertEqual(rep["state"], "ARCHIVE VERIFIED")
        self.assertTrue(rep["frame_decoded"])
        self.assertEqual(rep["diagnostics"]["kind"], "dav")

    def test_available_frame_unverified(self):
        _c, rep = self._run(_inspect=lambda d, c, t: (None, {"kind": "dav", "decoded": False}))
        self.assertEqual(rep["state"], "ARCHIVE AVAILABLE — FRAME DECODE UNVERIFIED")
        self.assertFalse(rep["frame_decoded"])

    def test_empty_and_unsupported(self):
        self.assertEqual(self._run(_archive=lambda d, c: {"status": "empty"})[1]["state"], "ARCHIVE EMPTY")
        self.assertEqual(self._run(_archive=lambda d, c: {"status": "unsupported"})[1]["state"],
                         "ARCHIVE UNSUPPORTED")

    def test_recorder_down_is_failed(self):
        def boom(c):
            raise RuntimeError("refused")
        self.assertEqual(self._run(_open_driver=boom)[1]["state"], "ARCHIVE FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=2)

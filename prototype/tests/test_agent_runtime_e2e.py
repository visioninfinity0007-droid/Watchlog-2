#!/usr/bin/env python3
"""End-to-end agent-runtime orchestration test. Drives the REAL AgentRuntime (composing the real
AnalyticsEngine + LeaseClient + ActionRuntime + ArchiveRuntime) with synthetic frames/detections
and a fake cloud transport — proving the whole flow, not just isolated modules:

  config -> frame -> detection -> temporal rule -> analytic event -> operations action
  archive request -> claim -> process -> recovered result
  lease -> authority -> fencing (no dual-active writes)

Also proves: action dispatch is idempotent + restart-safe, standby/superseded agents suppress
authoritative writes, and a cloud outage leaves events spooled (no loss, no fabrication)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from runtime import AgentRuntime            # noqa: E402
from analytics import AnalyticsEngine       # noqa: E402
from lease_client import LeaseClient        # noqa: E402
from action_runtime import ActionRuntime    # noqa: E402
from archive_runtime import ArchiveRuntime  # noqa: E402

STATE = {"agent_id": "A", "agent_key": "k"}
ZONE = [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]


class D:
    def __init__(self, label, x, y, confidence=0.9, w=20, h=20):
        self.label = label
        self.confidence = confidence
        self.box = (x - w // 2, y - h // 2, x + w // 2, y + h // 2)


class FakeSpool:
    def __init__(self):
        self.rows = []
        self._id = 0

    def add(self, ev):
        self._id += 1
        self.rows.append((self._id, ev))

    def take(self, batch):
        sel = self.rows[:batch]
        return [i for i, _ in sel], [r for _, r in sel]

    def ack(self, ids):
        s = set(ids)
        self.rows = [(i, r) for i, r in self.rows if i not in s]

    def count(self):
        return len(self.rows)


class FakeLeaseServer:
    def __init__(self, lease_seconds=90):
        self.holder = None
        self.generation = 0
        self.expires_at = 0.0
        self.clock = 0.0
        self.lease_seconds = lease_seconds

    def tick(self, dt):
        self.clock += dt

    def acquire(self, agent, lease_seconds):
        expired = self.holder is None or self.clock >= self.expires_at
        if self.holder is not None and self.holder != agent and not expired:
            return {"granted": False, "generation": self.generation, "mode": "standby"}
        gen = self.generation if (self.holder == agent and not expired) else self.generation + 1
        self.holder, self.generation, self.expires_at = agent, gen, self.clock + lease_seconds
        return {"granted": True, "generation": gen, "mode": "primary"}

    def is_authority(self, agent, generation):
        return self.holder == agent and self.generation == generation and self.clock < self.expires_at


class FakeCloud:
    def __init__(self, lease_server=None, fail_ingest=False, scans=None):
        self.lease = lease_server
        self.fail_ingest = fail_ingest
        self.scans = scans or []
        self.ingested = []
        self.archive_recorded = []
        self.archive_status = []

    def call(self, fn, **kw):
        if fn == "wl_ingest_analytic_events":
            if self.fail_ingest:
                raise RuntimeError("cloud down")
            self.ingested.extend(kw["p_events"])
            return {"inserted": len(kw["p_events"])}
        if fn == "wl_agent_acquire_lease":
            return self.lease.acquire(kw["p_agent_id"], kw["p_lease_seconds"])
        if fn == "wl_agent_is_fenced_authority":
            return self.lease.is_authority(kw["p_agent_id"], kw["p_generation"])
        if fn == "wl_agent_claim_archive_scans":
            return self.scans
        if fn == "wl_agent_record_archive_result":
            self.archive_recorded.append(kw)
            return {"provenance_label": "Recovered from recorder archive"}
        if fn == "wl_agent_set_archive_scan_status":
            self.archive_status.append(kw)
            return {"status": kw["p_status"]}
        raise KeyError(fn)


def engine_with(*rules):
    cfg = {"timezone": "Asia/Karachi", "schedules": [],
           "cameras": [{"id": "cam-1", "channel": "1", "analytics_enabled": True, "rules": list(rules)}]}
    eng = AnalyticsEngine(log=lambda _m: None)
    eng.configure(cfg)
    return eng


EXC_RULE = {"id": "r1", "camera_id": "cam-1", "channel": "1", "rule_type": "zone_entry",
            "object_classes": ["person"], "enabled": True, "geometry": {"points": ZONE},
            "schedule_id": None, "severity": "attention", "cooldown_seconds": 300,
            "actions": [{"type": "capture_still"}, {"type": "request_footage"}]}


class RuntimeE2E(unittest.TestCase):
    def _actions(self):
        self.stills = []
        self.footage = []
        return ActionRuntime(snapshot=lambda ch: b"jpeg-bytes",
                             upload_still=lambda cam, b64: self.stills.append(cam) or "still-ref",
                             request_footage=lambda inc: self.footage.append(inc) or {"status": "pending", "request_id": "f1"})

    def test_pipeline_frame_to_event_to_action_and_idempotent(self):
        clockbox = {"t": 1000.0}
        with tempfile.TemporaryDirectory() as d:
            rt = AgentRuntime(cloud=FakeCloud(), state=STATE, engine=engine_with(EXC_RULE),
                              actions=self._actions(), dedup_path=Path(d) / "dedup.json",
                              clock=lambda: clockbox["t"])
            spool = FakeSpool()
            t0 = datetime.now(timezone.utc)
            rt.on_frame("1", [D("person", 150, 500)], (1000, 1000), t0)                 # outside
            evs = rt.on_frame("1", [D("person", 300, 500)], (1000, 1000), t0 + timedelta(seconds=1))  # enters
            for e in evs:
                spool.add(e)
            self.assertEqual(1, len(evs))
            self.assertEqual("zone_entry", evs[0]["event_type"])
            self.assertEqual(0.9, evs[0]["metadata"]["confidence"])          # confidence carried
            self.assertEqual(["cam-1"], self.stills)                          # still captured
            self.assertEqual(1, len(self.footage))                           # bounded footage requested once
            # a SECOND entry of the same condition within cooldown must NOT re-dispatch actions
            rt.on_frame("1", [D("person", 150, 500)], (1000, 1000), t0 + timedelta(seconds=2))   # exits
            rt.on_frame("1", [D("person", 300, 500)], (1000, 1000), t0 + timedelta(seconds=3))   # re-enters (fires event)
            self.assertEqual(["cam-1"], self.stills)                          # still just one still
            self.assertEqual(1, len(self.footage))                           # still just one footage request

    def test_action_dispatch_restart_safe(self):
        clockbox = {"t": 2000.0}
        with tempfile.TemporaryDirectory() as d:
            dedup = Path(d) / "dedup.json"
            eng = engine_with(EXC_RULE)
            rt = AgentRuntime(cloud=FakeCloud(), state=STATE, engine=eng, actions=self._actions(),
                              dedup_path=dedup, clock=lambda: clockbox["t"])
            t = datetime.now(timezone.utc)
            rt.on_frame("1", [D("person", 150, 500)], (1000, 1000), t)
            rt.on_frame("1", [D("person", 300, 500)], (1000, 1000), t + timedelta(seconds=1))
            self.assertEqual(1, len(self.footage))
            # RESTART: fresh runtime + engine, SAME dedup file, within cooldown -> no re-request
            rt2 = AgentRuntime(cloud=FakeCloud(), state=STATE, engine=engine_with(EXC_RULE),
                               actions=self._actions(), dedup_path=dedup, clock=lambda: clockbox["t"] + 10)
            rt2.on_frame("1", [D("person", 150, 500)], (1000, 1000), t + timedelta(seconds=5))
            rt2.on_frame("1", [D("person", 300, 500)], (1000, 1000), t + timedelta(seconds=6))
            self.assertEqual(0, len(self.footage))     # rt2's footage list — not re-requested after restart

    def test_lease_fencing_suppresses_authoritative_writes(self):
        srv = FakeLeaseServer()
        cloudA, cloudB = FakeCloud(srv), FakeCloud(srv)
        leaseA = LeaseClient(cloudA, "A", "k", feature_enabled=True, lease_seconds=90)
        leaseB = LeaseClient(cloudB, "B", "k", feature_enabled=True, lease_seconds=90)
        rtA = AgentRuntime(cloud=cloudA, state={"agent_id": "A", "agent_key": "k"}, engine=engine_with(), lease=leaseA)
        rtB = AgentRuntime(cloud=cloudB, state={"agent_id": "B", "agent_key": "k"}, engine=engine_with(), lease=leaseB)
        rtA.refresh_lease()
        self.assertTrue(rtA.authoritative())                 # A primary
        rtB.refresh_lease()
        self.assertFalse(rtB.authoritative())                # B standby -> fenced
        # A's lease expires; B takes over (generation N+1)
        srv.tick(120)
        rtB.refresh_lease()
        self.assertTrue(rtB.authoritative())
        # stale A is now superseded -> writes fenced; feed A spooled events and prove upload is suppressed
        spool = FakeSpool()
        spool.add({"e": 1})
        self.assertEqual(0, rtA.upload_analytics(spool))     # suppressed
        self.assertEqual(1, spool.count())                   # events retained, not lost
        self.assertGreaterEqual(rtA.suppressed_writes, 1)
        self.assertFalse(rtA.authoritative())

    def test_two_agent_takeover_full_fencing_sequence(self):
        """A primary (gen 1) -> B standby -> A lease expires -> B takeover (gen 2) -> stale A
        (still gen 1) fenced out of every authoritative write -> B expires -> A re-acquires
        (gen 3). The fencing token is monotonic and no event is lost or duplicated."""
        srv = FakeLeaseServer()
        cloudA, cloudB = FakeCloud(srv), FakeCloud(srv)
        leaseA = LeaseClient(cloudA, "A", "k", feature_enabled=True)
        leaseB = LeaseClient(cloudB, "B", "k", feature_enabled=True)
        rtA = AgentRuntime(cloud=cloudA, state={"agent_id": "A", "agent_key": "k"}, engine=engine_with(), lease=leaseA)
        rtB = AgentRuntime(cloud=cloudB, state={"agent_id": "B", "agent_key": "k"}, engine=engine_with(), lease=leaseB)

        rtA.refresh_lease()
        self.assertTrue(rtA.authoritative())
        self.assertEqual(1, leaseA.generation)                 # A holds fencing token gen 1
        rtB.refresh_lease()
        self.assertFalse(rtB.authoritative())                  # B standby, not authoritative
        spA = FakeSpool(); spA.add({"e": "a1"})
        self.assertEqual(1, rtA.upload_analytics(spA))         # A writes as authority

        srv.tick(120)                                          # A dies; its 90s lease expires
        rtB.refresh_lease()
        self.assertTrue(rtB.authoritative())
        self.assertEqual(2, leaseB.generation)                # B takes over, token bumps to gen 2

        spA.add({"e": "a2"})                                  # stale A (still believes gen 1) tries to write
        self.assertEqual(0, rtA.upload_analytics(spA))        # fenced out
        self.assertEqual(1, spA.count())                      # a2 retained (not lost)
        self.assertFalse(rtA.authoritative())
        spB = FakeSpool(); spB.add({"e": "b1"})
        self.assertEqual(1, rtB.upload_analytics(spB))        # B is the sole writer

        srv.tick(120)                                         # B's lease lapses; A returns
        rtA.refresh_lease()
        self.assertTrue(rtA.authoritative())
        self.assertEqual(3, leaseA.generation)               # monotonic: gen 3, never reused
        self.assertEqual(1, rtA.upload_analytics(spA))        # a2 finally lands, exactly once
        self.assertEqual([{"e": "a1"}, {"e": "a2"}], cloudA.ingested)   # no loss, no duplicate
        self.assertEqual([{"e": "b1"}], cloudB.ingested)

    def test_recorder_loss_and_recovery_survived(self):
        """A recorder outage (no frames -> empty detections) must not crash the orchestration or
        fabricate events; when the recorder recovers the pipeline resumes and fires normally."""
        clockbox = {"t": 5000.0}
        with tempfile.TemporaryDirectory() as d:
            rt = AgentRuntime(cloud=FakeCloud(), state=STATE, engine=engine_with(EXC_RULE),
                              actions=self._actions(), dedup_path=Path(d) / "dd.json",
                              clock=lambda: clockbox["t"])
            t = datetime.now(timezone.utc)
            # recorder outage: empty frames -> no events, no actions, no exception, no fabrication
            self.assertEqual([], rt.on_frame("1", [], (1000, 1000), t))
            self.assertEqual([], rt.on_frame("1", [], (1000, 1000), t + timedelta(seconds=1)))
            self.assertEqual([], self.footage)
            # recorder recovers: a real entry fires and dispatches evidence exactly once
            rt.on_frame("1", [D("person", 150, 500)], (1000, 1000), t + timedelta(seconds=2))   # outside
            evs = rt.on_frame("1", [D("person", 300, 500)], (1000, 1000), t + timedelta(seconds=3))  # enters
            self.assertEqual(1, len(evs))
            self.assertEqual(1, len(self.footage))

    def test_cloud_loss_keeps_events_spooled(self):
        rt = AgentRuntime(cloud=FakeCloud(fail_ingest=True), state=STATE, engine=engine_with())
        spool = FakeSpool()
        spool.add({"e": 1})
        spool.add({"e": 2})
        with self.assertRaises(RuntimeError):
            rt.upload_analytics(spool)                       # cloud down
        self.assertEqual(2, spool.count())                   # nothing acked/lost -> retried later

    def test_archive_background_bounded_and_provenance(self):
        scans = [{"scan_id": "s1", "camera_ids": ["cam-1"], "rule_ids": None,
                  "from_ts": "2026-09-01T00:00:00Z", "to_ts": "2026-09-01T00:30:00Z"}]
        cloud = FakeCloud(scans=scans)
        archive = ArchiveRuntime(cloud=cloud, state=STATE,
                                 retrieve_frames=lambda cam, a, b: [(b"j", "2026-09-01T00:05:00Z")],
                                 analyze=lambda cam, j, ts, r: [{"result_type": "zone_entry", "recovered_at": ts, "confidence": 0.7}])
        rt = AgentRuntime(cloud=FakeCloud(), state=STATE, engine=engine_with(), archive=archive)
        out = rt.poll_archive()
        self.assertEqual("complete", out[0]["status"])
        self.assertEqual(1, len(cloud.archive_recorded))
        self.assertEqual({"source": "archive"}, cloud.archive_recorded[0]["p_detail"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

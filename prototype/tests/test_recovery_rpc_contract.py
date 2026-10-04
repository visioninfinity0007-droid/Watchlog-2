#!/usr/bin/env python3
"""recovery_worker against the REAL recovery RPC signatures (MNVR-006, MNVR-007).

wl_open_recovery_interval takes p_cameras uuid[] (recovery_intervals.cameras is uuid[]). The Agent
used to send the startup recorder channel numbers ('1'..'8'), which PostgREST cannot cast to uuid
(22P02), so every interval open failed and the error was only logged. When the startup inventory
was empty it sent [] instead, and the runner then read only a guessed channel "1" and completed
the whole-site interval as recovered.

StrictCloud reads the latest CREATE FUNCTION for each recovery RPC from the migrations, rejects
unknown parameters, and rejects a non-UUID element in a uuid[] parameter exactly as PostgREST does.
"""
from __future__ import annotations

import re
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import backfill  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402

MIGRATIONS = ROOT / "supabase" / "migrations"
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
CHANNELS = [{"channel": "1", "name": "Gate"}, {"channel": "3", "name": "Yard"}]
CAMERA_IDS = {"1": "11111111-1111-4111-8111-111111111111",
              "3": "33333333-3333-4333-8333-333333333333"}
T0 = datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc)


def _latest_params(fn: str) -> dict:
    """{param: sql type} from the newest migration that (re)defines ``fn``."""
    pattern = re.compile(
        r"create\s+or\s+replace\s+function\s+(?:public\.)?" + fn + r"\s*\((.*?)\)\s*returns",
        re.S | re.I)
    signature = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            signature = match.group(1)
    assert signature is not None, f"{fn} is not defined by any migration"
    params = {}
    for part in signature.split(","):
        bits = part.split()
        if len(bits) >= 2 and bits[0].lower().startswith("p_"):
            params[bits[0].lower()] = bits[1].lower()
    return params


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


class StrictCloud:
    """Recovery RPCs with PostgREST argument checking and an in-memory 0098 ledger."""

    def __init__(self, sync_failures=0):
        self.sync_failures = sync_failures
        self.calls, self.rejected, self.opened, self.completes = [], [], [], []
        self.intervals = []

    def _check(self, fn, params):
        sig = _latest_params(fn)
        unknown = set(params) - set(sig)
        if unknown:
            raise core.CloudError(fn, 404, "PGRST202", f"no {fn} with params {sorted(unknown)}")
        for name, sql_type in sig.items():
            if sql_type == "uuid[]" and name in params:
                bad = [v for v in params[name] or [] if not _is_uuid(v)]
                if bad:
                    self.rejected.append((fn, name, bad))
                    raise core.CloudError(fn, 400, "22P02",
                                          f'invalid input syntax for type uuid: "{bad[0]}"')

    def call(self, fn, **params):
        self.calls.append(fn)
        if fn == "wl_sync_cameras":
            if self.sync_failures:
                self.sync_failures -= 1
                raise requests.ConnectionError("network not ready")
            assert params["p_cameras"], "the camera sync payload must not be empty"
            return {c["channel"]: CAMERA_IDS[c["channel"]] for c in params["p_cameras"]}
        self._check(fn, params)
        if fn == "wl_open_recovery_interval":
            iv = {"id": f"iv-{len(self.intervals) + 1}", "started_at": params["p_started_at"],
                  "ended_at": params["p_ended_at"], "cameras": list(params["p_cameras"]),
                  "checkpoint": {}, "attempts": 0, "status": "pending"}
            self.intervals.append(iv)
            self.opened.append(iv)
            return {"ok": True, "id": iv["id"], "status": "pending"}
        if fn == "wl_agent_claim_recovery":
            due = [iv for iv in self.intervals if iv["status"] == "pending"][:params["p_limit"]]
            for iv in due:
                iv["status"], iv["attempts"] = "in_progress", iv["attempts"] + 1
            return [dict(iv) for iv in due]
        if fn == "wl_complete_recovery":
            self.completes.append(params)
            for iv in self.intervals:
                if iv["id"] == params["p_id"]:
                    iv["status"], iv["checkpoint"] = params["p_status"], params["p_checkpoint"]
            return {"ok": True}
        raise AssertionError(fn)


class _Spool:
    def __init__(self, gap=None):
        self.gap, self.added = gap, []

    def pending_recovery_gap(self):
        return self.gap

    def clear_recovery_gap(self, *gap):
        if tuple(gap) == tuple(self.gap or ()):
            self.gap = None
            return True
        return False

    def count(self):
        return 0

    def add(self, event):
        self.added.append(event)


class _Archive(backfill.ReferenceArchiveDriver):
    def __init__(self):
        super().__init__([{"ts": (T0 + timedelta(minutes=10)).isoformat(), "type": "person",
                           "device_event_id": "E1"}], page_size=10)
        self.channels = []

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.channels.append(str(channel))
        return super().enumerate_historical_events(channel, start, end, cursor, limit)

    def close(self):
        pass


class _Cycles(threading.Event):
    """A stop event that ends recovery_worker after ``cycles`` loop iterations, synchronously."""

    def __init__(self, cycles, between=None):
        super().__init__()
        self.left = cycles + 1                     # +1 for the start-up settle wait
        self.between, self.waits = between, 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.between and self.waits > 1:
            self.between()                         # what other threads do between cycles
        self.left -= 1
        if self.left <= 0:
            self.set()
        return self.is_set()


class _Patch:
    def __init__(self, module, **attrs):
        self.module, self.attrs, self.saved = module, attrs, {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, value)

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)


class _Recorder:
    """open_driver stand-in for re-enumeration; channels=None means still unreachable."""

    def __init__(self, channels=None):
        self.channels, self.opens = channels, 0

    def open(self, _cfg):
        self.opens += 1
        if self.channels is None:
            raise core.DriverError("recorder unreachable")
        chans = [SimpleNamespace(channel=c["channel"], name=c["name"]) for c in self.channels]
        return SimpleNamespace(list_channels=lambda: chans, close=lambda: None), None


class RecoveryWorkerRpcContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = SimpleNamespace(
            recovery_enabled=True, recovery_seconds=300, recovery_ai_enabled=False,
            last_live_path=Path(self.tmp.name) / "last_live.json",
            recovery_threshold_seconds=180, recovery_chunk_seconds=3600,
            recovery_throttle_seconds=0.0, recovery_live_backlog=500,
            recovery_ai_max_frames=40, recovery_snapshot_seconds=300,
            nvr_driver="hikvision-isapi", nvr_url="http://192.0.2.10",
            nvr_username="local-user", nvr_password="local-password")
        self.archive = _Archive()

    def tearDown(self):
        self.tmp.cleanup()

    def _work(self, cloud, spool, channels, cycles=1, recorder=None, holder=None, between=None):
        recorder = recorder or _Recorder()
        if holder is None:
            holder = {"recorder_live_at": __import__("time").monotonic()}
        with _Patch(core, open_archive_driver=lambda _cfg: (self.archive, None),
                    open_driver=recorder.open, log=lambda *_a: None):
            core.recovery_worker(self.cfg, STATE, cloud, _Cycles(cycles, between), spool,
                                 channels, holder)
        return holder

    def test_signature_under_test_is_uuid_array(self):
        self.assertEqual(_latest_params("wl_open_recovery_interval")["p_cameras"], "uuid[]")

    def test_spool_overflow_opens_with_camera_uuids_and_recovers_their_channels(self):
        gap = ((T0).isoformat(), (T0 + timedelta(hours=1)).isoformat())
        cloud, spool = StrictCloud(), _Spool(gap)
        self._work(cloud, spool, CHANNELS)
        self.assertEqual(cloud.rejected, [], "the Agent sent a value PostgREST rejects")
        self.assertEqual(len(cloud.opened), 1)
        self.assertEqual(sorted(cloud.opened[0]["cameras"]), sorted(CAMERA_IDS.values()))
        self.assertIsNone(spool.gap, "the overflow marker is cleared once the interval opened")
        # The claimed interval is read by recorder channel, never by camera UUID.
        self.assertEqual(set(self.archive.channels), {"1", "3"})
        self.assertEqual(cloud.completes[-1]["p_status"], "recovered")

    def test_restart_gap_opens_with_camera_uuids(self):
        recovery.persist_last_live(self.cfg.last_live_path, datetime.now(timezone.utc)
                                   - timedelta(hours=2))
        cloud = StrictCloud()
        self._work(cloud, _Spool(), CHANNELS)
        self.assertEqual(cloud.rejected, [])
        self.assertEqual(len(cloud.opened), 1)
        self.assertEqual(sorted(cloud.opened[0]["cameras"]), sorted(CAMERA_IDS.values()))
        last = recovery.read_last_live(self.cfg.last_live_path)
        self.assertLess(datetime.now(timezone.utc) - last, timedelta(minutes=1))

    def test_without_a_camera_mapping_opening_is_deferred_not_lost(self):
        gap = ((T0).isoformat(), (T0 + timedelta(hours=1)).isoformat())
        recovery.persist_last_live(self.cfg.last_live_path, datetime.now(timezone.utc)
                                   - timedelta(hours=2))
        cloud, spool = StrictCloud(sync_failures=1), _Spool(gap)
        self._work(cloud, spool, CHANNELS, cycles=1)
        self.assertEqual(cloud.opened, [], "no interval may open without camera UUIDs")
        self.assertNotIn("wl_agent_claim_recovery", cloud.calls)
        self.assertEqual(spool.gap, gap, "the overflow marker survives until it can be opened")
        # Next cycle the mapping exists: both the overflow and the restart gap open with UUIDs.
        self._work(cloud, spool, CHANNELS, cycles=1)
        self.assertEqual(cloud.rejected, [])
        self.assertEqual(len(cloud.opened), 2)
        for iv in cloud.opened:
            self.assertEqual(sorted(iv["cameras"]), sorted(CAMERA_IDS.values()))

    def test_empty_inventory_never_opens_an_interval(self):
        gap = ((T0).isoformat(), (T0 + timedelta(hours=1)).isoformat())
        recovery.persist_last_live(self.cfg.last_live_path, datetime.now(timezone.utc)
                                   - timedelta(hours=2))
        cloud, spool = StrictCloud(), _Spool(gap)
        self._work(cloud, spool, [], cycles=2, recorder=_Recorder(None))
        self.assertNotIn("wl_open_recovery_interval", cloud.calls)
        self.assertNotIn("wl_agent_claim_recovery", cloud.calls)
        self.assertEqual(self.archive.channels, [], "no recorder channel may be guessed")
        self.assertEqual(spool.gap, gap)

    def test_empty_startup_inventory_is_re_enumerated(self):
        gap = ((T0).isoformat(), (T0 + timedelta(hours=1)).isoformat())
        cloud, spool = StrictCloud(), _Spool(gap)
        recorder = _Recorder(CHANNELS)
        self._work(cloud, spool, [], recorder=recorder)
        self.assertEqual(recorder.opens, 1)
        self.assertEqual(cloud.rejected, [])
        self.assertEqual(sorted(cloud.opened[0]["cameras"]), sorted(CAMERA_IDS.values()))
        self.assertEqual(set(self.archive.channels), {"1", "3"})


class LastLiveHandshake(RecoveryWorkerRpcContract):
    """The heartbeat may refresh last_live.json only after recovery has read it while the
    recorder was live (holder[LAST_LIVE_CHECKED]); otherwise a restart gap could be overwritten
    before it was ever detected."""

    def test_gap_check_is_not_marked_while_the_recorder_is_down(self):
        recovery.persist_last_live(self.cfg.last_live_path, datetime.now(timezone.utc)
                                   - timedelta(hours=2))
        holder = self._work(StrictCloud(), _Spool(), CHANNELS, holder={})
        self.assertFalse(holder.get(core.LAST_LIVE_CHECKED))

    def test_a_held_gap_survives_the_heartbeat_overwriting_last_live(self):
        lost_at = datetime.now(timezone.utc) - timedelta(hours=2)
        recovery.persist_last_live(self.cfg.last_live_path, lost_at)
        cloud = StrictCloud(sync_failures=1)          # first cycle: no camera mapping yet
        holder = {"recorder_live_at": __import__("time").monotonic()}

        def heartbeat():
            if holder.get(core.LAST_LIVE_CHECKED):
                recovery.persist_last_live(self.cfg.last_live_path, datetime.now(timezone.utc))

        self._work(cloud, _Spool(), CHANNELS, cycles=2, holder=holder, between=heartbeat)
        self.assertTrue(holder.get(core.LAST_LIVE_CHECKED))
        self.assertEqual(cloud.rejected, [])
        self.assertEqual(len(cloud.opened), 1, "the restart gap was lost")
        opened = datetime.fromisoformat(cloud.opened[0]["started_at"].replace("Z", "+00:00"))
        self.assertLess(abs((opened - lost_at).total_seconds()), 1)
        self.assertEqual(sorted(cloud.opened[0]["cameras"]), sorted(CAMERA_IDS.values()))


class CompleteRecoveryContract(unittest.TestCase):
    def test_runner_completion_parameters_exist_in_the_rpc(self):
        sig = _latest_params("wl_complete_recovery")
        cloud = StrictCloud()
        cloud.intervals.append({"id": "iv-1", "started_at": T0.isoformat(),
                                "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                                "cameras": [], "checkpoint": {}, "attempts": 0,
                                "status": "pending"})
        runner = recovery.RecoveryRunner(cloud, "agent", "key", _Archive(), [].append,
                                         log=lambda *a: None)
        runner.run_once(limit=1)
        self.assertTrue(cloud.completes)
        for params in cloud.completes:
            self.assertLessEqual(set(params), set(sig))


if __name__ == "__main__":
    unittest.main(verbosity=2)

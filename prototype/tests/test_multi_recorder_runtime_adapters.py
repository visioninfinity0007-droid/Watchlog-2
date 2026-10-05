"""Multi-recorder runtime adapter contracts.

These are hardware-free executable tests for the compatibility boundary between
RecorderContext-local state and the recorder-aware DB RPCs.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import backfill  # noqa: E402
import recovery  # noqa: E402
import watchlog_agent as core  # noqa: E402


T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
RID = "11111111-1111-1111-1111-111111111111"
# The recorder-less contract names cameras by cloud camera UUID (p_cameras uuid[]).
CAM1 = "c1c1c1c1-0000-4000-8000-000000000001"
CAM3 = "c3c3c3c3-0000-4000-8000-000000000003"


class FakeStore:
    def __init__(self):
        self.observed = []
        self.checkpoints = []
        self.compacted = 0

    def observe(self, *args, **kwargs):
        self.observed.append((args, kwargs))
        return {"ok": True}

    def checkpoint(self, *args, **kwargs):
        self.checkpoints.append((args, kwargs))
        return {"ok": True}

    def compact(self):
        self.compacted += 1


def test_persist_health_and_storage_carry_recorder_provenance():
    store = FakeStore()
    holder = {"store": store, "recorder_cloud_id": RID}
    state = {"agent_id": "agent-a"}
    cfg = SimpleNamespace(recorder_cloud_id=RID)

    core.persist_health(
        holder, state, cfg,
        {"cameras": [{"channel": "1", "health": "offline",
                      "reason": "nvr_unreachable", "source": "probe"}]},
        {"nvr": {"state": "offline"}},
    )
    core.persist_recording_storage(
        holder, state,
        {
            "recording": {"channels": [
                {"channel": "1", "state": "not_recording",
                 "reason": "not_recording"}
            ]},
            "storage": {"state": "fault", "reason": "disk_error"},
        },
    )

    assert len(store.observed) == 3
    assert all(kwargs.get("recorder_id") == RID for _, kwargs in store.observed)
    assert len(store.checkpoints) == 1
    assert store.checkpoints[0][1]["recorder_id"] == RID


def test_legacy_persistence_keeps_null_recorder_provenance():
    store = FakeStore()
    holder = {"store": store}
    state = {"agent_id": "agent-legacy"}
    cfg = SimpleNamespace()

    core.persist_health(
        holder, state, cfg,
        {"cameras": [{"channel": "1", "health": "operational",
                      "reason": "ok", "source": "probe"}]},
        {"nvr": {"state": "operational"}},
    )
    core.persist_recording_storage(
        holder, state,
        {
            "recording": {"channels": [
                {"channel": "1", "state": "recording", "reason": "ok"}
            ]},
            "storage": {"state": "ok", "reason": "ok"},
        },
    )

    assert all(kwargs.get("recorder_id") is None for _, kwargs in store.observed)
    assert store.checkpoints[0][1].get("recorder_id") is None


class RecorderCloud:
    def __init__(self):
        self.calls = []
        self.interval = {
            "id": "iv-r1",
            "recorder_id": RID,
            "started_at": T0.isoformat(),
            "ended_at": (T0 + timedelta(hours=1)).isoformat(),
            "channels": ["1"],
            "cameras": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
            "checkpoint": {},
            "status": "pending",
        }

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_open_recorder_recovery_interval":
            return {"ok": True, "id": "open-r1"}
        if name == "wl_agent_claim_recorder_recovery":
            if self.interval["status"] in ("pending", "in_progress"):
                self.interval["status"] = "in_progress"
                return [self.interval]
            return []
        if name == "wl_complete_recorder_recovery":
            self.interval["status"] = kw["p_status"]
            self.interval["checkpoint"] = kw["p_checkpoint"]
            return {"ok": True}
        return {}


def test_recorder_recovery_uses_new_rpcs_channels_and_stamps_events():
    cloud = RecorderCloud()
    driver = backfill.ReferenceArchiveDriver([
        {
            "ts": (T0 + timedelta(minutes=5)).isoformat(),
            "type": "person",
            "device_event_id": "evt-1",
        }
    ])
    events = []
    runner = recovery.RecoveryRunner(
        cloud, "agent", "key", driver, events.append,
        recorder_id=RID,
        chunk_seconds=3600,
        log=lambda *a: None,
    )

    opened = runner.report_outage(T0, T0 + timedelta(minutes=30), cameras=["1"])
    assert opened["ok"] is True
    open_name, open_args = cloud.calls[0]
    assert open_name == "wl_open_recorder_recovery_interval"
    assert open_args["p_recorder_id"] == RID
    assert open_args["p_channels"] == ["1"]
    assert "p_cameras" not in open_args

    out = runner.run_once(limit=1)
    assert out[0]["status"] == "recovered"
    assert len(events) == 1
    assert events[0]["channel"] == "1"
    assert events[0]["recorder_id"] == RID

    names = [name for name, _ in cloud.calls]
    assert "wl_agent_claim_recorder_recovery" in names
    assert "wl_complete_recorder_recovery" in names
    assert "wl_agent_claim_recovery" not in names
    assert "wl_complete_recovery" not in names

    for name, args in cloud.calls:
        if name in ("wl_agent_claim_recorder_recovery",
                    "wl_complete_recorder_recovery"):
            assert args["p_recorder_id"] == RID


def test_recorder_recovery_never_uses_camera_uuid_as_archive_channel():
    cloud = RecorderCloud()
    cloud.interval["channels"] = []
    cloud.interval["cameras"] = ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
    events = []
    driver = backfill.ReferenceArchiveDriver([{
        "ts": (T0 + timedelta(minutes=5)).isoformat(),
        "type": "person",
        "device_event_id": "must-not-run",
    }])
    runner = recovery.RecoveryRunner(
        cloud, "agent", "key", driver, events.append,
        recorder_id=RID,
        chunk_seconds=3600,
        log=lambda *a: None,
    )

    out = runner.run_once(limit=1)

    assert out == [{
        "id": "iv-r1",
        "status": "unrecoverable",
        "recovered": 0,
        "yielded": False,
        "reason": "missing_channels",
    }]
    assert events == []
    completes = [
        args for name, args in cloud.calls
        if name == "wl_complete_recorder_recovery"
    ]
    assert len(completes) == 1
    assert completes[0]["p_status"] == "unrecoverable"
    assert completes[0]["p_recorder_id"] == RID


class LegacyCloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_open_recovery_interval":
            return {"ok": True, "id": "legacy-open"}
        if name == "wl_agent_claim_recovery":
            return []
        return {}


def test_legacy_recovery_rpc_contract_is_unchanged():
    cloud = LegacyCloud()
    runner = recovery.RecoveryRunner(
        cloud, "agent", "key",
        backfill.ReferenceArchiveDriver([]),
        lambda _e: None,
        log=lambda *a: None,
    )
    runner.report_outage(T0, T0 + timedelta(minutes=30), cameras=[CAM1, CAM3])
    runner.run_once(limit=1)

    assert cloud.calls[0][0] == "wl_open_recovery_interval"
    assert cloud.calls[0][1]["p_cameras"] == [CAM1, CAM3]
    assert "p_channels" not in cloud.calls[0][1]
    assert cloud.calls[1][0] == "wl_agent_claim_recovery"


def test_watchlog_recovery_open_helper_selects_contract_by_context():
    calls = []

    class C:
        def call(self, name, **kw):
            calls.append((name, kw))
            return {"ok": True}

    state = {"agent_id": "agent", "agent_key": "key"}
    core._open_recovery_interval_for_cfg(
        C(), state, SimpleNamespace(recorder_cloud_id=RID),
        T0.isoformat(), (T0 + timedelta(minutes=5)).isoformat(), ["1"],
    )
    assert calls[-1][0] == "wl_open_recorder_recovery_interval"
    assert calls[-1][1]["p_recorder_id"] == RID
    assert calls[-1][1]["p_channels"] == ["1"]

    core._open_recovery_interval_for_cfg(
        C(), state, SimpleNamespace(),
        T0.isoformat(), (T0 + timedelta(minutes=5)).isoformat(), [CAM1],
    )
    assert calls[-1][0] == "wl_open_recovery_interval"
    assert calls[-1][1]["p_cameras"] == [CAM1]
    assert "p_channels" not in calls[-1][1]


def test_recorder_rejection_categories_fail_closed_locally():
    assert "invalid_recorder_id" in core._PERMANENT_REJECTIONS
    assert "missing_recorder" in core._PERMANENT_REJECTIONS
    # An otherwise valid cloud recorder that is not present yet may become valid
    # after recorder sync, so keep it bounded-retry rather than immediate poison.
    assert "invalid_recorder" not in core._PERMANENT_REJECTIONS

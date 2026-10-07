#!/usr/bin/env python3
"""Evidence lease and provenance on the Agent side (5.1.2 workstream E).

- At startup the Agent releases its OWN in-flight clips and stills
  (wl_agent_release_inflight_evidence, migration 0150) before any worker claims, instead of
  leaving what a previous run held to the server lease. A server without the RPC, or one that
  refuses (a stale Agent identity), never blocks the runtime.
- A completed clip whose checksum WatchLog recomputed and rejected (0164 returns ok=false and
  has already failed the request) is not reported as uploaded and is not failed a second time.
- An event still records its real capture time (payload.snapshot_captured_at, the moment the
  still was requested) next to the unchanged event time; 0164 stores it as captured_at.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from native_collector_harness import Info, run_collector, stop_after_first_wait  # noqa: E402
import incident_evidence as ie  # noqa: E402
import native_event_collector as nec  # noqa: E402
import recorder_runtime  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, Event  # noqa: E402

STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class Cloud:
    def __init__(self, handlers=None):
        self.calls = []
        self.lock = threading.Lock()
        self.handlers = handlers or {}

    def call(self, name, **kw):
        with self.lock:
            self.calls.append((name, kw))
        handler = self.handlers.get(name)
        if handler is not None:
            return handler(**kw)
        if name in ("wl_agent_claim_clip_requests", "wl_agent_claim_incident_stills"):
            return []
        return {"ok": True}

    def names(self):
        with self.lock:
            return [name for name, _kw in self.calls]


@pytest.fixture
def quiet(monkeypatch):
    logs = []
    monkeypatch.setattr(ie.core, "log", logs.append)
    return logs


def test_startup_releases_in_flight_evidence_before_any_claim(monkeypatch, quiet):
    cloud = Cloud({"wl_agent_release_inflight_evidence": lambda **kw: {
        "ok": True, "clips_failed": 1, "stills_released": 2, "stills_failed": 0}})
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(recorder_runtime, "mark_registry_required", lambda cfg: False)
    monkeypatch.setattr(recorder_runtime, "registry_unavailable", lambda cfg: False)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(ie, "STILL_POLL_SECONDS", 0.01)
    seen_at_start = []

    def original(cfg, state, cloud_arg, once, device=None, channels=None):
        seen_at_start.append(list(cloud.names()))
        deadline = time.monotonic() + 3
        while ("wl_agent_claim_clip_requests" not in cloud.names()
               or "wl_agent_claim_incident_stills" not in cloud.names()):
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)
        return 0

    cfg = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                          nvr_url="http://recorder.invalid")
    ie.wrap_cmd_run(original)(cfg, STATE, cloud, False, None, [])
    names = cloud.names()
    assert names[0] == "wl_agent_release_inflight_evidence"
    assert seen_at_start and seen_at_start[0][0] == "wl_agent_release_inflight_evidence"
    release = cloud.calls[0][1]
    assert release == {"p_agent_id": "agent-1", "p_agent_key": "key-1"}
    assert "wl_agent_claim_clip_requests" in names and "wl_agent_claim_incident_stills" in names
    assert names.count("wl_agent_release_inflight_evidence") == 1
    assert any("released 1 clip(s) and 2 still(s)" in line for line in quiet)


def test_one_shot_runs_release_nothing(monkeypatch, quiet):
    cloud = Cloud()
    ie.wrap_cmd_run(lambda *a, **k: 0)(SimpleNamespace(), STATE, cloud, True, None, [])
    assert cloud.names() == []


@pytest.mark.parametrize("error, expected", [
    (RuntimeError("Could not find the function public.wl_agent_release_inflight_evidence"
                  "(p_agent_id, p_agent_key) in the schema cache"), "cannot release"),
    (RuntimeError("agent is not the current site authority"), "was not released"),
])
def test_release_failure_never_blocks_the_runtime(quiet, error, expected):
    def boom(**_kw):
        raise error
    cloud = Cloud({"wl_agent_release_inflight_evidence": boom})
    assert ie.release_inflight_evidence(cloud, STATE) is None
    assert any(expected in line for line in quiet), quiet


def test_a_rejected_checksum_is_not_reported_as_uploaded_or_failed_again(monkeypatch, quiet):
    stop = threading.Event()
    rows = [{"request_id": "req-1", "event_id": 7, "recorder_id": "rec-1", "channel": "1",
             "start_at": "2026-10-04T07:59:45Z", "end_at": "2026-10-04T08:00:30Z"}]

    def claim(**_kw):
        if rows:
            return [rows.pop(0)]
        stop.set()
        return []

    cloud = Cloud({
        "wl_agent_claim_clip_requests": claim,
        "wl_agent_complete_clip": lambda **kw: {"ok": False, "status": "failed",
                                                "reason": "checksum_mismatch"},
    })

    class Driver:
        name = "fake-archive"

        def get_clip(self, channel, start, end):
            return MP4

        def close(self):
            pass

    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.recorder_runtime, "config_for_cloud_recorder",
                        lambda cfg, rid: SimpleNamespace(nvr_url="http://recorder.invalid"))
    monkeypatch.setattr(ie.core, "open_archive_driver",
                        lambda cfg: (Driver(), DeviceInfo(vendor="Dahua", model="X")))
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.01)
    cfg = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                          nvr_url="http://recorder.invalid")
    ie.footage_worker(cfg, STATE, stop)
    names = cloud.names()
    assert "wl_agent_complete_clip" in names
    assert "wl_agent_fail_clip" not in names, "WatchLog already failed the request"
    assert not any("incident footage: uploaded" in line for line in quiet)
    assert any("could not verify" in line for line in quiet), quiet


WHEN = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
SHOT = datetime(2026, 10, 4, 16, 0, 2, tzinfo=timezone.utc)


class StillDriver:
    name = "fake"
    verified_against_hardware = True

    def __init__(self, events, still=b"\xff\xd8\xff\xe0jpeg"):
        self.events, self.still = events, still

    def stream_events(self, stop):
        yield from self.events

    def get_snapshot(self, channel):
        return self.still

    def close(self):
        pass


def test_event_still_records_its_real_capture_time_next_to_the_event_time(monkeypatch):
    monkeypatch.setattr(nec.core, "now_utc", lambda: SHOT)
    driver = StillDriver([Event(channel="2", event_type="person", device_ts=WHEN,
                                payload={"native_ai": True, "source": "recorder_native_ai"})])
    spool, _holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()),
        reconnect_wait=stop_after_first_wait([]))
    row = spool.rows[0]
    assert row["snapshot_b64"]
    assert row["device_ts"] == "2026-10-04T16:00:00Z", "device_ts stays the event time"
    assert row["payload"]["snapshot_captured_at"] == core.iso(SHOT)
    assert row["payload"]["snapshot_source"] == "live_after_event"
    assert row["payload"]["source"] == "recorder_native_ai"


def test_event_without_a_still_reports_no_capture_time(monkeypatch):
    driver = StillDriver([Event(channel="2", event_type="person", device_ts=WHEN,
                                payload={"native_ai": True})], still=None)
    spool, _holder, _ok = run_collector(
        monkeypatch, lambda cfg: (driver, Info()),
        reconnect_wait=stop_after_first_wait([]))
    row = spool.rows[0]
    assert "snapshot_b64" not in row
    assert "snapshot_captured_at" not in row["payload"]


def test_capture_time_is_taken_when_the_still_is_requested():
    ev = Event(channel="1", event_type="person", device_ts=WHEN, payload={})
    nec.mark_snapshot_capture(ev, WHEN + timedelta(seconds=1))
    assert ev.payload == {"snapshot_captured_at": "2026-10-04T16:00:01Z",
                          "snapshot_source": "live_after_event"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

#!/usr/bin/env python3
"""Owner decision (2026-10-06): the server owns scheduled periodic restaurant stills.

With 0156 live, an Agent advertising ``config_snapshot_requests`` receives the server's
scheduled capture requests (0125) for every enabled interval/hybrid restaurant camera. The
5.1.x Agent ALSO took its own periodic stills of every configured camera: two scheduled stills
of the same camera. Now a camera the server sent a capture request for is server-owned for
SERVER_OWNED_SECONDS; both Agent samplers (the Hikvision in-stream sampler and the separate
worker used for Dahua/ONVIF) skip it and resume if the server stops. Cameras the server never
asks for are unchanged; native alarms are never touched. Server requests on Hikvision run on
the live collector's own session, never a new login per request.
"""
from __future__ import annotations

import random
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import periodic_stills  # noqa: E402
import server_capture  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
REC = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def clean():
    server_capture.reset()
    yield
    server_capture.reset()


class _Spool:
    max_rows = 10_000

    def __init__(self):
        self.rows = []

    def count(self):
        return len(self.rows)

    def add(self, row):
        self.rows.append(row)

    def trim(self):
        return 0


def _sampler(recorder_id=REC, channels=("1", "2")):
    taken = []
    drv = SimpleNamespace(
        name="hikvision-isapi",
        list_channels=lambda: [SimpleNamespace(channel=c, enabled=True) for c in channels],
        get_snapshot=lambda ch: taken.append(ch) or JPEG)
    cfg = SimpleNamespace(snapshots=True, snapshot_min_interval=60, _ini_path=None,
                          camera_profiles=[], recorder_cloud_id=recorder_id)
    clock = {"t": 1000.0}
    s = periodic_stills.StreamStillSampler(cfg, drv, _Spool(), clock=lambda: clock["t"],
                                           rng=random.Random(3))
    return s, clock, taken


def _run(sampler, clock, rounds=200, step=7.0):
    for _ in range(rounds):
        clock["t"] += step
        sampler()


def test_no_duplicate_scheduled_still_while_the_server_schedules_the_camera():
    s, clock, taken = _sampler()
    server_capture.note_requests([{"camera_id": "cam-1", "recorder_id": REC, "channel": "1"}],
                                 now=clock["t"])
    _run(s, clock, rounds=100)                 # 700 s, inside the server-owned window
    assert "1" not in taken, "the Agent took its own still of a server-scheduled camera"
    assert "2" in taken, "a camera the server does not schedule keeps the Agent's stills"


def test_the_agent_resumes_when_the_server_stops_scheduling():
    s, clock, taken = _sampler()
    server_capture.note_requests([{"recorder_id": REC, "channel": "1"}], now=clock["t"])
    clock["t"] += server_capture.SERVER_OWNED_SECONDS + 1
    _run(s, clock, rounds=100)
    assert "1" in taken


def test_a_single_recorder_runtime_matches_by_channel():
    s, clock, taken = _sampler(recorder_id=None)
    server_capture.note_requests([{"recorder_id": REC, "channel": "1"}], now=clock["t"])
    _run(s, clock, rounds=100)
    assert "1" not in taken and "2" in taken


def test_another_recorders_channel_one_is_not_suppressed():
    s, clock, taken = _sampler(recorder_id="22222222-2222-2222-2222-222222222222")
    server_capture.note_requests([{"recorder_id": REC, "channel": "1"}], now=clock["t"])
    _run(s, clock, rounds=100)
    assert "1" in taken


def test_the_separate_worker_also_skips_server_scheduled_cameras(monkeypatch):
    # Dahua/ONVIF use the separate worker; same rule.
    monkeypatch.setattr(periodic_stills, "MIN_CADENCE_SECONDS", 1)
    monkeypatch.setattr(periodic_stills, "STARTUP_DELAY_SECONDS", 0.0)
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILL_SECONDS", "1")
    taken = []
    stop = threading.Event()
    drv = SimpleNamespace(name="dahua-cgi", close=lambda: None,
                          list_channels=lambda: [{"channel": "1", "enabled": True},
                                                 {"channel": "2", "enabled": True}],
                          get_snapshot=lambda ch: (taken.append(ch), JPEG)[1])
    cfg = SimpleNamespace(nvr_url="http://192.0.2.20", nvr_driver="dahua-cgi", snapshots=True,
                          snapshot_min_interval=1, _ini_path=None, camera_profiles=[],
                          recorder_cloud_id=REC)
    server_capture.note_requests([{"recorder_id": REC, "channel": "1"}])
    t = threading.Thread(target=periodic_stills.periodic_still_worker,
                         args=(cfg, _Spool(), stop),
                         kwargs={"open_driver": lambda c: (drv, None)}, daemon=True)
    t.start()
    import time
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and taken.count("2") < 2:
        time.sleep(0.05)
    stop.set()
    t.join(5)
    assert taken.count("2") >= 2 and "1" not in taken


def test_server_requests_on_hikvision_use_the_live_session_not_a_new_login(monkeypatch):
    import analytics_agent
    live_calls, opened = [], []
    live = SimpleNamespace(base_url="http://192.0.2.10", samples_in_stream=True,
                           get_snapshot=lambda ch: live_calls.append(ch) or JPEG,
                           close=lambda: pytest.fail("the live session must not be closed"))
    server_capture.register_live_driver(live)
    monkeypatch.setattr(analytics_agent, "_open_analytics_driver",
                        lambda cfg: opened.append(cfg) or pytest.fail("opened a new login"))
    job_cfg = SimpleNamespace(nvr_url="http://192.0.2.10/")
    monkeypatch.setattr(analytics_agent.recorder_runtime, "config_for_cloud_recorder",
                        lambda cfg, rid: job_cfg)
    uploads = []
    cloud = SimpleNamespace(call=lambda fn, **kw: uploads.append(fn))
    done = analytics_agent._service_snapshot_requests(
        cloud, {"agent_id": "a", "agent_key": "k"}, SimpleNamespace(), None,
        [{"camera_id": "cam-1", "recorder_id": REC, "channel": "1"}])
    assert done == 1 and live_calls == ["1"] and opened == []
    assert uploads == ["wl_upload_config_snapshot"]
    server_capture.unregister_live_driver(live)
    assert server_capture.live_driver_for("http://192.0.2.10") is None


def test_a_non_hikvision_live_driver_is_never_shared():
    server_capture.register_live_driver(SimpleNamespace(base_url="http://192.0.2.20",
                                                        samples_in_stream=False))
    assert server_capture.live_driver_for("http://192.0.2.20") is None


def test_native_alarm_flow_is_independent_of_still_ownership():
    # Ownership only changes which cameras the periodic samplers skip; no native event path
    # reads it. Guard against a future import into the stream/collector event path.
    agent = Path(__file__).resolve().parents[1] / "agent"
    for name in ("drivers/hikvision.py", "drivers/dahua.py", "drivers/onvif_driver.py"):
        assert "server_capture" not in (agent / name).read_text(encoding="utf-8"), name
    collector = (agent / "native_event_collector.py").read_text(encoding="utf-8")
    assert collector.count("server_capture.") == 2   # register + unregister only

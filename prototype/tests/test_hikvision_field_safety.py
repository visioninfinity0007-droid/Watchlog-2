#!/usr/bin/env python3
"""T0: the Build 69 / Build 75 / shipped 5.0.26 Hikvision field behaviour, in its 5.1.x form.

Field DS-7608NI-Q1 (Chai Wala): a permanently-open alertStream plus independent health, still
and recovery logins made the recorder refuse sessions and time out while still reachable. The
field builds (811d378e, 3d02a7b2, a3266326) fixed it with one authenticated HTTP operation at a
time, a 30 s bounded alert-stream slice, ONE rotating still between slices on the same session,
and a health cycle that does not open a competing session while the live stream proves the
recorder. Canonical 5.1.0 had lost all of it. These tests pin the restored behaviour, per
recorder, without turning liveness into recording/storage proof.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import drivers.hikvision as hik  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

ALERT = (b"--boundary\r\nContent-Type: application/xml\r\n\r\n"
         b"<EventNotificationAlert><ipAddress>10.0.0.5</ipAddress><channelID>3</channelID>"
         b"<dateTime>2026-10-06T10:00:00+05:00</dateTime><activePostCount>1</activePostCount>"
         b"<eventType>VMD</eventType><eventState>active</eventState>"
         b"<eventDescription>Motion alarm</eventDescription></EventNotificationAlert>\r\n")
KEEPALIVE = b"--boundary\r\n"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class StreamResp:
    """A streaming alertStream response whose chunks come from a script.

    Each script item is bytes (a chunk), a float (sleep before the next chunk), or an
    Exception instance (raised from iter_content)."""

    def __init__(self, script, status=200):
        self.status_code = status
        self.script = list(script)
        self.headers = {}
        self.content = b""
        self.text = ""
        self.closed = False

    def iter_content(self, chunk_size=1024):
        for item in self.script:
            if isinstance(item, float):
                time.sleep(item)
                continue
            if isinstance(item, BaseException):
                raise item
            yield item

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def short_slice(monkeypatch):
    # The field value is asserted separately; the mechanics run on a short slice.
    monkeypatch.setattr(hik, "HIKVISION_STREAM_SLICE_SECONDS", 0.3)
    hik._HTTP_LOCKS.clear()
    yield
    hik._HTTP_LOCKS.clear()


def _driver(host="10.0.0.5"):
    return HikvisionDriver(f"http://{host}", "admin", "secret", timeout=2)


# --- 9.1 the 30 s field slice -------------------------------------------------------------

def test_field_slice_is_thirty_seconds():
    import importlib
    fresh = importlib.reload(hik)
    try:
        assert fresh.HIKVISION_STREAM_SLICE_SECONDS == 30
    finally:
        importlib.reload(hik)


# --- Build 69 LAN session (b0da326f, abd098a5) ---------------------------------------------

@pytest.mark.parametrize("cls", [HikvisionDriver, DahuaDriver, OnvifDriver])
def test_recorder_sessions_ignore_the_pc_proxy_and_accept_self_signed_https(cls):
    d = cls("http://192.168.1.64", "admin", "secret")
    assert d.s.trust_env is False
    assert d.s.verify is False


# --- 9.2 one authenticated HTTP operation at a time, PER RECORDER --------------------------

class _Counting:
    def __init__(self, hold=0.15):
        self.hold, self.live, self.peak = hold, 0, 0
        self.guard = threading.Lock()

    def request(self, method, url, **kw):
        with self.guard:
            self.live += 1
            self.peak = max(self.peak, self.live)
        time.sleep(self.hold)
        with self.guard:
            self.live -= 1
        return SimpleNamespace(status_code=200, content=JPEG, headers={}, text="", close=lambda: None)


def _parallel(calls):
    threads = [threading.Thread(target=c) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)


def test_two_drivers_for_the_same_recorder_never_send_in_parallel():
    counter = _Counting()
    a, b = _driver(), _driver()          # e.g. collector and recovery each with their own session
    a.s = b.s = counter
    _parallel([lambda: a.get_snapshot("1"), lambda: b.get_snapshot("2"),
               lambda: a.get_snapshot("3")])
    assert counter.peak == 1


def test_two_recorders_are_never_serialized_against_each_other():
    counter = _Counting(hold=0.3)
    a, b = _driver("10.0.0.5"), _driver("10.0.0.6")
    a.s = b.s = counter
    _parallel([lambda: a.get_snapshot("1"), lambda: b.get_snapshot("1")])
    assert counter.peak == 2


def test_a_slice_holds_its_recorder_but_not_another_recorder():
    stop = threading.Event()
    a, b = _driver("10.0.0.5"), _driver("10.0.0.6")
    a.s = SimpleNamespace(request=lambda *x, **k: StreamResp([KEEPALIVE] + [0.05, KEEPALIVE] * 20),
                          close=lambda: None)
    waited = {}

    def other_recorder():
        b.s = _Counting(hold=0.0)
        t0 = time.monotonic()
        b.get_snapshot("1")
        waited["b"] = time.monotonic() - t0

    def same_recorder():
        a2 = _driver("10.0.0.5")
        a2.s = _Counting(hold=0.0)
        t0 = time.monotonic()
        a2.get_snapshot("1")
        waited["a"] = time.monotonic() - t0

    gen = a.stream_events(stop)
    # Drive the stream on its own thread: slices back to back.
    runner = threading.Thread(target=lambda: _consume(gen, stop, 1.5))
    runner.start()
    time.sleep(0.05)                      # the slice is open and holds 10.0.0.5
    _parallel([other_recorder, same_recorder])
    stop.set()
    runner.join(5)
    assert waited["b"] < 0.1, "recorder B waited on recorder A's stream"
    assert waited["a"] >= 0.1, "a second session reached recorder A during its slice"
    assert waited["a"] < 1.0, "a waiter on recorder A was starved past the slice handoff"


def _consume(gen, stop, seconds):
    end = time.monotonic() + seconds
    try:
        for _ in gen:
            if time.monotonic() > end:
                break
    finally:
        stop.set()
        gen.close()


# --- slices loop inside the generator; liveness stays truthful ------------------------------

def _scripted(driver, slices):
    it = iter(slices)
    driver.s = SimpleNamespace(request=lambda *a, **k: StreamResp(next(it)), close=lambda: None)


def test_planned_slice_ends_do_not_end_the_stream_or_flap_liveness():
    d = _driver()
    _scripted(d, [[KEEPALIVE, 0.35, KEEPALIVE]] * 3 + [[]])
    stop = threading.Event()
    calls = []
    d.between_slices = lambda: calls.append(time.monotonic()) or []
    seen_since = []
    gen = d.stream_events(stop)

    def watch():
        while not stop.is_set():
            if d.event_stream.get("connected"):
                seen_since.append(d.event_stream.get("connected_at"))
            time.sleep(0.02)
    w = threading.Thread(target=watch)
    w.start()
    list(gen)                             # 3 planned slices, then the recorder ends the stream
    stop.set()
    w.join(2)
    assert len(calls) == 3, "between_slices runs once after every planned slice"
    assert len(set(seen_since)) == 1, "connected_at must not change on a planned slice end"
    assert d.event_stream["connected"] is False   # the real end is still a real end


def test_a_silent_stream_is_a_stream_error_not_a_quiet_reopen():
    # No frame, not even a keep-alive, for the whole read window is a stale stream: it is
    # recorded and raised to the collector's back-off. A quiet re-open here would loop
    # against a misbehaving recorder with no delay.
    d = _driver()
    before = d.last_activity_monotonic
    opened = []
    d.s = SimpleNamespace(request=lambda *a, **k: opened.append(1) or StreamResp(
        [requests.exceptions.ConnectionError("Read timed out.")]), close=lambda: None)
    with pytest.raises(requests.exceptions.ConnectionError):
        list(d.stream_events(threading.Event()))
    assert opened == [1]
    assert d.last_activity_monotonic == before
    assert d.event_stream["connected"] is False and "timed out" in d.event_stream["last_error"]


def test_native_alarms_pass_immediately_and_are_not_held_for_the_still():
    d = _driver()
    _scripted(d, [[ALERT, 0.35, KEEPALIVE], []])
    order = []
    d.between_slices = lambda: order.append("still") or []
    for ev in d.stream_events(threading.Event()):
        order.append(ev.event_type)
    assert order[0] != "still" and order.index("still") > 0


def test_a_failing_still_never_ends_native_monitoring():
    d = _driver()
    _scripted(d, [[KEEPALIVE, 0.35, KEEPALIVE], [ALERT, 0.35, KEEPALIVE], []])

    def broken():
        raise RuntimeError("camera refused")
    d.between_slices = broken
    events = list(d.stream_events(threading.Event()))
    assert [e.event_type for e in events if e.event_type != "visual_sample"], \
        "the alarm after a failed still must still arrive"


# --- 9.4 rotating still: one per slice, configured cameras, canonical row -------------------

class _Spool:
    def __init__(self, waiting=0, max_rows=10000):
        self.waiting, self.max_rows, self.rows = waiting, max_rows, []

    def count(self):
        return self.waiting

    def add(self, row):
        self.rows.append(row)

    def trim(self):
        return 0


def _sampler(profiles=None, spool=None, snapshot=lambda ch: JPEG, channels=("1", "2", "3")):
    import periodic_stills
    drv = SimpleNamespace(name="hikvision-isapi",
                          list_channels=lambda: [SimpleNamespace(channel=c, enabled=True) for c in channels],
                          get_snapshot=snapshot)
    cfg = SimpleNamespace(snapshots=True, snapshot_min_interval=60, _ini_path=None,
                          camera_profiles=profiles or [], recorder_cloud_id="rec-1")
    clock = {"t": 1000.0}
    s = periodic_stills.StreamStillSampler(cfg, drv, spool or _Spool(), clock=lambda: clock["t"],
                                           rng=__import__("random").Random(1))
    return s, clock


def test_sampler_takes_at_most_one_still_per_slice_with_the_canonical_row():
    s, clock = _sampler()
    clock["t"] += 31.0 + 300.0             # past the start-up delay: every camera is due
    first = s()
    assert len(first) == 1
    ev = first[0]
    assert ev.event_type == "visual_sample"
    assert ev.payload == {"sample": True, "source": "periodic_snapshot", "vendor": "hikvision"}
    assert ev.device_event_id.startswith(f"hikvision-sample-{ev.channel}-")
    assert ev.recorder_id == "rec-1"
    assert s() == [], "two stills never back to back within the minimum spacing"


def test_sampler_respects_ignore_and_the_spool_high_water():
    s, clock = _sampler(profiles=[{"channel": "2", "monitored": False}])
    clock["t"] += 10_000.0
    got = set()
    for _ in range(20):
        clock["t"] += 3.0
        got |= {e.channel for e in s()}
    assert "2" not in got and got
    full, clock2 = _sampler(spool=_Spool(waiting=10**6))
    clock2["t"] += 10_000.0
    assert full() == []


def test_sampler_failure_returns_nothing_and_never_raises():
    def boom(ch):
        raise requests.exceptions.ConnectionError("reset")
    s, clock = _sampler(snapshot=boom)
    clock["t"] += 10_000.0
    assert s() == []


def test_separate_still_worker_stands_down_for_hikvision(monkeypatch):
    import periodic_stills
    opened = []
    cfg = SimpleNamespace(nvr_url="http://10.0.0.5", nvr_driver="hikvision-isapi", snapshots=True,
                          snapshot_min_interval=60, _ini_path=None, camera_profiles=[])
    periodic_stills.periodic_still_worker(cfg, _Spool(), threading.Event(),
                                          open_driver=lambda c: opened.append(c))
    assert opened == [], "no second session is opened to a Hikvision recorder"


# --- 9.3 live collector truth in the health cycle ---------------------------------------------

def _holder(activity_age=5.0, real_age=10.0, connected=True, enumerated=True):
    now = time.monotonic()
    live = SimpleNamespace(samples_in_stream=True, last_activity_monotonic=now - activity_age,
                           event_stream={"connected": connected})
    return {"live_driver": live,
            "real_assessment": {"at": now - real_age,
                                "nvr": {"vendor": "Hikvision", "model": "DS-7608NI-Q1",
                                        "state": "ok", "reachable": True},
                                "channels": {"enumerated": enumerated,
                                             "reported": [{"channel": "1", "enabled": True},
                                                          {"channel": "2", "enabled": True}]}}}


def _cfg():
    return SimpleNamespace(snapshots=True, snapshot_min_interval=60, _ini_path=None,
                           nvr_driver="hikvision-isapi")


def test_live_stream_proves_connectivity_from_the_last_real_enumeration():
    import watchlog_agent as core
    out = core._live_stream_health(_cfg(), _holder())
    assert out["nvr"]["state"] == "ok" and out["nvr"]["model"] == "DS-7608NI-Q1"
    assert [r["channel"] for r in out["channels"]["reported"]] == ["1", "2"]
    assert out["channels"]["current_faults"] == {"supported": False}
    assert "recording" not in out and "storage" not in out


@pytest.mark.parametrize("kw", [
    {"activity_age": 151.0},     # stale collector truth expires
    {"real_age": 901.0},         # a real assessment is due
    {"connected": False},        # the stream is down
    {"enumerated": False},       # no real inventory to reuse
])
def test_stale_or_unproven_live_truth_falls_back_to_a_real_assessment(kw):
    import watchlog_agent as core
    assert core._live_stream_health(_cfg(), _holder(**kw)) is None


def test_health_cycle_on_live_truth_opens_no_session_and_leaves_recording_unobserved(monkeypatch):
    import watchlog_agent as core
    import camera_health
    import recording_health

    def no_session(*a, **k):
        raise AssertionError("health opened a competing recorder session")
    monkeypatch.setattr(core, "build", no_session)
    monkeypatch.setattr(core, "autodetect", no_session)
    monkeypatch.setattr(recording_health, "assess_recording_storage",
                        lambda *a, **k: pytest.fail("recording/storage inferred from liveness"))
    reported = []
    cloud = SimpleNamespace(call=lambda rpc, **k: reported.append((rpc, k)) or {})
    holder = _holder()
    holder["snapshot_ok"] = {"1": time.monotonic() - 10}
    probes = {}

    class Mon:
        channels = ["1", "2"]

        def run_cycle(self, assess, probe):
            probes.update({c: probe(c) for c in self.channels})
            return {"cameras": []}
    holder["monitor"] = Mon()
    monkeypatch.setattr(core, "persist_health", lambda *a, **k: None)
    monkeypatch.setattr(core, "reconcile_health", lambda *a, **k: None)
    cfg = SimpleNamespace(**vars(_cfg()), nvr_url="http://10.0.0.5", nvr_username="u",
                          nvr_password="p", recorder_cloud_id=None, health_batch=4,
                          health_concurrency=2)
    monkeypatch.setattr(core, "_reload_credential_if_changed", lambda c: None)
    core.health_cycle(cloud, {"agent_id": "a", "agent_key": "k"}, cfg, holder)
    assert reported and reported[0][1]["p_report"]["nvr"]["state"] == "ok"
    assert probes["1"].ok is True and probes["2"].ok is False   # no fresh still: not proven
    assert isinstance(probes["2"], camera_health.ProbeResult)

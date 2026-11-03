#!/usr/bin/env python3
"""Worker registry + supervisor (5.1.2, agent/worker_supervisor.py).

Before 5.1.2 a worker thread that raised was logged and ended for the life of the process
while the Agent kept heartbeating. These tests drive real threads under a fake supervisor
clock and prove:

- a dead worker is restarted, after an exponential backoff that is capped;
- a worker that stops ticking is reported stalled, asked to stop, and restarted only after
  that thread has ended (never two copies at once);
- more than the hourly restart budget marks it failed: never restarted again, still reported;
- one-shot workers, and workers that say complete()/disable(), are completed/disabled, not dead;
- a recorder-scoped worker failing never restarts or affects another recorder's worker;
- the runtime's stop still stops every incarnation, and a stopping runtime restarts nothing;
- health is never reported without evidence (tick, no error since success, probe);
- the recorded error is redacted (no URL, credential or recorder address).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import worker_supervisor as ws  # noqa: E402


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _sup(clock, **options):
    return ws.Supervisor(clock=clock, log=lambda _m: None, **options)


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _dead(handle):
    return _wait(lambda: not handle.is_alive())


def test_dead_worker_is_restarted_after_backoff_with_redacted_error():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    runs = []

    def target(stop_event):
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("connect to http://admin:secret@10.1.2.3/ISAPI failed")
        ws.tick()
        stop_event.wait()

    h = sup.supervised("health", target, args=(stop,), stop=stop, recorder_local_id="rec-a")
    h.start()
    assert _dead(h)
    sup.check(clock())
    row = sup.snapshot(clock())[0]
    assert row["state"] == "restarting" and row["healthy"] is False
    assert "RuntimeError" in row["last_error"]
    for leak in ("admin", "secret", "10.1.2.3", "http://"):
        assert leak not in row["last_error"], row["last_error"]

    sup.check(clock.advance(ws.BACKOFF_BASE_SECONDS - 1))
    assert sup.snapshot(clock())[0]["state"] == "restarting"
    sup.check(clock.advance(1))
    assert _wait(lambda: len(runs) == 2)
    assert h.is_alive()
    assert _wait(lambda: sup.snapshot(clock())[0]["state"] == "running")
    assert h.restart_count == 1
    stop.set()
    assert _dead(h)


def test_backoff_doubles_and_is_capped():
    clock = Clock()
    sup = _sup(clock, backoff_base=5, backoff_cap=20, max_restarts_per_hour=100)
    stop = threading.Event()

    def boom(_stop):
        raise ValueError("no")

    h = sup.supervised("archive", boom, args=(stop,), stop=stop)
    h.start()
    delays = []
    for _ in range(5):
        assert _dead(h)
        sup.check(clock())
        assert h.state == "restarting"
        delays.append(h._next_start_at - clock())
        clock.now = h._next_start_at
        sup.check(clock())                      # restart now due
    assert delays == [5, 10, 20, 20, 20]
    stop.set()


def test_max_restarts_per_hour_marks_failed_and_keeps_reporting_it():
    clock = Clock()
    sup = _sup(clock, backoff_base=1, backoff_cap=1, max_restarts_per_hour=3)
    stop = threading.Event()
    starts = []

    def boom(_stop):
        starts.append(1)
        raise OSError("down")

    h = sup.supervised("site_control", boom, args=(stop,), stop=stop)
    h.start()
    for _ in range(10):
        assert _dead(h)
        sup.check(clock())
        if h.state == "failed":
            break
        clock.advance(1)
        sup.check(clock())
    assert h.state == "failed"
    assert len(starts) == 4                     # first start + 3 restarts in the hour
    sup.check(clock.advance(30))
    assert len(starts) == 4, "a failed worker is never started again in this run"
    row = sup.snapshot(clock())[0]
    assert row["state"] == "failed" and row["healthy"] is False and row["restart_count"] == 3
    stop.set()


def test_stalled_worker_is_stopped_then_restarted_never_two_copies():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    alive = []
    overlap = []
    lock = threading.Lock()

    def target(stop_event):
        with lock:
            alive.append(1)
            if len(alive) > 1:
                overlap.append(len(alive))
        try:
            ws.tick()                          # one tick, then no progress
            stop_event.wait()
        finally:
            with lock:
                alive.pop()

    h = sup.supervised("analytics", target, args=(stop,), stop=stop, stall_after=60)
    h.start()
    assert _wait(lambda: h.state == "running")
    sup.check(clock.advance(30))
    assert h.state == "running"
    sup.check(clock.advance(31))               # 61 s without a tick
    assert h.state == "stalled"
    row = sup.snapshot(clock())[0]
    assert row["healthy"] is False and "stalled" in row["last_error"]
    # The stalled incarnation was asked to stop through its own stop event only.
    assert not stop.is_set()
    assert _dead(h)
    sup.check(clock())
    assert h.state == "restarting"
    sup.check(clock.advance(ws.BACKOFF_BASE_SECONDS))
    assert _wait(lambda: h.state == "running")
    assert h.restart_count == 1 and not overlap
    stop.set()
    assert _dead(h)


def test_hung_worker_stays_stalled_and_is_not_duplicated():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    release = threading.Event()
    starts = []

    def target(_stop):
        starts.append(1)
        release.wait(10)                       # ignores its stop: truly hung

    h = sup.supervised("periodic_stills", target, args=(stop,), stop=stop, stall_after=10)
    h.start()
    sup.check(clock.advance(11))
    assert h.state == "stalled"
    for _ in range(3):
        sup.check(clock.advance(600))
    assert h.state == "stalled" and len(starts) == 1
    release.set()
    stop.set()
    assert _dead(h)


def test_one_shot_and_intentional_exits_are_not_deaths():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()

    def done(_stop):
        ws.success()

    def says_complete(_stop):
        ws.complete("staged update; restarting")

    def says_disabled(_stop):
        ws.disable("off here")

    one = sup.supervised("capability_sync", done, args=(stop,), stop=stop)
    comp = sup.supervised("remote_update", says_complete, args=(stop,), stop=stop)
    dis = sup.supervised("periodic_stills", says_disabled, args=(stop,), stop=stop)
    off = sup.supervised("site_control", done, args=(stop,), stop=stop, enabled=lambda: False)
    for h in (one, comp, dis, off):
        h.start()
    for h in (one, comp, dis):
        assert _dead(h)
    sup.check(clock())
    sup.check(clock.advance(3600))
    assert one.one_shot and one.state == "completed" and one.restart_count == 0
    assert comp.state == "completed" and comp.restart_count == 0
    assert dis.state == "disabled" and dis.restart_count == 0
    assert off.state == "disabled" and not off.is_alive()
    rows = {r["worker"]: r for r in sup.snapshot(clock())}
    assert rows["capability_sync"]["healthy"] is True        # completed with proof of success
    assert rows["remote_update"]["healthy"] is False          # completed without proof
    assert rows["periodic_stills"]["enabled"] is False
    assert rows["site_control"]["enabled"] is False and rows["site_control"]["state"] == "disabled"
    stop.set()


def test_a_worker_that_cannot_run_is_failed_at_once_with_its_reason():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()

    def no_queue(_stop):
        ws.fail("this recorder's event queue cannot be opened")

    h = sup.supervised("collector", no_queue, args=(stop,), stop=stop, recorder_local_id="r")
    h.start()
    assert _dead(h)
    sup.check(clock())
    sup.check(clock.advance(3600))
    row = sup.snapshot(clock())[0]
    assert row["state"] == "failed" and row["restart_count"] == 0 and row["healthy"] is False
    assert "queue cannot be opened" in row["last_error"]
    stop.set()


def test_a_held_recorder_worker_is_alive_but_never_healthy():
    import multi_recorder_fanout as fanout

    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    ready = threading.Event()
    ran = threading.Event()

    def target(_cfg, stop_event):
        ws.success()
        ran.set()
        stop_event.wait()

    h = sup.supervised("health", fanout._gated(target, ready, stop), args=(None, stop),
                       stop=stop, stall_after=30, recorder_local_id="held")
    h.start()
    assert _wait(lambda: sup.snapshot(clock())[0]["last_error"] is not None, timeout=5)
    row = sup.snapshot(clock())[0]
    assert row["state"] == "running" and row["healthy"] is False
    assert "login cannot be read" in row["last_error"]
    ready.set()                                 # Setup repaired the login
    assert ran.wait(5)
    assert _wait(lambda: sup.snapshot(clock())[0]["healthy"] is True)
    stop.set()
    assert _dead(h)


def test_recorder_scoped_failure_never_touches_another_recorder():
    clock = Clock()
    sup = _sup(clock, backoff_base=1, backoff_cap=1, max_restarts_per_hour=2)
    stop = threading.Event()
    b_starts = []

    def failing(_stop):
        raise RuntimeError("recorder A broke")

    def healthy(stop_event):
        b_starts.append(threading.get_ident())
        while not stop_event.wait(0.01):
            ws.tick()

    a = sup.supervised("collector", failing, args=(stop,), stop=stop,
                       recorder_local_id="rec-a", recorder_id="11111111-1111-1111-1111-111111111111")
    b = sup.supervised("collector", healthy, args=(stop,), stop=stop,
                       recorder_local_id="rec-b", recorder_id="22222222-2222-2222-2222-222222222222")
    assert a.key != b.key and len(sup.workers()) == 2
    a.start()
    b.start()
    for _ in range(10):
        assert _dead(a)
        sup.check(clock())
        if a.state == "failed":
            break
        sup.check(clock.advance(1))
    assert a.state == "failed"
    assert b.is_alive() and b.state == "running"
    assert b.restart_count == 0 and len(b_starts) == 1, "B was never restarted"
    rows = {r["recorder_id"]: r for r in sup.snapshot(clock())}
    assert rows["11111111-1111-1111-1111-111111111111"]["state"] == "failed"
    assert rows["22222222-2222-2222-2222-222222222222"]["healthy"] is True
    assert rows["22222222-2222-2222-2222-222222222222"]["last_error"] is None
    stop.set()
    assert _dead(b)


def test_runtime_stop_stops_every_incarnation_and_restarts_nothing():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    seen = []

    def target(stop_event):
        seen.append(stop_event)
        ws.tick()
        stop_event.wait()

    h = sup.supervised("health", target, args=(stop,), stop=stop)
    h.start()
    assert _wait(lambda: seen)
    assert seen[0] is not stop, "each incarnation runs on its own linked stop event"
    assert h._args == (stop,), "the registered args are kept as given"
    stop.set()
    assert _dead(h), "setting the runtime's stop stops the incarnation"
    sup.check(clock.advance(3600))
    assert h.state == "completed" and h.restart_count == 0
    sup.forget_stopped()
    assert sup.workers() == []


def test_start_twice_is_refused_like_a_thread():
    sup = _sup(Clock())
    stop = threading.Event()
    h = sup.supervised("recovery", lambda s: s.wait(), args=(stop,), stop=stop)
    h.start()
    try:
        h.start()
        raise AssertionError("a second start must be refused")
    except RuntimeError:
        pass
    stop.set()
    assert _dead(h)


def test_health_needs_evidence():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    go = threading.Event()
    gate = {"probe": True}

    def target(stop_event):
        go.wait(5)
        ws.tick()
        ws.record_error(RuntimeError("claim failed for 192.168.1.20"))
        ws.success()
        ws.record_error(RuntimeError("again"))
        stop_event.wait()

    h = sup.supervised("collector", target, args=(stop,), stop=stop,
                       probe=lambda: {"healthy": gate["probe"], "event_stream": "up"})
    h.start()
    row = sup.snapshot(clock())[0]
    assert row["state"] == "starting" and row["healthy"] is False, "alive is not evidence"
    go.set()
    assert _wait(lambda: sup.snapshot(clock())[0]["last_error"] == "RuntimeError: again")
    assert sup.snapshot(clock())[0]["healthy"] is False, "an error since the last success"
    stop.set()
    assert _dead(h)

    stop2 = threading.Event()

    def ticking(stop_event):
        ws.success()
        stop_event.wait()

    h2 = sup.supervised("collector", ticking, args=(stop2,), stop=stop2, recorder_local_id="r2",
                        probe=lambda: {"healthy": gate["probe"], "event_stream": "up"})
    h2.start()
    assert _wait(lambda: h2.state == "running")
    row = [r for r in sup.snapshot(clock()) if r["detail"].get("local_id") == "r2"][0]
    assert row["healthy"] is True and row["detail"]["event_stream"] == "up"
    gate["probe"] = False
    row = [r for r in sup.snapshot(clock()) if r["detail"].get("local_id") == "r2"][0]
    assert row["healthy"] is False, "the probe (event stream) is the collector's evidence"
    stop2.set()
    assert _dead(h2)


def test_poller_freshness_comes_from_the_registry():
    clock = Clock()
    sup = _sup(clock)
    stop = threading.Event()
    assert sup.poller_fresh("site_control", 60) is None      # not registered: unknown
    polled = threading.Event()

    def poller(stop_event):
        ws.success()
        polled.set()
        stop_event.wait()

    h = sup.supervised("site_control", poller, args=(stop,), stop=stop)
    h.start()
    assert polled.wait(5) and _wait(lambda: h.state == "running")
    assert sup.poller_fresh("site_control", 60, clock()) is True
    assert sup.poller_fresh("site_control", 60, clock() + 61) is False
    stop.set()
    assert _dead(h)
    assert sup.poller_fresh("site_control", 60, clock()) is False, "a dead poller is not fresh"


def test_ticks_outside_a_supervised_thread_are_harmless():
    ws.tick()
    ws.success()
    ws.record_error(RuntimeError("x"))
    ws.complete()
    ws.disable()


def test_unknown_worker_names_are_refused():
    try:
        _sup(Clock()).supervised("not_a_worker", lambda: None)
        raise AssertionError("unknown names must be refused")
    except ValueError:
        pass


def test_policy_defaults():
    assert ws.policy("collector")["critical"] is True
    assert ws.policy("collector")["stall_after"] is None       # evidence is the event stream
    assert ws.policy("capability_sync")["one_shot"] is True
    assert ws.policy("health", type("C", (), {"health_seconds": 300})())["stall_after"] >= 900
    assert set(ws.CRITICAL_WORKERS) <= set(ws.KNOWN_WORKERS)
    assert set(ws.ONE_SHOT_WORKERS) <= set(ws.KNOWN_WORKERS)

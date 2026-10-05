#!/usr/bin/env python3
"""NEW-L2: the canonical Agent produces the periodic stills the deployed Agents emit.

Deployed Hikvision Agents at HASCO Head Office and Chai Wala emit, for every configured
camera, a timed 'visual_sample' event with payload.source 'periodic_snapshot' and a still,
about every 300 s. 0125/0126 (wl_vision_claim_snapshots_v2) and the vision workers consume
them. Nothing in this repository produced them, so a 5.0.28 upgrade would have removed the
only timed stills at both sites.

These tests drive periodic_stills.periodic_still_worker on a simulated clock and check:
  * the spooled row matches the production contract exactly (event_type, payload keys and
    values, device_event_id format, device_ts/agent_ts, inline still), using a real
    production row's timestamp and identifier;
  * cadence (300 s per camera, staggered evenly) and jitter bounds;
  * rate limits (incident-still floor, minimum request spacing, still size bound);
  * unconfigured (Ignore) and recorder-disabled cameras are never sampled;
  * no still -> no event;
  * an unreachable or refusing recorder backs the worker off;
  * the event survives the spool and uploads through the normal upload path;
  * the packaged run loop starts the worker (run mode only).
"""
from __future__ import annotations

import base64
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import periodic_stills as ps  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import Channel, NvrAuthFailed, NvrUnreachable  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402
from spool import Spool  # noqa: E402

JPEG = b"\xff\xd8" + b"\x00" * 30000 + b"\xff\xd9"
BASE = datetime(2026, 10, 4, 22, 0, 0, tzinfo=timezone.utc)


class SimClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def wall(self):
        return BASE + timedelta(seconds=self.t - 1000.0)


class SimStop:
    """threading.Event stand-in: wait() advances the simulated clock."""

    def __init__(self, clock, seconds):
        self.clock, self.until = clock, clock.t + seconds

    def is_set(self):
        return self.clock.t >= self.until

    def wait(self, seconds=None):
        self.clock.t += max(float(seconds or 0.0), 0.01)
        return self.is_set()

    def set(self):
        self.until = self.clock.t


class FakeDriver:
    def __init__(self, clock, name="hikvision-isapi", channels=None, still=JPEG, cost=0.5):
        self.clock, self.name, self.cost = clock, name, cost
        self.channels = channels if channels is not None else [
            Channel(str(i), f"Camera {i}") for i in range(1, 9)]
        self.still = still
        self.calls: list[tuple[float, str]] = []
        self.closed = 0
        # driver.probe(): None = recorder healthy; an exception (or a callable returning one)
        # = the recorder itself is down / refusing.
        self.probe_error = None
        self.probes: list[float] = []

    def list_channels(self):
        return self.channels

    def get_snapshot(self, channel):
        self.calls.append((self.clock.t, str(channel)))
        self.clock.t += self.cost
        still = self.still(channel) if callable(self.still) else self.still
        if isinstance(still, Exception):
            raise still
        return still

    def probe(self):
        self.probes.append(self.clock.t)
        error = self.probe_error() if callable(self.probe_error) else self.probe_error
        if isinstance(error, Exception):
            raise error
        return SimpleNamespace(vendor="x", model=None)

    def close(self):
        self.closed += 1


class ListSpool:
    def __init__(self, waiting=0, max_rows=200_000):
        self.rows, self.waiting, self.max_rows = [], waiting, max_rows

    def add(self, row):
        self.rows.append(row)

    def count(self):
        return self.waiting + len(self.rows)

    def trim(self):
        return 0


def _cfg(**extra):
    base = dict(nvr_url="http://recorder.test", snapshots=True, snapshot_min_interval=60,
                camera_profiles=[])
    base.update(extra)
    return SimpleNamespace(**base)


def _run(seconds, *, driver=None, opener=None, cfg=None, spool=None, channels=None,
         clock=None, seed=7):
    clock = clock or SimClock()
    driver = driver or FakeDriver(clock)
    spool = spool if spool is not None else ListSpool()
    opens = []

    def default_opener(_cfg):
        opens.append(clock.t)
        return driver, SimpleNamespace(vendor="x", model=None)

    import random
    ps.periodic_still_worker(cfg or _cfg(), spool, SimStop(clock, seconds), channels,
                             open_driver=opener or default_opener, clock=clock,
                             wall=clock.wall, rng=random.Random(seed))
    return SimpleNamespace(clock=clock, driver=driver, spool=spool, opens=opens)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("WATCHLOG_PERIODIC_STILLS", raising=False)
    monkeypatch.delenv("WATCHLOG_PERIODIC_STILL_SECONDS", raising=False)
    monkeypatch.setattr(core, "log", lambda _m: None)
    # Never read the real credential store under ProgramData from a unit test.
    monkeypatch.setattr(core, "_credential_generation", lambda: "gen-0")


# --- contract ------------------------------------------------------------------------------

def test_device_event_id_matches_a_production_row():
    # Production (Chai Wala, 5.0.17): device_ts 2026-10-04 22:18:12.472565Z, channel 7
    # -> device_event_id 'hikvision-sample-7-59705076'.
    captured = datetime(2026, 10, 4, 22, 18, 12, 472565, tzinfo=timezone.utc)
    assert ps.device_event_id("hikvision", "7", captured) == "hikvision-sample-7-59705076"
    captured = datetime(2026, 9, 30, 11, 26, 34, 853537, tzinfo=timezone.utc)
    assert ps.device_event_id("hikvision", "4", captured) == "hikvision-sample-4-59692253"


def test_spooled_row_is_the_production_contract():
    run = _run(80)
    assert run.spool.rows, "no periodic still was produced"
    row = run.spool.rows[0]
    assert set(row) == {"channel", "event_type", "device_event_id", "device_ts", "agent_ts",
                        "payload", "snapshot_b64"}
    assert row["event_type"] == "visual_sample"
    assert row["payload"] == {"sample": True, "source": "periodic_snapshot",
                              "vendor": "hikvision"}
    assert row["channel"] == "1"
    assert re.fullmatch(r"hikvision-sample-1-\d+", row["device_event_id"])
    device_ts = datetime.fromisoformat(row["device_ts"].replace("Z", "+00:00"))
    agent_ts = datetime.fromisoformat(row["agent_ts"].replace("Z", "+00:00"))
    assert row["device_event_id"] == ps.device_event_id("hikvision", "1", device_ts)
    assert timedelta(0) <= agent_ts - device_ts < timedelta(seconds=1)
    assert base64.b64decode(row["snapshot_b64"]) == JPEG


@pytest.mark.parametrize("cls,vendor", [(HikvisionDriver, "hikvision"),
                                        (DahuaDriver, "dahua"), (OnvifDriver, "onvif")])
def test_vendor_word_matches_driver_family(cls, vendor):
    clock = SimClock()
    run = _run(80, driver=FakeDriver(clock, name=cls.name), clock=clock)
    assert run.spool.rows
    assert run.spool.rows[0]["payload"]["vendor"] == vendor
    assert run.spool.rows[0]["device_event_id"].startswith(f"{vendor}-sample-1-")


# --- cadence, jitter, rate limits ----------------------------------------------------------

def test_cadence_is_300s_per_camera_staggered_across_cameras():
    run = _run(3600)
    per_cam: dict[str, list[float]] = {}
    for t, ch in run.driver.calls:
        per_cam.setdefault(ch, []).append(t)
    assert sorted(per_cam, key=int) == [str(i) for i in range(1, 9)]
    for ch, times in per_cam.items():
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert gaps and all(284.0 <= g <= 316.0 for g in gaps), (ch, gaps)
        assert 11 <= len(times) <= 12, (ch, len(times))
    first_round = [t for t, _ in run.driver.calls[:8]]
    spacing = [b - a for a, b in zip(first_round, first_round[1:])]
    assert all(36.0 <= s <= 39.0 for s in spacing), spacing        # 300 s / 8 cameras
    assert first_round[0] - 1000.0 >= ps.STARTUP_DELAY_SECONDS - 1
    assert len(run.spool.rows) == len(run.driver.calls)


def test_jitter_stays_within_bounds_and_varies():
    import random
    schedule = ps.Schedule(300, floor=60, rng=random.Random(1))
    samples = [schedule.interval() for _ in range(2000)]
    assert min(samples) >= 300 * (1 - ps.JITTER_FRACTION)
    assert max(samples) <= 300 * (1 + ps.JITTER_FRACTION)
    assert max(samples) - min(samples) > 10                      # actually jittered
    fast = ps.Schedule(60, floor=60, rng=random.Random(1))
    assert min(fast.interval() for _ in range(2000)) >= 60       # never below the floor


def test_cadence_setting_is_clamped_to_the_still_rate_limits(monkeypatch):
    assert ps.load_settings(_cfg())["cadence_seconds"] == 300
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILL_SECONDS", "10")
    assert ps.load_settings(_cfg())["cadence_seconds"] == 60
    assert ps.load_settings(_cfg(snapshot_min_interval=120))["cadence_seconds"] == 120
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILL_SECONDS", "999999")
    assert ps.load_settings(_cfg())["cadence_seconds"] == ps.MAX_CADENCE_SECONDS
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILL_SECONDS", "not-a-number")
    assert ps.load_settings(_cfg())["cadence_seconds"] == 300


def test_cadence_setting_from_watchlog_ini(tmp_path):
    ini = tmp_path / "watchlog.ini"
    ini.write_text("[watchlog]\nperiodic_still_seconds = 600\n", encoding="utf-8")
    assert ps.load_settings(_cfg(_ini_path=ini))["cadence_seconds"] == 600
    ini.write_text("[watchlog]\nperiodic_stills = false\n", encoding="utf-8")
    assert ps.load_settings(_cfg(_ini_path=ini))["enabled"] is False


def test_requests_never_closer_than_the_minimum_spacing():
    import random
    schedule = ps.Schedule(300, rng=random.Random(2))
    schedule.set_channels(["1", "2", "3"], 0.0, start=0.0)
    schedule.next_due = {"1": 0.0, "2": 0.0, "3": 0.0}           # all overdue at once
    assert schedule.due(10.0) == "1"
    schedule.done("1", 10.0)
    assert schedule.due(10.5) is None
    assert schedule.due(10.0 + ps.MIN_SPACING_SECONDS) == "2"
    run = _run(3600)
    times = [t for t, _ in run.driver.calls]
    assert all(b - a >= ps.MIN_SPACING_SECONDS for a, b in zip(times, times[1:]))


def test_oversized_or_non_jpeg_still_produces_no_event():
    clock = SimClock()
    big = b"\xff\xd8" + b"\x00" * core.SNAPSHOT_MAX_BYTES
    run = _run(400, driver=FakeDriver(clock, still=big), clock=clock)
    assert run.driver.calls and run.spool.rows == []
    clock = SimClock()
    run = _run(400, driver=FakeDriver(clock, still=b"<html>login</html>"), clock=clock)
    assert run.driver.calls and run.spool.rows == []


def test_no_still_produces_no_event():
    clock = SimClock()
    run = _run(400, driver=FakeDriver(clock, still=None), clock=clock)
    assert run.driver.calls and run.spool.rows == []
    clock = SimClock()
    stills = lambda ch: None if ch == "2" else JPEG                # noqa: E731
    run = _run(400, driver=FakeDriver(clock, still=stills), clock=clock)
    assert {r["channel"] for r in run.spool.rows} == {"1", "3", "4", "5", "6", "7", "8"}


# --- configured cameras only ---------------------------------------------------------------

def test_ignored_and_disabled_cameras_are_never_sampled():
    clock = SimClock()
    channels = [Channel("1"), Channel("2"), Channel("3", enabled=False), Channel("4")]
    cfg = _cfg(camera_profiles=[{"channel": "2", "monitored": False},
                                {"channel": "4", "monitored": True}])
    run = _run(1200, driver=FakeDriver(clock, channels=channels), cfg=cfg, clock=clock)
    asked = {ch for _, ch in run.driver.calls}
    assert asked == {"1", "4"}
    assert {r["channel"] for r in run.spool.rows} == {"1", "4"}


def test_startup_channels_are_the_fallback_when_the_list_cannot_be_read():
    clock = SimClock()
    driver = FakeDriver(clock, channels=[])
    run = _run(400, driver=driver, clock=clock,
               channels=[{"channel": "3", "name": "Gate"}, {"channel": "5", "name": None}],
               cfg=_cfg(camera_profiles=[{"channel": "5", "monitored": False}]))
    assert {ch for _, ch in run.driver.calls} == {"3"}


def test_no_configured_camera_means_no_request():
    clock = SimClock()
    run = _run(1200, driver=FakeDriver(clock, channels=[]), clock=clock, channels=[])
    assert run.driver.calls == [] and run.spool.rows == []


def test_disabled_by_configuration(monkeypatch):
    def opener(_cfg):
        raise AssertionError("recorder must not be contacted")
    run = _run(400, opener=opener, cfg=_cfg(snapshots=False))
    assert run.spool.rows == []
    monkeypatch.setenv("WATCHLOG_PERIODIC_STILLS", "false")
    run = _run(400, opener=opener)
    assert run.spool.rows == []
    run = _run(400, opener=opener, cfg=_cfg(nvr_url=""))
    assert run.spool.rows == []


# --- outage back-off -----------------------------------------------------------------------

def test_unreachable_recorder_backs_off_exponentially():
    clock = SimClock()
    attempts = []

    def opener(_cfg):
        attempts.append(clock.t)
        raise NvrUnreachable("http://192.168.1.64/ISAPI/System/deviceInfo: timed out")

    run = _run(3600, opener=opener, clock=clock)
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert run.spool.rows == []
    assert 4 <= len(attempts) <= 8, attempts
    assert 60 <= gaps[0] <= 67 and 120 <= gaps[1] <= 133 and 240 <= gaps[2] <= 265, gaps
    assert all(g <= ps.BACKOFF_MAX_SECONDS * 1.1 for g in gaps)


def test_auth_refusal_waits_the_maximum_at_once():
    clock = SimClock()
    attempts = []

    def opener(_cfg):
        attempts.append(clock.t)
        raise NvrAuthFailed("HTTP 401 — recorder rejected the username or password")

    _run(1800, opener=opener, clock=clock)
    assert len(attempts) == 2
    assert attempts[1] - attempts[0] >= ps.BACKOFF_MAX_SECONDS


def test_recorder_dropping_mid_run_closes_the_driver_and_backs_off():
    clock = SimClock()
    state = {"down": False}

    def still(_ch):
        return NvrUnreachable("connection timed out") if state["down"] else JPEG

    driver = FakeDriver(clock, still=still)
    driver.probe_error = lambda: NvrUnreachable("connection timed out") if state["down"] else None
    opens = []

    def opener(_cfg):
        opens.append(clock.t)
        if state["down"]:
            raise NvrUnreachable("connection timed out")
        return driver, None

    import random
    spool = ListSpool()
    stop = SimStop(clock, 100)
    ps.periodic_still_worker(_cfg(), spool, stop, None, open_driver=opener, clock=clock,
                             wall=clock.wall, rng=random.Random(3))
    good = len(spool.rows)
    assert good >= 1
    state["down"] = True
    stop = SimStop(clock, 1800)
    calls_before = len(driver.calls)
    ps.periodic_still_worker(_cfg(), spool, stop, None, open_driver=opener, clock=clock,
                             wall=clock.wall, rng=random.Random(3))
    assert len(spool.rows) == good                              # no event while down
    assert len(driver.calls) - calls_before <= 2                # stops asking at once
    assert driver.closed >= 1
    reopen_gaps = [b - a for a, b in zip(opens[2:], opens[3:])]
    assert reopen_gaps and reopen_gaps == sorted(reopen_gaps)   # growing back-off


def test_every_camera_failing_quietly_is_treated_as_the_recorder():
    # dahua-cgi and onvif return None (not an exception) when the recorder is unreachable.
    clock = SimClock()
    driver = FakeDriver(clock, name="dahua-cgi", still=None)
    run = _run(3600, driver=driver, clock=clock)
    assert run.spool.rows == []
    # 8 cameras fail -> back off 60 s, reconnect, 8 more fail -> 120 s, ...; never one
    # still per camera per 37 s for an hour (96 requests).
    assert len(run.driver.calls) <= 48, len(run.driver.calls)
    assert len(run.opens) >= 2 and run.driver.closed >= 1


def test_after_a_back_off_cameras_are_restaggered_not_burst():
    clock = SimClock()
    state = {"fail": 0}

    def opener(_cfg):
        if state["fail"] < 2:
            state["fail"] += 1
            raise NvrUnreachable("timed out")
        return driver, None

    driver = FakeDriver(clock)
    import random
    spool = ListSpool()
    ps.periodic_still_worker(_cfg(), spool, SimStop(clock, 900), None, open_driver=opener,
                             clock=clock, wall=clock.wall, rng=random.Random(4))
    times = [t for t, _ in driver.calls[:8]]
    assert len(times) == 8
    assert all(b - a >= 30 for a, b in zip(times, times[1:])), times


def _per_channel(run):
    times: dict[str, list[float]] = {}
    for t, ch in run.driver.calls:
        times.setdefault(ch, []).append(t)
    rows: dict[str, int] = {}
    for r in run.spool.rows:
        rows[r["channel"]] = rows.get(r["channel"], 0) + 1
    return times, rows


def test_one_camera_refusing_or_timing_out_never_starves_the_others():
    # PS-1: one channel's still timing out (NvrUnreachable) and another's refused (403 for an
    # account without preview rights on that channel) while driver.probe() proves the recorder
    # healthy are camera faults: every other camera keeps one still per cadence.
    clock = SimClock()

    def still(ch):
        if ch == "3":
            return NvrUnreachable("http://recorder.test/ISAPI/Streaming/channels/301/picture: "
                                  "timed out")
        if ch == "6":
            return NvrAuthFailed("http://recorder.test/ISAPI/Streaming/channels/601/picture: "
                                 "HTTP 403 — recorder rejected the username or password")
        return JPEG

    run = _run(4 * 3600, driver=FakeDriver(clock, still=still), clock=clock)
    times, rows = _per_channel(run)
    assert len(run.opens) == 1, run.opens                       # never treated as the recorder
    assert run.driver.probes, "the recorder was never re-checked"
    for ch in [str(i) for i in range(1, 9)]:
        gaps = [b - a for a, b in zip(times[ch], times[ch][1:])]
        assert gaps and min(gaps) >= 300 * (1 - ps.JITTER_FRACTION) - 1, (ch, min(gaps))
        if ch in ("3", "6"):
            assert ch not in rows                               # no still -> no event
        else:
            assert 46 <= rows.get(ch, 0) <= 49, (ch, rows)      # ~4 h / 300 s


def test_recorder_flapping_mid_round_still_rotates_through_every_camera():
    # PS-1: a recorder that drops ~100 s after every reconnect (genuinely down: the recheck
    # fails too) must not restagger from the head of the list each time, or cameras late in
    # the list never get a turn. Rotation: the camera waiting longest goes first.
    clock = SimClock()
    state = {"up_until": 0.0}

    def down():
        return clock.t > state["up_until"]

    driver = FakeDriver(clock, still=lambda _ch: NvrUnreachable("timed out") if down() else JPEG)
    driver.probe_error = lambda: NvrUnreachable("timed out") if down() else None

    def opener(_cfg):
        state["up_until"] = clock.t + 100.0
        return driver, None

    import random
    spool = ListSpool()
    ps.periodic_still_worker(_cfg(), spool, SimStop(clock, 4 * 3600), None, open_driver=opener,
                             clock=clock, wall=clock.wall, rng=random.Random(5))
    _times, rows = _per_channel(SimpleNamespace(driver=driver, spool=spool))
    assert sorted(rows, key=int) == [str(i) for i in range(1, 9)], rows
    assert min(rows.values()) >= 20, rows
    stills: dict[str, list[datetime]] = {}
    for r in spool.rows:
        stills.setdefault(r["channel"], []).append(
            datetime.fromisoformat(r["device_ts"].replace("Z", "+00:00")))
    for ch, ts in stills.items():                               # stills, not failed attempts
        gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
        assert min(gaps) >= 300 * (1 - ps.JITTER_FRACTION) - 1, (ch, min(gaps))


def test_snapshot_refused_and_recheck_refused_is_a_recorder_auth_back_off():
    clock = SimClock()
    refused = NvrAuthFailed("HTTP 401 — recorder rejected the username or password")
    driver = FakeDriver(clock, still=refused)
    driver.probe_error = refused
    run = _run(1800, driver=driver, clock=clock)
    assert run.spool.rows == []
    first = run.driver.calls[0][0]
    assert run.driver.probes and run.driver.probes[0] >= first   # the recorder was re-checked
    assert len(run.opens) == 2 and run.opens[1] - first >= ps.BACKOFF_MAX_SECONDS, run.opens
    assert run.driver.closed >= 1


def test_auth_refusal_escalates_like_the_collector():
    # PS-2: a wrong password must not cost the recorder more failed logins than the event
    # collector's own 5/15/30-min breaker (about 6 in 2 h): 15 min, then 30 min thereafter.
    clock = SimClock()
    attempts = []

    def opener(_cfg):
        attempts.append(clock.t)
        raise NvrAuthFailed("HTTP 401 — recorder rejected the username or password")

    _run(2 * 3600, opener=opener, clock=clock)
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert len(attempts) <= 5, gaps
    assert 900 <= gaps[0] <= 990, gaps
    assert all(1800 <= g <= 1980 for g in gaps[1:]), gaps


def test_credential_change_in_setup_wakes_the_auth_back_off(monkeypatch):
    # PS-2: when Setup rewrites the recorder credential the stills resume within seconds,
    # with the new credential loaded, instead of waiting out the auth back-off.
    clock = SimClock()
    changed_at = clock.t + 120.0
    monkeypatch.setattr(core, "_credential_generation",
                        lambda: "gen-1" if clock.t >= changed_at else "gen-0")
    attempts, reloads = [], []
    driver = FakeDriver(clock)

    def opener(_cfg):
        attempts.append(clock.t)
        if reloads:
            return driver, None
        raise NvrAuthFailed("HTTP 401 — recorder rejected the username or password")

    cfg = _cfg(load_recorder_credential=lambda: reloads.append(clock.t))
    run = _run(600, opener=opener, cfg=cfg, clock=clock, driver=driver)
    assert len(reloads) == 1 and changed_at <= reloads[0] <= changed_at + 6, reloads
    assert len(attempts) == 2 and attempts[1] <= changed_at + 6, attempts
    assert run.spool.rows                                       # stills flow again


# --- spool: survives an outage, uploads through the normal path ----------------------------

def test_spool_round_trip_and_normal_upload(tmp_path):
    spool = Spool(tmp_path / "spool.sqlite")
    try:
        run = _run(400, spool=spool)
        assert spool.count() == len(run.driver.calls) >= 8
        spool.close()
        spool = Spool(tmp_path / "spool.sqlite")                 # restart: still queued
        ids, rows = spool.take(500)
        assert len(rows) == len(run.driver.calls)
        assert all(r["payload"] == {"sample": True, "source": "periodic_snapshot",
                                    "vendor": "hikvision"} for r in rows)

        sent = []

        class Cloud:
            def call(self, fn, **kw):
                sent.append((fn, kw))
                return {"received": len(kw["p_events"]), "inserted": len(kw["p_events"]),
                        "skipped": 0, "snapshots": len(kw["p_events"])}

        core.upload_once(Cloud(), {"agent_id": "a", "agent_key": "k"}, spool)
        assert sent and sent[0][0] == "wl_ingest_events"
        uploaded = sent[0][1]["p_events"]
        assert uploaded == rows[:len(uploaded)]
        assert all(base64.b64decode(r["snapshot_b64"]) == JPEG for r in uploaded)
        assert spool.count() == len(rows) - len(uploaded)
    finally:
        spool.close()


def test_long_outage_pauses_stills_before_recorder_events_are_trimmed():
    spool = ListSpool(waiting=ps.SPOOL_HIGH_WATER_ROWS)
    run = _run(1200, spool=spool)
    assert run.driver.calls == [] and spool.rows == []
    small = ListSpool(waiting=300, max_rows=1000)                # cap/4 = 250 < 2000
    run = _run(1200, spool=small)
    assert run.driver.calls == [] and small.rows == []


# --- packaged run loop wiring --------------------------------------------------------------

def test_shipped_run_loop_starts_the_periodic_still_worker(monkeypatch, tmp_path):
    started = {}
    ready = threading.Event()

    def worker(cfg, spool, stop, channels):
        started.update(cfg=cfg, spool=spool, channels=channels,
                       thread=threading.current_thread().name)
        ready.set()

    def idle(*args, **kwargs):
        return None

    class FakeSpool:
        def __init__(self, *a, **k):
            pass

        def count(self):
            return 0

        def close(self):
            pass

    sleeps = {"n": 0}

    def sleep(_s):
        sleeps["n"] += 1
        ready.wait(5)
        if sleeps["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core, "collector", idle)
    for name in ("recovery_worker", "health_worker", "command_worker", "health_cycle",
                 "upload_once", "heartbeat"):
        monkeypatch.setattr(core, name, idle)
    monkeypatch.setattr(analytics_agent, "analytics_worker", idle)
    monkeypatch.setattr(analytics_agent, "archive_worker", idle)
    monkeypatch.setattr(analytics_agent, "Spool", FakeSpool)
    monkeypatch.setattr(analytics_agent.time, "sleep", sleep)
    monkeypatch.setattr(ps, "periodic_still_worker", worker)
    cfg = SimpleNamespace(spool_path=tmp_path / "spool.sqlite", spool_max_rows=0,
                          health_batch=4, health_concurrency=2, upload_seconds=15,
                          heartbeat_seconds=60, health_seconds=300, recovery_enabled=False,
                          last_live_path=tmp_path / "last_live.json",
                          recovery_threshold_seconds=180)
    chans = [{"channel": "1", "name": "Gate"}]
    analytics_agent.enhanced_cmd_run(cfg, {"agent_id": "a", "agent_key": "k"}, object(),
                                     once=False, device=None, channels=chans)
    assert ready.is_set(), "the packaged run loop did not start the periodic still worker"
    assert started["cfg"] is cfg and started["channels"] == chans
    assert isinstance(started["spool"], FakeSpool)                # the normal event spool
    assert started["thread"] == "periodic-stills"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

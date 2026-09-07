#!/usr/bin/env python3
"""Phase A — increment 4: camera HYBRID health (native fault + bounded probe).

Ties the Increment-1 state machine to real signals: a native video-loss event is an
immediate OFFLINE, and a bounded, authenticated snapshot probe verifies a quiet camera.

The two non-negotiables under test:
  1. an upper-layer failure is NEVER a camera failure — a probe that hits 401/403 or a dead
     recorder makes ALL dependent cameras UNKNOWN, only a channel-specific failure against a
     PROVEN-HEALTHY recorder may degrade/offline that one camera;
  2. the recorder is protected — one recorder assessment per cycle, fair round-robin probing,
     a strict concurrency cap (never "all 8 at once"), and a probe stall/exception can never
     propagate to break the caller (heartbeat/event ingestion).

Red before camera_health.py exists.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import camera_health as ch  # noqa: E402
from camera_health import (CameraHealthMonitor, ProbeResult,  # noqa: E402
                           classify_snapshot_probe, round_robin_batch)
from drivers.base import NvrAuthFailed, NvrUnreachable, DriverError  # noqa: E402
from health_model import Health  # noqa: E402

EIGHT = [str(i) for i in range(1, 9)]
JPEG = b"\xff\xd8\xff\xe0rest-of-a-jpeg"


# ---- helpers: fake recorder assessments + probes ------------------------------

def assess_ok(reported=EIGHT, disabled=()):
    return {"nvr": {"reachable": True, "auth_ok": True, "state": "ok", "reason": "ok"},
            "channels": {"enumerated": True,
                         "reported": [{"channel": c, "name": None, "enabled": c not in disabled}
                                      for c in reported]}}


def assess_unreachable():
    return {"nvr": {"reachable": False, "auth_ok": None, "state": "unreachable",
                    "reason": "nvr_unreachable"}, "channels": {"enumerated": False}}


def assess_auth_failed():
    return {"nvr": {"reachable": True, "auth_ok": False, "state": "auth_failed",
                    "reason": "nvr_auth_failed"}, "channels": {"enumerated": False}}


def probe_ok(_ch):
    return ProbeResult(ok=True)


def probe_fail(_ch):
    return ProbeResult(ok=False, upper=None, reason="probe_timeout")


def probe_auth(_ch):
    return ProbeResult(ok=False, upper="nvr_auth_failed", reason="nvr_auth_failed")


def state_of(mon, channel):
    return mon.machines[channel].state


# ---- 1. probe classification: upper-layer vs channel-specific -----------------

def test_probe_valid_jpeg_is_ok():
    assert classify_snapshot_probe(JPEG, lambda: None).ok is True


def test_probe_401_is_nvr_auth_not_camera():
    def recheck(): raise NvrAuthFailed("HTTP 401")
    r = classify_snapshot_probe(None, recheck)
    assert r.ok is False and r.upper == "nvr_auth_failed"


def test_probe_recorder_unreachable_is_nvr_unreachable():
    def recheck(): raise NvrUnreachable("no route")
    r = classify_snapshot_probe(None, recheck)
    assert r.ok is False and r.upper == "nvr_unreachable"


def test_probe_channel_fail_while_recorder_healthy_is_channel_specific():
    # snapshot failed but the recorder re-check succeeds -> the ONE camera is at fault
    r = classify_snapshot_probe(None, lambda: None)
    assert r.ok is False and r.upper is None


def test_probe_recheck_errors_do_not_blame_the_camera():
    # if we cannot PROVE the recorder healthy, we must not fault the camera
    def recheck(): raise DriverError("HTTP 500")
    r = classify_snapshot_probe(None, recheck)
    assert r.ok is False and r.upper is not None


# ---- 2. camera state machine fed by real signals ------------------------------

def one_cam(**kw):
    return CameraHealthMonitor(["1"], batch_size=1, concurrency=1, **kw)


def test_quiet_healthy_camera_becomes_operational():
    m = one_cam()
    m.run_cycle(assess_ok, probe_ok)
    assert state_of(m, "1") == Health.OPERATIONAL


def test_one_failed_probe_is_degraded_not_offline():
    m = one_cam(fail_threshold=3)
    m.run_cycle(assess_ok, probe_ok)      # OPERATIONAL
    m.run_cycle(assess_ok, probe_fail)    # 1 failure
    assert state_of(m, "1") == Health.DEGRADED


def test_k_failures_go_offline():
    m = one_cam(fail_threshold=3)
    for _ in range(3):
        m.run_cycle(assess_ok, probe_fail)
    assert state_of(m, "1") == Health.OFFLINE


def test_native_video_loss_is_immediate_offline():
    m = one_cam()
    m.run_cycle(assess_ok, probe_ok)      # OPERATIONAL
    m.record_native_fault("1")            # a VideoLoss event arrives
    assert state_of(m, "1") == Health.OFFLINE


def test_recovery_requires_j_successful_probes():
    m = one_cam(fail_threshold=2, recover_threshold=2)
    m.run_cycle(assess_ok, probe_fail)
    m.run_cycle(assess_ok, probe_fail)    # OFFLINE
    assert state_of(m, "1") == Health.OFFLINE
    m.run_cycle(assess_ok, probe_ok)      # 1 good — still offline
    assert state_of(m, "1") == Health.OFFLINE
    m.run_cycle(assess_ok, probe_ok)      # J=2 good — recovered
    assert state_of(m, "1") == Health.OPERATIONAL


def test_missing_camera_never_offline():
    m = one_cam(fail_threshold=1)
    m.run_cycle(assess_ok, probe_fail)                       # OFFLINE candidate
    m.run_cycle(lambda: assess_ok(reported=[]), probe_ok)    # ch1 no longer reported -> missing
    assert state_of(m, "1") == Health.UNKNOWN and state_of(m, "1") != Health.OFFLINE


def test_disabled_camera_never_offline():
    m = one_cam(fail_threshold=1)
    m.run_cycle(lambda: assess_ok(disabled=["1"]), probe_ok)
    assert state_of(m, "1") == Health.UNKNOWN and state_of(m, "1") != Health.OFFLINE


def test_nvr_unreachable_makes_all_cameras_unknown():
    m = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2, fail_threshold=1)
    # drive some cameras to OFFLINE first
    for _ in range(2):
        m.run_cycle(assess_ok, probe_fail)
    assert any(state_of(m, c) == Health.OFFLINE for c in EIGHT)
    m.run_cycle(assess_unreachable, probe_ok)
    assert all(state_of(m, c) == Health.UNKNOWN for c in EIGHT)


def test_nvr_auth_failed_makes_all_cameras_unknown():
    m = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    m.run_cycle(assess_auth_failed, probe_ok)
    assert all(state_of(m, c) == Health.UNKNOWN for c in EIGHT)


def test_probe_revealing_auth_midcycle_makes_all_unknown():
    # recorder assessment says OK, but a snapshot probe surfaces 401 -> the WHOLE recorder is
    # in auth failure; no camera may be blamed.
    m = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    m.run_cycle(assess_ok, probe_auth)
    assert all(state_of(m, c) == Health.UNKNOWN for c in EIGHT)


def test_probe_exception_cannot_break_the_cycle():
    m = one_cam()

    def boom(_ch):
        raise RuntimeError("snapshot hard crash")

    # must not raise — a broken probe cannot be allowed to kill the caller's loop
    m.run_cycle(assess_ok, boom)
    assert state_of(m, "1") in (Health.DEGRADED, Health.UNKNOWN, Health.OPERATIONAL)


# ---- 3. recorder protection ---------------------------------------------------

def test_one_recorder_assessment_per_cycle():
    calls = {"n": 0}

    def assess():
        calls["n"] += 1
        return assess_ok()

    m = CameraHealthMonitor(EIGHT, batch_size=4, concurrency=2)
    m.run_cycle(assess, probe_ok)
    assert calls["n"] == 1                    # exactly one authenticated recorder assessment


def test_round_robin_is_fair_over_cycles():
    seen = {c: 0 for c in EIGHT}

    def probe(c):
        seen[c] += 1
        return ProbeResult(ok=True)

    m = CameraHealthMonitor(EIGHT, batch_size=2, concurrency=1)
    for _ in range(4):                        # 4 cycles x 2 = every channel exactly once
        m.run_cycle(assess_ok, probe)
    assert set(seen.values()) == {1}, seen    # perfectly fair coverage


def test_concurrency_cap_is_never_exceeded():
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def probe(_c):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0.03)
        with lock:
            live["now"] -= 1
        return ProbeResult(ok=True)

    m = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)   # all 8 this cycle, cap 2
    m.run_cycle(assess_ok, probe)
    assert live["max"] <= 2, f"concurrency cap exceeded: {live['max']}"


def test_batch_size_prevents_probing_everything_at_once():
    seen = []

    def probe(c):
        seen.append(c)
        return ProbeResult(ok=True)

    m = CameraHealthMonitor(EIGHT, batch_size=2, concurrency=2)
    m.run_cycle(assess_ok, probe)
    assert len(seen) == 2                      # one cycle probes only the batch, not all 8


def test_round_robin_batch_pure():
    b, cur = round_robin_batch(0, EIGHT, 3)
    assert b == ["1", "2", "3"] and cur == 3
    b2, cur2 = round_robin_batch(cur, EIGHT, 3)
    assert b2 == ["4", "5", "6"] and cur2 == 6


# ---- telemetry safety ---------------------------------------------------------

def test_report_carries_no_image_bytes_or_secrets():
    import json
    m = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    rep = m.run_cycle(assess_ok, probe_ok)
    # only channel/health/reason per camera — no bytes, no base64, nothing sensitive
    for cam in rep["cameras"]:
        assert set(cam) == {"channel", "health", "reason"}
    blob = json.dumps(rep).lower()
    for bad in ("base64", "jpeg", "\\xff", "password", "http://", "authorization"):
        assert bad not in blob


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

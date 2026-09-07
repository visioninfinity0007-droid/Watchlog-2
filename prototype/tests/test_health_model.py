#!/usr/bin/env python3
"""Phase A — camera health STATE MACHINE (increment 1: schema/state contract).

Pure-logic, no I/O — the authoritative transition rules for a single channel, per the
approved design (docs/design/OPERATIONAL_INTELLIGENCE_ARCHITECTURE.md §5/§6):

  * inventory_state {PRESENT|MISSING|DISABLED|UNKNOWN} is SEPARATE from health_state
    {OPERATIONAL|DEGRADED|OFFLINE|UNKNOWN}; a removed/disabled channel is NOT "offline".
  * an upper layer down (NVR unreachable/auth-failed, or NVR/agent unknown) makes the
    camera UNKNOWN, never OFFLINE.
  * hysteresis: K consecutive probe failures (or a native video-loss) -> OFFLINE; J
    consecutive good probes -> OPERATIONAL; an isolated blip -> DEGRADED, never OFFLINE.
  * health is NEVER inferred from detection volume — only probes / native fault events.

These are red before health_model.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import health_model as hm  # noqa: E402
from health_model import Inventory, Health, Nvr, Reason, Probe, CameraHealthMachine  # noqa: E402

OK = Probe(ok=True)
FAIL = Probe(ok=False, reason=Reason.PROBE_TIMEOUT)
PRESENT_OK = dict(inventory=Inventory.PRESENT, nvr=Nvr.OK)
T = 0  # tests are tick-based; the machine only uses `now` for timestamps


def feed(m, *steps):
    """Apply a sequence of observe() calls; return the last state."""
    last = None
    for i, kw in enumerate(steps):
        last = m.observe(i + 1, **{**PRESENT_OK, **kw})
    return last


def test_present_healthy_is_operational():
    m = CameraHealthMachine()
    feed(m, {"probe": OK}, {"probe": OK}, {"probe": OK})
    assert m.state == Health.OPERATIONAL and m.reason == Reason.OK


def test_single_failure_is_degraded_not_offline():  # anti-flap
    m = CameraHealthMachine()
    feed(m, {"probe": OK}, {"probe": OK})
    m.observe(3, **PRESENT_OK, probe=FAIL)
    assert m.state == Health.DEGRADED and m.state != Health.OFFLINE


def test_sustained_failure_offline_with_hysteresis():
    m = CameraHealthMachine(fail_threshold=3)
    feed(m, {"probe": OK})
    m.observe(2, **PRESENT_OK, probe=FAIL); assert m.state == Health.DEGRADED   # 1 fail
    m.observe(3, **PRESENT_OK, probe=FAIL); assert m.state == Health.DEGRADED   # 2 fails, still not offline
    m.observe(4, **PRESENT_OK, probe=FAIL); assert m.state == Health.OFFLINE    # 3rd consecutive -> offline
    assert m.reason == Reason.PROBE_TIMEOUT


def test_recovery_needs_J_successes():
    m = CameraHealthMachine(fail_threshold=3, recover_threshold=2)
    feed(m, {"probe": FAIL}, {"probe": FAIL}, {"probe": FAIL})
    assert m.state == Health.OFFLINE
    m.observe(4, **PRESENT_OK, probe=OK); assert m.state == Health.OFFLINE      # 1 ok, not yet recovered
    m.observe(5, **PRESENT_OK, probe=OK); assert m.state == Health.OPERATIONAL  # J=2 -> recovered
    assert m.last_recovery_at == 5


def test_native_video_loss_is_immediate_offline():
    m = CameraHealthMachine()
    feed(m, {"probe": OK}, {"probe": OK})
    m.observe(3, **PRESENT_OK, native_video_loss=True)
    assert m.state == Health.OFFLINE and m.reason == Reason.VIDEO_LOSS


def test_missing_channel_is_unknown_not_offline():
    m = CameraHealthMachine()
    feed(m, {"probe": OK})
    r = m.observe(2, inventory=Inventory.MISSING, nvr=Nvr.OK)
    assert m.state == Health.UNKNOWN and m.reason == Reason.CHANNEL_MISSING
    assert m.state != Health.OFFLINE


def test_disabled_channel_is_unknown_not_broken():
    m = CameraHealthMachine()
    r = m.observe(1, inventory=Inventory.DISABLED, nvr=Nvr.OK)
    assert m.state == Health.UNKNOWN and m.reason == Reason.CHANNEL_DISABLED
    assert m.state != Health.OFFLINE


def test_nvr_unreachable_makes_camera_unknown():
    m = CameraHealthMachine()
    feed(m, {"probe": OK}, {"probe": OK})           # OPERATIONAL
    m.observe(3, inventory=Inventory.PRESENT, nvr=Nvr.UNREACHABLE)
    assert m.state == Health.UNKNOWN and m.reason == Reason.NVR_UNREACHABLE
    assert m.state != Health.OFFLINE                # the CAMERA is not "offline" — the recorder is


def test_nvr_auth_failed_is_classified_unknown():
    m = CameraHealthMachine()
    m.observe(1, inventory=Inventory.PRESENT, nvr=Nvr.AUTH_FAILED)
    assert m.state == Health.UNKNOWN and m.reason == Reason.NVR_AUTH_FAILED


def test_nvr_unknown_maps_to_agent_unreachable_reason():
    m = CameraHealthMachine()
    m.observe(1, inventory=Inventory.UNKNOWN, nvr=Nvr.UNKNOWN)
    assert m.state == Health.UNKNOWN and m.reason == Reason.AGENT_UNREACHABLE


def test_upper_layer_dominates_a_failing_camera():
    m = CameraHealthMachine(fail_threshold=2)
    feed(m, {"probe": FAIL}, {"probe": FAIL})       # OFFLINE
    assert m.state == Health.OFFLINE
    m.observe(3, inventory=Inventory.PRESENT, nvr=Nvr.UNREACHABLE)
    assert m.state == Health.UNKNOWN                # recorder down -> we can no longer claim OFFLINE


def test_no_flap_on_a_single_blip():
    m = CameraHealthMachine(fail_threshold=3, recover_threshold=2)
    feed(m, {"probe": OK}, {"probe": OK})           # OPERATIONAL
    states = []
    for p in (FAIL, OK, OK):                        # a ~one-cycle blip then recovery
        m.observe(9, **PRESENT_OK, probe=p); states.append(m.state)
    assert Health.OFFLINE not in states             # a blip must never reach OFFLINE
    assert m.state == Health.OPERATIONAL            # and it recovers


def test_stale_frame_is_degraded():
    m = CameraHealthMachine()
    feed(m, {"probe": OK}, {"probe": OK})
    m.observe(3, **PRESENT_OK, probe=Probe(ok=True, reason=Reason.STALE_FRAME))
    assert m.state == Health.DEGRADED and m.reason == Reason.STALE_FRAME


def test_transition_reports_change_flag():
    m = CameraHealthMachine(fail_threshold=1)
    t1 = m.observe(1, **PRESENT_OK, probe=OK)
    assert t1.changed and t1.frm == Health.UNKNOWN and t1.to == Health.OPERATIONAL
    t2 = m.observe(2, **PRESENT_OK, probe=OK)       # steady state
    assert not t2.changed and t2.to == Health.OPERATIONAL


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

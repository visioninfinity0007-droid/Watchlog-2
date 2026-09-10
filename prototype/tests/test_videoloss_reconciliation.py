#!/usr/bin/env python3
"""VideoLoss health-truth reconciliation (M1 release blocker).

The defect: the recorder reported channels in VideoLoss (Al-Khalid Ch5/7/8) while WatchLog
showed them operational. Root cause — VideoLoss only reached health via a native EVENT
(a transition); a camera already lost before the agent started never produced one, and the
liveness snapshot is fooled because a video-loss channel still returns the recorder's black
placeholder JPEG (valid header -> reads as live).

The fix reconciles the recorder's CURRENT VideoLoss set (an active authenticated API) into
every health cycle, so an already-lost camera is OFFLINE on the FIRST cycle. These tests pin
that behaviour, including the exact regression the client demo needs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import nvr_health  # noqa: E402
from camera_health import CameraHealthMonitor  # noqa: E402
from health_model import Health, Reason  # noqa: E402
from drivers.base import Channel, DeviceInfo, DriverError  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

EIGHT = [str(i) for i in range(1, 9)]


def assess(video_loss=(), *, supported=True, reported=EIGHT, disabled=()):
    """A recorder assessment (nvr OK, enumerated) carrying a current-fault set."""
    return {
        "nvr": {"reachable": True, "auth_ok": True, "state": "ok", "reason": "ok"},
        "channels": {
            "enumerated": True,
            "reported": [{"channel": c, "name": None, "enabled": c not in disabled}
                         for c in reported],
            "current_faults": ({"supported": True,
                                "video_loss": [str(c) for c in video_loss],
                                "video_blind": []}
                               if supported else {"supported": False}),
        },
    }


def probe_ok(_ch):
    from camera_health import ProbeResult
    return ProbeResult(ok=True)   # simulate the placeholder JPEG that reads as 'live'


# ---- 1. THE mandated regression: already-lost camera OFFLINE on the first cycle ----

def test_pre_existing_videoloss_is_offline_on_first_cycle():
    mon = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    # Recorder currently reports Ch5/7/8 in VideoLoss; every snapshot probe returns OK (fooled).
    mon.run_cycle(lambda: assess(video_loss=["5", "7", "8"]), probe_ok)
    for lost in ("5", "7", "8"):
        assert mon.machines[lost].state is Health.OFFLINE, f"ch{lost} should be OFFLINE"
        assert mon.machines[lost].reason is Reason.VIDEO_LOSS
    for live in ("1", "2", "3", "4", "6"):
        assert mon.machines[live].state is Health.OPERATIONAL, f"ch{live} should be OPERATIONAL"


# ---- 2. unqueryable current state must NOT fabricate a fault (supported=False) ----

def test_unsupported_fault_query_falls_back_to_probe():
    mon = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    mon.run_cycle(lambda: assess(video_loss=["5"], supported=False), probe_ok)
    # No authoritative signal -> the (ok) probe governs; behaviour unchanged, no false OFFLINE.
    assert mon.machines["5"].state is Health.OPERATIONAL


# ---- 3. recovery once the recorder clears VideoLoss ----

def test_channel_recovers_after_videoloss_clears():
    mon = CameraHealthMonitor(EIGHT, batch_size=8, concurrency=2)
    mon.run_cycle(lambda: assess(video_loss=["5"]), probe_ok)
    assert mon.machines["5"].state is Health.OFFLINE
    # Recorder no longer lists Ch5; clean probes recover it (hysteresis J=2 by default).
    for _ in range(3):
        mon.run_cycle(lambda: assess(video_loss=[]), probe_ok)
    assert mon.machines["5"].state is Health.OPERATIONAL


# ---- 4. Dahua driver parses the live VideoLoss index (0-based -> 1-based) ----

def test_dahua_current_faults_parses_event_indexes():
    d = DahuaDriver("http://recorder", "u", "p")

    def fake_get(path):
        if "VideoLoss" in path:
            return "channels[0]=4\r\nchannels[1]=6\r\nchannels[2]=7\r\n"
        if "VideoBlind" in path:
            return ""              # nothing tampered
        raise AssertionError(path)
    d._get = fake_get

    faults = d.current_faults()
    assert faults["supported"] is True
    assert faults["video_loss"] == ["5", "7", "8"]     # index 4/6/7 -> channel 5/7/8
    assert faults["video_blind"] == []


def test_dahua_current_faults_failsafe_on_error():
    d = DahuaDriver("http://recorder", "u", "p")

    def boom(_path):
        raise DriverError("recorder unreachable")
    d._get = boom

    faults = d.current_faults()
    assert faults["supported"] is False               # could not query -> not a clean 'no faults'
    assert faults["video_loss"] == []


# ---- 5. assess_nvr_health threads current faults into the assessment ----

class _FakeDriver:
    def probe(self):
        return DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", firmware="4.x",
                          channel_count=8, driver="dahua-cgi")

    def list_channels(self):
        return [Channel(channel=str(i)) for i in range(1, 9)]

    def current_faults(self):
        return {"supported": True, "video_loss": ["5", "7", "8"], "video_blind": []}


def test_assess_includes_current_faults():
    report = nvr_health.assess_nvr_health(_FakeDriver())
    cf = report["channels"]["current_faults"]
    assert cf["supported"] is True
    assert cf["video_loss"] == ["5", "7", "8"]


def test_assess_failsafe_when_fault_query_raises():
    drv = _FakeDriver()
    drv.current_faults = lambda: (_ for _ in ()).throw(DriverError("boom"))
    report = nvr_health.assess_nvr_health(drv)
    assert report["channels"]["current_faults"] == {"supported": False}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

#!/usr/bin/env python3
"""5.1.2 camera health: tamper, native fault ends, present-tense faults on both vendors, and
camera disconnect / reconnect events derived from health transitions.

* A native tamper is a camera fault: OFFLINE, reason tamper (no usable picture). It is
  cleared by the recorder's tamper end, or by clean probes (J of them, as for any OFFLINE).
  A Dahua VideoBlind index re-asserts it every cycle, so a black-but-valid still cannot
  clear a lens the recorder says is still blinded.
* The recorder's own end of a fault clears that fault and nothing else; it is never
  evidence for a camera whose state is unknown.
* A health cycle that moves a camera between two KNOWN states yields camera_disconnect
  (up -> OFFLINE) or camera_reconnect (OFFLINE -> OPERATIONAL), source health_probe, so
  the transition is on the event timeline even when the recorder sent nothing. UNKNOWN is
  never a disconnect or a reconnect, and a tamper is not a disconnect.
* Hikvision now answers current_faults (IP channel online=false -> video_loss) with the
  Dahua contract; an unreadable status is supported=False, never a clean fault set.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import camera_health  # noqa: E402
import watchlog_agent as core  # noqa: E402
from camera_health import CameraHealthMonitor, ProbeResult  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import HikvisionDriver, _strip_ns  # noqa: E402
from health_model import CameraHealthMachine, Health, Inventory, Nvr, Probe, Reason  # noqa: E402

OK = Probe(ok=True, reason=Reason.OK)
FAIL = Probe(ok=False, reason=Reason.PROBE_TIMEOUT)


def _obs(m, t, **kw):
    return m.observe(t, inventory=Inventory.PRESENT, nvr=Nvr.OK, **kw)


# --- the state machine -----------------------------------------------------------------

def test_native_tamper_is_an_authoritative_fault():
    m = CameraHealthMachine()
    _obs(m, 1, probe=OK)
    t = _obs(m, 2, native_tamper=True)
    assert (t.to, t.reason) == (Health.OFFLINE, Reason.TAMPER)


def test_tamper_end_clears_tamper_and_only_tamper():
    m = CameraHealthMachine()
    _obs(m, 1, native_tamper=True)
    t = _obs(m, 2, native_clear=Reason.VIDEO_LOSS)       # wrong fault: no change
    assert (t.to, t.reason) == (Health.OFFLINE, Reason.TAMPER)
    t = _obs(m, 3, native_clear=Reason.TAMPER)
    assert (t.to, t.reason) == (Health.OPERATIONAL, Reason.OK)


def test_video_restore_clears_video_loss():
    m = CameraHealthMachine()
    _obs(m, 1, native_video_loss=True)
    t = _obs(m, 2, native_clear=Reason.VIDEO_LOSS)
    assert (t.to, t.reason, t.changed) == (Health.OPERATIONAL, Reason.OK, True)


def test_a_native_end_is_no_evidence_for_an_unknown_camera():
    m = CameraHealthMachine()
    t = _obs(m, 1, native_clear=Reason.VIDEO_LOSS)
    assert t.to == Health.UNKNOWN and not t.changed
    _obs(m, 2, probe=FAIL)                                # DEGRADED
    t = _obs(m, 3, native_clear=Reason.VIDEO_LOSS)
    assert t.to == Health.DEGRADED


def test_tamper_is_cleared_by_clean_probes_with_hysteresis():
    m = CameraHealthMachine(recover_threshold=2)
    _obs(m, 1, native_tamper=True)
    t = _obs(m, 2, probe=OK)
    assert (t.to, t.reason) == (Health.OFFLINE, Reason.TAMPER)
    t = _obs(m, 3, probe=OK)
    assert (t.to, t.reason) == (Health.OPERATIONAL, Reason.OK)


def test_tamper_does_not_mask_video_loss():
    m = CameraHealthMachine()
    _obs(m, 1, native_video_loss=True)
    t = _obs(m, 2, native_tamper=True)
    assert t.reason == Reason.VIDEO_LOSS


# --- the monitor -----------------------------------------------------------------------

def _assess(faults=None, channels=("1", "2")):
    return {"nvr": {"state": "ok"},
            "channels": {"enumerated": True,
                         "reported": [{"channel": c, "enabled": True} for c in channels],
                         "current_faults": faults or {"supported": False}}}


def _probe(ok_by_channel):
    return lambda c: ProbeResult(ok=ok_by_channel.get(c, True),
                                 reason="ok" if ok_by_channel.get(c, True) else "probe_timeout")


def _health(report, channel):
    row = next(r for r in report["cameras"] if r["channel"] == channel)
    return row["health"], row["reason"], row["source"]


def test_monitor_native_tamper_and_tamper_end():
    mon = CameraHealthMonitor(["1", "2"], batch_size=2)
    mon.run_cycle(lambda: _assess(), _probe({}))
    mon.record_native_tamper("2")
    assert _health(mon.report(), "2") == ("offline", "tamper", "native")
    mon.record_native_clear("2", "video_loss")
    assert _health(mon.report(), "2")[:2] == ("offline", "tamper")
    mon.record_native_clear("2", "tamper")
    assert _health(mon.report(), "2")[:2] == ("operational", "ok")
    mon.record_native_clear("2", "not-a-reason")          # ignored, never raises
    # Native signals are on the timeline already: no derived transition events.
    assert [e["event_type"] for e in mon.drain_transition_events()] == []


def test_current_video_blind_keeps_a_blinded_lens_in_tamper():
    mon = CameraHealthMonitor(["1", "2"], batch_size=2, recover_threshold=1)
    blind = {"supported": True, "video_loss": [], "video_blind": ["2"]}
    for _ in range(4):        # clean (black but valid) stills every cycle
        report = mon.run_cycle(lambda: _assess(blind), _probe({}))
        assert _health(report, "2")[:2] == ("offline", "tamper")
    report = mon.run_cycle(lambda: _assess({"supported": True, "video_loss": [],
                                            "video_blind": []}), _probe({}))
    assert _health(report, "2")[:2] == ("operational", "ok")
    # A tamper is neither a disconnect nor, when it ends, a reconnect.
    assert [e["event_type"] for e in mon.drain_transition_events()] == []


def test_unsupported_current_faults_never_assert_a_fault():
    mon = CameraHealthMonitor(["1"], batch_size=1)
    report = mon.run_cycle(lambda: _assess({"supported": False, "video_blind": ["1"]},
                                           channels=("1",)), _probe({}))
    assert _health(report, "1")[:2] == ("operational", "ok")


def test_probe_transitions_become_disconnect_and_reconnect_events():
    mon = CameraHealthMonitor(["1"], batch_size=1, fail_threshold=2, recover_threshold=2)
    cycle = lambda ok: mon.run_cycle(lambda: _assess(channels=("1",)), _probe({"1": ok}))
    cycle(True)                         # UNKNOWN -> OPERATIONAL: no event (was unknown)
    assert mon.drain_transition_events() == []
    cycle(False)                        # DEGRADED
    cycle(False)                        # OFFLINE
    events = mon.drain_transition_events()
    assert [(e["event_type"], e["channel"], e["from"], e["to"], e["reason"], e["source"])
            for e in events] == [("camera_disconnect", "1", "degraded", "offline",
                                  "probe_timeout", "health_probe")]
    cycle(True)                         # still recovering (J=2)
    cycle(True)                         # OFFLINE -> OPERATIONAL
    events = mon.drain_transition_events()
    assert [(e["event_type"], e["reason"]) for e in events] == [
        ("camera_reconnect", "probe_timeout")]
    assert mon.drain_transition_events() == []


def test_present_tense_video_loss_from_operational_is_a_disconnect():
    mon = CameraHealthMonitor(["1"], batch_size=1)
    mon.run_cycle(lambda: _assess(channels=("1",)), _probe({}))
    mon.run_cycle(lambda: _assess({"supported": True, "video_loss": ["1"]}, channels=("1",)),
                  _probe({}))
    assert [(e["event_type"], e["reason"]) for e in mon.drain_transition_events()] == [
        ("camera_disconnect", "video_loss")]


def test_unknown_is_never_a_disconnect_or_reconnect():
    mon = CameraHealthMonitor(["1"], batch_size=1, fail_threshold=1, recover_threshold=1)
    mon.run_cycle(lambda: _assess(channels=("1",)), _probe({"1": False}))   # UNKNOWN -> OFFLINE
    down = {"nvr": {"state": "unreachable"}, "channels": {"enumerated": False}}
    mon.run_cycle(lambda: down, _probe({}))                                 # -> UNKNOWN
    mon.run_cycle(lambda: _assess(channels=("1",)), _probe({}))             # UNKNOWN -> OPERATIONAL
    assert mon.drain_transition_events() == []


def test_pending_transition_events_are_bounded():
    mon = CameraHealthMonitor(["1"], batch_size=1, fail_threshold=1, recover_threshold=1)
    for _ in range(camera_health.MAX_PENDING_TRANSITION_EVENTS + 20):
        mon.run_cycle(lambda: _assess(channels=("1",)), _probe({"1": False}))
        mon.run_cycle(lambda: _assess(channels=("1",)), _probe({"1": True}))
    assert len(mon.drain_transition_events()) == camera_health.MAX_PENDING_TRANSITION_EVENTS


# --- spooling the derived events -------------------------------------------------------

class _Spool:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)


def test_health_cycle_spools_derived_events_with_the_recorder_id():
    mon = CameraHealthMonitor(["3"], batch_size=1, fail_threshold=1)
    mon.run_cycle(lambda: _assess(channels=("3",)), _probe({}))
    mon.run_cycle(lambda: _assess(channels=("3",)), _probe({"3": False}))
    spool = _Spool()
    holder = {"event_spool": spool, "recorder_cloud_id": "rec-uuid-1"}
    assert core.spool_health_transition_events(holder, object(), mon) == 1
    row = spool.rows[0]
    assert row["channel"] == "3" and row["event_type"] == "camera_disconnect"
    assert row["recorder_id"] == "rec-uuid-1"
    assert row["payload"]["source"] == "health_probe"
    assert row["payload"]["synthetic"] is True
    assert row["payload"]["health_from"] == "operational"
    datetime.fromisoformat(row["device_ts"].replace("Z", "+00:00"))


def test_without_a_spool_derived_events_wait_in_the_monitor():
    mon = CameraHealthMonitor(["3"], batch_size=1, fail_threshold=1)
    mon.run_cycle(lambda: _assess(channels=("3",)), _probe({}))
    mon.run_cycle(lambda: _assess(channels=("3",)), _probe({"3": False}))
    assert core.spool_health_transition_events({}, object(), mon) == 0
    spool = _Spool()
    assert core.spool_health_transition_events({"event_spool": spool}, object(), mon) == 1


# --- Hikvision current_faults ----------------------------------------------------------

STATUS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<InputProxyChannelStatusList xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <InputProxyChannelStatus><id>1</id><online>true</online>
    <chanDetectResult>connect</chanDetectResult></InputProxyChannelStatus>
  <InputProxyChannelStatus><id>2</id><online>false</online>
    <chanDetectResult>netUnreachable</chanDetectResult></InputProxyChannelStatus>
  <InputProxyChannelStatus><id>10</id><online>false</online></InputProxyChannelStatus>
  <InputProxyChannelStatus><id>4</id></InputProxyChannelStatus>
</InputProxyChannelStatusList>"""


def _hik(responses):
    d = HikvisionDriver("http://127.0.0.1", "u", "p", timeout=1)
    asked = []

    def fake_xml(path):
        asked.append(path)
        body = responses.get(path)
        if body is None:
            raise DriverError(f"{path}: HTTP 404")
        return _strip_ns(ET.fromstring(body))

    d._xml = fake_xml
    return d, asked


def test_hikvision_current_faults_reports_offline_ip_channels():
    d, asked = _hik({"/ISAPI/ContentMgmt/InputProxy/channels/status": STATUS_XML})
    assert d.current_faults() == {"supported": True, "video_loss": ["2", "10"],
                                  "video_blind": []}
    assert asked == ["/ISAPI/ContentMgmt/InputProxy/channels/status"]


@pytest.mark.parametrize("responses", [
    {},                                                                   # 404: a DVR
    {"/ISAPI/ContentMgmt/InputProxy/channels/status":
     b"<InputProxyChannelStatusList/>"},                                  # nothing listed
])
def test_hikvision_current_faults_fails_safe(responses):
    d, _ = _hik(responses)
    assert d.current_faults() == {"supported": False, "video_loss": [], "video_blind": []}


def test_hikvision_current_faults_reach_the_health_assessment():
    import nvr_health
    from drivers.base import Channel, DeviceInfo

    d, _ = _hik({"/ISAPI/ContentMgmt/InputProxy/channels/status": STATUS_XML})
    d.probe = lambda: DeviceInfo(vendor="Hikvision", model="DS-7608NI", channel_count=2)
    d.list_channels = lambda: [Channel("1"), Channel("2")]
    report = nvr_health.assess_nvr_health(d)
    assert report["channels"]["current_faults"] == {
        "supported": True, "video_loss": ["2", "10"], "video_blind": []}
    mon = CameraHealthMonitor(["1", "2"], batch_size=2)
    out = mon.run_cycle(lambda: report, _probe({}))
    assert _health(out, "2")[:2] == ("offline", "video_loss")
    assert _health(out, "1")[:2] == ("operational", "ok")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

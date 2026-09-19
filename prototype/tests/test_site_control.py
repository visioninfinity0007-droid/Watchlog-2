#!/usr/bin/env python3
"""Site Control read executor (H6, P1).

The recorder reads themselves were proven LIVE against the real Al-Khalid
DH-XVR1B08-I earlier in the engagement (identity, VideoLoss, clock, channels,
analytics, snapshot). These tests pin the executor that wraps them: correct action
dispatch, a composite inspect that survives a partial failure, read-only surface,
and a driver fault turning into a structured error (never a crash, never a leak).

The FakeDriver returns this site's real field-observed values.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import site_control  # noqa: E402
from drivers.base import Channel, DeviceInfo, DriverError, NvrUnreachable  # noqa: E402


class FakeDriver:
    def probe(self):
        return DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I",
                          firmware="4.001.0000001.6.R", serial="8E06857PAZ7EB3A",
                          channel_count=8, driver="dahua-cgi")

    def list_channels(self):
        names = {"1": "Reception", "2": "Directors Office", "3": "Armory Gate",
                 "4": "Admin Manager", "5": "Armory"}
        return [Channel(channel=str(i), name=names.get(str(i), f"Channel{i}")) for i in range(1, 9)]

    def get_clock(self):
        return {"supported": True, "current_time": "2026-09-10 15:05:30",
                "timezone": "Islamabad", "dst_enabled": False,
                "ntp_enabled": True, "ntp_server": "time.windows.com"}

    def current_faults(self):
        return {"supported": True, "video_loss": ["5", "7", "8"], "video_blind": []}

    def capabilities(self):
        return {"channels": [{"channel": "1", "name": "Reception", "analytics": [
            {"key": "human_vehicle", "label": "Human/Vehicle (SMD)", "supported": True,
             "active": True, "geometry": False}]}]}

    def recording_status(self, _ch):
        return {"supported": True, "channels": {"1": None}}

    def storage_status(self):
        return {"supported": True, "state": "ok"}

    def get_snapshot(self, channel):
        return b"\xff\xd8fake-jpeg"


def test_identity_read():
    r = site_control.execute_read(FakeDriver(), "get_recorder_identity")
    assert r["ok"] and r["data"]["model"] == "DH-XVR1B08-I" and r["data"]["channel_count"] == 8


def test_video_loss_read():
    r = site_control.execute_read(FakeDriver(), "get_video_loss_state")
    assert r["ok"] and r["data"]["video_loss"] == ["5", "7", "8"]


def test_clock_read():
    r = site_control.execute_read(FakeDriver(), "get_clock_config")
    assert r["ok"] and r["data"]["ntp_enabled"] is True and r["data"]["dst_enabled"] is False


def test_snapshot_is_liveness_not_image_transport():
    r = site_control.execute_read(FakeDriver(), "request_snapshot", {"channel": "1"})
    assert r["ok"] and r["data"]["obtained"] is True and r["data"]["bytes"] > 0
    assert "image" not in r["data"] and "b64" not in r["data"]   # P1 does not ship bytes


def test_inspect_is_composite():
    r = site_control.execute_read(FakeDriver(), "inspect_recorder")
    assert r["ok"]
    d = r["data"]
    assert set(d) >= {"identity", "channels", "clock", "video_loss", "analytics", "recording", "storage"}
    assert d["identity"]["model"] == "DH-XVR1B08-I"
    assert set(d["video_loss"]["video_loss"]) == {"5", "7", "8"}


def test_unsupported_action_is_refused():
    r = site_control.execute_read(FakeDriver(), "reboot_recorder")   # not a read action
    assert r["ok"] is False and r["error"] == "unsupported_read_action"


def test_driver_fault_becomes_structured_error_no_crash():
    class Broken(FakeDriver):
        def probe(self):
            raise NvrUnreachable("http://192.168.1.9/cgi-bin/x: timed out")
    r = site_control.execute_read(Broken(), "get_recorder_identity")
    assert r["ok"] is False and "192.168" not in r["error"]        # sanitised, no recorder address


def test_inspect_survives_partial_failure():
    class HalfBroken(FakeDriver):
        def current_faults(self):
            raise DriverError("boom")
    r = site_control.execute_read(HalfBroken(), "inspect_recorder")
    assert r["ok"]
    assert r["data"]["identity"]["model"] == "DH-XVR1B08-I"        # other reads still present
    assert r["data"]["video_loss"] == {"supported": False}        # failed read -> honest default


class MockRecorder:
    """In-memory recorder with real read-back, for the transactional write engine.
    apply_broken simulates a write that returns 200 but does not actually change state."""
    def __init__(self, apply_broken=False):
        self.apply_broken = apply_broken
        self.smd = {"1": {"enable": True, "human": True, "vehicle": True, "sensitivity": "Middle"}}
        self.titles = {"1": "Channel1"}
        self.clock = {"current_time": "2026-09-10 15:00:00", "timezone": "Islamabad",
                      "dst_enabled": True, "ntp_enabled": False, "ntp_server": "x"}

    def get_smd(self, ch):
        return dict(self.smd[str(ch)])

    def set_smd(self, ch, **kw):
        if self.apply_broken:
            return
        for k, v in kw.items():
            if v is not None:
                self.smd[str(ch)][k] = v

    def get_channel_title(self, ch):
        return self.titles.get(str(ch))

    def set_channel_title(self, ch, name):
        if self.apply_broken:
            return
        self.titles[str(ch)] = name

    def get_clock(self):
        return dict(self.clock)

    def set_time_config(self, **kw):
        for k, v in kw.items():
            if v is not None and k in self.clock:
                self.clock[k] = v


def test_smd_write_read_back_verified():
    r = MockRecorder()
    res = site_control.execute_write(r, "configure_smd", {"channel": "1", "vehicle": False})
    assert res["ok"] and res["verified"] and res["changed"]
    assert res["before"]["vehicle"] is True and res["after"]["vehicle"] is False
    assert r.smd["1"]["vehicle"] is False and r.smd["1"]["human"] is True   # minimal diff


def test_write_is_noop_when_already_desired():
    res = site_control.execute_write(MockRecorder(), "configure_smd", {"channel": "1", "vehicle": True})
    assert res["ok"] and res["verified"] and res["changed"] is False


def test_write_verify_fail_rolls_back():
    r = MockRecorder(apply_broken=True)            # write silently does not take
    res = site_control.execute_write(r, "configure_smd", {"channel": "1", "vehicle": False})
    assert res["ok"] is False and res["verified"] is False and res["rolled_back"] is True
    assert r.smd["1"]["vehicle"] is True           # state preserved


def test_apply_fault_triggers_rollback():
    class Raises(MockRecorder):
        def set_smd(self, ch, **kw):
            raise DriverError("recorder write failed")
    r = Raises()
    res = site_control.execute_write(r, "configure_smd", {"channel": "1", "vehicle": False})
    assert res["ok"] is False and "error" in res
    assert r.smd["1"]["vehicle"] is True           # no partial mutation left behind


def test_rename_and_time_writes_verify():
    r = MockRecorder()
    a = site_control.execute_write(r, "rename_channel", {"channel": "1", "name": "Reception"})
    assert a["ok"] and r.titles["1"] == "Reception"
    b = site_control.execute_write(r, "configure_time",
                                   {"dst_enabled": False, "ntp_enabled": True})
    assert b["ok"] and r.clock["dst_enabled"] is False and r.clock["ntp_enabled"] is True


def test_unsupported_write_refused():
    assert site_control.execute_write(MockRecorder(), "factory_reset", {})["ok"] is False


def test_command_worker_is_dormant_when_disabled():
    """OFF by default: a new capability is never auto-enabled on a live site."""
    import threading
    import watchlog_agent

    class _Cfg:
        site_control_enabled = False
        site_control_seconds = 15

    class _Cloud:
        def __init__(self):
            self.called = False

        def call(self, *a, **k):
            self.called = True

    cloud = _Cloud()
    watchlog_agent.command_worker(_Cfg(), {"agent_id": "a", "agent_key": "k"},
                                  cloud, threading.Event())
    assert cloud.called is False        # returned immediately; never polled the cloud


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

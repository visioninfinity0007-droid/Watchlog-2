#!/usr/bin/env python3
"""MNVR-001: an ONVIF driver obtained through open_driver() must attribute events
and fetch stills per physical camera.

open_driver() only builds and probes. The live collector, the incident-stills
worker, the analytics sampler and Site Control all use such a driver and never
call list_channels() on it, so the driver must load its own profile maps. The
existing test_onvif_physical_channels.py calls list_channels() directly and
therefore cannot see this.
"""
from __future__ import annotations

import base64
import sys
import threading
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
import watchlog_agent as core  # noqa: E402
import native_event_collector  # noqa: E402
import incident_evidence  # noqa: E402

MOTION_RULE = "tns1:RuleEngine/CellMotionDetector/Motion"
MOTION_ALARM = "tns1:VideoSource/MotionAlarm"


@pytest.fixture
def recorder(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    return rec


def _open(recorder):
    driver, info = core.open_driver(fx.FakeCfg())
    assert driver.name == "onvif"
    return driver, info


def _camera6_rule_motion(utc="2026-10-04T10:00:00Z"):
    # Rule-engine topics identify the input by its VideoSourceConfiguration token.
    return fx.notification(MOTION_RULE, utc,
                           {"VideoSourceConfigurationToken": fx.config_token(6),
                            "VideoAnalyticsConfigurationToken": "VideoAnalyticsConfig_006",
                            "Rule": "MyMotionDetectorRule"},
                           {"IsMotion": "true"})


def _camera3_source_motion(utc="2026-10-04T10:00:10Z"):
    # VideoSource/* topics identify the input by its VideoSource token.
    return fx.notification(MOTION_ALARM, utc, {"Source": fx.source_token(3)},
                           {"State": "true"})


def test_open_driver_attributes_events_to_physical_channels(recorder):
    driver, _info = _open(recorder)
    recorder.queue(_camera6_rule_motion(), _camera3_source_motion())
    events = fx.stream(driver, recorder)
    driver.close()

    # Camera 6 then camera 3, ten seconds apart: two cameras, two events.
    assert [(e.channel, e.event_type) for e in events] == [("6", "motion"), ("3", "motion")]


def test_profile_token_resolves_like_list_channels(recorder):
    driver, _info = _open(recorder)
    # A SubStream profile token must land on the same physical camera that
    # list_channels() collapses it into.
    recorder.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z",
                                   {"ProfileToken": fx.profile_token(5, "sub")},
                                   {"State": "true"}))
    events = fx.stream(driver, recorder)
    channels = [c.channel for c in driver.list_channels()]
    driver.close()

    assert channels == [str(n) for n in range(1, 9)]
    assert [e.channel for e in events] == ["5"]


def test_unknown_token_is_dropped_and_counted_never_channel_one(recorder):
    driver, _info = _open(recorder)
    recorder.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z",
                                   {"Source": "VideoSourceToken_099"}, {"State": "true"}),
                   fx.notification(MOTION_ALARM, "2026-10-04T10:00:01Z",
                                   {}, {"State": "true"}))
    events = fx.stream(driver, recorder)
    driver.close()

    assert events == []
    assert driver.dropped_unmapped == 2


def test_unmapped_drop_is_reported_through_the_log_hook(recorder):
    # A dropped event must not be silent: the first one is logged with the
    # Source items it carried (the token the device really sends), then every
    # LOG_EVERY-th. Never the recorder address or the credentials.
    driver, _info = _open(recorder)
    lines = []
    driver.log = lines.append
    every = fx.onvif_driver.LOG_EVERY
    for _ in range(every):
        # Distinct monotonic times are irrelevant: unmapped events never
        # reach the burst filter.
        recorder.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z",
                                       {"Source": "VideoSourceToken_099"},
                                       {"State": "true"}))
    events = fx.stream(driver, recorder)
    driver.close()

    assert events == []
    assert driver.dropped_unmapped == every
    assert len(lines) == 2
    assert "Source=VideoSourceToken_099" in lines[0]
    assert "motion" in lines[0]
    assert f"{every}" in lines[1]
    for line in lines:
        assert "192.0.2.10" not in line
        assert fx.FakeCfg.nvr_password not in line


def test_single_camera_event_without_a_source_item_is_that_camera(monkeypatch):
    # One physical camera: an event that names no video source at all can
    # only be that camera's. That is not an unknown token.
    rec = fx.FakeRecorder(cameras=1)
    rec.install(monkeypatch)
    driver, _info = _open(rec)
    rec.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z", {}, {"State": "true"}))
    events = fx.stream(driver, rec)
    driver.close()

    assert [(e.channel, e.event_type) for e in events] == [("1", "motion")]
    assert driver.dropped_unmapped == 0


def test_single_camera_unknown_token_is_still_dropped(monkeypatch):
    # A token that is present but matches nothing is unknown, even with one camera.
    rec = fx.FakeRecorder(cameras=1)
    rec.install(monkeypatch)
    driver, _info = _open(rec)
    rec.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z",
                              {"Source": "VideoSourceToken_099"}, {"State": "true"}))
    events = fx.stream(driver, rec)
    driver.close()

    assert events == []
    assert driver.dropped_unmapped == 1


def test_unknown_token_does_not_burst_suppress_a_real_camera(recorder):
    driver, _info = _open(recorder)
    recorder.queue(fx.notification(MOTION_ALARM, "2026-10-04T10:00:00Z",
                                   {"Source": "VideoSourceToken_099"}, {"State": "true"}),
                   fx.notification(MOTION_ALARM, "2026-10-04T10:00:05Z",
                                   {"Source": fx.source_token(2)}, {"State": "true"}))
    events = fx.stream(driver, recorder)
    driver.close()

    assert [e.channel for e in events] == ["2"]


def test_open_driver_snapshot_uses_the_physical_cameras_profile(recorder):
    driver, _info = _open(recorder)
    raw = driver.get_snapshot("6")
    driver.close()

    snaps = recorder.calls_of("GetSnapshotUri")
    assert len(snaps) == 1
    assert f">{fx.profile_token(6, 'main')}<" in snaps[0]["body"]
    assert raw == fx.JPEG + fx.profile_token(6, "main").encode()


def test_built_but_unprobed_driver_snapshot(recorder):
    # Site Control's request_snapshot builds the driver without probe().
    driver = core.build("onvif", fx.FakeCfg.nvr_url, "local-user", "local-password")
    raw = driver.get_snapshot("4")
    driver.close()

    assert raw == fx.JPEG + fx.profile_token(4, "main").encode()
    assert recorder.snapshot_urls == [
        f"http://192.0.2.10/onvif/snapshot?profile={fx.profile_token(4, 'main')}"]


def test_collector_spools_each_camera_with_its_own_still(recorder, monkeypatch):
    class Spool:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def trim(self):
            return 0

    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "absent")
    spool = Spool()
    recorder.queue(_camera6_rule_motion(), _camera3_source_motion())
    stop = threading.Event()
    recorder.stop = stop
    native_event_collector.collector(fx.FakeCfg(), spool, stop)

    assert [(r["channel"], r["event_type"]) for r in spool.rows] == [("6", "motion"), ("3", "motion")]
    stills = [base64.b64decode(r["snapshot_b64"]) for r in spool.rows]
    assert stills == [fx.JPEG + fx.profile_token(6, "main").encode(),
                      fx.JPEG + fx.profile_token(3, "main").encode()]


def test_stills_worker_uploads_the_requested_cameras_still(recorder, monkeypatch):
    stop = threading.Event()
    calls = []

    class Cloud:
        def __init__(self, *_a, **_k):
            self.claims = 0

        def call(self, name, **kw):
            calls.append((name, kw))
            if name == "wl_agent_claim_incident_stills":
                self.claims += 1
                if self.claims == 1:
                    return [{"request_id": "req-0001", "channel": "4"}]
                stop.set()
                return []
            return {}

    monkeypatch.setattr(core, "Cloud", Cloud)
    incident_evidence.stills_worker(fx.FakeCfg(), {"agent_id": "a", "agent_key": "k"}, stop)

    names = [name for name, _kw in calls]
    assert "wl_agent_fail_incident_still" not in names
    upload = [kw for name, kw in calls if name == "wl_agent_upload_incident_still"]
    assert len(upload) == 1
    assert base64.b64decode(upload[0]["p_image_b64"]) == fx.JPEG + fx.profile_token(4, "main").encode()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

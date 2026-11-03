#!/usr/bin/env python3
"""Native-event evidence attribution (5.1.2 workstream E): camera, recorder and time.

A clip from the wrong camera or the wrong time is worse than a clean failure. Migration 0164
creates a native-event clip request T-15 s .. T+30 s bound to the event's camera and
recorder; these tests pin what the Agent does with such a claimed row:

- Dahua: mediaFileFind (condition.Channel) and loadfile (channel) address the camera's own
  1-based WatchLog channel, and the window sent is T-15 .. T+30 in recorder-local wall time,
  recorder clock drift included exactly once (agent-stamped events move by zone + measured
  drift, recorder-stamped ONVIF events by zone only; the claim now carries clock_source).
- Hikvision: the ISAPI search and the download address track channel*100+1, and the window
  sent is T-15 .. T+30 in UTC.
- Multi-recorder: a clip for recorder B's channel 1 is exported by recorder B's driver, never
  recorder A's (both have a channel 1), and a recorder this PC cannot resolve fails the clip
  instead of falling back to another recorder.

Field-only (IMPLEMENTED_UNVERIFIED): how far a real recorder drifts, and whether its export
trims to the exact window.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from dahua_fake_recorder import FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime  # noqa: E402
import credential_store as cs  # noqa: E402
import dahua_archive as da  # noqa: E402
import hikvision_archive as ha  # noqa: E402
import incident_evidence as ie  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
import windows_secret as ws  # noqa: E402
from drivers.base import DeviceInfo  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
EVENT = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)          # true UTC instant (T)
PRE, POST = timedelta(seconds=15), timedelta(seconds=30)          # the 0164 window
FILES = continuous_files(local("2026-10-04 12:00:00"), local("2026-10-04 14:00:00"))
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
CFG = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                      nvr_url="http://192.168.1.108")
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def native_row(device_ts, channel="1", **extra):
    """The row 0164's claim hands out for a native-event clip."""
    return {"request_id": "11111111-2222-3333-4444-555555555555", "event_id": 42,
            "camera_id": "cam-1", "recorder_id": extra.pop("recorder_id", "rec-1"),
            "channel": channel,
            "start_at": (device_ts - PRE).isoformat(),
            "end_at": (device_ts + POST).isoformat(), **extra}


class Cloud:
    def __init__(self, stop, rows):
        self.calls, self._stop, self._rows = [], stop, list(rows)
        self.lock = threading.Lock()

    def call(self, name, **kw):
        with self.lock:
            self.calls.append((name, kw))
        if name == "wl_agent_claim_clip_requests":
            if self._rows:
                return [self._rows.pop(0)]
            self._stop.set()
            return []
        return {"ok": True}

    def named(self, name):
        return [kw for call, kw in self.calls if call == name]


def run_worker(monkeypatch, rows, open_archive):
    stop = threading.Event()
    cloud = Cloud(stop, rows)
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_archive_driver", open_archive)
    monkeypatch.setattr(ie.core, "log", lambda msg: None)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.01)
    ie.footage_worker(CFG, STATE, stop)
    return cloud


# ---------------------------------------------------------------------------------------------
# Dahua
# ---------------------------------------------------------------------------------------------

def dahua_clip(monkeypatch, recorder, driver, device_ts, **row):
    da.install()
    monkeypatch.setattr(da, "datetime", pinned_datetime(PC_NOW))
    cloud = run_worker(monkeypatch, [native_row(device_ts, **row)],
                       lambda cfg: (driver, DeviceInfo(vendor="Dahua", model="XVR")))
    assert cloud.named("wl_agent_fail_clip") == []
    assert len(cloud.named("wl_agent_complete_clip")) == 1
    return recorder.calls_to("mediaFileFind.cgi", "findFile"), \
        recorder.calls_to("loadfile.cgi", "startLoad")


@pytest.mark.parametrize("channel", ["1", "5"])
def test_dahua_search_and_export_address_the_cameras_own_channel(monkeypatch, channel):
    rec = FakeRecorder(PC_NOW, files=FILES)
    finds, loads = dahua_clip(monkeypatch, rec, FakeDahua(rec), EVENT, channel=channel)
    assert [str(c[1]["condition.Channel"]) for c in finds] == [channel]
    assert [str(c[1]["channel"]) for c in loads] == [channel]


@pytest.mark.parametrize("drift_seconds", [0, 25, -40])
def test_dahua_live_event_window_is_t_minus_15_to_t_plus_30_on_the_recorder(monkeypatch,
                                                                            drift_seconds):
    # Dahua CGI device_ts is the agent's receive time (true UTC): the window moves by the
    # recorder's zone (+5:00 here) AND its measured drift, unrounded.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=drift_seconds), files=FILES)
    finds, loads = dahua_clip(monkeypatch, rec, FakeDahua(rec), EVENT)
    drift = timedelta(seconds=drift_seconds)
    expected = (local("2026-10-04 12:59:45") + drift, local("2026-10-04 13:00:30") + drift)
    assert rec.loadfile_windows() == [expected]
    assert (local(finds[0][1]["condition.StartTime"]),
            local(finds[0][1]["condition.EndTime"])) == expected
    assert expected[1] - expected[0] == PRE + POST


def test_dahua_recorder_stamped_event_counts_the_drift_once(monkeypatch):
    # ONVIF-live Dahua: the event's device_ts is the recorder's own UtcTime (90 s fast) and
    # the claim says so (payload clock_source 'recorder', 0164). Zone only, no second drift.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    mapped = core._MappedArchiveDriver(FakeDahua(rec), {"1": "1"})
    dahua_clip(monkeypatch, rec, mapped, EVENT + timedelta(seconds=90),
               clock_source="recorder")
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:15"), local("2026-10-04 13:02:00"))]


def test_dahua_event_without_a_clock_source_value_uses_the_agent_clock(monkeypatch):
    # 0164 passes clock_source as JSON null when the event has none (a recovered event or one
    # from an Agent before 5.0.28): the agent clock, drift included.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    mapped = core._MappedArchiveDriver(FakeDahua(rec), {"1": "1"})
    dahua_clip(monkeypatch, rec, mapped, EVENT, clock_source=None)
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:15"), local("2026-10-04 13:02:00"))]


# ---------------------------------------------------------------------------------------------
# Hikvision
# ---------------------------------------------------------------------------------------------

class HikSession:
    """A Hikvision recorder that has one recording segment for every searched track."""

    def __init__(self):
        self.calls = []
        self.auth = None

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode()
        self.calls.append(("POST", url, body))
        track = re.search(r"<trackID>(\d+)</trackID>", body).group(1)
        uri = (f"rtsp://192.168.1.64/Streaming/tracks/{track}/"
               "?starttime=20261004T073000Z&endtime=20261004T083000Z&name=seg&size=9000000")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">'
               "<responseStatusStrg>OK</responseStatusStrg><matchList><searchMatchItem>"
               f"<trackID>{track}</trackID><timeSpan><startTime>2026-10-04T07:30:00Z</startTime>"
               "<endTime>2026-10-04T08:30:00Z</endTime></timeSpan><mediaSegmentDescriptor>"
               f"<playbackURI>{uri.replace('&', '&amp;')}</playbackURI>"
               "</mediaSegmentDescriptor></searchMatchItem></matchList></CMSearchResult>")
        return _Response(xml.encode())

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        self.calls.append((method, url, data.decode()))
        return _Response(MP4)

    def close(self):
        pass

    def searches(self):
        return [c[2] for c in self.calls if c[1].endswith("/ISAPI/ContentMgmt/search")]

    def downloads(self):
        return [c[2] for c in self.calls if c[1].endswith("/ISAPI/ContentMgmt/download")]


class _Response:
    def __init__(self, content):
        self.status_code = 200
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


@pytest.fixture
def hik(monkeypatch):
    monkeypatch.setattr(sys.modules["drivers.hikvision"], "_HTTP_LOCKS", {})
    monkeypatch.setattr(ha, "_probe_clip", lambda data: None, raising=False)
    ha.install()
    session = HikSession()
    driver = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    driver.s = session
    return driver, session


@pytest.mark.parametrize("channel, track", [("1", "101"), ("3", "301")])
def test_hikvision_search_and_export_address_track_channel_times_100_plus_1(monkeypatch, hik,
                                                                          channel, track):
    driver, session = hik
    cloud = run_worker(monkeypatch, [native_row(EVENT, channel=channel)],
                       lambda cfg: (driver, DeviceInfo(vendor="Hikvision", model="NVR")))
    assert cloud.named("wl_agent_fail_clip") == []
    assert len(cloud.named("wl_agent_complete_clip")) == 1
    assert [re.findall(r"<trackID>(\d+)</trackID>", body) for body in session.searches()] \
        == [[track]]
    downloads = session.downloads()
    assert downloads and all(f"/Streaming/tracks/{track}/" in body for body in downloads)


def test_hikvision_window_is_t_minus_15_to_t_plus_30_in_utc(monkeypatch, hik):
    driver, session = hik
    run_worker(monkeypatch, [native_row(EVENT)],
               lambda cfg: (driver, DeviceInfo(vendor="Hikvision", model="NVR")))
    search = session.searches()[0]
    assert "<startTime>2026-10-04T07:59:45Z</startTime>" in search
    assert "<endTime>2026-10-04T08:00:30Z</endTime>" in search
    download = session.downloads()[0]
    assert "starttime=20261004T075945Z" in download
    assert "endtime=20261004T080030Z" in download


# ---------------------------------------------------------------------------------------------
# Multi-recorder routing
# ---------------------------------------------------------------------------------------------

CLOUD_A = "11111111-1111-1111-1111-111111111111"
CLOUD_B = "22222222-2222-2222-2222-222222222222"


def _isolated_registry(monkeypatch, tmp_path, rows):
    """A real recorders.json + per-recorder credentials under a temp ProgramData."""
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))

    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        text = Path(path).read_text(encoding="utf-8")
        if not text.startswith("JSON:"):
            raise ws.SecretError("corrupt")
        return json.loads(text[5:])

    monkeypatch.setattr(cs, "write_json_secret", wjs)
    monkeypatch.setattr(cs, "read_json_secret", rjs)
    for row in rows:
        cs.save_recorder_credential(row["local_id"], f"user-{row['display_name']}",
                                    f"pw-{row['display_name']}")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": rows})


class Exporter:
    """One recorder's archive driver; records every export it is asked for."""

    def __init__(self, url, log):
        self.name = "fake-archive"
        self.url, self.log = url, log

    def get_clip(self, channel, start, end):
        self.log.append((self.url, str(channel), start, end))
        return MP4

    def close(self):
        pass


def test_recorder_b_channel_1_clip_is_exported_by_recorder_b_never_a(monkeypatch, tmp_path):
    _isolated_registry(monkeypatch, tmp_path, [
        {"local_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cloud_recorder_id": CLOUD_A,
         "display_name": "a", "url": "http://recorder-a.invalid", "driver": "dahua",
         "is_primary": True, "is_configured": True},
        {"local_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cloud_recorder_id": CLOUD_B,
         "display_name": "b", "url": "http://recorder-b.invalid", "driver": "dahua",
         "is_primary": False, "is_configured": True},
    ])
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://recorder-a.invalid", recorder_cloud_id=None,
                           nvr_driver="dahua", nvr_username="user-a", nvr_password="pw-a",
                           state_path=tmp_path / "WatchLog" / "agent_state.json")
    exports, opened = [], []

    def open_archive(cfg):
        opened.append((cfg.nvr_url, cfg.recorder_cloud_id))
        return Exporter(cfg.nvr_url, exports), DeviceInfo(vendor="Dahua", model="XVR")

    stop = threading.Event()
    cloud = Cloud(stop, [native_row(EVENT, channel="1", recorder_id=CLOUD_B)])
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_archive_driver", open_archive)
    monkeypatch.setattr(ie.core, "log", lambda msg: None)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.01)
    ie.footage_worker(base, STATE, stop)

    assert opened == [("http://recorder-b.invalid", CLOUD_B)]
    assert [(url, ch) for url, ch, _s, _e in exports] == [("http://recorder-b.invalid", "1")]
    _url, _ch, start, end = exports[0]
    assert (start, end) == (EVENT - PRE, EVENT + POST)
    assert len(cloud.named("wl_agent_complete_clip")) == 1


def test_a_recorder_this_pc_cannot_resolve_fails_the_clip_not_another_recorder(monkeypatch,
                                                                              tmp_path):
    _isolated_registry(monkeypatch, tmp_path, [
        {"local_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cloud_recorder_id": CLOUD_A,
         "display_name": "a", "url": "http://recorder-a.invalid", "driver": "dahua",
         "is_primary": True, "is_configured": True},
        {"local_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cloud_recorder_id": CLOUD_B,
         "display_name": "b", "url": "http://recorder-b.invalid", "driver": "dahua",
         "is_primary": False, "is_configured": True},
    ])
    base = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                           nvr_url="http://recorder-a.invalid", recorder_cloud_id=None,
                           nvr_driver="dahua", nvr_username="user-a", nvr_password="pw-a",
                           state_path=tmp_path / "WatchLog" / "agent_state.json")
    opened = []
    stop = threading.Event()
    unknown = "33333333-3333-3333-3333-333333333333"
    cloud = Cloud(stop, [native_row(EVENT, channel="1", recorder_id=unknown)])
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_archive_driver",
                        lambda cfg: opened.append(cfg) or (Exporter(cfg.nvr_url, []), None))
    monkeypatch.setattr(ie.core, "log", lambda msg: None)
    monkeypatch.setattr(ie, "POLL_SECONDS", 0.01)
    ie.footage_worker(base, STATE, stop)

    assert opened == [], "no recorder may be opened for a recorder this PC cannot resolve"
    failed = cloud.named("wl_agent_fail_clip")
    assert len(failed) == 1 and failed[0]["p_reason"] == ie.RECORDER_UNAVAILABLE
    assert cloud.named("wl_agent_complete_clip") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

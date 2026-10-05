#!/usr/bin/env python3
"""MNVR-028 follow-up: `watchlog-agent.exe --probe` must survive a recorder-scoped event.

Hikvision and Dahua now yield Event.channel None for recorder-scoped alerts (Dahua
StorageNotExist/StorageFailure/StorageLowSpace/AlarmLocal, Hikvision diskfull/diskerror/...)
and for camera alerts without a channel id. cmd_probe, the diagnostic the collector tells
installers to run, prints each live event with ``ch{ev.channel:<4}``, which raises
TypeError on None, and it only catches DriverError: the probe dies with a traceback when,
for example, a Dahua XVR with a missing or failed disk reports it during the 20 s listen.

cmd_probe now prints a channel-less event with "-" in the channel column. The dormant
watchlog_agent.collector had the same None blind spots (a still requested for "camera None",
a channel-less video loss fed to the camera health monitor); it now skips both, like the
packaged native_event_collector.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, Event  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402


WHEN = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)


class Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, lines):
        self.lines = lines

    def iter_lines(self, chunk_size=512):
        yield from self.lines

    def close(self):
        pass


class Session:
    auth = None

    def __init__(self, resp):
        self.resp = resp

    def get(self, url, **kw):
        return self.resp

    def close(self):
        pass


class NoDiskXvr(DahuaDriver):
    """A Dahua recorder whose attach stream reports a recorder-level storage alarm."""

    def __init__(self):
        super().__init__("http://192.0.2.11", "admin", "x", timeout=1)
        self.s = Session(Resp([b"--myboundary", b"Code=StorageLowSpace;action=Start;index=0"]))

    def list_channels(self):
        return []

    def capabilities(self):
        return {}


class Cfg:
    nvr_url = "http://192.0.2.11"


def test_dahua_storage_alarm_is_recorder_scoped():
    ev = NoDiskXvr()._parse_line("Code=StorageLowSpace;action=Start;index=0")
    assert ev.channel is None and ev.payload.get("recorder_scoped") is True


def test_probe_prints_a_recorder_scoped_event_without_crashing(monkeypatch, capsys):
    driver = NoDiskXvr()
    info = DeviceInfo(vendor="Dahua", model="XVR", firmware="1", serial=None,
                      channel_count=None, driver=driver.name, raw={})
    monkeypatch.setattr(core, "open_driver", lambda cfg: (driver, info))
    monkeypatch.setattr(core, "log", lambda *a, **k: None)

    class NoTimer:                    # the probe's 20 s listen timer is not needed here
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            pass
    monkeypatch.setattr(core.threading, "Timer", NoTimer)
    core.cmd_probe(Cfg())
    out = capsys.readouterr().out
    assert "disk_full" in out
    assert "None" not in out


def test_dormant_collector_spools_a_recorder_scoped_event_with_null_channel(monkeypatch):
    """watchlog_agent.collector spools through Event.to_json directly (no spool_row)."""
    class Spool:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def trim(self):
            return 0

    class Monitor:
        def __init__(self):
            self.faults = []

        def record_native_fault(self, channel):
            self.faults.append(channel)

    stop = threading.Event()

    class Driver:
        name = "fake"
        verified_against_hardware = True

        def __init__(self):
            self.snapshots = []

        def stream_events(self, _stop):
            yield Event(channel=None, event_type="disk_error", device_ts=WHEN,
                        payload={"recorder_scoped": True})
            yield Event(channel=None, event_type="video_loss", device_ts=WHEN,
                        payload={"channel_unknown": True})
            yield Event(channel=None, event_type="motion", device_ts=WHEN,
                        payload={"channel_unknown": True})
            yield Event(channel="2", event_type="motion", device_ts=WHEN)
            stop.set()

        def get_snapshot(self, channel):
            self.snapshots.append(channel)
            return None

        def close(self):
            pass

    class Info:
        vendor, model, firmware = "TestVendor", "T-1", "1.0"

    class DormantCfg:
        snapshots = True
        snapshot_min_interval = 0

    driver = Driver()
    monkeypatch.setattr(core, "open_driver", lambda cfg: (driver, Info()))
    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core, "log", lambda *_a, **_k: None)
    spool, monitor = Spool(), Monitor()
    core.collector(DormantCfg(), spool, stop, {"monitor": monitor})

    assert [row["channel"] for row in spool.rows] == [None, None, None, "2"]
    # No still for an event without a camera, and no camera marked offline by a
    # channel-less video loss.
    assert driver.snapshots == ["2"]
    assert monitor.faults == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

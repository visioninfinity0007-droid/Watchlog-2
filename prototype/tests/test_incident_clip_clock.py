#!/usr/bin/env python3
"""MNVR-035 (incident side): an incident clip is requested in the clock that stamped its window.

A clip window is the event's device_ts -10 s / +20 s. Since 5.0.28 an ONVIF event's device_ts is
the recorder's own UtcTime whenever the recorder clock is within 300 s of the PC (otherwise the
PC receive time, with payload.clock_skew_s). On an ONVIF-live Dahua site (Al-Khalid) the footage
worker reads through the vendor-native dahua-cgi archive, which defaulted to the AGENT clock: it
added the recorder's drift on top of a time that already contained it, so a recorder 90 s fast
got a window 90 s late and a 30 s clip could miss the incident entirely.

Not every window is recorder-stamped, so the rule follows what the claimed row says:
- no event_id (operations / rule clip): the window comes from the Agent's analytics time or the
  server's clock, so the AGENT clock (RV-5028-02);
- a row carrying the event's clock_source (a server that passes payload.clock_source): the
  RECORDER clock for "recorder", the AGENT clock for anything else, including no value (recovered
  events, events from Agents before 5.0.28), and no guessing when the recorder clock has since
  become unreadable;
- an event row without clock_source (the schema today): the RECORDER clock, and the AGENT clock
  when dahua_archive refuses because the recorder clock is more than 5 minutes from every civil
  offset. That still places two kinds of window wrongly; they are pinned below as strict xfails
  until the claim carries clock_source (cross-package hand-off to the DB owner).
A Dahua-live site (agent-stamped events) keeps the agent clock, and the Hikvision archive takes
no clock argument (see the 5.0.28 release notes).

Field-only (IMPLEMENTED_UNVERIFIED): real recorder drift, and whether a recorder's ONVIF UtcTime
and CGI wall clock agree.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

from dahua_fake_recorder import FakeDahua, FakeRecorder, continuous_files, local, pinned_datetime  # noqa: E402
import dahua_archive as da  # noqa: E402
import incident_evidence as ie  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo  # noqa: E402

PC_NOW = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
TRUE_EVENT = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)      # the true UTC instant
FILES = continuous_files(local("2026-10-04 12:00:00"), local("2026-10-04 14:00:00"))
CFG = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                      nvr_url="http://192.168.1.108")
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}


class FakeCloud:
    def __init__(self, stop, device_ts, row=None):
        self.calls, self._stop = [], stop
        self._rows = [{"request_id": "11111111-2222-3333-4444-555555555555", "event_id": 42,
                       "channel": "1",
                       "start_at": (device_ts - timedelta(seconds=10)).isoformat(),
                       "end_at": (device_ts + timedelta(seconds=20)).isoformat(),
                       **(row or {})}]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_agent_claim_clip_requests":
            if self._rows:
                return [self._rows.pop(0)]
            self._stop.set()
            return []
        return {"ok": True}

    def named(self, name):
        return [kw for call, kw in self.calls if call == name]


def run_worker(monkeypatch, archive_driver, device_ts, row=None):
    da.install()
    monkeypatch.setattr(da, "datetime", pinned_datetime(PC_NOW))
    stop = threading.Event()
    cloud = FakeCloud(stop, device_ts, row)
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_archive_driver",
                        lambda cfg: (archive_driver, DeviceInfo(vendor="Dahua", model="XVR")))
    monkeypatch.setattr(ie.core, "log", lambda msg: None)
    ie.footage_worker(CFG, STATE, stop)
    return cloud


def onvif_mapped(recorder):
    """The archive reader open_archive_driver returns for an ONVIF-live Dahua recorder."""
    return core._MappedArchiveDriver(FakeDahua(recorder), {"1": "1"})


def test_onvif_recorder_stamped_event_uses_the_recorder_clock(monkeypatch):
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    device_ts = TRUE_EVENT + timedelta(seconds=90)       # the recorder's UtcTime (90 s fast)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), device_ts)
    assert cloud.named("wl_agent_fail_clip") == []
    assert len(cloud.named("wl_agent_complete_clip")) == 1
    # The incident is on the recorder's footage at 13:01:30 recorder time.
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:20"), local("2026-10-04 13:01:50"))]


def test_onvif_receive_stamped_event_falls_back_to_the_agent_clock(monkeypatch):
    # 20 min fast: no civil zone within 5 min, so the ONVIF driver did not trust its stamp and
    # used the PC receive time (true UTC); the recorder clock path refuses, the agent clock
    # (drift included) places it.
    rec = FakeRecorder(PC_NOW, drift=timedelta(minutes=20), files=FILES)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT)
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:19:50"), local("2026-10-04 13:20:20"))]


def test_dahua_live_event_keeps_the_agent_clock(monkeypatch):
    # Dahua CGI events carry the agent's receive time.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    cloud = run_worker(monkeypatch, FakeDahua(rec), TRUE_EVENT)
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:20"), local("2026-10-04 13:01:50"))]


def test_operations_clip_without_an_event_keeps_the_agent_clock(monkeypatch):
    # RV-5028-02 (a): a rule clip's window is the analytics time the Agent stamped (or the
    # server's now()), not a recorder stamp. Recorder 90 s fast: the incident is at 13:01:30.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT, {"event_id": None})
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:20"), local("2026-10-04 13:01:50"))]


def test_row_clock_source_recorder_uses_the_recorder_clock(monkeypatch):
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    device_ts = TRUE_EVENT + timedelta(seconds=90)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), device_ts, {"clock_source": "recorder"})
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:20"), local("2026-10-04 13:01:50"))]


def test_row_clock_source_receive_uses_the_agent_clock_near_another_zone(monkeypatch):
    # RV-5028-02 (b): a +5:00 recorder 25 min fast reads as +5:30 with 5 min of drift. The ONVIF
    # driver stamped the event with receive time; the agent clock (drift included) places it.
    rec = FakeRecorder(PC_NOW, drift=timedelta(minutes=25), files=FILES)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT,
                       {"clock_source": "agent_receive"})
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:24:50"), local("2026-10-04 13:25:20"))]


def test_row_without_a_clock_source_value_uses_the_agent_clock(monkeypatch):
    # A recovered event (archive segment time, agent clock) or one an Agent before 5.0.28
    # uploaded (ONVIF device_ts was PC time): the server passes clock_source with no value.
    rec = FakeRecorder(PC_NOW, drift=timedelta(seconds=90), files=FILES)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT, {"clock_source": None})
    assert cloud.named("wl_agent_fail_clip") == []
    assert rec.loadfile_windows() == [(local("2026-10-04 13:01:20"), local("2026-10-04 13:01:50"))]


def test_recorder_stamped_window_is_not_guessed_when_the_zone_is_unreadable(monkeypatch):
    # The row says the recorder's clock stamped it, but that clock is now 20 min off every civil
    # offset: no window can be placed, so the request fails (retryable) instead of guessing.
    rec = FakeRecorder(PC_NOW, drift=timedelta(minutes=20), files=FILES)
    cloud = run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT, {"clock_source": "recorder"})
    assert rec.loadfile_windows() == []
    assert cloud.named("wl_agent_complete_clip") == []
    failed = cloud.named("wl_agent_fail_clip")
    assert len(failed) == 1 and failed[0]["p_unsupported"] is False
    assert failed[0]["p_reason"] == ie.CLOCK_UNREADABLE


@pytest.mark.xfail(strict=True, reason=(
    "Known wrong window while the clip claim carries no clock_source (schema today): an event "
    "row is asked for in the recorder clock. Needs the DB hand-off (the claim returns the "
    "event's payload clock_source); see the 5.0.28 release notes."))
@pytest.mark.parametrize("drift, expected", [
    # receive-stamped event, +5:00 recorder 25 min fast: taken for +5:30, window 5 min late
    (timedelta(minutes=25), ("2026-10-04 13:24:50", "2026-10-04 13:25:20")),
    # recovered event (agent-clock segment time), recorder 90 s fast: window 90 s early
    (timedelta(seconds=90), ("2026-10-04 13:01:20", "2026-10-04 13:01:50")),
], ids=["receive-stamped-near-another-zone", "recovered-event"])
def test_event_rows_without_clock_source_known_wrong_windows(monkeypatch, drift, expected):
    rec = FakeRecorder(PC_NOW, drift=drift, files=FILES)
    run_worker(monkeypatch, onvif_mapped(rec), TRUE_EVENT)
    assert rec.loadfile_windows() == [(local(expected[0]), local(expected[1]))]


@pytest.mark.parametrize("mapped", [False, True])
def test_hikvision_archive_is_called_without_a_clock_argument(monkeypatch, mapped):
    calls = []

    class Hikvision:
        name = "hikvision-isapi"

        def get_clip(self, channel, start, end):
            calls.append((channel, start, end))
            return None

        def close(self):
            pass

    driver = core._MappedArchiveDriver(Hikvision(), {"1": "1"}) if mapped else Hikvision()
    cloud = run_worker(monkeypatch, driver, TRUE_EVENT)
    assert len(calls) == 1
    assert cloud.named("wl_agent_fail_clip")[0]["p_unsupported"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

#!/usr/bin/env python3
"""A Hikvision recovery interval is judged by its recovered footage (MNVR-061 release gate).

Hikvision recording segments are not recorder events, so the installed archive reports
historical events as 'unsupported' and segments as 'supported'. The recovery runner must then
judge the interval by the footage backfill alone: full visual recovery ends 'recovered' (as it
did before the capability change), and a visual backfill that decodes nothing never does.

This drives the real RecoveryRunner over the installed Hikvision archive with a fake recorder.
recovery.py no longer counts the absent event-replay leg of a footage recorder as a failure, so
the archive change does not move recovered time to unverified; this keeps it that way.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
import recovery  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
SEARCH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">
  <responseStatusStrg>OK</responseStatusStrg>
  <matchList>
    <searchMatchItem>
      <trackID>101</trackID>
      <timeSpan>
        <startTime>2026-09-25T08:10:00Z</startTime>
        <endTime>2026-09-25T08:11:00Z</endTime>
      </timeSpan>
      <mediaSegmentDescriptor>
        <playbackURI>rtsp://192.168.1.64/Streaming/tracks/101/?starttime=20260925T081000Z&amp;endtime=20260925T081100Z&amp;name=ch01_0810&amp;size=4096</playbackURI>
      </mediaSegmentDescriptor>
    </searchMatchItem>
  </matchList>
</CMSearchResult>"""
JPEG = b"\xff\xd8\xff" + b"jpeg"


class Response:
    def __init__(self, content=b""):
        self.status_code = 200
        self.headers = {}
        self.content = content

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        pass


class Session:
    """A recorder whose archive search answers one recorded segment inside the interval."""

    auth = None

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        assert url.endswith("/ISAPI/ContentMgmt/search"), url
        return Response(SEARCH_XML)

    def close(self):
        pass


class Cloud:
    def __init__(self, interval):
        self.interval = interval
        self.completes = []

    def call(self, name, **kw):
        if name == "wl_agent_claim_recovery":
            claimed, self.interval = [self.interval] if self.interval else [], None
            return claimed
        if name == "wl_complete_recovery":
            self.completes.append(kw)
        return {"ok": True}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sys.modules["drivers.hikvision"], "_HTTP_LOCKS", {})
    ha.install()


def recover(frame_provider):
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = Session()
    interval = {"id": "iv1", "started_at": T0.isoformat(),
                "ended_at": (T0 + timedelta(hours=1)).isoformat(),
                "cameras": ["1"], "status": "pending", "checkpoint": {}}
    cloud, events = Cloud(interval), []
    runner = recovery.RecoveryRunner(cloud, "agent", "key", d, events.append,
                                     frame_provider=frame_provider, log=lambda *a: None)
    [outcome] = runner.run_once()
    return outcome, events, cloud


def test_full_visual_recovery_of_a_hikvision_interval_ends_recovered():
    outcome, events, cloud = recover(lambda drv, ch, ts: JPEG)
    assert events, "the recovered footage produced visual evidence"
    assert all(e.get("type") != "recorded_segment" for e in events), \
        "recording segments are not replayed as recorder events"
    assert outcome["status"] == "recovered", outcome
    assert cloud.completes[-1]["p_status"] == "recovered"


def test_hikvision_interval_without_decoded_footage_is_never_recovered():
    outcome, events, cloud = recover(lambda drv, ch, ts: None)
    assert events == []
    assert outcome["status"] != "recovered", outcome
    assert cloud.completes[-1]["p_status"] != "recovered"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

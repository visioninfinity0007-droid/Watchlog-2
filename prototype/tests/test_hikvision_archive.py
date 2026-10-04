#!/usr/bin/env python3
"""Hardware-free contracts for the Hikvision ISAPI archive/download implementation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))

import backfill  # noqa: E402
import hikvision_archive as ha  # noqa: E402
import recovery_ai  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402


SEARCH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">
  <responseStatusStrg>MORE</responseStatusStrg>
  <numOfMatches>1</numOfMatches>
  <matchList>
    <searchMatchItem>
      <trackID>101</trackID>
      <timeSpan>
        <startTime>2026-09-25T08:00:00Z</startTime>
        <endTime>2026-09-25T08:01:00Z</endTime>
      </timeSpan>
      <mediaSegmentDescriptor>
        <playbackURI>rtsp://192.168.1.64/Streaming/tracks/101/?starttime=20260925T080000Z&amp;endtime=20260925T080100Z</playbackURI>
      </mediaSegmentDescriptor>
    </searchMatchItem>
  </matchList>
</CMSearchResult>"""


class Response:
    def __init__(self, content=b"", status=200, chunks=None):
        self.content = content
        self.status_code = status
        self.headers = {}
        self.text = content.decode("utf-8", "replace")
        self._chunks = chunks if chunks is not None else [content]
    def close(self):
        pass
    def iter_content(self, chunk_size=1):
        yield from self._chunks


class Session:
    def __init__(self):
        self.calls = []
        self.auth = None
        self.search_xml = SEARCH_XML
    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        self.calls.append(("POST", url, body, bool(stream), timeout))
        if url.endswith("/ISAPI/ContentMgmt/search"):
            return Response(self.search_xml)
        raise AssertionError(url)
    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        self.calls.append((method, url, body, bool(stream), timeout))
        if url.endswith("/ISAPI/ContentMgmt/download"):
            return Response(chunks=[b"RECORDED-", b"VIDEO"])
        raise AssertionError(url)
    def close(self):
        pass


def driver():
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = Session()
    return d


def last_page_driver():
    # One complete page: the shared fixture always answers MORE, which pages forever.
    d = driver()
    d.s.search_xml = SEARCH_XML.replace(b"MORE", b"OK")
    return d


def test_search_track_time_and_pagination():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    result = ha.search_recordings(d, "1", start, end, offset=0, limit=8)
    assert result["status"] == "supported"
    assert result["matches"][0]["track_id"] == "101"
    assert result["matches"][0]["playback_uri"].startswith("rtsp://")
    assert result["next_offset"] == 1
    body = d.s.calls[0][2]
    assert d.s.calls[0][0] == "POST"
    assert "<trackID>101</trackID>" in body
    assert "<startTime>2026-09-25T08:00:00Z</startTime>" in body
    assert "<contentType>video</contentType>" in body
    assert "<searchResultPostion>0</searchResultPostion>" in body
    assert "//recordType.meta.std-cgi.com" in body


def test_enumeration_is_recovery_shape():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    result = ha.enumerate_historical_events(d, "1", start, end, None, 8)
    event = result["events"][0]
    assert event["type"] == "recorded_segment"
    assert event["channel"] == "1"
    assert event["segment"]["start"] == "2026-09-25T08:00:00Z"
    assert event["segment"]["end"] == "2026-09-25T08:01:00Z"


def test_enumerated_segments_carry_no_recorder_address():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    row = ha.enumerate_historical_events(d, "1", start, end, None, 8)["events"][0]
    assert row["device_event_id"].startswith("hik:")
    assert "://" not in row["device_event_id"]
    assert "192.168" not in json.dumps(row) and "rtsp" not in json.dumps(row)
    again = ha.enumerate_historical_events(driver(), "1", start, end, None, 8)["events"][0]
    assert again["device_event_id"] == row["device_event_id"], "stable dedupe key"


def test_segments_are_not_replayed_as_recorder_events():
    ha.install()
    d = last_page_driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    events = []
    result = backfill.backfill_events(d, "1", start, end, on_event=events.append)
    assert events == []
    assert result["status"] == "unsupported"


def test_recovered_snapshots_carry_no_recorder_address():
    ha.install()
    d = last_page_driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    events = []
    seen = set()
    result = recovery_ai.backfill_intelligence(
        d, None, "1", start, end, seen=seen, on_event=events.append,
        frame_provider=lambda drv, ch, ts: b"\xff\xd8\xff" + b"jpeg")
    assert result["status"] == "supported" and events
    for event in events:
        assert "://" not in event["device_event_id"]
        assert "192.168" not in json.dumps(event["payload"])
    assert not any("://" in key for key in seen), "recovery checkpoint keys stay address-free"


def test_clip_download_is_bounded_binary_path():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 0, 10, tzinfo=timezone.utc)
    data = ha.get_clip(d, "1", start, end)
    assert data == b"RECORDED-VIDEO"
    assert d.s.calls[0][1].endswith("/ISAPI/ContentMgmt/search")
    download = [row for row in d.s.calls if row[1].endswith("/ISAPI/ContentMgmt/download")]
    assert download and download[0][0] == "GET"
    assert "<downloadRequest" in download[0][2]
    assert "<playbackURI>" in download[0][2]
    assert "name=" not in download[0][2] or "Streaming/tracks/101" in download[0][2]


def test_install_exposes_archive_to_production_driver():
    ha.install()
    d = driver()
    assert callable(getattr(d, "get_clip"))
    assert d.historical_capability()["segments"] == "supported"
    # Recording segments are not recorder events; the recorder's event log is not searched.
    assert d.historical_capability()["events"] == "unsupported"


if __name__ == "__main__":
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} Hikvision archive/download contracts passed")

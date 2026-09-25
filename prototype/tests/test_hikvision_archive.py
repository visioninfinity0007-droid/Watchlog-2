#!/usr/bin/env python3
"""Hardware-free contracts for the Hikvision ISAPI archive/download implementation."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
sys.path.insert(0, str(AGENT))

import hikvision_archive as ha  # noqa: E402
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
        self.posts = []
        self.auth = None
    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        body = data.decode() if isinstance(data, bytes) else str(data or "")
        self.posts.append((url, body, bool(stream), timeout))
        if url.endswith("/ISAPI/ContentMgmt/search"):
            return Response(SEARCH_XML)
        if url.endswith("/ISAPI/ContentMgmt/download"):
            return Response(chunks=[b"RECORDED-", b"VIDEO"])
        raise AssertionError(url)
    def close(self):
        pass


def driver():
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = Session()
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
    body = d.s.posts[0][1]
    assert "<trackID>101</trackID>" in body
    assert "<startTime>2026-09-25T08:00:00Z</startTime>" in body


def test_enumeration_is_recovery_shape():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 2, tzinfo=timezone.utc)
    result = ha.enumerate_historical_events(d, "1", start, end, None, 8)
    event = result["events"][0]
    assert event["type"] == "recorded_segment"
    assert event["channel"] == "1"
    assert event["segment"]["playback_uri"].startswith("rtsp://")


def test_clip_download_is_bounded_binary_path():
    d = driver()
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, 8, 0, 10, tzinfo=timezone.utc)
    data = ha.get_clip(d, "1", start, end)
    assert data == b"RECORDED-VIDEO"
    download = [row for row in d.s.posts if row[0].endswith("/ISAPI/ContentMgmt/download")]
    assert download and "<downloadRequest" in download[0][1]
    assert "<playbackURI>" in download[0][1]


def test_install_exposes_archive_to_production_driver():
    ha.install()
    d = driver()
    assert callable(getattr(d, "get_clip"))
    assert d.historical_capability()["segments"] == "supported"
    assert d.historical_capability()["events"] == "supported"


if __name__ == "__main__":
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} Hikvision archive/download contracts passed")

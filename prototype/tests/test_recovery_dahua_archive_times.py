#!/usr/bin/env python3
"""Recovery over the real Dahua archive path, with recorder times that carry no zone (MNVR-032).

dahua_archive.enumerate_historical_events passes mediaFileFind StartTime/EndTime through as bare
wall-clock strings ("2026-06-01 10:00:00"). Clipping samples to the recovery window compared them
with the zone-aware window bounds and raised TypeError on every chunk, so a Dahua interval ended
partial/archive_error with no frame recovered. A bare time is read as UTC, as before the clipping.
"""
from __future__ import annotations

import base64
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import dahua_archive  # noqa: E402
import recovery  # noqa: E402
import recovery_ai  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
CAM1 = "11111111-1111-4111-8111-111111111111"
GAP = (datetime(2026, 6, 1, 10, 20, tzinfo=timezone.utc),
       datetime(2026, 6, 1, 10, 40, tzinfo=timezone.utc))
INSTALLED = ("get_clip", "enumerate_historical_events", "get_recorded_segment",
             "historical_capability")


class _Resp:
    def __init__(self, text="", content=b""):
        self.status_code, self.text, self._content = 200, text, content

    def iter_content(self, chunk_size=0):
        yield self._content

    def close(self):
        pass


class _DahuaCgi:
    """The CGI calls dahua_archive makes, answered like a recorder that writes hourly files and
    reports every time as its own wall clock without a zone. ``offset_hours`` is its UTC offset."""

    def __init__(self, offset_hours=0):
        self.offset = timedelta(hours=offset_hours)
        self.auth, self.window, self.loads = None, None, []

    def get(self, url, params=None, timeout=None, stream=False):
        params = params or {}
        action = params.get("action")
        if "global.cgi" in url:
            wall = datetime.now(timezone.utc).replace(tzinfo=None) + self.offset
            return _Resp(text=f"result={wall.strftime(FMT)}")
        if "mediaFileFind.cgi" in url:
            if action == "factory.create":
                return _Resp(text="result=7")
            if action == "findFile":
                self.window = (datetime.strptime(params["condition.StartTime"], FMT),
                               datetime.strptime(params["condition.EndTime"], FMT))
                return _Resp(text="OK")
            if action == "findNextFile":
                return _Resp(text=self._files())
            return _Resp(text="OK")                       # close / destroy
        if "loadfile.cgi" in url:
            local = datetime.strptime(params["startTime"], FMT)
            seconds = (datetime.strptime(params["endTime"], FMT) - local).total_seconds()
            self.loads.append(params["startTime"])
            # Footage cut to the request; it records which UTC moment it shows.
            shows = (local - self.offset).replace(tzinfo=timezone.utc)
            return _Resp(content=f"MEDIA|{shows.isoformat()}|{int(seconds)}".encode())
        raise AssertionError(url)

    def _files(self):
        lo, hi = self.window
        cur, lines, i = lo.replace(minute=0, second=0), [], 0
        while cur < hi:
            end = cur + timedelta(hours=1)
            lines += [f"items[{i}].Channel=0", f"items[{i}].StartTime={cur.strftime(FMT)}",
                      f"items[{i}].EndTime={end.strftime(FMT)}",
                      f"items[{i}].FilePath=/mnt/dvr/{cur:%Y-%m-%d}/001/dav/{cur:%H}.dav",
                      f"items[{i}].Type=dav"]
            cur, i = end, i + 1
        return "found=%d\r\n" % i + "\r\n".join(lines)


def _decode(clip, offset=0.0):
    """Frame ``offset`` seconds into the fake media; None past its end."""
    _tag, shows, seconds = clip.decode().split("|")
    if offset > int(seconds):
        return None
    return b"FRAME@" + (datetime.fromisoformat(shows) + timedelta(seconds=offset)).isoformat().encode()


class _Cloud:
    def __init__(self):
        self.iv = {"id": "iv-1", "started_at": GAP[0].isoformat(), "ended_at": GAP[1].isoformat(),
                   "cameras": [CAM1], "checkpoint": {}, "attempts": 0, "status": "pending"}
        self.completes = []

    def call(self, fn, **kw):
        if fn == "wl_agent_claim_recovery":
            if self.iv["status"] not in ("pending", "in_progress"):
                return []
            self.iv["status"], self.iv["attempts"] = "in_progress", self.iv["attempts"] + 1
            return [dict(self.iv)]
        if fn == "wl_complete_recovery":
            self.completes.append(kw)
            self.iv["status"], self.iv["checkpoint"] = kw["p_status"], kw["p_checkpoint"]
            return {"ok": True}
        raise AssertionError(fn)


class DahuaArchiveRecovery(unittest.TestCase):
    def setUp(self):
        self.saved = {name: vars(DahuaDriver).get(name) for name in INSTALLED}
        self.ffmpeg = (recovery_ai._ffmpeg_exe, recovery_ai._ffmpeg_first_frame)
        dahua_archive.install()
        # The production decode path, with the fake media standing in for FFmpeg.
        recovery_ai._ffmpeg_exe = lambda: "ffmpeg"
        recovery_ai._ffmpeg_first_frame = _decode

    def tearDown(self):
        recovery_ai._ffmpeg_exe, recovery_ai._ffmpeg_first_frame = self.ffmpeg
        for name, value in self.saved.items():
            if value is None:
                delattr(DahuaDriver, name)
            else:
                setattr(DahuaDriver, name, value)

    def _driver(self, offset_hours=0):
        driver = DahuaDriver("http://192.0.2.10", "local-user", "local-password", timeout=2)
        driver.s = _DahuaCgi(offset_hours)
        return driver

    def _claims(self, driver, claims=4):
        cloud, events, logs = _Cloud(), [], []
        runner = recovery.RecoveryRunner(cloud, "agent", "key", driver, events.append,
                                         chunk_seconds=3600, camera_channels={CAM1: "1"},
                                         log=logs.append)
        for _ in range(claims):
            runner.run_once(limit=1)
        return cloud, events, logs

    def test_rows_carry_bare_wall_clock_times(self):
        rows = self._driver().enumerate_historical_events("1", *GAP)["events"]
        self.assertTrue(rows)
        datetime.strptime(rows[0]["segment"]["start"], FMT)   # no zone, exactly as the recorder said

    def test_visual_backfill_reads_bare_segment_times(self):
        got = []
        summary = recovery_ai.backfill_intelligence(self._driver(), None, "1", *GAP,
                                                    on_event=got.append)
        self.assertEqual(summary["status"], recovery_ai.SUPPORTED)
        self.assertEqual([e["device_ts"] for e in got], [
            "2026-06-01T10:20:00+00:00", "2026-06-01T10:25:00+00:00",
            "2026-06-01T10:30:00+00:00", "2026-06-01T10:35:00+00:00"])
        for event in got:
            # Each snapshot holds the footage of the moment it is stamped with.
            self.assertEqual(base64.b64decode(event["snapshot_b64"]),
                             b"FRAME@" + event["device_ts"].encode())

    def test_a_dahua_interval_is_recovered_in_one_claim(self):
        cloud, events, logs = self._claims(self._driver())
        self.assertEqual([c["p_status"] for c in cloud.completes][-1], "recovered")
        self.assertEqual(sum(1 for c in cloud.completes if c["p_status"] != "in_progress"), 1)
        self.assertEqual(len(events), 4)
        self.assertFalse([m for m in logs if "archive read failed" in m], logs)


if __name__ == "__main__":
    unittest.main(verbosity=2)

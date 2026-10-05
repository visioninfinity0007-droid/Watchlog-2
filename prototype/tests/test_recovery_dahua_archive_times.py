#!/usr/bin/env python3
"""Recovery over the real Dahua archive path (MNVR-032 with MNVR-019).

mediaFileFind answers with recorder wall-clock strings that carry no zone ("2026-06-01 15:00:00").
Clipping samples to the recovery window once compared such bare strings with the zone-aware window
bounds and raised TypeError on every chunk, so a Dahua interval ended partial/archive_error with no
frame recovered. dahua_archive now returns segment times as UTC on the agent clock, so samples are
clipped to the gap and each frame is the footage of the moment it is stamped with, whatever the
recorder's UTC offset.

A recorder whose file list is stamped in another clock than its own (the MNVR-019 shape) returns
segments that do not overlap the window at all. No footage of the gap was examined then, so the
window must not count as recovered.
"""
from __future__ import annotations

import base64
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "tests"))

import dahua_archive  # noqa: E402
import recovery  # noqa: E402
import recovery_ai  # noqa: E402
from dahua_fake_recorder import pinned_datetime  # noqa: E402
from drivers.dahua import DahuaDriver  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)      # recorder clock reads are pinned
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
    reports every time as its own wall clock without a zone. ``offset_hours`` is its UTC offset;
    ``files_shifted_hours`` stamps its file list that far from its own clock."""

    def __init__(self, offset_hours=0, files_shifted_hours=0):
        self.offset = timedelta(hours=offset_hours)
        self.shift = timedelta(hours=files_shifted_hours)
        self.auth, self.window, self.loads, self.served = None, None, [], False

    def get(self, url, params=None, timeout=None, stream=False):
        params = params or {}
        action = params.get("action")
        if "global.cgi" in url:
            wall = NOW.replace(tzinfo=None) + self.offset
            return _Resp(text=f"result={wall.strftime(FMT)}")
        if "mediaFileFind.cgi" in url:
            if action == "factory.create":
                return _Resp(text="result=7")
            if action == "findFile":
                self.window = (datetime.strptime(params["condition.StartTime"], FMT),
                               datetime.strptime(params["condition.EndTime"], FMT))
                self.served = False
                return _Resp(text="OK")
            if action == "findNextFile":
                # Like a real finder: the files once, then an empty page.
                if self.served:
                    return _Resp(text="found=0\r\n")
                self.served = True
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
            lines += [f"items[{i}].Channel=0",
                      f"items[{i}].StartTime={(cur + self.shift).strftime(FMT)}",
                      f"items[{i}].EndTime={(end + self.shift).strftime(FMT)}",
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
        patcher = mock.patch.object(dahua_archive, "datetime", pinned_datetime(NOW))
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def _driver(self, offset_hours=0, files_shifted_hours=0):
        driver = DahuaDriver("http://192.0.2.10", "local-user", "local-password", timeout=2)
        driver.s = _DahuaCgi(offset_hours, files_shifted_hours)
        return driver

    def _claims(self, driver, claims=4):
        cloud, events, logs = _Cloud(), [], []
        runner = recovery.RecoveryRunner(cloud, "agent", "key", driver, events.append,
                                         chunk_seconds=3600, camera_channels={CAM1: "1"},
                                         log=logs.append)
        for _ in range(claims):
            runner.run_once(limit=1)
        return cloud, events, logs

    def test_rows_come_back_as_utc_on_the_agent_clock(self):
        for offset in (0, 5):
            rows = self._driver(offset_hours=offset).enumerate_historical_events("1", *GAP)["events"]
            self.assertEqual(rows[0]["segment"]["start"], "2026-06-01T10:00:00Z", offset)

    def test_visual_backfill_samples_the_gap(self):
        for offset in (0, 5):
            got = []
            summary = recovery_ai.backfill_intelligence(self._driver(offset_hours=offset), None,
                                                        "1", *GAP, on_event=got.append)
            self.assertEqual(summary["status"], recovery_ai.SUPPORTED, offset)
            self.assertEqual([e["device_ts"] for e in got], [
                "2026-06-01T10:20:00+00:00", "2026-06-01T10:25:00+00:00",
                "2026-06-01T10:30:00+00:00", "2026-06-01T10:35:00+00:00"], offset)
            for event in got:
                # Each snapshot holds the footage of the moment it is stamped with.
                self.assertEqual(base64.b64decode(event["snapshot_b64"]),
                                 b"FRAME@" + event["device_ts"].encode(), offset)

    def test_a_dahua_interval_is_recovered_in_one_claim(self):
        for offset in (0, 5):
            cloud, events, logs = self._claims(self._driver(offset_hours=offset))
            self.assertEqual([c["p_status"] for c in cloud.completes][-1], "recovered", offset)
            self.assertEqual(sum(1 for c in cloud.completes if c["p_status"] != "in_progress"), 1)
            self.assertEqual(len(events), 4)
            self.assertFalse([m for m in logs if "archive read failed" in m], logs)

    def test_segments_outside_the_window_are_not_a_recovered_window(self):
        # File times stamped five hours from the recorder's own clock land after the gap.
        driver = self._driver(files_shifted_hours=5)
        summary = recovery_ai.backfill_intelligence(driver, None, "1", *GAP, on_event=[].append)
        self.assertEqual(summary["status"], recovery_ai.UNKNOWN)
        self.assertEqual(summary["frames"], 0)
        self.assertTrue(summary["reason"])
        self.assertEqual(driver.s.loads, [], "nothing outside the gap is downloaded")

    def test_an_interval_whose_segments_miss_the_window_is_not_recovered(self):
        cloud, events, logs = self._claims(self._driver(files_shifted_hours=5))
        final = cloud.completes[-1]
        # No footage of the gap was examined and nothing was recovered.
        self.assertEqual(final["p_status"], "unrecoverable")
        self.assertEqual(events, [])
        # Settled on the first claim from what the archive said, not after failed reads.
        self.assertEqual(sum(1 for c in cloud.completes if c["p_status"] != "in_progress"), 1)
        self.assertEqual(cloud.iv["attempts"], 1)
        self.assertFalse([m for m in logs if "archive read failed" in m], logs)


if __name__ == "__main__":
    unittest.main(verbosity=2)

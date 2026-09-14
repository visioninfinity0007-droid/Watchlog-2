#!/usr/bin/env python3
"""0.4.4 §6 — setup-time archive PROOF (no hardware / no network).

Before WatchLog promises "an outage means delayed, not lost, intelligence", setup must PROVE the
recorder actually retains retrievable footage. ``dahua_archive.prove_recorder_archive`` is the
bounded, read-only, NEVER-raising probe over one channel; ``setup_backend.verify_recorder_archive``
is the setup wrapper that builds one driver, installs the archive impl, probes the first channel
and ALWAYS closes the driver. Honest four-way verdict — verified / empty / unsupported / unknown —
never a fabricated 'supported'.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import dahua_archive             # noqa: E402
import setup_backend as backend  # noqa: E402

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


def seg(start, end, path):
    return {"ts": start, "type": "recorded_segment", "device_event_id": path,
            "channel": "1", "segment": {"start": start, "end": end, "path": path}}


class FakeArchiveDriver:
    """Driver whose archive behaviour the test supplies; records enumerate() call args."""
    def __init__(self, *, events=None, status="supported", capability="default",
                 enumerate_raises=None, capability_raises=None):
        self._events = [] if events is None else events
        self._status = status
        self._capability = capability
        self._enum_raise = enumerate_raises
        self._cap_raise = capability_raises
        self.calls = []
        self.closed = False

    def historical_capability(self):
        if self._cap_raise:
            raise self._cap_raise
        if self._capability == "default":
            return {"events": "supported", "snapshots": "unsupported", "segments": "supported"}
        return self._capability

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.calls.append({"channel": channel, "start": start, "end": end,
                           "cursor": cursor, "limit": limit})
        if self._enum_raise:
            raise self._enum_raise
        return {"status": self._status, "events": list(self._events), "next_cursor": None}

    def close(self):
        self.closed = True


class FakeNoArchiveDriver:
    """A recorder driver with NO archive enumeration at all (e.g. an unsupported vendor)."""
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


# --- prove_recorder_archive: the pure probe ---------------------------------

class ProveRecorderArchive(unittest.TestCase):
    def test_verified_when_segments_present(self):
        d = FakeArchiveDriver(events=[seg("2026-09-14 16:40:00", "2026-09-14 16:45:00", "/a.dav"),
                                      seg("2026-09-14 16:45:00", "2026-09-14 16:50:00", "/b.dav")])
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW)
        self.assertEqual(p["status"], "verified")
        self.assertEqual(p["segments_found"], 2)
        self.assertEqual(len(p["sample"]), 2)
        self.assertIn("retrievable", p["detail"])

    def test_empty_when_reachable_but_no_footage(self):
        p = dahua_archive.prove_recorder_archive(FakeArchiveDriver(events=[]), "1", now=NOW)
        self.assertEqual(p["status"], "empty")
        self.assertEqual(p["segments_found"], 0)
        self.assertIn("No recorded footage", p["detail"])

    def test_unsupported_when_driver_cannot_enumerate(self):
        p = dahua_archive.prove_recorder_archive(FakeNoArchiveDriver(), "1", now=NOW)
        self.assertEqual(p["status"], "unsupported")
        self.assertEqual(p["segments_found"], 0)

    def test_unsupported_when_capability_says_segments_unsupported(self):
        # method present, but the driver's own capability declares no archive segments
        d = FakeArchiveDriver(events=[seg("x", "y", "/z")],
                              capability={"events": "unsupported", "segments": "unsupported"})
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW)
        self.assertEqual(p["status"], "unsupported")
        self.assertEqual(d.calls, [])          # never even searched — capability gated it out

    def test_unknown_when_enumerate_status_not_supported(self):
        d = FakeArchiveDriver(status="unknown", events=[])
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW)
        self.assertEqual(p["status"], "unknown")
        self.assertIn("inconclusive", p["detail"])

    def test_never_raises_when_enumerate_throws(self):
        d = FakeArchiveDriver(enumerate_raises=RuntimeError("recorder connection reset"))
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW)   # must NOT raise
        self.assertEqual(p["status"], "unknown")
        self.assertIn("did not answer", p["detail"])
        # and the raw error text is never surfaced to the customer
        self.assertNotIn("reset", p["detail"])

    def test_capability_probe_failure_is_tolerated(self):
        d = FakeArchiveDriver(events=[seg("a", "b", "/c")],
                              capability_raises=RuntimeError("cap boom"))
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW)
        self.assertEqual(p["status"], "verified")   # falls through to the search, still honest

    def test_window_is_bounded_and_recent(self):
        d = FakeArchiveDriver(events=[])
        dahua_archive.prove_recorder_archive(d, "7", now=NOW, window_seconds=600, limit=5)
        self.assertEqual(len(d.calls), 1)
        call = d.calls[0]
        self.assertEqual(call["channel"], "7")
        self.assertEqual(call["end"], NOW)
        self.assertEqual(call["start"], NOW - timedelta(seconds=600))
        self.assertEqual(call["limit"], 5)

    def test_tiny_window_is_floored(self):
        d = FakeArchiveDriver(events=[])
        p = dahua_archive.prove_recorder_archive(d, "1", now=NOW, window_seconds=1)
        self.assertGreaterEqual(p["window_seconds"], 60)     # floored to a sane minimum
        self.assertEqual(d.calls[0]["start"], NOW - timedelta(seconds=60))


# --- verify_recorder_archive: the setup wrapper -----------------------------

class VerifyRecorderArchive(unittest.TestCase):
    def _make_build(self, driver):
        seen = {}
        def _build(name, url, user, password, timeout):
            seen["args"] = (name, url, user, password, timeout)
            return driver
        return _build, seen

    def test_builds_probes_and_closes(self):
        d = FakeArchiveDriver(events=[seg("a", "b", "/c")])
        _build, seen = self._make_build(d)
        installed = {"n": 0}
        p = backend.verify_recorder_archive(
            "http://10.0.0.5", "dahua", "admin", "secret",
            [{"channel": "1", "name": "Reception"}],
            now=NOW, _build=_build, _install=lambda: installed.__setitem__("n", installed["n"] + 1))
        self.assertEqual(p["status"], "verified")
        self.assertTrue(d.closed, "the setup driver must always be closed")
        self.assertEqual(installed["n"], 1, "the archive impl must be installed before probing")
        self.assertEqual(seen["args"][1], "http://10.0.0.5")
        self.assertNotIn("secret", str(p))     # never echo the recorder password

    def test_channel_objects_are_accepted(self):
        d = FakeArchiveDriver(events=[])
        _build, _ = self._make_build(d)
        backend.verify_recorder_archive("http://x", "dahua", "admin", "pw",
                                        [SimpleNamespace(channel="5")],
                                        now=NOW, _build=_build, _install=lambda: None)
        self.assertEqual(d.calls[0]["channel"], "5")

    def test_no_channels_is_unknown(self):
        called = {"built": False}
        def _build(*a, **k):
            called["built"] = True
            return FakeArchiveDriver()
        p = backend.verify_recorder_archive("http://x", "dahua", "admin", "pw", [],
                                            now=NOW, _build=_build, _install=lambda: None)
        self.assertEqual(p["status"], "unknown")
        self.assertIn("No camera channel", p["detail"])
        self.assertFalse(called["built"], "must not build a driver when there is no channel")

    def test_build_failure_never_propagates(self):
        def _build(*a, **k):
            raise RuntimeError("connection refused")
        p = backend.verify_recorder_archive("http://x", "dahua", "admin", "pw",
                                            [{"channel": "1"}],
                                            now=NOW, _build=_build, _install=lambda: None)
        self.assertEqual(p["status"], "unknown")
        self.assertIn("could not be checked", p["detail"])
        self.assertNotIn("refused", str(p))    # raw error never surfaced


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""0.4.4 §10 — post-install acceptance self-test (no cloud / recorder / spool / database).

Two layers:
  * ``acceptance.run_checks`` / ``map_archive_status`` — the pure gating logic: ordered execution,
    hard-vs-soft verdict, a throwing probe becomes 'blocked' (never a crash), archive mapping.
  * ``watchlog_agent.cmd_accept`` — the real command flow driven entirely through injected fakes,
    proving an ACCEPTED site returns 0, a BLOCKED site returns 2, and the recorder password never
    appears in the output.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

AGENT = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT))

import acceptance                 # noqa: E402
import watchlog_agent as wa       # noqa: E402

SECRET_PW = "Sup3rSecretRecorderPw!"


# --- pure gating logic ------------------------------------------------------

class RunChecks(unittest.TestCase):
    def test_all_pass_is_ready(self):
        rep = acceptance.run_checks([
            {"key": "a", "label": "A", "hard": True, "run": lambda: "pass"},
            {"key": "b", "label": "B", "hard": True, "run": lambda: ("pass", "ok")},
        ])
        self.assertTrue(rep["ready"])
        self.assertEqual(rep["summary"]["passed"], 2)
        self.assertEqual(rep["summary"]["hard_failures"], 0)

    def test_hard_failure_blocks(self):
        rep = acceptance.run_checks([
            {"key": "a", "label": "A", "hard": True, "run": lambda: "pass"},
            {"key": "b", "label": "B", "hard": True, "run": lambda: ("blocked", "down")},
        ])
        self.assertFalse(rep["ready"])
        self.assertEqual(rep["summary"]["hard_failures"], 1)

    def test_soft_warn_does_not_block(self):
        rep = acceptance.run_checks([
            {"key": "a", "label": "A", "hard": True, "run": lambda: "pass"},
            {"key": "b", "label": "B", "hard": False, "run": lambda: ("warn", "quiet")},
        ])
        self.assertTrue(rep["ready"])
        self.assertEqual(rep["summary"]["warned"], 1)

    def test_throwing_probe_becomes_blocked_not_crash(self):
        def boom():
            raise RuntimeError("recorder connection reset by peer")
        rep = acceptance.run_checks([{"key": "x", "label": "X", "hard": True, "run": boom}])
        self.assertFalse(rep["ready"])
        self.assertEqual(rep["checks"][0]["status"], "blocked")
        self.assertNotIn("reset", rep["checks"][0]["detail"])   # raw error never surfaced

    def test_unknown_status_is_coerced_blocked(self):
        rep = acceptance.run_checks([{"key": "x", "label": "X", "hard": False, "run": lambda: "banana"}])
        self.assertEqual(rep["checks"][0]["status"], "blocked")

    def test_missing_run_is_skipped(self):
        rep = acceptance.run_checks([{"key": "x", "label": "X", "hard": False}])
        self.assertEqual(rep["checks"][0]["status"], "skipped")

    def test_checks_run_in_order(self):
        seq = []
        acceptance.run_checks([
            {"key": "1", "label": "1", "hard": False, "run": lambda: seq.append("a") or "pass"},
            {"key": "2", "label": "2", "hard": False, "run": lambda: seq.append("b") or "pass"},
        ])
        self.assertEqual(seq, ["a", "b"])

    def test_archive_status_mapping(self):
        self.assertEqual(acceptance.map_archive_status("verified"), ("pass", True))
        for s in ("empty", "unsupported", "unknown", "whatever"):
            self.assertEqual(acceptance.map_archive_status(s), ("warn", False))


# --- fakes for cmd_accept ---------------------------------------------------

class FakeDriver:
    def __init__(self, *, channels=1, events=1):
        self._channels = channels
        self._events = events
        self.closed = False

    def list_channels(self):
        return [SimpleNamespace(channel=str(i + 1), name=f"Cam {i + 1}") for i in range(self._channels)]

    def stream_events(self, stop):
        for i in range(self._events):
            if stop.is_set():
                return
            yield SimpleNamespace(device_ts=None, channel="1", event_type="motion")

    def close(self):
        self.closed = True


class FakeSpool:
    def __init__(self, n=0):
        self._n = n
        self.closed = False

    def count(self):
        return self._n

    def close(self):
        self.closed = True


class AcceptDetector:
    """A packaged detector that discards a blank frame -> the AI acceptance check passes."""
    available = True
    model_name = "accept-yolo"

    def classify_event(self, jpeg):
        return False, []


class UnavailableDetector:
    available = False
    model_name = None

    def classify_event(self, jpeg):
        return True, None


def _cfg():
    return SimpleNamespace(
        nvr_url="http://10.0.0.9", supabase_url="https://x.supabase.co",
        publishable_key="pk_test", nvr_driver="dahua", nvr_password=SECRET_PW,
        state_path=Path("/nonexistent/state.json"),
        spool_path=Path("/nonexistent/spool.db"), spool_max_rows=0)


def _run_accept(**over):
    """Drive cmd_accept with all dependencies faked; return (exit_code, stdout, report)."""
    kwargs = dict(
        _state={"agent_id": "a1", "agent_key": "k1", "site_id": "s1"},
        _open_driver=lambda cfg: (FakeDriver(), SimpleNamespace(vendor="Dahua", model="XVR")),
        _cloud_factory=lambda: object(),
        _heartbeat=lambda cloud, state, device: None,
        _spool_factory=lambda: FakeSpool(),
        _archive=lambda driver, channel: {"status": "verified", "detail": "2 segment(s) retrievable"},
        _detector=AcceptDetector(),
        _ini_text="",                       # no plaintext recorder password on disk
        live_seconds=1,
    )
    kwargs.update(over)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = wa.cmd_accept(_cfg(), **kwargs)
    out = buf.getvalue()
    report = json.loads(out.split("ACCEPTANCE_JSON ", 1)[1].splitlines()[0])
    return code, out, report


class CmdAccept(unittest.TestCase):
    def test_healthy_site_is_accepted(self):
        code, out, report = _run_accept()
        self.assertEqual(code, 0)
        self.assertIn("RESULT: ACCEPTED", out)
        self.assertTrue(report["ready"])
        keys = [c["key"] for c in report["checks"]]
        self.assertEqual(keys, ["config", "identity", "runtime", "cloud", "recorder",
                                "cameras", "archive", "live", "ai", "spool", "security"])

    def test_plaintext_recorder_password_blocks(self):
        code, out, report = _run_accept(_ini_text="[watchlog]\nnvr_password = Sup3rSecret!\n")
        self.assertEqual(code, 2)
        by = {c["key"]: c for c in report["checks"]}
        self.assertEqual(by["security"]["status"], "blocked")
        self.assertIn("plaintext recorder password", by["security"]["detail"])

    def test_ai_unavailable_warns_but_accepts(self):
        code, out, report = _run_accept(_detector=UnavailableDetector())
        self.assertEqual(code, 0)                       # AI is fail-open: warns, does not block
        by = {c["key"]: c for c in report["checks"]}
        self.assertEqual(by["ai"]["status"], "warn")

    def test_recorder_down_blocks(self):
        def boom(cfg):
            raise wa.DriverError("connection refused")
        code, out, report = _run_accept(_open_driver=boom)
        self.assertEqual(code, 2)
        self.assertIn("RESULT: BLOCKED", out)
        by = {c["key"]: c for c in report["checks"]}
        self.assertEqual(by["recorder"]["status"], "blocked")
        self.assertEqual(by["cameras"]["status"], "blocked")   # depends on the recorder
        self.assertEqual(by["archive"]["status"], "warn")      # soft: can't check, not a hard fail

    def test_not_enrolled_blocks(self):
        code, out, report = _run_accept(_state=None)
        self.assertEqual(code, 2)
        by = {c["key"]: c for c in report["checks"]}
        self.assertEqual(by["identity"]["status"], "blocked")
        self.assertEqual(by["cloud"]["status"], "blocked")

    def test_quiet_site_warns_but_accepts(self):
        code, out, report = _run_accept(
            _open_driver=lambda cfg: (FakeDriver(events=0), SimpleNamespace(vendor="Dahua", model="XVR")))
        self.assertEqual(code, 0)               # no live events is a warning, not a block
        by = {c["key"]: c for c in report["checks"]}
        self.assertEqual(by["live"]["status"], "warn")

    def test_recorder_password_never_leaks(self):
        _code, out, report = _run_accept()
        self.assertNotIn(SECRET_PW, out)
        self.assertNotIn(SECRET_PW, json.dumps(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)

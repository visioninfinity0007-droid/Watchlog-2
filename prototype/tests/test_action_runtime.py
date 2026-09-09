#!/usr/bin/env python3
"""Evidence action runtime tests (workstream 2): the dispatcher executes configured actions,
reuses injected transports, and — crucially — reports UNSUPPORTED truthfully instead of
fabricating evidence. No continuous video; footage is a single bounded request."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from action_runtime import ActionRuntime  # noqa: E402


class ActionTests(unittest.TestCase):
    def test_capture_still_success(self):
        uploaded = {}

        def upload(cam, b64):
            uploaded["cam"] = cam
            return "still-ref"
        rt = ActionRuntime(snapshot=lambda ch: b"jpegbytes" * 10, upload_still=upload)
        out = rt.run([{"type": "capture_still"}], channel="1", camera_id="cam-1")
        self.assertEqual("captured", out[0]["result"])
        self.assertEqual("still-ref", out[0]["ref"])
        self.assertEqual("cam-1", uploaded["cam"])

    def test_capture_still_unsupported_when_no_image(self):
        rt = ActionRuntime(snapshot=lambda ch: None, upload_still=lambda cam, b: "x")
        out = rt.run([{"type": "capture_still"}], channel="1", camera_id="cam-1")
        self.assertEqual("unsupported", out[0]["result"])       # truthful, not fabricated

    def test_capture_still_unsupported_when_no_capability(self):
        rt = ActionRuntime()                                     # no snapshot/upload injected
        out = rt.run([{"type": "capture_still"}], channel="1", camera_id="cam-1")
        self.assertEqual("unsupported", out[0]["result"])

    def test_capture_still_skips_oversize(self):
        rt = ActionRuntime(snapshot=lambda ch: b"x" * 5_000_000, upload_still=lambda cam, b: "x",
                           still_max_bytes=3_000_000)
        out = rt.run([{"type": "capture_still"}], channel="1", camera_id="cam-1")
        self.assertEqual("skipped", out[0]["result"])

    def test_request_footage_uses_bounded_transport(self):
        calls = []
        rt = ActionRuntime(request_footage=lambda inc: calls.append(inc) or {"status": "pending", "request_id": "r1"})
        out = rt.run([{"type": "request_footage"}], incident={"id": 7, "event_id": "e1"})
        self.assertEqual("requested", out[0]["result"])
        self.assertEqual("pending", out[0]["status"])
        self.assertEqual("r1", out[0]["request_id"])
        self.assertEqual(1, len(calls))

    def test_request_footage_unsupported_without_source(self):
        rt = ActionRuntime()                                     # no requester
        out = rt.run([{"type": "request_footage"}], incident={"id": 7})
        self.assertEqual("unsupported", out[0]["result"])

    def test_flags_and_unknown_and_error(self):
        def boom(cam, b):
            raise RuntimeError("upload failed")
        rt = ActionRuntime(snapshot=lambda ch: b"img", upload_still=boom)
        out = rt.run([{"type": "mark_review"}, {"type": "include_in_report"},
                      {"type": "escalate_severity", "severity": "critical"},
                      {"type": "notify"}, {"type": "not_a_real_action"}, {"type": "capture_still"}],
                     channel="1", camera_id="cam-1")
        by = {o["type"]: o for o in out}
        self.assertEqual("marked", by["mark_review"]["result"])
        self.assertEqual("flagged", by["include_in_report"]["result"])
        self.assertEqual("critical", by["escalate_severity"]["to"])
        self.assertEqual("queued", by["notify"]["result"])
        self.assertEqual("skipped_unknown", by["not_a_real_action"]["result"])
        self.assertEqual("error", by["capture_still"]["result"])   # uploader raised, reported honestly


if __name__ == "__main__":
    unittest.main(verbosity=1)

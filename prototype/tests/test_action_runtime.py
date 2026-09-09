#!/usr/bin/env python3
"""Evidence + notification action runtime tests. Evidence (capture_still / request_footage) is
SERVER-authorized and fulfilled by the evidence workers, so the frame-time runtime only
ACKNOWLEDGES it (never captures arbitrary frame-time evidence). Recorded-intent actions apply
locally; unknown actions are skipped, never stored."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from action_runtime import ActionRuntime  # noqa: E402


class ActionTests(unittest.TestCase):
    def test_capture_still_is_server_authorized(self):
        rt = ActionRuntime()
        out = rt.run([{"type": "capture_still"}], channel="1", camera_id="cam-1", incident={"id": 7})
        self.assertEqual("authorized", out[0]["result"])            # not captured at frame time
        self.assertIn("evidence worker", out[0]["detail"])

    def test_request_footage_is_server_authorized(self):
        rt = ActionRuntime()
        out = rt.run([{"type": "request_footage"}], incident={"id": 7})
        self.assertEqual("authorized", out[0]["result"])            # reuses the bounded clip transport
        self.assertIn("evidence worker", out[0]["detail"])

    def test_recorded_intent_actions_apply_locally(self):
        rt = ActionRuntime()
        out = rt.run([{"type": "mark_review"}, {"type": "include_in_report"},
                      {"type": "escalate_severity", "severity": "critical"}, {"type": "notify"}])
        by = {o["type"]: o for o in out}
        self.assertEqual("marked", by["mark_review"]["result"])
        self.assertEqual("flagged", by["include_in_report"]["result"])
        self.assertEqual("critical", by["escalate_severity"]["to"])
        self.assertEqual("queued", by["notify"]["result"])

    def test_unknown_action_skipped_not_stored(self):
        rt = ActionRuntime()
        out = rt.run([{"type": "not_a_real_action"}, "capture_still"])
        by = {o["type"]: o for o in out}
        self.assertEqual("skipped_unknown", by["not_a_real_action"]["result"])
        self.assertEqual("authorized", by["capture_still"]["result"])   # string form accepted too


if __name__ == "__main__":
    unittest.main(verbosity=1)

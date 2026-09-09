#!/usr/bin/env python3
"""Archive runtime tests (workstream 3): claims scans, reprocesses recovered frames, records
results with detail.source='archive', and — when footage retrieval is unavailable — reports the
scan 'failed' honestly instead of fabricating recovered events. The DB provenance label itself
is enforced by 0051 and proven in e2e_archive_pg.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from archive_runtime import ArchiveRuntime  # noqa: E402

STATE = {"agent_id": "A", "agent_key": "k"}


class FakeCloud:
    def __init__(self, scans):
        self.scans = scans
        self.recorded = []
        self.status = []

    def call(self, fn, **kw):
        if fn == "wl_agent_claim_archive_scans":
            return self.scans
        if fn == "wl_agent_record_archive_result":
            self.recorded.append(kw)
            return {"id": len(self.recorded), "provenance_label": "Recovered from recorder archive"}
        if fn == "wl_agent_set_archive_scan_status":
            self.status.append(kw)
            return {"status": kw["p_status"]}
        raise KeyError(fn)


class ArchiveTests(unittest.TestCase):
    def test_reprocess_and_record_with_provenance(self):
        scans = [{"scan_id": "s1", "camera_ids": ["cam-1"], "rule_ids": None,
                  "from_ts": "2026-09-01T00:00:00Z", "to_ts": "2026-09-01T01:00:00Z"}]
        cloud = FakeCloud(scans)
        rt = ArchiveRuntime(
            cloud=cloud, state=STATE,
            retrieve_frames=lambda cam, a, b: [(b"jpeg", "2026-09-01T00:10:00Z")],
            analyze=lambda cam, jpeg, ts, rules: [{"result_type": "zone_entry", "recovered_at": ts, "confidence": 0.8}],
        )
        out = rt.poll_and_process()
        self.assertEqual("complete", out[0]["status"])
        self.assertEqual(1, out[0]["candidates"])
        self.assertEqual(1, len(cloud.recorded))
        self.assertEqual({"source": "archive"}, cloud.recorded[0]["p_detail"])
        self.assertEqual("complete", cloud.status[-1]["p_status"])

    def test_retrieval_unavailable_fails_honestly(self):
        scans = [{"scan_id": "s2", "camera_ids": ["cam-1"], "rule_ids": None,
                  "from_ts": "x", "to_ts": "y"}]
        cloud = FakeCloud(scans)
        rt = ArchiveRuntime(cloud=cloud, state=STATE,
                            retrieve_frames=lambda cam, a, b: None,   # cannot retrieve
                            analyze=lambda *a: [])
        out = rt.poll_and_process()
        self.assertEqual("failed", out[0]["status"])
        self.assertEqual(0, out[0]["candidates"])
        self.assertEqual([], cloud.recorded)                          # nothing fabricated
        self.assertIn("retrieval unavailable", cloud.status[-1]["p_error"])

    def test_partial_retrieval_is_marked(self):
        scans = [{"scan_id": "s3", "camera_ids": ["cam-1", "cam-2"], "rule_ids": None,
                  "from_ts": "x", "to_ts": "y"}]
        cloud = FakeCloud(scans)
        rt = ArchiveRuntime(
            cloud=cloud, state=STATE,
            retrieve_frames=lambda cam, a, b: [(b"j", "t")] if cam == "cam-1" else None,
            analyze=lambda cam, j, ts, r: [{"result_type": "zone_empty", "recovered_at": ts}])
        out = rt.poll_and_process()
        self.assertEqual("complete", out[0]["status"])
        self.assertEqual(1, out[0]["candidates"])
        self.assertIn("partial", cloud.status[-1]["p_error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

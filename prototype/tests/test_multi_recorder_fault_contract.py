#!/usr/bin/env python3
"""Static contract: recorder-scoped current truth in the LATEST SQL definitions.

No database. Reads every migration in order and checks the last definition of
each function, so a later migration that re-creates one without recorder
identity fails here before it reaches the Postgres gates
(e2e_multi_recorder_faults_pg.py proves the behaviour).

- MNVR-013: recording/storage current proof is recorder-scoped; the legacy
  site-scoped RPC resolves the singleton recorder (fails closed on multi).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


def latest_body(name: str) -> tuple[str, str]:
    """(migration file, full text of the last `create or replace function public.<name>`)."""
    pattern = re.compile(
        rf"create\s+or\s+replace\s+function\s+public\.{re.escape(name)}\s*\("
        r".*?as\s+\$(\w*)\$(.*?)\$\1\$",
        re.I | re.S,
    )
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            found = (path.name, m.group(0))
    if found is None:
        raise AssertionError(f"function public.{name} is not defined by any migration")
    return found


class RecorderCurrentProofContract(unittest.TestCase):
    def test_recorder_current_proof_rpc_is_recorder_scoped(self):
        _, rpc = latest_body("wl_report_recorder_recording_storage_current")
        self.assertIn("p_recorder_id uuid", rpc)
        self.assertIn("wl_assert_current_agent_authority", rpc)
        self.assertIn("wl_report_recorder_recording_storage_current_core", rpc)
        _, core = latest_body("wl_report_recorder_recording_storage_current_core")
        self.assertRegex(core, r"c\.recorder_id\s*=\s*p_recorder_id",
                         "cameras must be mapped by recorder+channel")
        self.assertIn("recorder_health", core, "storage proof is kept per recorder")
        self.assertNotIn("nvr_health", core,
                         "the recorder core never writes the agent-wide nvr_health row")

    def test_legacy_current_proof_requires_singleton_recorder(self):
        _, legacy = latest_body("wl_report_recording_storage_current")
        self.assertIn("wl_legacy_recorder_for_agent", legacy,
                      "the site-scoped RPC must fail closed on a multi-recorder site")
        self.assertIn("wl_report_recorder_recording_storage_current_core", legacy)
        self.assertIsNone(
            re.search(r"c\.site_id\s*=\s*v_agent\.site_id\s+and\s+c\.channel", legacy),
            "the legacy RPC must not map cameras by site+channel",
        )


if __name__ == "__main__":
    sys.exit(unittest.main())

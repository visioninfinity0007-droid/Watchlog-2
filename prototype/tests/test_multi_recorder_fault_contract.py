#!/usr/bin/env python3
"""Static contract: recorder-scoped current truth in the LATEST SQL definitions.

No database. Reads every migration in order and checks the last definition of
each function, so a later migration that re-creates one without recorder
identity fails here before it reaches the Postgres gates
(e2e_multi_recorder_faults_pg.py proves the behaviour).

- MNVR-013: recording/storage current proof is recorder-scoped; the legacy
  site-scoped RPC resolves the singleton recorder (fails closed on multi).
- MNVR-014: wl_reconcile_site_faults reads recorder_health, keys recorder
  faults 'nvr:'||recorder_id on multi-recorder sites, gates camera faults on
  the camera's own recorder; the sweep visits recorder_health sites.
- Recorder-scoped disk events are skipped by the activity/episode pipeline
  and read as recorder storage evidence by the fault reconciliation.
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


class RecorderAwareFaultContract(unittest.TestCase):
    def test_reconcile_reads_recorder_health_and_keys_by_recorder(self):
        _, rec = latest_body("wl_reconcile_site_faults")
        self.assertIn("public.recorder_health", rec)
        self.assertRegex(rec, r"'nvr:'\s*\|\|\s*r\.id::text",
                         "multi-recorder faults are keyed by recorder")
        self.assertRegex(rec, r"o\.recorder_id\s*=\s*c\.recorder_id",
                         "camera faults are gated by the camera's own recorder")
        self.assertIn("security definer", rec.lower())
        self.assertIn("set search_path = public", rec)

    def test_sweep_visits_recorder_health_sites(self):
        _, sweep = latest_body("wl_sweep_faults")
        self.assertIn("recorder_health", sweep)


class RecorderScopedEventContract(unittest.TestCase):
    def test_disk_events_without_camera_are_not_camera_activity(self):
        for name in ("wl_derive_activities", "wl_derive_episodes"):
            _, body = latest_body(name)
            self.assertIn("wl_event_is_recorder_scoped_disk", body,
                          f"{name} must skip recorder-scoped disk events")
        _, rec = latest_body("wl_reconcile_site_faults")
        self.assertIn("wl_event_is_recorder_scoped_disk", rec,
                      "recorder-scoped disk events are recorder storage evidence")
        _, pred = latest_body("wl_event_is_recorder_scoped_disk")
        for flag in ("recorder_scoped", "recorder_scope", "p_camera_id is null"):
            self.assertIn(flag, pred)


if __name__ == "__main__":
    sys.exit(unittest.main())

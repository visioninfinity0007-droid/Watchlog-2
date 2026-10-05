#!/usr/bin/env python3
"""Static guard: the multi-recorder Postgres e2e gates test the FINAL chain.

CI applies every migration once with apply_migrations.py. An e2e script that
then cur.execute()s an older migration file swaps that file's function bodies
back in for the rest of its transaction, so it tests code production will never
run (an overwritten wl_sync_recorders, dedupe key or coverage function) and
hides defects in the final bodies. Pre-migration legacy state must be created
with data, and a production-order upgrade must go through apply_migrations.py
(see e2e_multi_recorder_upgrade_rehearsal_pg.py), never by replaying DDL.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REHEARSAL = TESTS / "e2e_multi_recorder_upgrade_rehearsal_pg.py"
CI = TESTS.parents[1] / ".github" / "workflows" / "ci.yml"

# Any reference to a numbered migration file, or to the migrations directory.
MIGRATION_REF = re.compile(r"\d{4}_[a-z0-9_]+\.sql|[\"']migrations[\"']|migrations/")


def gated_scripts() -> list[Path]:
    scripts = sorted(TESTS.glob("e2e_multi_recorder_*_pg.py"))
    scripts.append(TESTS / "e2e_recovery_coverage_pg.py")
    return [p for p in scripts if p != REHEARSAL]


class NoStaleMigrationRerunTests(unittest.TestCase):
    def test_gated_scripts_exist(self):
        names = {p.name for p in gated_scripts()}
        for required in ("e2e_multi_recorder_foundation_pg.py",
                         "e2e_multi_recorder_continuity_pg.py",
                         "e2e_recovery_coverage_pg.py"):
            self.assertIn(required, names)

    def test_e2e_scripts_never_reference_migration_files(self):
        offenders = {}
        for path in gated_scripts():
            text = path.read_text(encoding="utf-8")
            hits = sorted({m.group(0) for m in MIGRATION_REF.finditer(text)})
            if hits:
                offenders[path.name] = hits
        self.assertEqual({}, offenders,
                         "e2e scripts must not re-execute migration files; create legacy "
                         "state with data instead")

    def test_ci_runs_this_guard_and_every_gated_script(self):
        # CI lists its scripts one by one; a guard CI never runs guards nothing.
        ci = CI.read_text(encoding="utf-8")
        self.assertIn(f"prototype/tests/{Path(__file__).name}", ci)
        missing = [p.name for p in gated_scripts() + [REHEARSAL]
                   if f"prototype/tests/{p.name}" not in ci]
        self.assertEqual([], missing, "every multi-recorder e2e gate must run in CI")

    def test_upgrade_rehearsal_applies_through_the_runner(self):
        text = REHEARSAL.read_text(encoding="utf-8")
        self.assertIn("apply_migrations.py", text)
        self.assertIn("WATCHLOG_MIGRATIONS_DIR", text)
        self.assertIsNone(
            re.search(r"execute\([^)]*(MIGRATIONS|migrations)", text),
            "the rehearsal must apply migrations with apply_migrations.py, not execute them",
        )


if __name__ == "__main__":
    sys.exit(unittest.main())

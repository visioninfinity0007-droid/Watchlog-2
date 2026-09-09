"""Regression tests for the migration runner's integrity model.

Two invariants the runner MUST hold (both were violated by the pre-hardening
version and cost us time during the production 0042-0048 apply):

  1. Checksums are computed over LINE-ENDING-NORMALIZED content, so a Windows
     CRLF checkout of a migration recorded on LF (or vice-versa) is NEVER seen
     as a changed migration.
  2. An already-applied migration whose (normalized) checksum no longer matches
     what was recorded is DRIFT: a bare apply FAILS CLOSED — it re-runs nothing
     (not even genuinely pending migrations) until an operator resolves it with
     an explicit --rehash (re-baseline checksum, no SQL) or --force (re-run SQL).

These are pure-function tests: no database, no psycopg required (the driver is
imported lazily inside connect(), so the module loads without it).
"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "supabase" / "apply_migrations.py"
_SPEC = importlib.util.spec_from_file_location("apply_migrations", _PATH)
am = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(am)


# --- 1. normalized checksum -------------------------------------------------

def test_normalized_sha_folds_all_line_endings():
    body = b"create table t (id int);%sselect 1;%s"
    lf = body % (b"\n", b"\n")
    crlf = body % (b"\r\n", b"\r\n")
    cr = body % (b"\r", b"\r")
    assert am.normalized_sha(lf) == am.normalized_sha(crlf) == am.normalized_sha(cr)


def test_normalized_sha_still_detects_a_real_content_change():
    assert am.normalized_sha(b"select 1;\n") != am.normalized_sha(b"select 2;\n")


# --- 2. classification ------------------------------------------------------

def test_classify_pending_applied_drift():
    done = {"0001_x.sql": "hashA"}
    assert am.classify("0002_y.sql", "whatever", done) == am.PENDING
    assert am.classify("0001_x.sql", "hashA", done) == am.APPLIED
    assert am.classify("0001_x.sql", "hashB", done) == am.DRIFT


def test_crlf_working_copy_of_applied_migration_is_applied_not_drift():
    """The exact CRLF drift bug: migration recorded on LF, working copy is CRLF.
    It must read APPLIED (skip), never DRIFT (which would fail closed or re-run)."""
    sql_lf = b"create table t (id int);\ninsert into t values (1);\n"
    recorded = am.normalized_sha(sql_lf)
    crlf_working_copy = sql_lf.replace(b"\n", b"\r\n")
    cur = am.normalized_sha(crlf_working_copy)
    assert am.classify("0016_x.sql", cur, {"0016_x.sql": recorded}) == am.APPLIED


# --- 3. fail-closed planner -------------------------------------------------

def test_bare_apply_blocks_on_drift_and_runs_nothing():
    entries = [
        ("0001_a.sql", am.APPLIED),
        ("0002_b.sql", am.DRIFT),
        ("0003_c.sql", am.PENDING),
    ]
    plan = am.build_plan(entries, force=False)
    assert plan["blocked"] == ["0002_b.sql"]
    # fail closed: not even the genuinely-pending 0003 may run while drift is unresolved
    assert plan["run"] == []


def test_pending_only_runs_in_order():
    entries = [
        ("0001_a.sql", am.APPLIED),
        ("0002_b.sql", am.PENDING),
        ("0003_c.sql", am.PENDING),
    ]
    plan = am.build_plan(entries, force=False)
    assert plan["run"] == ["0002_b.sql", "0003_c.sql"]
    assert plan["skip"] == ["0001_a.sql"]
    assert plan["blocked"] == []


def test_force_reruns_everything_including_drift():
    entries = [
        ("0001_a.sql", am.APPLIED),
        ("0002_b.sql", am.DRIFT),
        ("0003_c.sql", am.PENDING),
    ]
    plan = am.build_plan(entries, force=True)
    assert plan["run"] == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]
    assert plan["blocked"] == []

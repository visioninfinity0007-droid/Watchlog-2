#!/usr/bin/env python3
"""Contract for the prepared production ledger reconciliation
(docs/production/ledger_reconciliation_2026-10-06.sql, MNVR-064 / PR-0).

Production's public.schema_migrations was read on 2026-10-06 (read-only): 114
rows, 0001..0110 without 0094, 0140, 0141, both 0142 files and 0143, every row
equal to the repo bytes. It lacks 0111..0139 (both 0115 files) and 0144/0145,
whose objects are live. The SQL records exactly those 32 files. This test pins:

  * the SQL inserts exactly the files missing from that production ledger list,
    each with the sha256 apply_migrations.py computes for the committed bytes;
  * after it, the runner's classify()/build_plan() see 0 DRIFT and run only the
    intended pending files (0146 onward: 0156 here, 0146..0156 on the DB chain);
  * the SQL's only write is that insert, ON CONFLICT DO NOTHING, in one
    transaction, guarded by a key-object check that covers every file.

    python -m pytest prototype/tests/test_ledger_reconciliation.py -q   # static
    python prototype/tests/test_ledger_reconciliation.py --pg           # disposable Postgres

--pg runs the SQL file against the DISPOSABLE CI Postgres only (refuses unless
WATCHLOG_CI_PLAIN_POSTGRES=1): it reduces the ledger to production's 114 rows,
proves the guard aborts the whole transaction, runs the file, checks
`apply_migrations.py --status`, and restores the ledger rows it removed.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPA = ROOT / "prototype" / "supabase"
MIG = SUPA / "migrations"
SQL_FILE = ROOT / "docs" / "production" / "ledger_reconciliation_2026-10-06.sql"
sys.path.insert(0, str(SUPA))
from apply_migrations import APPLIED, DRIFT, PENDING, build_plan, classify, normalized_sha  # noqa: E402

# Production public.schema_migrations on 2026-10-06: 0001..0110 (0094 never existed),
# 0140, 0141, both 0142 files, 0143. md5 over "filename:sha256" lines (collate "C",
# joined by LF) = PRODUCTION_LEDGER_DIGEST, identical to the repo bytes.
PRODUCTION_LEDGER_ROWS = 114
PRODUCTION_LEDGER_DIGEST = "7b5024031f9e3887e039427f6ea30f96"


def _production_recorded(name: str) -> bool:
    n = name[:4]
    return n <= "0110" or n in ("0140", "0141", "0142", "0143")


# The files production lacks although their objects are live (read 2026-10-06).
PRODUCTION_MISSING = [
    "0111_visual_snapshot_pipeline.sql",
    "0112_prioritize_recent_visual_review.sql",
    "0113_private_media_mirror.sql",
    "0114_vision_worker_heartbeat.sql",
    "0115_context_aware_visual_review.sql",
    "0115_recorder_push_status.sql",
    "0116_camera_preview_realtime.sql",
    "0117_site_lifecycle_notifications_camera_identity.sql",
    "0118_visual_review_canonical_cameras.sql",
    "0119_remote_agent_maintenance.sql",
    "0120_recovered_snapshot_timestamps.sql",
    "0121_restaurant_visual_analytics.sql",
    "0122_vision_worker_runtime.sql",
    "0123_schedule_vision_worker.sql",
    "0124_restaurant_preopen_service_day.sql",
    "0125_restaurant_server_capture_scheduler.sql",
    "0126_chaiwala_ai_context_alignment.sql",
    "0127_chaiwala_report_windows.sql",
    "0128_chaiwala_analytics_quality.sql",
    "0129_office_reporting_context.sql",
    "0130_business_day_evidence_window.sql",
    "0131_report_recommendation_notifications.sql",
    "0132_chaiwala_yesterday_service_day_rule.sql",
    "0133_existing_site_preflight_auth.sql",
    "0134_report_recommendation_feedback.sql",
    "0135_report_recommendation_feedback_indexes.sql",
    "0136_service_day_monitoring_truth.sql",
    "0137_chaiwala_monitoring_context_truth.sql",
    "0138_rolling_saved_report_windows.sql",
    "0139_visual_service_day_windows.sql",
    "0144_portal_qa_truth_contracts.sql",
    "0145_camera_preview_performance.sql",
]


def _intended_pending(name: str) -> bool:
    """Files that are genuinely not applied in production: 0146 onward
    (0156 hotfix; the multi-recorder chain 0146..0155 and later)."""
    return name[:4] >= "0146"


def _repo_files() -> dict[str, str]:
    return {f.name: normalized_sha(f.read_bytes()) for f in sorted(MIG.glob("*.sql"))}


def _sql() -> str:
    return SQL_FILE.read_text(encoding="ascii")


LEDGER_ROW = re.compile(r"^\s*\('(\d{4}_\w+\.sql)', '([0-9a-f]{64})'\),?$", re.M)
OBJ_ROW = re.compile(r"^\s*\('(\d{4}_\w+\.sql)','(\w+)','([^']+)'\),?$", re.M)


def _insert_rows() -> dict[str, str]:
    m = re.search(r"insert into public\.schema_migrations \(filename, sha256\) values\n(.*?)\n"
                  r"on conflict \(filename\) do nothing;", _sql(), re.S)
    assert m, "insert ... on conflict (filename) do nothing not found"
    rows = LEDGER_ROW.findall(m.group(1))
    assert len(rows) == len(m.group(1).splitlines()), "unparsed insert rows"
    return dict(rows)


def _ledger_blocks() -> list[list[tuple[str, str]]]:
    blocks = re.findall(r"ledger\(filename, sha256\) as \(values\n(.*?)\n\s*\)", _sql(), re.S)
    return [LEDGER_ROW.findall(b) for b in blocks]


def _object_blocks() -> list[list[tuple[str, str, str]]]:
    blocks = re.findall(r"o\(filename, kind, name\) as \(values\n(.*?)\n\s*\),", _sql(), re.S)
    return [OBJ_ROW.findall(b) for b in blocks]


def test_production_ledger_rows_match_repo_bytes():
    files = _repo_files()
    recorded = sorted((n for n in files if _production_recorded(n)), key=lambda s: s.encode())
    assert len(recorded) == PRODUCTION_LEDGER_ROWS, len(recorded)
    digest = hashlib.md5("\n".join(f"{n}:{files[n]}" for n in recorded).encode()).hexdigest()
    assert digest == PRODUCTION_LEDGER_DIGEST, digest


def test_sql_inserts_exactly_the_files_missing_from_production():
    files = _repo_files()
    rows = _insert_rows()
    assert sorted(rows) == PRODUCTION_MISSING
    derived = sorted(n for n in files if not _production_recorded(n) and not _intended_pending(n))
    assert derived == PRODUCTION_MISSING, sorted(set(derived) ^ set(PRODUCTION_MISSING))


def test_every_sha_matches_the_runner_computation():
    files = _repo_files()
    rows = _insert_rows()
    for name, sha in rows.items():
        assert sha == files[name], (name, sha, files[name])
        assert sha == normalized_sha((MIG / name).read_bytes()), name


def test_ledger_copies_and_key_object_lists_agree():
    blocks = _ledger_blocks()
    assert len(blocks) == 2, len(blocks)   # guard conflict check + post-check
    for b in blocks:
        assert dict(b) == _insert_rows()
    objs = _object_blocks()
    assert len(objs) == 2, len(objs)       # step-1 read-only query + guard
    assert objs[0] == objs[1] and objs[0]
    assert {f for f, _k, _n in objs[0]} == set(PRODUCTION_MISSING), "every file needs a key object"
    kinds = {k for _f, k, _n in objs[0]}
    assert kinds <= {"function", "table", "index", "column", "trigger", "policy", "cron", "jsonkey"}, kinds


def test_runner_reports_only_intended_pending_after_reconciliation():
    files = _repo_files()
    done = {n: s for n, s in files.items() if _production_recorded(n)}
    before = [(n, classify(n, s, done)) for n, s in files.items()]
    assert {n for n, st in before if st == PENDING} >= set(PRODUCTION_MISSING)
    done.update(_insert_rows())
    entries = [(n, classify(n, s, done)) for n, s in files.items()]
    states = dict(entries)
    assert not [n for n, st in entries if st == DRIFT]
    pending = sorted(n for n, st in entries if st == PENDING)
    assert pending == sorted(n for n in files if _intended_pending(n)), pending
    assert "0156_production_truth_hotfix.sql" in pending
    assert all(states[n] == APPLIED for n in PRODUCTION_MISSING)
    plan = build_plan(entries, force=False)
    assert plan["blocked"] == [] and sorted(plan["run"]) == pending


def _strip_comments_and_literals(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"'[^']*'", "''", sql)


def test_only_write_is_the_guarded_ledger_insert_in_one_transaction():
    code = _strip_comments_and_literals(_sql()).lower()
    for word in ("update", "delete", "drop", "alter", "create", "truncate", "grant", "revoke"):
        assert not re.search(rf"\b{word}\b", code), word
    assert len(re.findall(r"\binsert\b", code)) == 1
    assert code.count("begin;") == 1 and code.count("commit;") == 1
    b, g, i, c = (code.index("begin;"), code.index("do $guard$"),
                  code.index("insert into public.schema_migrations"), code.index("commit;"))
    assert b < g < i < c
    assert "raise exception" in code[g:i]


# ------------------------------------------------------------------- --pg mode

def _connect():
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1" or not os.environ.get("SUPABASE_DB_HOST"):
        sys.exit("FATAL: --pg runs only against the disposable CI Postgres "
                 "(WATCHLOG_CI_PLAIN_POSTGRES=1 and SUPABASE_DB_* set).")
    import psycopg

    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), autocommit=True)


def _status() -> dict[str, str]:
    out = subprocess.run([sys.executable, str(SUPA / "apply_migrations.py"), "--status"],
                         capture_output=True, text=True, check=True, env=os.environ.copy()).stdout
    return {m.group(2): m.group(1) for m in re.finditer(r"^\s+(\S+)\s+(\d{4}_\S+\.sql)$", out, re.M)}


def _pg_check() -> None:
    sql = _sql()
    rows = _insert_rows()
    files = _repo_files()
    conn = _connect()
    failures: list[str] = []

    def check(ok, name):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)

    def run_file():
        try:
            conn.execute(sql)
            return None
        except Exception as exc:  # noqa: BLE001 - the error text is the assertion
            conn.execute("rollback")
            return str(exc)

    def recorded():
        return dict(conn.execute("select filename, sha256 from schema_migrations "
                                 "where filename = any(%s)", (list(rows),)).fetchall())

    saved = dict(conn.execute("select filename, sha256 from schema_migrations").fetchall())
    removed = {n: s for n, s in saved.items() if not _production_recorded(n)}
    tenant = None
    try:
        conn.execute("delete from schema_migrations where filename = any(%s)", (list(removed),))
        n_rows = conn.execute("select count(*) from schema_migrations").fetchone()[0]
        check(n_rows == PRODUCTION_LEDGER_ROWS, f"ledger reduced to production's {PRODUCTION_LEDGER_ROWS} rows")

        # Guard 1: a key object is missing (the Chai Wala reporting_prefs paths of
        # 0132/0137 exist only with tenant data) -> nothing is recorded.
        err = run_file()
        check(err is not None and "key objects missing" in err and "0132_" in err,
              "guard aborts when a key object is missing")
        check(recorded() == {}, "aborted run records nothing")

        tag = uuid.uuid4().hex[:8]
        tenant = conn.execute("insert into tenants (name) values (%s) returning id",
                              (f"ledger-test-{tag}",)).fetchone()[0]
        site = conn.execute("insert into sites (tenant_id, name) values (%s, 'Ledger Site') returning id",
                            (tenant,)).fetchone()[0]
        conn.execute(
            "insert into site_business_context (site_id, tenant_id, site_type, reporting_prefs) "
            "values (%s, %s, 'restaurant', '{\"restaurant_intelligence_context\": "
            "{\"service_day\": {\"yesterday_rule\": \"x\"}, \"monitoring_truth\": {}}}'::jsonb)",
            (site, tenant))

        # Guard 2: a file already recorded with a different sha256 -> nothing else is recorded.
        conn.execute("insert into schema_migrations (filename, sha256) values (%s, 'not-the-repo-sha')",
                     (PRODUCTION_MISSING[0],))
        err = run_file()
        check(err is not None and "different sha256" in err, "guard aborts on a conflicting recorded sha256")
        check(recorded() == {PRODUCTION_MISSING[0]: "not-the-repo-sha"}, "conflicting run records nothing")
        conn.execute("delete from schema_migrations where filename = %s", (PRODUCTION_MISSING[0],))

        err = run_file()
        check(err is None, f"reconciliation runs cleanly ({err})")
        check(recorded() == rows, "all 32 files recorded with the runner's sha256")
        n_rows = conn.execute("select count(*) from schema_migrations").fetchone()[0]
        check(n_rows == PRODUCTION_LEDGER_ROWS + len(rows), f"ledger has {n_rows} rows (114 + 32)")

        status = _status()
        pending = sorted(n for n, s in status.items() if s == PENDING)
        want = sorted(n for n in files if _intended_pending(n))
        check(pending == want, f"apply_migrations.py --status: PENDING only {want} (got {pending})")
        check(DRIFT not in status.values(), "apply_migrations.py --status: no DRIFT")

        err = run_file()
        check(err is None and recorded() == rows, "a second run is a no-op (ON CONFLICT DO NOTHING)")
    finally:
        conn.execute("insert into schema_migrations (filename, sha256) "
                     "select * from unnest(%s::text[], %s::text[]) "
                     "on conflict (filename) do update set sha256 = excluded.sha256",
                     (list(saved), list(saved.values())))
        if tenant:
            conn.execute("delete from site_business_context where tenant_id = %s", (tenant,))
            conn.execute("delete from sites where tenant_id = %s", (tenant,))
            conn.execute("delete from tenants where id = %s", (tenant,))
        restored = dict(conn.execute("select filename, sha256 from schema_migrations").fetchall())
        check(restored == saved, "ledger restored to its pre-test rows")
        conn.close()
    if failures:
        sys.exit(f"FAIL: {len(failures)} ledger reconciliation step(s)")
    print("OK: ledger reconciliation SQL verified on the disposable Postgres")


if __name__ == "__main__":
    if "--pg" in sys.argv[1:]:
        _pg_check()
        sys.exit(0)
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} ledger reconciliation contracts passed")

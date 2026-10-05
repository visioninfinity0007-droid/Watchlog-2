#!/usr/bin/env python3
"""Per-step ACL gate for the tenant-scoped coverage functions (MNVR-065).

The `integration` job applies the whole migration chain before any e2e runs,
so it only ever sees the FINAL grants. Production applies one file at a time
with autocommit, a failed file stops the runner with the earlier files kept,
and the multi-recorder chain may be approved in stages. Every intermediate
state is therefore a state production can be left in.

This script reproduces that on its OWN disposable database:

  stage 1  apply 0001..0143 (the production baseline) with apply_migrations.py;
  stage 2  apply every later migration ONE AT A TIME with the same runner;
  check    after the baseline and after every later step: a SECURITY DEFINER
           function in schema public whose name contains "coverage" and that
           takes p_site_id is never executable by anon, and is executable by
           authenticated (directly or through PUBLIC) only while its body
           checks the caller's site: wl_assert_my_site(...), or the older
           wl_my_tenant() idiom (0043) that compares the site's tenant.

0103 left the coverage functions service_role-only. A later migration that
grants one to authenticated before the tenant check exists lets any signed-in
user read another tenant's coverage gaps, Agent timeline and recorder names.

KNOWN_OPEN lists exposures in migrations this work package does not own. They
are printed on every run (never silently passed) so their owner can fix them;
any other exposure fails the gate.

Disposable plain Postgres only (WATCHLOG_CI_PLAIN_POSTGRES=1, local host): the
script creates and drops <SUPABASE_DB_NAME>_acl_steps next to SUPABASE_DB_NAME
and never touches SUPABASE_DB_NAME itself.

    python prototype/tests/e2e_coverage_classes_acl_per_step_pg.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
APPLY = ROOT / "supabase" / "apply_migrations.py"
PRELUDE = ROOT / "supabase" / "ci_prelude.sql"
BASELINE_LAST = 143          # production baseline: 0001..0143

# (function name, migration number) exposures owned by another work package.
# 0153 re-grants wl_site_coverage_report_classes to authenticated without a
# tenant check; 0155 adds wl_assert_my_site. Owner: the 0153 coverage package.
KNOWN_OPEN = {
    ("wl_site_coverage_report_classes", 153),
    ("wl_site_coverage_report_classes", 154),
}

ENV = {}
env_path = ROOT.parent / ".env"
for line in env_path.read_text(errors="ignore").splitlines() if env_path.exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
          "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

STEPS: list[bool] = []
TENANT_GUARD = re.compile(r"wl_assert_my_site\s*\(|wl_my_tenant\s*\(", re.I)


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def dsn(dbname: str, autocommit: bool = False) -> dict:
    return dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=dbname,
        connect_timeout=30,
        autocommit=autocommit,
    )


def migration_number(path: Path) -> int:
    return int(path.name[:4])


def apply_migrations(dbname: str, migrations_dir: Path) -> str:
    env = dict(os.environ)
    env["SUPABASE_DB_NAME"] = dbname
    env["WATCHLOG_MIGRATIONS_DIR"] = str(migrations_dir)
    proc = subprocess.run([sys.executable, str(APPLY)], capture_output=True,
                          text=True, env=env, timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"apply_migrations.py failed (exit {proc.returncode}):\n{out[-4000:]}")
    return out


def exposures(conn) -> tuple[int, list[tuple[str, str]]]:
    """(watched function count, [(function name, why it is exposed)])."""
    rows = conn.execute(
        """select p.proname,
                  p.oid::regprocedure::text,
                  p.prosrc,
                  has_function_privilege('anon', p.oid, 'EXECUTE'),
                  has_function_privilege('authenticated', p.oid, 'EXECUTE')
             from pg_proc p
             join pg_namespace n on n.oid=p.pronamespace
            where n.nspname='public'
              and p.prosecdef
              and p.proname like '%%coverage%%'
              and 'p_site_id' = any(coalesce(p.proargnames, '{}'::text[]))"""
    ).fetchall()
    found = []
    for name, signature, body, anon_x, auth_x in rows:
        if anon_x:
            found.append((name, f"{signature} is executable by anon"))
        elif auth_x and not TENANT_GUARD.search(body or ""):
            found.append((name, f"{signature} is executable by authenticated "
                                "without a site check"))
    return len(rows), found


def check(conn, number: int, label: str) -> None:
    watched, found = exposures(conn)
    unexpected = [(n, why) for n, why in found if (n, number) not in KNOWN_OPEN]
    for n, why in found:
        if (n, number) in KNOWN_OPEN:
            print(f"  KNOWN OPEN ({label}, owned by another work package): {why}")
    for n in sorted({f for f, num in KNOWN_OPEN if num == number}):
        if n not in {f for f, _ in found}:
            print(f"  NOTE ({label}): known exposure of {n} no longer reproduces; "
                  "remove it from KNOWN_OPEN")
    step(watched > 0 and not unexpected,
         f"{label}: no tenant coverage function is exposed to browser roles "
         "without a site check",
         f"{watched} watched; " + "; ".join(why for _, why in unexpected) if unexpected
         else f"{watched} watched")


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("FATAL: disposable plain-Postgres gate only (set WATCHLOG_CI_PLAIN_POSTGRES=1).")
    if ENV.get("SUPABASE_DB_HOST") not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("FATAL: this gate creates and drops a database; local Postgres only.")

    admin_db = ENV.get("SUPABASE_DB_NAME", "postgres")
    steps_db = f"{admin_db}_acl_steps"
    admin = psycopg.connect(**dsn(admin_db, autocommit=True))
    admin.execute(f'drop database if exists "{steps_db}" with (force)')
    admin.execute(f'create database "{steps_db}"')
    stage = Path(tempfile.mkdtemp(prefix="wl-acl-steps-"))
    try:
        with psycopg.connect(**dsn(steps_db, autocommit=True)) as c:
            c.execute(PRELUDE.read_text(encoding="utf-8"))

        files = sorted(MIGRATIONS.glob("*.sql"))
        baseline = [p for p in files if migration_number(p) <= BASELINE_LAST]
        pending = [p for p in files if migration_number(p) > BASELINE_LAST]
        for p in baseline:
            shutil.copy2(p, stage / p.name)
        out = apply_migrations(steps_db, stage)
        step(f"{len(baseline)} migration(s) executed" in out and bool(pending),
             "stage 1 applies the 0001..0143 production baseline",
             f"{len(baseline)} baseline, {len(pending)} later")
        with psycopg.connect(**dsn(steps_db, autocommit=True)) as conn:
            check(conn, BASELINE_LAST, f"after {baseline[-1].name}")

        for p in pending:
            shutil.copy2(p, stage / p.name)
            out = apply_migrations(steps_db, stage)
            ran = re.findall(r"apply\s+(\S+\.sql)", out)
            step(ran == [p.name], f"{p.name} applies on its own", ", ".join(ran))
            with psycopg.connect(**dsn(steps_db, autocommit=True)) as conn:
                check(conn, migration_number(p), f"after {p.name}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        admin.execute(f'drop database if exists "{steps_db}" with (force)')
        admin.close()

    passed = sum(1 for s_ in STEPS if s_)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

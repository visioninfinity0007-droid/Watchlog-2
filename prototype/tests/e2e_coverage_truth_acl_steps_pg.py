#!/usr/bin/env python3
"""Per-step ACL gate for the 0153..0155 coverage migrations (MNVR-065, 0153 part).

The `integration` job applies the whole chain before any e2e runs, so it only
sees the FINAL grants. Production applies one file at a time and the
multi-recorder chain may be approved in stages, so the state after each file is
a state production can be left in.

0153 redefines the SECURITY DEFINER wl_site_coverage_report_classes without a
tenant check (it returns any site's gaps, Agent timeline and recorder names);
only 0155 adds wl_assert_my_site. A grant to `authenticated` in 0153 would let
any signed-in user read another tenant's coverage until 0155 is applied.

This script, on its OWN disposable database:

  stage 1  applies every migration numbered below 0153 with apply_migrations.py;
  stage 2  applies 0153, 0154 and 0155 ONE AT A TIME with the same runner;
  check    after each of those steps: no SECURITY DEFINER function in schema
           public whose name contains "coverage" and that takes p_site_id is
           executable by anon, or by authenticated (directly or through PUBLIC)
           unless its body checks the caller's site (wl_assert_my_site(...) or
           the older wl_my_tenant() idiom); and after 0155 the customer entry
           point wl_site_coverage_report_classes is executable by authenticated
           and service_role again, behind its site check.

Earlier steps (0147..0152) are gated by e2e_coverage_classes_acl_per_step_pg.py.

Disposable plain Postgres only (WATCHLOG_CI_PLAIN_POSTGRES=1, local host): the
script creates and drops <SUPABASE_DB_NAME>_covacl next to SUPABASE_DB_NAME and
never touches SUPABASE_DB_NAME itself.

    python prototype/tests/e2e_coverage_truth_acl_steps_pg.py
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
FIRST_STEP = 153
STEPPED = (153, 154, 155)
ENTRY = "public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz)"

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


def exposures(conn) -> tuple[int, list[str]]:
    rows = conn.execute(
        """select p.oid::regprocedure::text,
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
    for signature, body, anon_x, auth_x in rows:
        if anon_x:
            found.append(f"{signature} is executable by anon")
        elif auth_x and not TENANT_GUARD.search(body or ""):
            found.append(f"{signature} is executable by authenticated without a site check")
    return len(rows), found


def check(conn, number: int, label: str) -> None:
    watched, found = exposures(conn)
    step(watched > 0 and not found,
         f"{label}: no tenant coverage function is exposed to browser roles without a site check",
         "; ".join(found) if found else f"{watched} watched")
    if number == 155:
        body, auth_x, svc_x = conn.execute(
            """select p.prosrc,
                      has_function_privilege('authenticated', p.oid, 'EXECUTE'),
                      has_function_privilege('service_role', p.oid, 'EXECUTE')
                 from pg_proc p where p.oid=%s::regprocedure""",
            (ENTRY,),
        ).fetchone()
        step(auth_x and svc_x and bool(TENANT_GUARD.search(body or "")),
             f"{label}: the coverage entry point is open to authenticated and "
             "service_role only behind its site check",
             f"authenticated={auth_x} service_role={svc_x}")


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("FATAL: disposable plain-Postgres gate only (set WATCHLOG_CI_PLAIN_POSTGRES=1).")
    if ENV.get("SUPABASE_DB_HOST") not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("FATAL: this gate creates and drops a database; local Postgres only.")

    admin_db = ENV.get("SUPABASE_DB_NAME", "postgres")
    steps_db = f"{admin_db}_covacl"
    admin = psycopg.connect(**dsn(admin_db, autocommit=True))
    admin.execute(f'drop database if exists "{steps_db}" with (force)')
    admin.execute(f'create database "{steps_db}"')
    stage = Path(tempfile.mkdtemp(prefix="wl-covacl-steps-"))
    try:
        with psycopg.connect(**dsn(steps_db, autocommit=True)) as c:
            c.execute(PRELUDE.read_text(encoding="utf-8"))

        files = sorted(MIGRATIONS.glob("*.sql"))
        before = [p for p in files if migration_number(p) < FIRST_STEP]
        stepped = [p for p in files if migration_number(p) in STEPPED]
        step([migration_number(p) for p in stepped] == list(STEPPED),
             "0153, 0154 and 0155 each exist exactly once",
             ", ".join(p.name for p in stepped))
        for p in before:
            shutil.copy2(p, stage / p.name)
        out = apply_migrations(steps_db, stage)
        step(f"{len(before)} migration(s) executed" in out,
             f"stage 1 applies every migration below {FIRST_STEP:04d}",
             f"{len(before)} applied")

        for p in stepped:
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

#!/usr/bin/env python3
"""
WatchLog prototype — apply the SQL migrations to a Supabase/Postgres project.

Reads credentials from projects/watchlog/.env (gitignored, Secrets Gate), or from
SUPABASE_DB_* environment variables (used by the disposable-Postgres CI gate).
Tracks what has run in a schema_migrations table.

    python supabase/apply_migrations.py            # apply pending (FAILS CLOSED on drift)
    python supabase/apply_migrations.py --status   # list state, change nothing
    python supabase/apply_migrations.py --rehash   # re-baseline applied checksums to the
                                                   # normalized scheme WITHOUT running any SQL
    python supabase/apply_migrations.py --force    # re-execute EVERY migration (dangerous)

Integrity model
---------------
* Checksums are computed over content with line endings normalized to LF
  (``normalized_sha``). CRLF (Windows), CR and LF all fold to LF before hashing,
  so a Windows checkout of a migration recorded on LF (or vice-versa) can NEVER
  look like a changed migration. Normalization is applied consistently on both
  the recorded side and the compared side.
* An already-applied migration whose normalized checksum no longer matches the
  recorded one is DRIFT. A bare apply FAILS CLOSED on any drift: it re-runs
  nothing — not even genuinely pending migrations — until the operator resolves
  it explicitly with either
     --rehash  (accept the current files as the baseline; updates the stored
                checksum only, never executes SQL) — use after verifying via
                ``git diff`` that the change is line-endings-only, or
     --force   (deliberately re-execute the migration SQL and rewrite the checksum).
  A changed applied-migration is therefore NEVER re-run automatically.

Requires: psycopg[binary] (imported lazily, only when actually touching the DB —
so the pure integrity functions above are unit-testable without the driver).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # projects/watchlog
# WATCHLOG_MIGRATIONS_DIR lets tests point the runner at a throwaway migrations
# directory against a disposable Postgres; unset in production (uses the real dir).
_MIG_ENV = os.environ.get("WATCHLOG_MIGRATIONS_DIR")
MIGRATIONS = Path(_MIG_ENV) if _MIG_ENV else (Path(__file__).resolve().parent / "migrations")

# migration states
PENDING = "PENDING"
APPLIED = "applied"
DRIFT = "DRIFT"


def normalized_sha(data: bytes) -> str:
    """SHA-256 over content with line endings normalized to LF.

    This is the ONLY checksum the runner stores or compares, so the exact same
    migration text always yields the same digest regardless of how git checked
    the file out (CRLF vs LF vs CR).
    """
    lf = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(lf).hexdigest()


def classify(name: str, cur_hash: str, done: dict) -> str:
    """PENDING (never applied), APPLIED (applied and checksum matches) or DRIFT
    (applied but the normalized checksum no longer matches what was recorded)."""
    if name not in done:
        return PENDING
    return APPLIED if done[name] == cur_hash else DRIFT


def build_plan(entries, *, force: bool) -> dict:
    """Pure planner (no DB, no I/O) so the fail-closed policy is unit-testable.

    ``entries`` is a list of ``(name, state)`` in apply order. Returns
    ``{'run': [...], 'skip': [...], 'blocked': [...]}``.

    ``blocked`` is non-empty only when there is DRIFT and --force was not given;
    in that case ``run`` is emptied too, because the caller must fail closed and
    execute nothing until the drift is resolved.
    """
    plan = {"run": [], "skip": [], "blocked": []}
    for name, state in entries:
        if state == PENDING:
            plan["run"].append(name)
        elif state == APPLIED:
            (plan["run"] if force else plan["skip"]).append(name)
        else:  # DRIFT
            (plan["run"] if force else plan["blocked"]).append(name)
    if plan["blocked"]:
        plan["run"] = []          # fail closed: resolve drift before ANYTHING runs
    return plan


def load_env() -> dict:
    env = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    # OS environment supplies/overrides — lets a DISPOSABLE target (CI integration Postgres) be set
    # without any .env on disk. Production still uses the .env; there is no .env in CI.
    for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
              "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if not env:
        sys.exit(f"FATAL: no .env at {env_path} and no SUPABASE_DB_* environment variables")
    return env


def connect(env: dict):
    import psycopg  # lazy: keeps the pure integrity functions importable without the driver
    missing = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER",
                           "SUPABASE_DB_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit("FATAL: .env is missing " + ", ".join(missing))
    return psycopg.connect(
        host=env["SUPABASE_DB_HOST"],
        port=int(env.get("SUPABASE_DB_PORT", 5432)),
        user=env["SUPABASE_DB_USER"],
        password=env["SUPABASE_DB_PASSWORD"],
        dbname=env.get("SUPABASE_DB_NAME", "postgres"),
        connect_timeout=15,
        autocommit=True,
    )


def _fail_closed(blocked: list) -> None:
    sys.stderr.write(
        "FATAL: refusing to proceed — these APPLIED migrations have a checksum "
        "mismatch (DRIFT):\n")
    for n in blocked:
        sys.stderr.write(f"    {n}\n")
    sys.stderr.write(
        "\nA changed applied-migration is NEVER re-run automatically. Resolve it:\n"
        "  * restore the file so it matches what was applied, OR\n"
        "  * if `git diff` shows the change is line-endings-only, re-baseline the\n"
        "    stored checksum (updates the record only, runs NO SQL):\n"
        "        python supabase/apply_migrations.py --rehash\n"
        "  * to DELIBERATELY re-execute the migration SQL:\n"
        "        python supabase/apply_migrations.py --force\n")
    sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply WatchLog SQL migrations (fail-closed).")
    ap.add_argument("--status", action="store_true", help="list migration state, change nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-execute EVERY migration and rewrite its checksum (dangerous)")
    ap.add_argument("--rehash", action="store_true",
                    help="re-baseline the stored checksums of DRIFTed applied migrations to the "
                         "normalized scheme WITHOUT running any SQL")
    args = ap.parse_args()

    if args.force and args.rehash:
        sys.exit("FATAL: --force and --rehash are mutually exclusive.")

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        sys.exit(f"FATAL: no .sql files in {MIGRATIONS}")
    hashes = {f.name: normalized_sha(f.read_bytes()) for f in files}

    with connect(load_env()) as conn:
        conn.execute("""
            create table if not exists schema_migrations (
              filename    text primary key,
              sha256      text not null,
              applied_at  timestamptz not null default now()
            )""")
        done = {r[0]: r[1] for r in
                conn.execute("select filename, sha256 from schema_migrations")}

        entries = [(f.name, classify(f.name, hashes[f.name], done)) for f in files]
        states = dict(entries)
        drift = [n for n, s in entries if s == DRIFT]

        if args.status:
            for f in files:
                print(f"  {states[f.name]:22} {f.name}")
            if drift:
                print(f"\n  {len(drift)} DRIFT (applied, checksum changed): " + ", ".join(drift))
                print("  Resolve with --rehash (line-endings-only) or --force (re-run SQL).")
            return

        if args.rehash:
            if not drift:
                print("Nothing to re-baseline; every applied checksum already matches "
                      "the normalized scheme.")
                return
            for name in drift:
                conn.execute("update schema_migrations set sha256 = %s where filename = %s",
                             (hashes[name], name))
                print(f"  rehash   {name}  (checksum re-baselined, SQL NOT run)")
            print(f"\nRe-baselined {len(drift)} checksum(s). No SQL executed.")
            return

        plan = build_plan(entries, force=args.force)
        if plan["blocked"]:
            _fail_closed(plan["blocked"])

        run = set(plan["run"])
        ran = 0
        for f in files:
            if f.name not in run:
                print(f"  skip     {f.name}")
                continue
            print(f"  apply    {f.name} ...", end="", flush=True)
            try:
                conn.execute(f.read_text(encoding="utf-8"))
            except Exception as e:
                print(" FAILED")
                sys.exit(f"\n{f.name}:\n{e}")
            conn.execute(
                "insert into schema_migrations (filename, sha256) values (%s, %s) "
                "on conflict (filename) do update set sha256 = excluded.sha256, "
                "applied_at = now()", (f.name, hashes[f.name]))
            print(" ok")
            ran += 1

    print(f"\nDone. {ran} migration(s) executed.")


if __name__ == "__main__":
    main()

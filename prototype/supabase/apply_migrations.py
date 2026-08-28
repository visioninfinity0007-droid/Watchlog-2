#!/usr/bin/env python3
"""
WatchLog prototype — apply the SQL migrations to the live Supabase project.

Reads credentials from projects/watchlog/.env (gitignored, Secrets Gate).
Tracks what has run in a schema_migrations table, so it is safe to re-run.

    python supabase/apply_migrations.py            # apply pending
    python supabase/apply_migrations.py --status   # list, change nothing
    python supabase/apply_migrations.py --force    # re-apply everything

Requires: psycopg[binary]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]        # projects/watchlog
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def load_env() -> dict:
    env_path = ROOT / ".env"
    if not env_path.exists():
        sys.exit(f"FATAL: no .env at {env_path}")
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def connect(env: dict) -> psycopg.Connection:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        sys.exit(f"FATAL: no .sql files in {MIGRATIONS}")

    with connect(load_env()) as conn:
        conn.execute("""
            create table if not exists schema_migrations (
              filename    text primary key,
              sha256      text not null,
              applied_at  timestamptz not null default now()
            )""")
        done = {r[0]: r[1] for r in
                conn.execute("select filename, sha256 from schema_migrations")}

        if args.status:
            for f in files:
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                if f.name not in done:
                    state = "PENDING"
                elif done[f.name] != digest:
                    state = "CHANGED SINCE APPLIED"
                else:
                    state = "applied"
                print(f"  {state:22} {f.name}")
            return

        for f in files:
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if not args.force and done.get(f.name) == digest:
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
                "applied_at = now()", (f.name, digest))
            print(" ok")

    print("\nAll migrations applied.")


if __name__ == "__main__":
    main()

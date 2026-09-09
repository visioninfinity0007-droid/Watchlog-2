#!/usr/bin/env python3
"""Live proof of the migration runner's integrity model against the DISPOSABLE
CI Postgres (SUPABASE_DB_* env). NEVER runs against production.

It drives the real apply_migrations.py CLI (via WATCHLOG_MIGRATIONS_DIR pointed at
a throwaway migrations dir) and asserts, against a real schema_migrations table:

  1. a fresh apply executes the migrations and records normalized checksums;
  2. re-running is idempotent (0 executed);
  3. re-checking out an applied migration with CRLF line endings is STILL idempotent
     (0 executed, no drift) — the exact production hazard we hit with 0016-0023/0039;
  4. a genuine content change makes a bare apply FAIL CLOSED (exit 2) and run nothing;
  5. --rehash re-baselines the checksum without executing SQL;
  6. --force re-executes.

Cleans up its own throwaway objects and schema_migrations rows afterwards.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

APPLY = Path(__file__).resolve().parents[1] / "supabase" / "apply_migrations.py"
PROBE_TABLE = "zz_apply_probe"
M1 = "zzt001_create.sql"
M2 = "zzt002_seed.sql"


def _run(migdir: Path, *args):
    env = dict(os.environ)
    env["WATCHLOG_MIGRATIONS_DIR"] = str(migdir)
    p = subprocess.run([sys.executable, str(APPLY), *args],
                       capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _connect():
    import psycopg
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), autocommit=True)


def _cleanup():
    try:
        with _connect() as c:
            c.execute(f"drop table if exists {PROBE_TABLE}")
            c.execute("delete from schema_migrations where filename in (%s, %s)", (M1, M2))
    except Exception as e:                                   # noqa: BLE001
        print(f"  (cleanup note: {e})")


def main() -> None:
    if not os.environ.get("SUPABASE_DB_HOST"):
        sys.exit("FATAL: SUPABASE_DB_* not set — this is a disposable-Postgres test.")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        m1 = d / M1
        m2 = d / M2
        # LF line endings on disk to start.
        m1.write_bytes(f"create table if not exists {PROBE_TABLE} (id int primary key);\n".encode())
        m2.write_bytes(f"insert into {PROBE_TABLE} (id) values (1) on conflict do nothing;\n".encode())

        try:
            # 1. fresh apply runs both
            rc, out = _run(d)
            assert rc == 0 and "2 migration(s) executed" in out, f"fresh apply:\n{out}"

            # 2. idempotent
            rc, out = _run(d)
            assert rc == 0 and "0 migration(s) executed" in out, f"re-apply:\n{out}"

            # 3. CRLF re-checkout of an applied migration is STILL idempotent, no drift
            m1.write_bytes(m1.read_bytes().replace(b"\n", b"\r\n"))
            rc, out = _run(d, "--status")
            assert rc == 0 and "DRIFT" not in out, f"CRLF must not be drift:\n{out}"
            rc, out = _run(d)
            assert rc == 0 and "0 migration(s) executed" in out, f"CRLF re-run:\n{out}"

            # 4. genuine content change -> bare apply FAILS CLOSED (exit 2), runs nothing
            m1.write_bytes(m1.read_bytes().replace(b"\r\n", b"\n")
                           + b"-- a real content change\n")
            rc, out = _run(d)
            assert rc == 2 and "DRIFT" in out and "executed" not in out, \
                f"content change must fail closed:\n{out}"

            # 5. --rehash re-baselines the checksum, runs no SQL
            rc, out = _run(d, "--rehash")
            assert rc == 0 and "Re-baselined 1" in out and "No SQL executed" in out, \
                f"rehash:\n{out}"
            rc, out = _run(d, "--status")
            assert rc == 0 and "DRIFT" not in out, f"post-rehash status:\n{out}"

            # 6. --force re-executes (both migrations re-run)
            rc, out = _run(d, "--force")
            assert rc == 0 and "2 migration(s) executed" in out, f"force:\n{out}"

            print("apply_migrations integrity verified on disposable Postgres: "
                  "CRLF idempotent, drift fails closed, rehash + force OK.")
        finally:
            _cleanup()


if __name__ == "__main__":
    main()

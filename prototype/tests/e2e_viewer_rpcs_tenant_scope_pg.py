#!/usr/bin/env python3
"""Viewer RPCs are tenant-scoped (0160): real Postgres, rolled back.

wl_get_snapshot(bigint) and wl_recent_events(int) (0005/0007) are SECURITY DEFINER and
granted to `authenticated`; before 0160 they had no tenant filter, so a member of tenant B
could read tenant A's CCTV stills by event id and list A's recent events. Proves:
- a member reads their own tenant's snapshot and sees their own events;
- a member of another tenant gets null for that snapshot and never sees its events;
- anon still cannot execute either function.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
JPEG = b"\xff\xd8\xff\xe0" + b"tenant-a-still" * 4


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=ENV.get("SUPABASE_DB_NAME", "postgres"),
        connect_timeout=30,
        autocommit=False,
    )

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            def as_auth(uid, sql, *params):
                cur.execute("savepoint auth_sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)",
                            (json.dumps({"sub": str(uid), "role": "authenticated"}),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")
                return row

            def tenant(label):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (f"{label}@watchlog.test",)).fetchone()[0]
                t = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                            f"{label} Tenant", f"{label} Site")[0]["tenant_id"]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (t,)).fetchone()[0]
                return uid, t, site

            uid_a, tenant_a, site_a = tenant("viewer-scope-a")
            uid_b, _tenant_b, _site_b = tenant("viewer-scope-b")
            event_a = cur.execute(
                """insert into events(tenant_id,site_id,event_type,device_ts,agent_ts,dedupe_key)
                   values (%s,%s,'person',now(),now(),'viewer-scope-a-1') returning id""",
                (tenant_a, site_a)).fetchone()[0]
            cur.execute(
                """insert into snapshots(tenant_id,event_id,site_id,image,bytes,content_type,
                                         captured_at)
                   values (%s,%s,%s,%s,%s,'image/jpeg',now())""",
                (tenant_a, event_a, site_a, JPEG, len(JPEG)))

            own = as_auth(uid_a, "select wl_get_snapshot(%s)", event_a)[0]
            step(own is not None and base64.b64decode(own["image_b64"]) == JPEG,
                 "a member reads their own tenant's snapshot")
            other = as_auth(uid_b, "select wl_get_snapshot(%s)", event_a)[0]
            step(other is None, "another tenant's member gets null for that snapshot",
                 json.dumps(other)[:80] if other else "")

            own_events = as_auth(uid_a, "select wl_recent_events(200)")[0]
            step(any(e["event_id"] == event_a for e in own_events),
                 "a member sees their own tenant's recent event")
            other_events = as_auth(uid_b, "select wl_recent_events(200)")[0]
            step(all(e["event_id"] != event_a for e in other_events),
                 "another tenant's member never sees it")
            step(all(e["site"] != "viewer-scope-a Site" for e in other_events),
                 "nor its site name")

            for fn in ("wl_get_snapshot(bigint)", "wl_recent_events(integer)"):
                anon_ok = cur.execute("select has_function_privilege('anon', %s, 'EXECUTE')",
                                      (f"public.{fn}",)).fetchone()[0]
                step(not anon_ok, f"anon cannot execute {fn}")
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

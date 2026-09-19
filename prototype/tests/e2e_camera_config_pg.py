#!/usr/bin/env python3
"""0.4.4 P4 — wl_sync_cameras honors setup-time Monitor/Ignore (real Postgres, rolled back).

Proves the 0100 change end to end: a monitored camera synced from the agent becomes
is_configured=true on first insert (so it is immediately health-monitored), an ignored channel
stays is_configured=false (so it never raises a false health warning), and a routine re-sync never
flips an is_configured decision (the operator/system-of-record wins). Backward-compatible: a camera
dict WITHOUT the flag still defaults to false.

    python prototype/tests/e2e_camera_config_pg.py
"""
from __future__ import annotations

import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in ((ROOT.parent / ".env").read_text(errors="ignore").splitlines()
             if (ROOT.parent / ".env").exists() else []):
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]
import psycopg  # noqa: E402

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    KEY = "camcfg-e2e-key"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('camcfg-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'cc','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, KEY)).fetchone()[0]

            cams = [{"channel": "1", "name": "Reception", "is_configured": True},
                    {"channel": "2", "name": "Spare", "is_configured": False},
                    {"channel": "3", "name": "Legacy"}]          # no flag -> default false (back-compat)
            cur.execute("select wl_sync_cameras(%s,%s,%s::jsonb)", (agent, KEY, json.dumps(cams)))
            cur.fetchone()

            def cfg(ch):
                return cur.execute("select is_configured, name from cameras where site_id=%s and channel=%s",
                                   (sid, ch)).fetchone()

            step(cfg("1")[0] is True, "monitored camera -> is_configured=true on first sync", str(cfg("1")))
            step(cfg("2")[0] is False, "ignored channel -> is_configured=false", str(cfg("2")))
            step(cfg("3")[0] is False, "no flag -> default false (backward-compatible)", str(cfg("3")))

            # operator later marks ch2 configured via the portal RPC; a routine agent re-sync must NOT flip it
            cur.execute("update cameras set is_configured=true where site_id=%s and channel='2'", (sid,))
            resync = [{"channel": "2", "name": "Spare Renamed", "is_configured": False}]
            cur.execute("select wl_sync_cameras(%s,%s,%s::jsonb)", (agent, KEY, json.dumps(resync)))
            cur.fetchone()
            step(cfg("2")[0] is True, "re-sync does NOT flip an operator's is_configured decision", str(cfg("2")))
            step(cfg("2")[1] == "Spare", "a configured camera's name is preserved on re-sync", str(cfg("2")))
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

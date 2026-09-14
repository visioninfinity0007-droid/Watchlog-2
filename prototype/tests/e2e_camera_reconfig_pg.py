#!/usr/bin/env python3
"""0.4.4 P1.2 — agent-authed Configure Cameras (wl_agent_set_camera_configured, 0101) on real PG.

Proves the post-install reconfigure RPC: the agent can flip a channel Monitor<->Ignore and rename
it (no reinstall), the change is authoritative (is_configured updated), and an unknown channel is
rejected. Rolled back — zero production writes.

    python prototype/tests/e2e_camera_reconfig_pg.py
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
    KEY = "reconfig-e2e-key"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('reconf-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'rc','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, KEY)).fetchone()[0]
            # channel starts unconfigured (as a fresh sync would leave it)
            cur.execute("select wl_sync_cameras(%s,%s,%s::jsonb)", (agent, KEY,
                        json.dumps([{"channel": "1", "name": "Reception", "is_configured": False}])))
            cur.fetchone()

            def cfg():
                return cur.execute("select is_configured, name from cameras where site_id=%s and channel='1'", (sid,)).fetchone()

            step(cfg()[0] is False, "channel starts Ignore/unconfigured", str(cfg()))

            r1 = cur.execute("select wl_agent_set_camera_configured(%s,%s,'1',true,'Reception Main Entrance')",
                             (agent, KEY)).fetchone()[0]
            step(r1.get("ok") and cfg()[0] is True, "Ignore -> Monitor flips is_configured=true", str(r1))
            step(cfg()[1] == "Reception Main Entrance", "rename applied", str(cfg()))

            r2 = cur.execute("select wl_agent_set_camera_configured(%s,%s,'1',false,null)", (agent, KEY)).fetchone()[0]
            step(r2.get("ok") and cfg()[0] is False, "Monitor -> Ignore flips is_configured=false", str(r2))

            r3 = cur.execute("select wl_agent_set_camera_configured(%s,%s,'77',true,null)", (agent, KEY)).fetchone()[0]
            step(r3.get("ok") is False, "unknown channel rejected", str(r3))
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

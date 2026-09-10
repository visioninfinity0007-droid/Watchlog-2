#!/usr/bin/env python3
"""Offline sync idempotency (item 7) — the server half of the resilience cycle.

The client half (spool: continue-while-offline, restart, at-least-once) is proven in
test_spool_offline.py. This proves the server half against live PG (rolled back): re-delivering
the SAME buffered batch to wl_ingest_events inserts nothing new (deduped), and a mixed
old+new batch inserts only the new — so an at-least-once client + this idempotent server
gives an exactly-once effect end to end.

    python prototype/tests/e2e_offline_sync_pg.py
"""
from __future__ import annotations

import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m: ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
import os
for k in ("SUPABASE_DB_HOST","SUPABASE_DB_PORT","SUPABASE_DB_USER","SUPABASE_DB_PASSWORD","SUPABASE_DB_NAME"):
    if os.environ.get(k): ENV[k] = os.environ[k]
import psycopg  # noqa: E402

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    KEY = "offline-sync-e2e-key"
    def ev(i):
        ts = f"2026-06-01T10:{i:02d}:00Z"
        return {"channel": "1", "event_type": "person", "device_event_id": f"OFF-{i}",
                "device_ts": ts, "agent_ts": ts, "payload": {}}
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('off-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'off','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Gate')",(tid,sid))
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",(tid,sid,KEY)).fetchone()[0]

            batch = [ev(i) for i in range(3)]
            r1 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",(agent,KEY,json.dumps(batch))).fetchone()[0]
            step(r1.get("inserted") == 3, "first delivery inserts all 3", str(r1))

            # cloud returns and the SAME buffered batch is re-delivered (at-least-once client)
            r2 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",(agent,KEY,json.dumps(batch))).fetchone()[0]
            step(r2.get("inserted") == 0 and r2.get("skipped") == 3, "re-delivery is idempotent (0 new, 3 skipped)", str(r2))

            mixed = [ev(1), ev(2), ev(3), ev(4)]        # 1,2 already stored; 3,4 new
            r3 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",(agent,KEY,json.dumps(mixed))).fetchone()[0]
            step(r3.get("inserted") == 2 and r3.get("skipped") == 2, "mixed batch inserts only the new", str(r3))

            total = cur.execute("select count(*) from events where site_id=%s",(sid,)).fetchone()[0]
            step(total == 5, "exactly 5 distinct events stored end to end", str(total))

            bad = None
            try:
                cur.execute("savepoint sp")
                cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",(agent,"wrong-key",json.dumps([ev(9)])))
                cur.fetchone(); bad = False
            except psycopg.Error:
                bad = True
            cur.execute("rollback to savepoint sp")
            step(bad, "a wrong agent key is rejected (auth gate holds)")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

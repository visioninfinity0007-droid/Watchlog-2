#!/usr/bin/env python3
"""Durable delivery outbox (0084, item 8) — provider idempotency + crash-edge reclaim.

Rolled-back txn over the full stack. Proves: enqueue is idempotent; claim is atomic; a stale
'sending' row (provider accepted, process crashed before persisting 'sent') is RECLAIMED with
the SAME idempotency key (so a key-honoring provider dedups = effective-once); a 'sent' row is
never reclaimed; a 'failed' row is retried.

    python prototype/tests/e2e_delivery_outbox_pg.py
"""
from __future__ import annotations

import re, sys
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

MIGS = [ROOT/"supabase"/"migrations"/m for m in
        ("0065_intelligence_pipeline.sql","0070_journeys.sql","0072_site_business_context.sql",
         "0073_entity_inference.sql","0076_report_calibration.sql","0080_journeys_v2_topology.sql",
         "0081_opening_closing_state_machine.sql","0082_entity_inference_v2.sql","0083_report_snapshots.sql",
         "0084_delivery_outbox.sql")]
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    D="2026-06-01"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS: cur.execute(p.read_text(encoding="utf-8"))
            tid = cur.execute("insert into tenants (name) values ('ob') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'ob','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            o = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'2','Office','office') returning id",(tid,sid)).fetchone()[0]
            cur.execute("insert into site_business_context (site_id,tenant_id,open_time,close_time) values (%s,%s,'08:00','18:00')",(sid,tid))
            for i,m0 in enumerate(range(0,60,15)):
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,o,f"{D} 09:{m0:02d}:00+05",f"{D} 09:{m0:02d}:00+05",f"ob-{i}"))
            rid = cur.execute("select wl_generate_daily_report(%s,%s::date)",(sid,D)).fetchone()[0]["report_id"]
            d1="923001112222"

            e1 = cur.execute("select wl_outbox_enqueue(%s,'whatsapp',%s)",(rid,d1)).fetchone()[0]
            e2 = cur.execute("select wl_outbox_enqueue(%s,'whatsapp',%s)",(rid,d1)).fetchone()[0]
            step(e1["id"]==e2["id"] and e1["idempotency_key"]==e2["idempotency_key"], "enqueue is idempotent (one row, stable key)")

            c1 = cur.execute("select wl_outbox_claim('whatsapp',50,300)").fetchone()[0]
            step(len(c1)==1 and c1[0]["attempts"]==1 and c1[0]["idempotency_key"]==e1["idempotency_key"], "atomic claim -> sending, attempt 1, key returned")
            key1 = c1[0]["idempotency_key"]

            # CRASH: provider accepted but we never marked. Row is stuck 'sending'; age it so it
            # looks like an attempt that crashed >5 min ago, then reclaim (crash-edge recovery).
            cur.execute("update delivery_outbox set updated_at = now() - interval '10 minutes' where status='sending'")
            c2 = cur.execute("select wl_outbox_claim('whatsapp',50,300)").fetchone()[0]
            step(len(c2)==1 and c2[0]["attempts"]==2 and c2[0]["idempotency_key"]==key1,
                 "crash edge: stale 'sending' reclaimed with the SAME idempotency key (provider dedups)")

            cur.execute("select wl_outbox_mark(%s,true,'provider-msg-1',null)",(c2[0]["id"],))
            c3 = cur.execute("select wl_outbox_claim('whatsapp',50,0)").fetchone()[0]
            step(c3==[], "a 'sent' row is never reclaimed")

            # failure -> retry
            d2="923003334444"
            f1 = cur.execute("select wl_outbox_enqueue(%s,'whatsapp',%s)",(rid,d2)).fetchone()[0]
            cf = cur.execute("select wl_outbox_claim('whatsapp',50,300)").fetchone()[0]
            cur.execute("select wl_outbox_mark(%s,false,null,'provider timeout')",(cf[0]["id"],))
            cr = cur.execute("select wl_outbox_claim('whatsapp',50,300)").fetchone()[0]
            step(len(cr)==1 and cr[0]["id"]==f1["id"] and cr[0]["attempts"]==2, "a 'failed' row is retried (re-claimed)")

            st = dict(cur.execute("select status,count(*) from delivery_outbox where site_id=%s group by 1",(sid,)).fetchall())
            step(st.get("sent")==1 and st.get("sending")==1, "outbox statuses persisted (1 sent, 1 in-flight retry)", str(st))
        finally:
            conn.rollback()
    ok=sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok==len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

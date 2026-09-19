#!/usr/bin/env python3
"""Daily intelligence v3 (0081) — the deployed function integrates the opening/closing state
machine, honest after-hours, topology journeys, and estimated behavioral classification.

Rolled-back txn over the full derived stack.
    python prototype/tests/e2e_daily_intelligence_v3_pg.py
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
         "0073_entity_inference.sql","0080_journeys_v2_topology.sql","0081_opening_closing_state_machine.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('v3') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'v3','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            g = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Gate','entrance') returning id",(tid,sid)).fetchone()[0]
            o = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'2','Office','office') returning id",(tid,sid)).fetchone()[0]
            cur.execute("""insert into site_business_context (site_id,tenant_id,open_time,close_time,entrance_camera_ids,internal_camera_ids)
                           values (%s,%s,'08:00','18:00',%s::uuid[],%s::uuid[])""",(sid,tid,[str(g)],[str(o)]))
            nn=[0]
            def ev(c,ts):
                nn[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,c,ts,ts,f"v3-{nn[0]}"))
            ev(g,f"{D} 08:55:00+05")
            for m0 in range(0,481,30):
                hh=9+(m0//60); mm=m0%60; ev(o,f"{D} {hh:02d}:{mm:02d}:00+05")

            r = cur.execute("select wl_daily_intelligence(%s,%s::date,true)",(sid,D)).fetchone()[0]
            step(r["schema"]=="daily_intelligence.v3", "schema v3", r["schema"])
            db=r["day_boundaries"]
            step(db.get("model")=="state_machine" and db.get("opening_at")=="09:00", "day_boundaries from the state machine")
            step(isinstance(r["after_hours"],dict) and r["after_hours"].get("verified") is True, "after_hours is the honest object (schedule known)")
            step(isinstance(r.get("people"),dict) and "summary" in r["people"], "people section present")
            step(any("estimated behavioral classifications" in h for h in r.get("honesty",[])), "honesty: estimated behavioral classification wording")
        finally:
            conn.rollback()
    ok=sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok==len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

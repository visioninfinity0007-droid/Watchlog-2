#!/usr/bin/env python3
"""Persisted report snapshot (0083, item 7) — generate once, freeze, immune to later changes.

Rolled-back txn over the full derived stack. Proves a report is generated once and preserved;
a later threshold/config change does NOT alter the frozen snapshot; force re-generation bumps
the revision; the PDF reference + delivery status attach to the snapshot.

    python prototype/tests/e2e_report_snapshot_pg.py
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

MIGS = [ROOT/"supabase"/"migrations"/m for m in
        ("0065_intelligence_pipeline.sql","0070_journeys.sql","0072_site_business_context.sql",
         "0073_entity_inference.sql","0076_report_calibration.sql","0080_journeys_v2_topology.sql",
         "0081_opening_closing_state_machine.sql","0082_entity_inference_v2.sql","0083_report_snapshots.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('snap') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'snap','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            o = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'2','Office','office') returning id",(tid,sid)).fetchone()[0]
            cur.execute("insert into site_business_context (site_id,tenant_id,open_time,close_time) values (%s,%s,'08:00','18:00')",(sid,tid))
            for i,m0 in enumerate(range(0,481,30)):
                hh=9+(m0//60); mm=m0%60
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,o,f"{D} {hh:02d}:{mm:02d}:00+05",f"{D} {hh:02d}:{mm:02d}:00+05",f"sn-{i}"))

            g1 = cur.execute("select wl_generate_daily_report(%s,%s::date)",(sid,D)).fetchone()[0]
            step(g1["frozen"] is False and g1["revision"]==1 and g1["payload"]["schema"]=="daily_intelligence.v3",
                 "first generation creates the snapshot (revision 1)")
            rid = g1["report_id"]

            g2 = cur.execute("select wl_generate_daily_report(%s,%s::date)",(sid,D)).fetchone()[0]
            step(g2["frozen"] is True and g2["report_id"]==rid and g2["revision"]==1, "second call returns the FROZEN snapshot (not regenerated)")

            # a later threshold change must NOT alter yesterday's frozen report
            cur.execute("insert into inference_config (site_id,version,config) values (%s,'site-tuned',%s::jsonb)",(sid,json.dumps({"margin":0.5})))
            g3 = cur.execute("select wl_generate_daily_report(%s,%s::date)",(sid,D)).fetchone()[0]
            step(g3["payload"]==g1["payload"], "a later config change does NOT change the frozen report payload")

            g4 = cur.execute("select wl_generate_daily_report(%s,%s::date,true)",(sid,D)).fetchone()[0]
            step(g4["frozen"] is False and g4["revision"]==2 and g4["report_id"]==rid, "force regeneration bumps revision, same report id")

            cur.execute("select wl_set_report_pdf(%s,%s,%s)",(rid,"deadbeef"*8,123456))
            cur.execute("select wl_set_report_delivery_status(%s,'delivered')",(rid,))
            snap = cur.execute("select wl_get_report_snapshot(%s)",(rid,)).fetchone()[0]
            step(snap["pdf_sha256"].startswith("deadbeef") and snap["pdf_bytes"]==123456, "PDF artifact reference attaches to the snapshot")
            step(snap["delivery_status"]=="delivered" and "inference" in snap["versions"], "delivery status + version stamps preserved")
        finally:
            conn.rollback()
    ok=sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok==len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

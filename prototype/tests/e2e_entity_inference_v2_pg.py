#!/usr/bin/env python3
"""Visitor/staff inference v2 (0082, item 4) — journey confidence + coverage + calibration exposed.

Rolled-back txn. A recurring topology-validated staff journey over 4 days classifies as
probable_regular_staff, and every row now exposes journey_confidence, monitoring_coverage,
calibration_state and the estimated_behavioral kind; confidence is dampened by the journey
confidence. Setting a per-site config override flips calibration_state to 'tuned'.

    python prototype/tests/e2e_entity_inference_v2_pg.py
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
         "0073_entity_inference.sql","0076_report_calibration.sql","0080_journeys_v2_topology.sql","0082_entity_inference_v2.sql")]
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS: cur.execute(p.read_text(encoding="utf-8"))
            tid = cur.execute("insert into tenants (name) values ('inf2') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'inf2','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cams={}
            for ch,nm,pu in [("1","Reception","reception"),("2","Admin","admin"),("4","Director","director_office")]:
                cams[nm]=cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,%s,%s,%s) returning id",(tid,sid,ch,nm,pu)).fetchone()[0]
            cur.execute("insert into site_business_context (site_id,tenant_id,management_camera_ids) values (%s,%s,%s::uuid[])",(sid,tid,[str(cams["Director"])]))
            for a,b in [("Reception","Admin"),("Admin","Director")]:
                cur.execute("insert into camera_topology (tenant_id,site_id,from_camera_id,to_camera_id,min_seconds,max_seconds) values (%s,%s,%s,%s,60,300)",(tid,sid,cams[a],cams[b]))
            n=[0]
            def ev(c,ts):
                n[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,c,ts,ts,f"i2-{n[0]}"))
            for d in ("01","02","03","04"):
                ev(cams["Reception"],f"2026-06-{d} 09:00:00+05"); ev(cams["Reception"],f"2026-06-{d} 09:00:10+05")
                ev(cams["Admin"],f"2026-06-{d} 09:02:00+05"); ev(cams["Admin"],f"2026-06-{d} 09:02:10+05")
                ev(cams["Director"],f"2026-06-{d} 09:04:00+05"); ev(cams["Director"],f"2026-06-{d} 09:04:10+05")
            for s in range(0,12,2): ev(cams["Reception"],f"2026-06-01 14:{s:02d}:00+05")   # a visitor

            lo,hi="2026-06-01 00:00+05","2026-06-05 00:00+05"
            def derive():
                for fn in ("wl_derive_activities","wl_derive_episodes","wl_derive_journeys"):
                    cur.execute(f"select {fn}(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi))
                cur.execute("select wl_derive_entity_inferences(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi))
            derive()

            r=cur.execute("select wl_site_entity_inferences(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            step("estimated behavioral" in r["method"], "method labelled estimated behavioral classification")
            step(r["calibration_state"]=="default_uncalibrated", "calibration_state exposed (uncalibrated)", r["calibration_state"])
            step(r["monitoring_coverage"] is not None, "monitoring coverage exposed")
            staff=[e for e in r["entities"] if e["classification"]=="probable_regular_staff"]
            step(len(staff)==4, "4 recurring topology journeys -> probable_regular_staff", str(len(staff)))
            f=staff[0]["factors"]
            step(f["classification_kind"]=="estimated_behavioral", "row kind = estimated_behavioral")
            step(abs(float(f["journey_confidence"])-0.85)<0.01, "journey confidence fed in", str(f.get("journey_confidence")))
            step(float(staff[0]["confidence"])<0.875, "confidence dampened by journey confidence (< undamped margin)", str(staff[0]["confidence"]))

            # tuning the site config flips calibration_state to 'tuned'
            cur.execute("insert into inference_config (site_id,version,config) values (%s,'site-tuned',%s::jsonb)",(sid,json.dumps({"margin":1.5})))
            derive()
            r2=cur.execute("select wl_site_entity_inferences(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            step(r2["calibration_state"]=="tuned", "site config override -> calibration_state 'tuned'", r2["calibration_state"])
        finally:
            conn.rollback()
    ok=sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok==len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

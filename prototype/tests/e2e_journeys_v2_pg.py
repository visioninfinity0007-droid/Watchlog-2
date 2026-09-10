#!/usr/bin/env python3
"""Journey Intelligence v2 (0080, item 3) — topology-aware, concurrency-separated, scored.

Rolled-back txn. Proves:
  * a topology-valid chain (Reception->Admin->Director) becomes ONE journey, confidence-scored;
  * an impossible transition (Director -> remote camera in 1 s) is REJECTED (temporal bound);
  * two simultaneous disjoint chains are NOT merged into one journey (concurrency separation);
  * a site with no topology falls back to timing-only, labelled lower-confidence;
  * re-derivation is stable (same identity, no churn).

    python prototype/tests/e2e_journeys_v2_pg.py
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
        ("0065_intelligence_pipeline.sql","0070_journeys.sql","0080_journeys_v2_topology.sql")]
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    D = "2026-06-01"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS: cur.execute(p.read_text(encoding="utf-8"))
            tid = cur.execute("insert into tenants (name) values ('jv2') returning id").fetchone()[0]
            def site(name): return cur.execute("insert into sites (tenant_id,name,timezone) values (%s,%s,'Asia/Karachi') returning id",(tid,name)).fetchone()[0]
            def cam(sid,ch,nm): return cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,%s,%s,'area') returning id",(tid,sid,ch,nm)).fetchone()[0]
            def edge(sid,a,b): cur.execute("insert into camera_topology (tenant_id,site_id,from_camera_id,to_camera_id,min_seconds,max_seconds) values (%s,%s,%s,%s,60,300)",(tid,sid,a,b))
            n=[0]
            def ev(sid,c,ts):
                n[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,c,ts,ts,f"jv2-{n[0]}"))
            def derive(sid):
                lo,hi=f"{D} 00:00+05",f"{D} 23:59+05"
                for fn in ("wl_derive_activities","wl_derive_episodes","wl_derive_journeys"):
                    cur.execute(f"select {fn}(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi))
                return cur.fetchone()[0]

            # --- Site 1: topology-valid chain + impossible (1s) rejection ---
            s1=site("topo"); rec=cam(s1,'1','Reception'); adm=cam(s1,'2','Admin'); dire=cam(s1,'4','Director'); rem=cam(s1,'9','Remote')
            edge(s1,rec,adm); edge(s1,adm,dire); edge(s1,dire,rem)   # Director->Remote edge exists but min 60s
            for t in ("09:00:00","09:00:10"): ev(s1,rec,f"{D} {t}+05")
            for t in ("09:02:00","09:02:10"): ev(s1,adm,f"{D} {t}+05")
            for t in ("09:04:00","09:04:10"): ev(s1,dire,f"{D} {t}+05")
            ev(s1,rem,f"{D} 09:04:11+05"); ev(s1,rem,f"{D} 09:04:15+05")   # 1s after Director -> below min 60
            derive(s1)
            j = cur.execute("select wl_site_journeys(%s,%s::timestamptz,%s::timestamptz)",(s1,f"{D} 00:00+05",f"{D} 23:59+05")).fetchone()[0]
            js = j["journeys"]
            step(len(js)==1 and js[0]["path"]==["Reception","Admin","Director"], "topology chain = one 3-hop journey, remote rejected", str([x["path"] for x in js]))
            step(js and js[0]["hops"]==3 and float(js[0]["confidence"])>=0.8 and js[0]["reasons"]["topology_used"] is True,
                 "journey scored with confidence + topology signals", str(js[0].get("confidence")))
            step(j["label"]=="Plausible movement journeys", "reporting language is 'plausible movement journeys' not 'unique visitors'")

            # --- Site 2: two simultaneous disjoint chains must NOT merge ---
            s2=site("concurrent"); r2=cam(s2,'1','Reception'); a2=cam(s2,'2','Admin'); g2=cam(s2,'3','Gate'); w2=cam(s2,'4','Warehouse')
            edge(s2,r2,a2); edge(s2,g2,w2)   # two disjoint edges, no cross edge
            for t in ("09:00:00","09:00:10"): ev(s2,r2,f"{D} {t}+05")
            for t in ("09:01:00","09:01:10"): ev(s2,g2,f"{D} {t}+05")   # concurrent second subject elsewhere
            for t in ("09:02:00","09:02:10"): ev(s2,a2,f"{D} {t}+05")
            for t in ("09:03:00","09:03:10"): ev(s2,w2,f"{D} {t}+05")
            derive(s2)
            j2 = cur.execute("select wl_site_journeys(%s,%s::timestamptz,%s::timestamptz)",(s2,f"{D} 00:00+05",f"{D} 23:59+05")).fetchone()[0]["journeys"]
            step(len(j2)==2 and all(x["hops"]==2 for x in j2), "two concurrent subjects -> two separate journeys, not one merged", str([x["path"] for x in j2]))

            # --- Site 3: no topology -> timing fallback, lower confidence, labelled ---
            s3=site("fallback"); r3=cam(s3,'1','Reception'); a3=cam(s3,'2','Admin'); d3=cam(s3,'4','Director')
            for t in ("09:00:00","09:00:10"): ev(s3,r3,f"{D} {t}+05")
            for t in ("09:02:00","09:02:10"): ev(s3,a3,f"{D} {t}+05")
            for t in ("09:04:00","09:04:10"): ev(s3,d3,f"{D} {t}+05")
            derive(s3)
            j3 = cur.execute("select wl_site_journeys(%s,%s::timestamptz,%s::timestamptz)",(s3,f"{D} 00:00+05",f"{D} 23:59+05")).fetchone()[0]["journeys"]
            step(len(j3)==1 and float(j3[0]["confidence"])<=0.5 and j3[0]["uncertainty"]=="high" and j3[0]["reasons"]["mode"]=="timing_fallback",
                 "no-topology site -> timing fallback, explicitly lower confidence", str(j3[0].get("confidence")))

            # --- stable identity across re-derivation ---
            before = cur.execute("select id from journeys where site_id=%s order by started_at",(s1,)).fetchall()
            derive(s1)
            after = cur.execute("select id from journeys where site_id=%s order by started_at",(s1,)).fetchall()
            step(before==after and len(after)==1, "re-derivation is stable (same journey id, no churn)")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

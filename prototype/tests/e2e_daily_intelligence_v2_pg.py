#!/usr/bin/env python3
"""Daily intelligence v2 (0074) — item 14 corrections + people section (item 1 folded in).

Rolled-back txn. Proves the claims the matrix must not hand-wave:
  * opening is the first SUSTAINED presence (09:00), NOT the 07:00 single-detection blip;
    closing is the last sustained presence, NOT the 20:00 blip.
  * after-hours honors the SITE's business hours (08:00-19:00) in the site timezone — generic.
  * restricted area comes from the site's configured restricted_purposes ({vault}), generic,
    not an "armory" literal.
  * a monitoring-coverage gap LOWERS opening/closing confidence and is disclosed.
  * the people (visitor/staff) section is present.

    python prototype/tests/e2e_daily_intelligence_v2_pg.py
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
         "0073_entity_inference.sql","0074_daily_intelligence_v2.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('v2-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'v2','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            rec = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Reception','reception') returning id",(tid,sid)).fetchone()[0]
            vault = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'5','Vault','vault') returning id",(tid,sid)).fetchone()[0]
            cur.execute("""insert into site_business_context (site_id,tenant_id,site_type,open_time,close_time,restricted_purposes)
                           values (%s,%s,'office','08:00','19:00',%s::text[])""",(sid,tid,['vault']))
            n=[0]
            def ev(cam, ts):
                n[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,cam,ts,ts,f"v2-{n[0]}"))
            ev(rec, f"{D} 07:00:00+05")                                  # blip (single) — must NOT open the day
            for s in range(0,25,5): ev(rec, f"{D} 09:{s:02d}:00+05")     # sustained 09:00-09:20 -> opening
            for s in range(0,35,5): ev(rec, f"{D} 17:{s:02d}:00+05")     # sustained 17:00-17:30
            for s in range(0,11,5): ev(vault, f"{D} 10:{s:02d}:00+05")   # vault daytime (restricted)
            ev(rec, f"{D} 20:00:00+05")                                  # blip (single) — must NOT close the day
            for s in range(0,11,5): ev(vault, f"{D} 22:{s:02d}:00+05")   # sustained after-hours vault -> closing
            # a monitoring-coverage gap 12:00-20:00 lowers boundary confidence
            cur.execute("""insert into agent_coverage_gaps (tenant_id, site_id, started_at, ended_at, cause)
                           values (%s,%s,%s::timestamptz,%s::timestamptz,'observation_gap')""",
                        (tid, sid, f"{D} 12:00+05", f"{D} 20:00+05"))

            r = cur.execute("select wl_daily_intelligence(%s,%s::date,true)",(sid,D)).fetchone()[0]
            step(r.get("schema") == "daily_intelligence.v2", "schema v2", r.get("schema"))
            b = r.get("day_boundaries") or {}
            step(b.get("opening_at") == "09:00", "opening = first SUSTAINED presence, not 07:00 blip", b.get("opening_at"))
            step(b.get("closing_at") == "22:10", "closing = last sustained presence, not 20:00 blip", b.get("closing_at"))
            step(float(b.get("confidence")) < 1.0 and b.get("note"), "coverage gap lowers boundary confidence + disclosed",
                 f"{b.get('confidence')}/{bool(b.get('note'))}")
            step(b.get("low_confidence") is True, "low_confidence flagged for the 8h gap", str(b.get("low_confidence")))
            bh = (r.get("meta") or {}).get("business_hours") or {}
            step(str(bh.get("open")).startswith("08:00"), "business hours from context, not hardcoded")
            step(r.get("after_hours_episodes",0) >= 2, "after-hours honors site schedule (07:00, 20:00, 22:00 outside 08-19)",
                 str(r.get("after_hours_episodes")))
            restr = r.get("restricted") or []
            step(any(x.get("purpose") == "vault" for x in restr), "restricted is generic (vault from config, not armory literal)")
            step(isinstance(r.get("people"), dict) and "summary" in r["people"], "people (visitor/staff) section present")
            step(any("identities" in h for h in (r.get("honesty") or [])), "honesty: visitor/staff are inferences not identities")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

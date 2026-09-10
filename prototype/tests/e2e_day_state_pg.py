#!/usr/bin/env python3
"""Opening/closing state machine + after-hours truth (0081, items 5+6).

Rolled-back txn. Proves the activity-session state machine:
  * a brief early cleaner visit does NOT count as opening — the office opens at the real session;
  * late isolated motion does not become opening/closing;
  * a monitoring gap lowers confidence;
  * no entrance mapping -> explicitly-labelled lower-confidence fallback;
  * a non-working day is flagged;
  * unknown business hours -> after-hours is NOT asserted (returns 'not verified').

    python prototype/tests/e2e_day_state_pg.py
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
        ("0065_intelligence_pipeline.sql","0072_site_business_context.sql","0081_opening_closing_state_machine.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('ds') returning id").fetchone()[0]
            n=[0]
            def site(nm): return cur.execute("insert into sites (tenant_id,name,timezone) values (%s,%s,'Asia/Karachi') returning id",(tid,nm)).fetchone()[0]
            def cam(sid,ch,nm): return cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,%s,%s,'area') returning id",(tid,sid,ch,nm)).fetchone()[0]
            def ev(sid,c,ts):
                n[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,c,ts,ts,f"ds-{n[0]}"))
            def burst(sid,c,day,h0,m0,mins,every=3):
                # a run of detections spanning `mins` minutes (so a session has real duration)
                t=m0
                for _ in range(0, mins+1, every):
                    hh=h0+(t//60); mm=t%60
                    ev(sid,c,f"{day} {hh:02d}:{mm:02d}:00+05"); t+=every
            def state(sid, day):
                lo,hi=f"{day} 00:00+05",f"{day} 23:59+05"
                cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi))
                cur.execute("select wl_derive_episodes(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi))
                return cur.execute("select wl_site_day_state(%s,%s::date)",(sid,day)).fetchone()[0]

            D="2026-06-01"   # Monday
            # Site A: cleaner (brief) then normal opening then late blip
            sa=site("A"); ga=cam(sa,'1','Gate'); oa=cam(sa,'2','Office')
            cur.execute("""insert into site_business_context (site_id,tenant_id,open_time,close_time,entrance_camera_ids,internal_camera_ids,quiet_period_minutes,min_activity,min_open_minutes)
                           values (%s,%s,'08:00','18:00',%s::uuid[],%s::uuid[],30,2,15)""",(sa,tid,[str(ga)],[str(oa)]))
            ev(sa,ga,f"{D} 06:30:00+05")                      # cleaner arrival
            burst(sa,oa,D,6,31,9)                             # cleaner brief internal 06:31-06:40 (9 min -> not sustained)
            ev(sa,ga,f"{D} 08:55:00+05")                      # real arrival
            burst(sa,oa,D,9,0,480,every=30)                   # office 09:00-17:00 (sustained)
            ev(sa,oa,f"{D} 20:00:00+05")                      # late isolated blip
            A=state(sa,D)
            step(A["opening_at"]=="09:00", "opening = real office session, NOT the 06:31 cleaner visit", A["opening_at"])
            step(A["closing_at"]=="17:00", "closing = end of the sustained session, not the 20:00 blip", A["closing_at"])
            step(A["model"]=="state_machine" and float(A["confidence"])>=0.85, "state-machine model, high confidence", str(A["confidence"]))

            # Site B: same but a big coverage gap -> confidence lowered
            sb=site("B"); gb=cam(sb,'1','Gate'); ob=cam(sb,'2','Office')
            cur.execute("""insert into site_business_context (site_id,tenant_id,open_time,close_time,entrance_camera_ids,internal_camera_ids)
                           values (%s,%s,'08:00','18:00',%s::uuid[],%s::uuid[])""",(sb,tid,[str(gb)],[str(ob)]))
            ev(sb,gb,f"{D} 08:55:00+05"); burst(sb,ob,D,9,0,480,every=30)
            cur.execute("insert into agent_coverage_gaps (tenant_id,site_id,started_at,ended_at,cause) values (%s,%s,%s::timestamptz,%s::timestamptz,'observation_gap')",
                        (tid,sb,f"{D} 06:00+05",f"{D} 16:00+05"))
            B=state(sb,D)
            step(float(B["confidence"])<float(A["confidence"]) and B["low_confidence"] is True, "monitoring gap lowers opening confidence", str(B["confidence"]))

            # Site C: business hours unknown -> after-hours not verified
            scc=site("C"); occ=cam(scc,'2','Office')
            cur.execute("insert into site_business_context (site_id,tenant_id) values (%s,%s)",(scc,tid))   # no hours, no entrance
            burst(scc,occ,D,9,0,60,every=15)
            C=state(scc,D)
            step(C["after_hours"]["verified"] is False and "incomplete" in (C["after_hours"]["reason"] or ""), "unknown hours -> after-hours NOT asserted")
            step(C["model"]=="fallback_sustained_presence" and any("entrance" in x for x in C["notes"]), "no entrance mapping -> labelled fallback model")

            # Site E: weekend / non-working day flagged
            SAT="2026-06-06"  # Saturday
            se=site("E"); ge=cam(se,'1','Gate'); oe=cam(se,'2','Office')
            cur.execute("""insert into site_business_context (site_id,tenant_id,open_time,close_time,working_days,entrance_camera_ids,internal_camera_ids)
                           values (%s,%s,'08:00','18:00','{1,2,3,4,5}'::int[],%s::uuid[],%s::uuid[])""",(se,tid,[str(ge)],[str(oe)]))
            ev(se,ge,f"{SAT} 08:55:00+05"); burst(se,oe,SAT,9,0,120,every=30)
            E=state(se,SAT)
            step(E["working_day"] is False and any("non-working" in x for x in E["notes"]), "non-working day flagged")
            step(E["after_hours"]["verified"] is True and E["after_hours"]["count"]>0, "weekend activity counts as after-hours (schedule known)")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Visitor / staff inference (0073, item 1) — multi-factor, honest, provenance-preserving.

Rolled-back txn. A 4-day window with:
  * a RECURRING staff journey (Reception->Admin->Director ~09:00 on 4 days) -> probable_regular_staff
    (recurrence + reaches-management + within-hours outweigh the short-walk visitor hint),
  * a short reception-ONLY visit (one day, 14:00) -> probable_visitor,
  * an ambiguous mid-length operations presence -> unclassified (no factor dominates),
  * an OVERLAPPING second visitor at Reception during the staff window -> a SEPARATE unit
    (never merged — we don't identity-track).
Asserts classifications, that ambiguity yields unclassified, provenance + config version, and
that overlapping units are distinct rows.

    python prototype/tests/e2e_entity_inference_pg.py
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
        ("0065_intelligence_pipeline.sql","0070_journeys.sql","0072_site_business_context.sql","0073_entity_inference.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('inf-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'inf','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cams = {}
            for ch,nm,pu in [("1","Reception","reception"),("2","Admin","admin"),
                             ("4","Director","director_office"),("3","Operations","operations")]:
                cams[nm] = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,%s,%s,%s) returning id",
                                       (tid,sid,ch,nm,pu)).fetchone()[0]
            cur.execute("""insert into site_business_context (site_id,tenant_id,site_type,open_time,close_time,
                           reception_camera_ids,management_camera_ids) values (%s,%s,'office','08:00','19:00',%s::uuid[],%s::uuid[])""",
                        (sid,tid,[str(cams["Reception"])],[str(cams["Director"])]))
            n = [0]
            def ev(cam, ts):
                n[0]+=1
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                            (tid,sid,cam,ts,ts,f"inf-{n[0]}"))
            # recurring staff journey on 4 days
            for d in ("01","02","03","04"):
                ev(cams["Reception"], f"2026-06-{d} 09:00:00+05")
                ev(cams["Admin"],     f"2026-06-{d} 09:03:00+05")
                ev(cams["Director"],  f"2026-06-{d} 09:06:00+05")
            # day 01 extras: short reception visit (14:00), ambiguous operations (12:00-12:40),
            # overlapping reception visitor during the staff window (09:15)
            for s in range(0, 12, 2): ev(cams["Reception"], f"2026-06-01 14:{0+s:02d}:00+05")
            for s in range(0, 45, 5): ev(cams["Operations"], f"2026-06-01 12:{0+s:02d}:00+05")
            for s in range(0, 12, 2): ev(cams["Reception"], f"2026-06-01 09:{15+s:02d}:00+05")

            lo, hi = "2026-06-01 00:00+05", "2026-06-05 00:00+05"
            for fn in ("wl_derive_activities","wl_derive_episodes","wl_derive_journeys"):
                cur.execute(f"select {fn}(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.execute("select wl_derive_entity_inferences(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            total = cur.fetchone()[0]

            cur.execute("select classification, count(*) from entity_inferences where site_id=%s group by 1 order by 1", (sid,))
            counts = dict(cur.fetchall())
            step(counts.get("probable_regular_staff",0) == 4, "4 recurring staff journeys -> probable_regular_staff", str(counts))
            step(counts.get("probable_visitor",0) >= 2, "short + overlapping reception visits -> probable_visitor", str(counts.get("probable_visitor")))
            step(counts.get("unclassified",0) >= 1, "ambiguous operations presence -> unclassified", str(counts.get("unclassified")))

            cur.execute("""select factors_json->>'reaches_management', factors_json->>'recurrence_days', config_version,
                           source_journey_id is not null, cardinality(source_episode_ids)
                           from entity_inferences where site_id=%s and classification='probable_regular_staff' limit 1""",(sid,))
            rm, rd, cv, hasj, epc = cur.fetchone()
            step(rm == "true" and rd == "4", "staff row: reaches_management + recurrence_days=4", f"{rm}/{rd}")
            step(cv == "inference-v1" and hasj and epc == 3, "staff row: config version + journey provenance (3 episodes)", f"{cv}/{hasj}/{epc}")

            # overlapping units on day 01 are distinct rows (not merged into one entity)
            cur.execute("""select count(*) from entity_inferences where site_id=%s
                           and (occurred_at at time zone 'Asia/Karachi')::date = '2026-06-01'""",(sid,))
            step(cur.fetchone()[0] >= 4, "overlapping same-day units are distinct rows (no identity merge)")

            # honest summary shape
            s = cur.execute("select wl_site_entity_inferences(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            step(s["summary"]["probable_regular_staff"] == 4 and "entities" in s, "summary + entities returned")
            step(total == sum(counts.values()), "all units classified", f"{total} rows")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

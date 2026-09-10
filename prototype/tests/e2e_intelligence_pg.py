#!/usr/bin/env python3
"""Intelligence pipeline integration (0065) — Detection -> Activity -> Episode -> Incident.

Deterministic, self-contained: builds a controlled synthetic scenario in a rolled-back
transaction (nothing persisted), applies 0065 in-txn (idempotent, so this runs against any
DB with the base schema — CI disposable PG or prod), and pins:

  * grouping / cooldown (gap-based episodes per camera),
  * per-site promotion policy (routine Reception stays Activity; restricted-area access and
    after-hours access promote to Incident) — no global hardcoding,
  * provenance all the way back (incident -> episode -> activity -> event),
  * idempotent re-derivation and no duplicate source ids.

    python prototype/tests/e2e_intelligence_pg.py     # exits 0 on success
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
import os
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
          "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

MIG = (ROOT / "supabase" / "migrations" / "0065_intelligence_pipeline.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    D = "2026-06-01"          # fixed date for determinism (a Monday)
    def ev(cur, tid, sid, cid, hhmm, i):
        ts = f"{D} {hhmm}:{i:02d}+05:00"
        cur.execute("""insert into events (tenant_id, site_id, camera_id, event_type, device_ts,
                       agent_ts, received_at, dedupe_key)
                       values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                    (tid, sid, cid, ts, ts, f"intel-e2e-{cid}-{hhmm}-{i}"))
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG)                                   # idempotent; self-contained
            cur.execute("insert into tenants (name) values ('intel-e2e') returning id")
            tid = cur.fetchone()[0]
            cur.execute("insert into sites (tenant_id, name, timezone) values (%s,'intel-e2e','Asia/Karachi') returning id", (tid,))
            sid = cur.fetchone()[0]
            cams = {}
            for ch, name, purpose in [("1", "Reception", "reception"), ("3", "Armory Gate", "armory")]:
                cur.execute("insert into cameras (tenant_id, site_id, channel, name, purpose) values (%s,%s,%s,%s,%s) returning id",
                            (tid, sid, ch, name, purpose))
                cams[ch] = cur.fetchone()[0]
            # Reception: 8 daytime detections (one long presence episode)
            for i in range(8):
                ev(cur, tid, sid, cams["1"], "10:0", i)
            # Armory Gate: 3 daytime (one episode) + 2 after-hours (a separate, >10min-gapped episode)
            for i in range(3):
                ev(cur, tid, sid, cams["3"], "10:1", i)
            for i in range(2):
                ev(cur, tid, sid, cams["3"], "22:0", i)
            # Per-site policy (restricted-area scoped; NOT global)
            for nm, mo, pil, ah, dw, to, sev in [
                ("armory access", "person", "%armory%", False, None, "restricted_area_access", "warning"),
                ("after-hours armory", "person", "%armory%", True, None, "after_hours_armory", "critical")]:
                cur.execute("""insert into incident_policies (tenant_id,site_id,name,match_object_class,
                               match_purpose_ilike,after_hours_only,min_dwell_seconds,promote_to,severity)
                               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (tid, sid, nm, mo, pil, ah, dw, to, sev))

            lo, hi = f"{D} 00:00+05", f"{D} 23:59+05"
            cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            a = cur.fetchone()[0]
            cur.execute("select wl_derive_episodes(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            e = cur.fetchone()[0]
            cur.execute("select wl_promote_incidents(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.fetchone()

            step(a == 13, "13 activities (1:1 to events)", str(a))
            step(e == 3, "3 episodes (reception 1 + armory day 1 + armory night 1)", str(e))
            cur.execute("select count(*) from intel_incidents ii join cameras c on c.id=ii.camera_id where c.name='Reception'")
            step(cur.fetchone()[0] == 0, "routine Reception stays Activity (no incident)")
            cur.execute("select count(*) from intel_incidents where incident_type='restricted_area_access'")
            step(cur.fetchone()[0] == 2, "both Armory episodes -> restricted_area_access")
            cur.execute("select count(*) from intel_incidents where incident_type='after_hours_armory' and severity='critical'")
            step(cur.fetchone()[0] == 1, "after-hours Armory -> critical incident")
            cur.execute("""select exists(select 1 from intel_incidents ii join episodes ep on ep.id=ii.source_id
                           join activities act on act.id=any(ep.source_activity_ids) join events evx on evx.id=act.source_event_id)""")
            step(cur.fetchone()[0], "provenance chain incident->episode->activity->event")
            cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            step(cur.fetchone()[0] == 0, "idempotent re-derive (0 new activities)")
            cur.execute("select bool_and(cardinality(source_activity_ids)=cardinality(array(select distinct unnest(source_activity_ids)))) from episodes")
            step(cur.fetchone()[0], "no duplicate source activity ids in episodes")
        finally:
            conn.rollback()                                    # nothing persisted
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

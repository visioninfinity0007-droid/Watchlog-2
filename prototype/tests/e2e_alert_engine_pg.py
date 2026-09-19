#!/usr/bin/env python3
"""Alert engine (0068) — immediate push for high-severity incidents.

Self-contained, non-persisting: applies 0065 + 0068 in a rolled-back transaction, seeds
incidents of mixed severity/status, and pins the guarantees:
  * severity threshold (info is never pushed at 'warning' level),
  * only OPEN incidents are claimed (an acknowledged one is not re-alerted),
  * dedup — a second claim on the same channel returns nothing,
  * channels are independent (whatsapp vs email each claim once),
  * mark-sent records the outcome.

    python prototype/tests/e2e_alert_engine_pg.py
"""
from __future__ import annotations

import re
import sys
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

MIG_PIPE = (ROOT / "supabase" / "migrations" / "0065_intelligence_pipeline.sql").read_text(encoding="utf-8")
MIG_ALERT = (ROOT / "supabase" / "migrations" / "0068_alert_engine.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG_PIPE)
            cur.execute(MIG_ALERT)
            cur.execute("insert into tenants (name) values ('alert-e2e') returning id")
            tid = cur.fetchone()[0]
            cur.execute("insert into sites (tenant_id, name, timezone) values (%s,'alert-e2e','Asia/Karachi') returning id", (tid,))
            sid = cur.fetchone()[0]
            cur.execute("insert into cameras (tenant_id, site_id, channel, name, purpose) values (%s,%s,'3','Armory','armory') returning id", (tid, sid))
            cam = cur.fetchone()[0]

            def incident(itype, sev, status, t):
                cur.execute("""insert into intel_incidents (tenant_id, site_id, camera_id, incident_type,
                               severity, occurred_at, source_kind, source_id, status)
                               values (%s,%s,%s,%s,%s,%s::timestamptz,'episode',gen_random_uuid(),%s) returning id""",
                            (tid, sid, cam, itype, sev, f"2026-06-01 {t}+05:00", status))
                return cur.fetchone()[0]

            crit = incident("after_hours_armory", "critical", "open", "22:00")
            warn = incident("restricted_area_access", "warning", "open", "10:00")
            info = incident("routine_presence", "info", "open", "11:00")
            ackd = incident("restricted_area_access", "warning", "acknowledged", "12:00")

            cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)", (sid,))
            claimed = cur.fetchone()[0]
            ids = [c["incident_id"] for c in claimed]
            step(len(claimed) == 2, "claims critical+warning (not info)", str(len(claimed)))
            step(str(crit) in ids and str(warn) in ids, "the two OPEN >=warning incidents are claimed")
            step(str(info) not in ids, "info incident is NOT pushed at warning threshold")
            step(str(ackd) not in ids, "acknowledged incident is NOT re-alerted")
            step(claimed[0]["severity"] == "critical", "critical returned first")

            cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)", (sid,))
            step(cur.fetchone()[0] == [], "second whatsapp claim is empty (dedup)")

            cur.execute("select wl_claim_alerts(%s,'email','warning',50)", (sid,))
            step(len(cur.fetchone()[0]) == 2, "email channel claims independently")

            cur.execute("select wl_mark_alert_sent(%s,'whatsapp',true,'{\"msg\":\"ok\"}')", (crit,))
            cur.execute("select status from alert_dispatches where incident_id=%s and channel='whatsapp'", (crit,))
            step(cur.fetchone()[0] == "sent", "mark_alert_sent records delivery")

            cur.execute("select wl_claim_alerts(%s,'sms','critical',50)", (sid,))
            crit_only = cur.fetchone()[0]
            step(len(crit_only) == 1 and crit_only[0]["severity"] == "critical",
                 "critical-only threshold claims just the critical")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Demo / Acceptance Preflight (0078, item 11) — real-state readiness, PASS/BLOCKED.

Rolled-back txn. A fully-set-up site reports ready=true with pass checks; a half-set-up site
reports ready=false with the blocking reasons. No fake data — every check reflects real rows.

    python prototype/tests/e2e_preflight_pg.py
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

MIGS = [ROOT/"supabase"/"migrations"/m for m in ("0065_intelligence_pipeline.sql","0078_demo_preflight.sql")]
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
            tid = cur.execute("insert into tenants (name) values ('pf-e2e') returning id").fetchone()[0]

            # READY site: online agent, known recorder, named camera, events today, health, policy, recipient
            sa = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'Ready HQ','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at,device_vendor,device_model)
                           values (%s,%s, encode(sha256('k1'::bytea),'hex'), now(), 'Dahua','DH-XVR1B08-I')""",(tid,sa))
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Reception','reception') returning id",(tid,sa)).fetchone()[0]
            cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                           values (%s,%s,%s,'person',now(),now(),now(),'pf-1')""",(tid,sa,cam))
            cur.execute("insert into camera_health (tenant_id,site_id,camera_id,health_state) values (%s,%s,%s,'operational')",(tid,sa,cam))
            cur.execute("""insert into incident_policies (tenant_id,site_id,name,promote_to,severity) values (%s,%s,'r','x','warning')""",(tid,sa))
            cur.execute("""insert into report_recipients (tenant_id,site_id,channel,destination,whatsapp_destination)
                           values (%s,%s,'whatsapp','923001112222','923001112222')""",(tid,sa))

            r = cur.execute("select wl_demo_preflight(%s)",(sa,)).fetchone()[0]
            checks = {c["key"]: c for c in r["checks"]}
            step(r["ready"] is True, "ready site -> ready=true", str(r.get("summary")))
            step(checks["agent_available"]["status"]=="pass", "agent_available pass")
            step(checks["recorder_identified"]["status"]=="pass" and "DH-XVR1B08-I" in checks["recorder_identified"]["detail"], "recorder identified from real agent report")
            step(checks["capability_profile"]["status"]=="pass", "capability profile resolved from KB")
            step(checks["events_flowing"]["status"]=="pass", "intelligence data available")
            step(checks["whatsapp_recipient"]["status"]=="pass", "whatsapp recipient configured")
            step(all("hard" in c for c in r["checks"]), "every check declares hard/soft")

            # BLOCKED site: agent enrolled but stale, no recorder model, no events
            sb = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'Blocked HQ','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at)
                           values (%s,%s, encode(sha256('k2'::bytea),'hex'), now() - interval '2 hours')""",(tid,sb))
            rb = cur.execute("select wl_demo_preflight(%s)",(sb,)).fetchone()[0]
            cb = {c["key"]: c for c in rb["checks"]}
            step(rb["ready"] is False, "blocked site -> ready=false")
            step(cb["agent_available"]["status"]=="blocked", "stale agent -> blocked with reason", cb["agent_available"]["detail"])
            step(cb["recorder_identified"]["status"]=="blocked", "no recorder model -> blocked")
            step(cb["events_flowing"]["status"]=="blocked", "no events -> blocked")
            step(cb["whatsapp_recipient"]["status"]=="warn", "missing recipient is a warn, not a hard block")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

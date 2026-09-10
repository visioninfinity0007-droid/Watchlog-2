#!/usr/bin/env python3
"""Critical-alert delivery integration (0075, item 9) — health transition -> incident -> claim.

Rolled-back txn. Deterministic transport chain (send is mocked via wl_mark_alert_sent). Proves:
  * a VideoLoss transition OPENS exactly one alert;
  * a persisting VideoLoss does NOT spam (no new transition -> no new incident -> no new claim);
  * a recovery sends exactly one recovery alert;
  * a critical camera's loss is critical severity;
  * a FAILED delivery is retried (re-claimable); a SENT one never re-sends;
  * duplicate triggers dedup.

    python prototype/tests/e2e_alert_delivery_pg.py
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
        ("0065_intelligence_pipeline.sql","0068_alert_engine.sql","0072_site_business_context.sql","0075_health_alert_delivery.sql")]
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    W = ("2026-06-01 22:00+05", "2026-06-01 23:00+05")
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS: cur.execute(p.read_text(encoding="utf-8"))
            tid = cur.execute("insert into tenants (name) values ('alrt-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'alrt','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Lobby','lobby') returning id",(tid,sid)).fetchone()[0]
            crit = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'5','Vault','vault') returning id",(tid,sid)).fetchone()[0]
            cur.execute("insert into site_business_context (site_id,tenant_id,critical_camera_ids) values (%s,%s,%s::uuid[])",(sid,tid,[str(crit)]))

            def trans(camera, frm, to, at):
                cur.execute("""insert into camera_health_transitions (tenant_id,site_id,camera_id,from_state,to_state,reason_code,at)
                               values (%s,%s,%s,%s::wl_health_state,%s::wl_health_state,'video_loss',%s::timestamptz)""",
                            (tid,sid,camera,frm,to,at))
            trans(cam, "operational","offline","2026-06-01 22:00+05")
            trans(crit,"operational","offline","2026-06-01 22:05+05")

            n = cur.execute("select wl_bridge_health_to_incidents(%s,%s::timestamptz,%s::timestamptz)",(sid,*W)).fetchone()[0]
            step(n == 2, "2 offline transitions -> 2 incidents", str(n))
            cur.execute("select wl_bridge_health_to_incidents(%s,%s::timestamptz,%s::timestamptz)",(sid,*W))  # idempotent
            tot = cur.execute("select count(*) from intel_incidents where site_id=%s",(sid,)).fetchone()[0]
            step(tot == 2, "re-bridge does not duplicate (no spam at incident layer)", str(tot))
            sev = dict(cur.execute("select incident_type||':'||severity, count(*) from intel_incidents where site_id=%s group by 1",(sid,)).fetchall())
            step(any(k.startswith("camera_offline:critical") for k in sev), "critical camera loss -> critical severity", str(sev))

            c1 = cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)",(sid,)).fetchone()[0]
            step(len(c1) == 2, "VideoLoss opens: both offline incidents claimed once", str(len(c1)))
            c2 = cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)",(sid,)).fetchone()[0]
            step(c2 == [], "persisting VideoLoss does NOT re-alert (no spam)")

            # recovery -> one recovery alert
            trans(cam, "offline","operational","2026-06-01 22:30+05")
            cur.execute("select wl_bridge_health_to_incidents(%s,%s::timestamptz,%s::timestamptz)",(sid,*W))
            rec = cur.execute("select wl_claim_alerts(%s,'whatsapp','info',50)",(sid,)).fetchone()[0]
            step(len(rec) == 1 and rec[0]["detail"]["incident_type"]=="camera_recovered", "recovery sends exactly one recovery alert")

            # mark one sent, one failed -> failed is retried, sent is not
            off = cur.execute("select id from intel_incidents where site_id=%s and incident_type='camera_offline' and severity='warning' limit 1",(sid,)).fetchone()[0]
            cur.execute("select wl_mark_alert_sent(%s,'whatsapp',true,'{}')",(off,))
            offc = cur.execute("select id from intel_incidents where site_id=%s and incident_type='camera_offline' and severity='critical' limit 1",(sid,)).fetchone()[0]
            cur.execute("select wl_mark_alert_sent(%s,'whatsapp',false,'{\"err\":\"timeout\"}')",(offc,))
            retry = cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)",(sid,)).fetchone()[0]
            step(len(retry) == 1 and str(retry[0]["incident_id"]) == str(offc), "FAILED delivery is retried; SENT is not")

            # duplicate trigger: bridge + claim again yields nothing new (all claimed/sent)
            cur.execute("select wl_bridge_health_to_incidents(%s,%s::timestamptz,%s::timestamptz)",(sid,*W))
            dup = cur.execute("select wl_claim_alerts(%s,'whatsapp','warning',50)",(sid,)).fetchone()[0]
            step(dup == [], "duplicate trigger dedups (nothing re-claimed)")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

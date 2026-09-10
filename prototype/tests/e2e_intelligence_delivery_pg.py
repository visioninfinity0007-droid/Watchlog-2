#!/usr/bin/env python3
"""Daily-intelligence delivery pipeline (item 8) — full path against live PG (rolled back).

Canonical dataset -> WhatsApp render -> delivery, with a mock transport. Proves: delivery +
report_deliveries persistence, idempotency (duplicate suppression), failure recording, retry of
a failed send (and NO re-send of a sent one), and the entitlement gate.

    python prototype/tests/e2e_intelligence_delivery_pg.py
"""
from __future__ import annotations

import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reporter"))
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m: ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
import os
for k in ("SUPABASE_DB_HOST","SUPABASE_DB_PORT","SUPABASE_DB_USER","SUPABASE_DB_PASSWORD","SUPABASE_DB_NAME"):
    if os.environ.get(k): ENV[k] = os.environ[k]
import psycopg  # noqa: E402
import intelligence_delivery as deliv  # noqa: E402

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
            tid = cur.execute("insert into tenants (name, subscription_status, trial_started_at, trial_days) "
                              "values ('deliv-e2e','active',now(),30) returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'deliv','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Reception','reception') returning id",(tid,sid)).fetchone()[0]
            for i in range(4):
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                            (tid,sid,cam,f"{D} 10:0{i}:00+05",f"{D} 10:0{i}:00+05",f"deliv-{i}"))
            d1 = "923001112222"; d2 = "923003334444"
            for d in (d1, d2):
                cur.execute("""insert into report_recipients (tenant_id,site_id,channel,destination,whatsapp_destination)
                               values (%s,%s,'whatsapp',%s,%s)""",(tid,sid,d,d))

            # first run: transport fails d2
            t1 = deliv.MockTransport(fail_dests=[d2])
            r1 = deliv.deliver_site_day(cur, sid, tid, D, transport=t1)
            step(r1["sent"] == 1 and r1["failed"] == 1, "first run: 1 sent, 1 failed", str({k:r1[k] for k in ('sent','failed','skipped')}))
            step(len(t1.sent) == 1 and t1.sent[0][0] == d1, "transport delivered to the good number only")
            step("*WatchLog" in r1["message"], "message rendered from canonical dataset")

            sent_rows = cur.execute("select destination,status from report_deliveries where site_id=%s order by destination",(sid,)).fetchall()
            step(("923001112222","sent") in [tuple(x) for x in sent_rows] and ("923003334444","failed") in [tuple(x) for x in sent_rows],
                 "report_deliveries persisted sent + failed", str([tuple(x) for x in sent_rows]))

            # second run: d1 already sent (dedup), d2 retried and now succeeds
            t2 = deliv.MockTransport()
            r2 = deliv.deliver_site_day(cur, sid, tid, D, transport=t2)
            step(r2["sent"] == 1 and r2["skipped"] == 1, "retry: d1 skipped (already sent), d2 retried+sent", str({k:r2[k] for k in ('sent','skipped','failed')}))
            step(len(t2.sent) == 1 and t2.sent[0][0] == d2, "only the previously-failed number was re-sent")

            # third run: both already sent -> full duplicate suppression, no transport calls
            t3 = deliv.MockTransport()
            r3 = deliv.deliver_site_day(cur, sid, tid, D, transport=t3)
            step(r3["sent"] == 0 and r3["skipped"] == 2 and len(t3.sent) == 0, "duplicate suppression: nothing re-sent")

            # entitlement gate: disable -> skipped_disabled, recorded
            cur.execute("update tenants set subscription_status='expired' where id=%s",(tid,))
            cur.execute("delete from report_deliveries where site_id=%s and destination=%s",(sid,d1))  # clear so we can observe the gate
            t4 = deliv.MockTransport()
            r4 = deliv.deliver_site_day(cur, sid, tid, D, transport=t4)
            step(r4["sent"] == 0 and any(o[1]=="skipped_disabled" for o in r4["outcomes"]) and len(t4.sent)==0,
                 "reporting disabled -> gated, nothing sent")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

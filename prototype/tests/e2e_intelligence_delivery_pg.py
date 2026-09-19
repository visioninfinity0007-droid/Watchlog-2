#!/usr/bin/env python3
"""Daily-intelligence delivery pipeline (items 1, 2, 8) — snapshot -> outbox -> provider.

Rolled-back txn over the full stack. Proves the production path: the report is a frozen snapshot,
a real PDF is generated and its reference saved to the snapshot, delivery goes through the durable
outbox, and the crash edge (provider accepted, process died before persisting) is recovered
effective-once via the idempotency key. Entitlement gates delivery.

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
         "0073_entity_inference.sql","0076_report_calibration.sql","0080_journeys_v2_topology.sql",
         "0081_opening_closing_state_machine.sql","0082_entity_inference_v2.sql","0083_report_snapshots.sql",
         "0084_delivery_outbox.sql")]
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    D="2026-06-01"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS: cur.execute(p.read_text(encoding="utf-8"))
            tid = cur.execute("insert into tenants (name,subscription_status,trial_started_at,trial_days) values ('dl','active',now(),30) returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'dl','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            o = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'2','Office','office') returning id",(tid,sid)).fetchone()[0]
            cur.execute("insert into site_business_context (site_id,tenant_id,open_time,close_time) values (%s,%s,'08:00','18:00')",(sid,tid))
            for i,m0 in enumerate(range(0,60,15)):
                cur.execute("""insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,received_at,dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",(tid,sid,o,f"{D} 09:{m0:02d}:00+05",f"{D} 09:{m0:02d}:00+05",f"dl-{i}"))
            d1="923001112222"
            cur.execute("insert into report_recipients (tenant_id,site_id,channel,destination,whatsapp_destination) values (%s,%s,'whatsapp',%s,%s)",(tid,sid,d1,d1))

            # enqueue: frozen snapshot + PDF saved + outbox row
            enq = deliv.enqueue_site_day(cur, sid, tid, D)
            step(enq["enqueued"]==1 and enq["pdf_saved"] is True, "enqueue: snapshot generated, PDF saved, recipient enqueued")
            snap = cur.execute("select wl_get_report_snapshot(%s)",(enq["report_id"],)).fetchone()[0]
            step(snap["pdf_sha256"] is not None and snap["pdf_bytes"]>800, "PDF artifact reference saved to the frozen snapshot")

            t = deliv.MockTransport()
            # simulate the crash edge: claim + provider-accept, but never mark (process dies)
            claimed = cur.execute("select wl_outbox_claim('whatsapp',50,300)").fetchone()[0]
            t.send(claimed[0]["destination"], "text", claimed[0]["idempotency_key"])   # provider accepted
            cur.execute("update delivery_outbox set updated_at = now() - interval '10 minutes' where status='sending'")
            drained = deliv.drain_outbox(cur, "whatsapp", t, stale_seconds=300)         # reclaim + re-send
            step(drained["sent"]==1 and len(t.sent)==1, "crash edge recovered EFFECTIVE-ONCE (provider dedup on key)", f"sends={len(t.sent)}")
            snap2 = cur.execute("select wl_get_report_snapshot(%s)",(enq["report_id"],)).fetchone()[0]
            step(snap2["delivery_status"]=="delivered", "snapshot delivery_status = delivered")

            # re-deliver: frozen snapshot, outbox already sent -> nothing re-sent
            r2 = deliv.deliver_site_day(cur, sid, tid, D, transport=deliv.MockTransport())
            step(r2["sent"]==0 and r2["attempted"]==0, "re-deliver sends nothing (frozen snapshot + sent outbox)")

            # entitlement gate
            cur.execute("update tenants set subscription_status='expired' where id=%s",(tid,))
            se = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'dl2','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            cur.execute("insert into report_recipients (tenant_id,site_id,channel,destination,whatsapp_destination) values (%s,%s,'whatsapp','923009998888','923009998888')",(tid,se))
            rg = deliv.deliver_site_day(cur, se, tid, D, transport=deliv.MockTransport())
            step(rg["skipped"] is True and rg["sent"]==0, "reporting disabled -> gated, nothing sent")
        finally:
            conn.rollback()
    ok=sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok==len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

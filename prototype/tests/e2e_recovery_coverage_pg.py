#!/usr/bin/env python3
"""Outage recovery ledger + three coverage classes (0098, directive §1/§2/§3).

Rolled-back txn. An outage window is UNVERIFIED; the Agent opens a recovery interval (pending);
the recovery worker claims it (in_progress) and completes it (recovered); coverage then reports
the window as RECOVERED — and LIVE + RECOVERED + UNVERIFIED sum to wall exactly, never blended.

    python prototype/tests/e2e_recovery_coverage_pg.py
"""
from __future__ import annotations

import json, re, sys
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

MIG = (ROOT/"supabase"/"migrations"/"0098_recovery_and_coverage_classes.sql").read_text(encoding="utf-8")
KEY = "recov-e2e-key"
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG)
            tid = cur.execute("insert into tenants (name) values ('recov') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'recov','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash, enrolled_at, last_seen_at)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex'), '2026-05-01', now()) returning id""",(tid,sid,KEY)).fetchone()[0]
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Gate') returning id",(tid,sid)).fetchone()[0]

            # 16h outage window, reported as an agent coverage gap -> UNVERIFIED
            gs, ge = "2026-06-01 17:00+05", "2026-06-02 09:00+05"
            lo, hi = "2026-06-01 00:00+05", "2026-06-02 10:00+05"
            cur.execute("""insert into agent_coverage_gaps (tenant_id,site_id,agent_id,started_at,ended_at,cause,source)
                           values (%s,%s,%s,%s::timestamptz,%s::timestamptz,'agent_restart','agent')""",(tid,sid,agent,gs,ge))

            c0 = cur.execute("select wl_site_coverage_report_classes(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            cl0 = c0["classes"]
            step(cl0["unverified_seconds"] > 50000 and cl0["recovered_seconds"] == 0,
                 "outage window is UNVERIFIED, nothing recovered yet", f"unv={cl0['unverified_seconds']}")

            o1 = cur.execute("select wl_open_recovery_interval(%s,%s,%s::timestamptz,%s::timestamptz,%s::uuid[])",(agent,KEY,gs,ge,[str(cam)])).fetchone()[0]
            step(o1["ok"] and o1["status"]=="pending", "agent opens recovery interval (pending)")
            rid = o1["id"]
            o2 = cur.execute("select wl_open_recovery_interval(%s,%s,%s::timestamptz,%s::timestamptz,%s::uuid[])",(agent,KEY,gs,ge,[str(cam)])).fetchone()[0]
            step(o2.get("duplicate") is True, "re-open is idempotent (duplicate)")

            c1 = cur.execute("select wl_site_coverage_report_classes(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            step(c1["classes"]["recovered_seconds"] == 0, "pending (not recovered) does not count as recovered coverage")

            claim = cur.execute("select wl_agent_claim_recovery(%s,%s,5,900)",(agent,KEY)).fetchone()[0]
            step(len(claim)==1 and str(claim[0]["id"])==str(rid), "worker claims the pending interval (in_progress)")

            cur.execute("select wl_complete_recovery(%s,%s,%s,'recovered',%s,%s::jsonb)",(agent,KEY,rid,42,json.dumps({"cursor":"done"})))
            c2 = cur.execute("select wl_site_coverage_report_classes(%s,%s::timestamptz,%s::timestamptz)",(sid,lo,hi)).fetchone()[0]
            cl2 = c2["classes"]
            step(cl2["recovered_seconds"] > 50000, "after recovery the window is RECOVERED", f"rec={cl2['recovered_seconds']}")
            step(cl2["unverified_seconds"] < 100, "unverified drops to ~0 after full recovery", f"unv={cl2['unverified_seconds']}")
            total = cl2["live_seconds"] + cl2["recovered_seconds"] + cl2["unverified_seconds"]
            step(abs(total - float(c2["wall_seconds"])) < 2, "LIVE + RECOVERED + UNVERIFIED == wall (never blended)", f"{total} vs {c2['wall_seconds']}")
            step(cl2["total_coverage_ratio"] > 0.99, "total coverage ~100% (live + recovered)", str(cl2["total_coverage_ratio"]))

            claim2 = cur.execute("select wl_agent_claim_recovery(%s,%s,5,900)",(agent,KEY)).fetchone()[0]
            step(claim2 == [], "a recovered interval is not re-claimed")
            rowc = cur.execute("select status, recovered_count from recovery_intervals where id=%s",(rid,)).fetchone()
            step(rowc[0]=="recovered" and rowc[1]==42, "recovery ledger persisted status + count + checkpoint")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

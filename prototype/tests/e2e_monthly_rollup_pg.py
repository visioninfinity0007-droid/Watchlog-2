#!/usr/bin/env python3
"""Monthly rollup (0069) — calendar-month aggregate over the intelligence layers.

Self-contained, rolled back: applies 0065 + 0069, seeds two days of a month, derives each
day (as the daily scheduler would), then aggregates the month and pins the totals, the
daily trend, the busiest day, after-hours load, restricted-access windows and coverage.

    python prototype/tests/e2e_monthly_rollup_pg.py
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
MIG_ROLL = (ROOT / "supabase" / "migrations" / "0069_monthly_rollup.sql").read_text(encoding="utf-8")

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
            cur.execute(MIG_ROLL)
            cur.execute("insert into tenants (name) values ('roll-e2e') returning id")
            tid = cur.fetchone()[0]
            cur.execute("insert into sites (tenant_id, name, timezone) values (%s,'roll-e2e','Asia/Karachi') returning id", (tid,))
            sid = cur.fetchone()[0]
            cams = {}
            for ch, name, purpose in [("1", "Reception", "reception"), ("3", "Armory Gate", "armory")]:
                cur.execute("insert into cameras (tenant_id, site_id, channel, name, purpose) values (%s,%s,%s,%s,%s) returning id",
                            (tid, sid, ch, name, purpose))
                cams[ch] = cur.fetchone()[0]

            def ev(cid, day, hhmm, i):
                ts = f"2026-06-{day} {hhmm}:{i:02d}+05:00"
                cur.execute("""insert into events (tenant_id, site_id, camera_id, event_type, device_ts,
                               agent_ts, received_at, dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                            (tid, sid, cid, ts, ts, f"roll-{cid}-{day}-{hhmm}-{i}"))

            # Day 01: reception 5 daytime + armory 2 after-hours (7 total, busiest day)
            for i in range(5):
                ev(cams["1"], "01", "10:0", i)
            for i in range(2):
                ev(cams["3"], "01", "22:0", i)
            # Day 02: reception 3 daytime + armory 3 daytime (6 total)
            for i in range(3):
                ev(cams["1"], "02", "10:0", i)
            for i in range(3):
                ev(cams["3"], "02", "10:1", i)

            for nm, mo, pil, ah, to, sev in [
                ("armory access", "person", "%armory%", False, "restricted_area_access", "warning"),
                ("after-hours armory", "person", "%armory%", True, "after_hours_armory", "critical")]:
                cur.execute("""insert into incident_policies (tenant_id,site_id,name,match_object_class,
                               match_purpose_ilike,after_hours_only,promote_to,severity)
                               values (%s,%s,%s,%s,%s,%s,%s,%s)""", (tid, sid, nm, mo, pil, ah, to, sev))

            for day in ("01", "02"):
                lo, hi = f"2026-06-{day} 00:00+05", f"2026-06-{day} 23:59+05"
                cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
                cur.execute("select wl_derive_episodes(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
                cur.execute("select wl_promote_incidents(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))

            cur.execute("select wl_monthly_rollup(%s, %s::date)", (sid, "2026-06-01"))
            r = cur.fetchone()[0]

            step(r.get("schema") == "monthly_rollup.v1", "schema tag")
            step((r.get("meta") or {}).get("month") == "2026-06", "meta month", (r.get("meta") or {}).get("month"))
            act = r.get("activity") or {}
            step(act.get("total_detections") == 13, "13 detections across the month", str(act.get("total_detections")))
            step(len(act.get("daily") or []) == 2, "daily trend has 2 active days", str(len(act.get("daily") or [])))
            step((act.get("busiest_day") or {}).get("detections") == 7, "busiest day = 7 detections (2026-06-01)",
                 str((act.get("busiest_day") or {}).get("detections")))
            step(act.get("after_hours_detections") == 2, "2 after-hours detections", str(act.get("after_hours_detections")))
            step(act.get("restricted_access_windows") == 2, "2 restricted-area windows", str(act.get("restricted_access_windows")))
            inc = r.get("incidents") or {}
            step(inc.get("total") == 3, "3 incidents in the month", str(inc.get("total")))
            step(inc.get("critical") == 1, "1 critical (after-hours armory)", str(inc.get("critical")))
            step(inc.get("warning") == 2, "2 warnings (restricted access, both days)", str(inc.get("warning")))
            step((r.get("coverage") or {}).get("coverage_ratio") is not None, "coverage composed for the month")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

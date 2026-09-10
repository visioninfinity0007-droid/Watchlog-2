#!/usr/bin/env python3
"""Canonical Daily Intelligence dataset (0067) — one dataset for Portal/PDF/WhatsApp.

Self-contained and non-persisting: applies 0065 (pipeline) + 0067 (dataset) inside a
rolled-back transaction, builds a controlled Al-Khalid-shaped day, then asserts the ONE
composed dataset that every report surface renders from:

  * schema + meta + office(0060) + coverage(0062) + access_windows/incidents(0065),
  * incidents promoted per the site's own policy (after-hours Armory -> critical),
  * idempotent refresh (calling twice does not double-count),
  * the honesty caveat is always present (detections != headcount),
  * the portal wrapper's tenant guard rejects an unauthenticated caller.

    python prototype/tests/e2e_daily_intelligence_pg.py     # exits 0 on success
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
MIG_DATA = (ROOT / "supabase" / "migrations" / "0067_daily_intelligence_dataset.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    D = "2026-06-01"          # a fixed PAST date (deterministic; full, not partial, day)

    def ev(cur, tid, sid, cid, hhmm, i):
        ts = f"{D} {hhmm}:{i:02d}+05:00"
        cur.execute("""insert into events (tenant_id, site_id, camera_id, event_type, device_ts,
                       agent_ts, received_at, dedupe_key)
                       values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                    (tid, sid, cid, ts, ts, f"di-e2e-{cid}-{hhmm}-{i}"))

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG_PIPE)
            cur.execute(MIG_DATA)
            cur.execute("insert into tenants (name) values ('di-e2e') returning id")
            tid = cur.fetchone()[0]
            cur.execute("insert into sites (tenant_id, name, timezone) values (%s,'di-e2e HQ','Asia/Karachi') returning id", (tid,))
            sid = cur.fetchone()[0]
            cams = {}
            for ch, name, purpose in [("1", "Reception", "reception"), ("3", "Armory Gate", "armory")]:
                cur.execute("insert into cameras (tenant_id, site_id, channel, name, purpose) values (%s,%s,%s,%s,%s) returning id",
                            (tid, sid, ch, name, purpose))
                cams[ch] = cur.fetchone()[0]
            for i in range(6):
                ev(cur, tid, sid, cams["1"], "10:0", i)     # reception daytime
            for i in range(3):
                ev(cur, tid, sid, cams["3"], "10:1", i)     # armory daytime
            for i in range(2):
                ev(cur, tid, sid, cams["3"], "22:0", i)     # armory after-hours
            for nm, mo, pil, ah, to, sev in [
                ("armory access", "person", "%armory%", False, "restricted_area_access", "warning"),
                ("after-hours armory", "person", "%armory%", True, "after_hours_armory", "critical")]:
                cur.execute("""insert into incident_policies (tenant_id,site_id,name,match_object_class,
                               match_purpose_ilike,after_hours_only,promote_to,severity)
                               values (%s,%s,%s,%s,%s,%s,%s,%s)""", (tid, sid, nm, mo, pil, ah, to, sev))

            cur.execute("select wl_daily_intelligence(%s, %s::date, true)", (sid, D))
            rpt = cur.fetchone()[0]

            step(rpt.get("schema") == "daily_intelligence.v1", "schema tag", rpt.get("schema"))
            meta = rpt.get("meta") or {}
            step(meta.get("site") == "di-e2e HQ" and meta.get("timezone") == "Asia/Karachi", "meta site/timezone")
            step(meta.get("partial_day") is False, "past date is a full (not partial) day", str(meta.get("partial_day")))
            step(isinstance(rpt.get("office"), dict) and "by_area" in rpt["office"], "composes office brief (0060)")
            step((rpt.get("coverage") or {}).get("coverage_ratio") is not None, "composes coverage report (0062)")
            step(isinstance(rpt.get("access_windows"), list) and len(rpt["access_windows"]) >= 3,
                 "access windows from episodes (0065)", str(len(rpt.get("access_windows") or [])))
            attn = rpt.get("attention") or {}
            step(attn.get("incidents_total") == 3, "3 incidents promoted", str(attn.get("incidents_total")))
            step(attn.get("critical") == 1, "after-hours Armory -> 1 critical", str(attn.get("critical")))
            step(attn.get("warning") == 2, "two restricted-area-access warnings", str(attn.get("warning")))
            inc = rpt.get("incidents") or []
            step(bool(inc) and inc[0]["severity"] == "critical", "incidents sorted, critical first")
            honesty = rpt.get("honesty") or []
            step(any("headcount" in h for h in honesty), "honesty caveat present (detections != headcount)")

            # Idempotent refresh: a second derive+read must not double the incidents.
            cur.execute("select wl_daily_intelligence(%s, %s::date, true)", (sid, D))
            rpt2 = cur.fetchone()[0]
            step((rpt2.get("attention") or {}).get("incidents_total") == 3, "idempotent refresh (still 3)")

            # Portal wrapper tenant guard: an unauthenticated caller (no JWT tenant) is rejected.
            cur.execute("savepoint sp_guard")
            cur.execute("select set_config('request.jwt.claims', '{}', true)")
            guarded = False
            try:
                cur.execute("select wl_my_daily_intelligence(%s, %s::date)", (sid, D))
                cur.fetchone()
            except psycopg.errors.Error:
                guarded = True
            cur.execute("rollback to savepoint sp_guard")
            step(guarded, "portal wrapper rejects unauthenticated caller (tenant guard)")
        finally:
            conn.rollback()

    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

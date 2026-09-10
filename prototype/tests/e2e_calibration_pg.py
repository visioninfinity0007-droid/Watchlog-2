#!/usr/bin/env python3
"""Report calibration tooling (0076, item 10) — reviewer feedback + config tuning as data.

Rolled-back txn + bootstrap harness. Proves reviewer feedback capture (with computed error),
the review list + calibration summary, tuning inference thresholds via a per-site config
override (no code edit), and tenant isolation.
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

MIGS = [ROOT/"supabase"/"migrations"/m for m in
        ("0072_site_business_context.sql","0073_entity_inference.sql","0076_report_calibration.sql")]
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
            def claims(uid): return json.dumps({"sub": str(uid), "role": "authenticated"})
            def as_ok(uid, sql, *p):
                cur.execute("savepoint sp"); cur.execute("select set_config('request.jwt.claims',%s,true)",(claims(uid),))
                cur.execute("set local role authenticated"); r = cur.execute(sql, p or None).fetchone()
                cur.execute("reset role"); cur.execute("release savepoint sp"); return r
            def as_raises(uid, sql, *p):
                cur.execute("savepoint sp"); cur.execute("select set_config('request.jwt.claims',%s,true)",(claims(uid),))
                cur.execute("set local role authenticated"); bad=False
                try: cur.execute(sql, p or None).fetchone()
                except psycopg.Error: bad=True
                cur.execute("rollback to savepoint sp"); return bad
            def bootstrap(email, co, site):
                uid = cur.execute("insert into auth.users (id,email) values (gen_random_uuid(),%s) returning id",(email,)).fetchone()[0]
                b = as_ok(uid,"select wl_bootstrap_tenant(%s,%s)",co,site)[0]; t=b["tenant_id"]
                sid = cur.execute("select id from sites where tenant_id=%s order by created_at limit 1",(t,)).fetchone()[0]
                return uid, t, sid

            ua, ta, sa = bootstrap("cal-a@watchlog.test","Cal A","A")
            ub, tb, sb = bootstrap("cal-b@watchlog.test","Cal B","B")

            r = as_ok(ua,"select wl_submit_report_review(%s,'2026-06-01'::date,'probable_staff','5','7','undercounted','inference-v1')",sa)[0]
            step(r.get("ok") and r.get("numeric_error") == 2, "review captured, numeric error computed", str(r.get("numeric_error")))
            as_ok(ua,"select wl_submit_report_review(%s,'2026-06-02'::date,'probable_staff','8','6',null,'inference-v1')",sa)
            as_ok(ua,"select wl_submit_report_review(%s,'2026-06-01'::date,'opening','09:12','09:00','12 min early',null)",sa)

            revs = as_ok(ua,"select wl_report_reviews(%s,365)",sa)[0]
            step(isinstance(revs,list) and len(revs) == 3, "reviews listed", str(len(revs)))
            summ = as_ok(ua,"select wl_review_summary(%s)",sa)[0]
            step("probable_staff" in summ and summ["probable_staff"]["reviews"] == 2, "calibration summary per metric", str(summ.get("probable_staff")))
            step(float(summ["probable_staff"]["mean_abs_error"]) == 2.0, "mean abs error (|+2|,|-2|)=2.0", str(summ["probable_staff"]["mean_abs_error"]))
            step(float(summ["probable_staff"]["bias"]) == 0.0, "bias (+2,-2)=0.0 net", str(summ["probable_staff"]["bias"]))

            step(as_raises(ua,"select wl_submit_report_review(%s,'2026-06-01'::date,'opening','1','2',null,null)",sb),
                 "cannot review another tenant's site")
            step(as_raises(ua,"select wl_report_reviews(%s,90)",sb), "cannot read another tenant's reviews")
            step(as_raises(ua,"select wl_submit_report_review(%s,'2026-06-01'::date,'bogus_metric','1','2',null,null)",sa),
                 "invalid metric rejected")

            tune = as_ok(ua,"select wl_upsert_inference_config(%s,%s::jsonb,'site-tuned')",sa,
                         json.dumps({"long_stay_seconds":3600,"margin":1.5,"weights":{"reaches_management":3.0}}))[0]
            step(tune.get("ok"), "tuned inference config as a per-site override (no code edit)")
            got = cur.execute("select config->>'long_stay_seconds' from inference_config where site_id=%s and version='site-tuned'",(sa,)).fetchone()
            step(got and got[0] == "3600", "override persisted for the derivation to read next run", str(got))
            step(as_raises(ua,"select wl_upsert_inference_config(%s,%s::jsonb,'x')",sb,json.dumps({"a":1})),
                 "cannot tune another tenant's config")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Site business context + onboarding foundation (0072) — item 3 backend.

Rolled-back txn against live PG. Proves the persisted business-context capture, the composed
onboarding checklist (derived technical steps + persisted business steps), resume support, and
the tenant/site guards (foreign site, cross-site camera ids, invalid type).

    python prototype/tests/e2e_onboarding_pg.py
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

MIG = (ROOT / "supabase" / "migrations" / "0072_site_business_context.sql").read_text(encoding="utf-8")
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

            ua, ta, sa = bootstrap("onb-a@watchlog.test","Onb A","Site A")
            ub, tb, sb = bootstrap("onb-b@watchlog.test","Onb B","Site B")
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'5','Armory','armory') returning id",(ta,sa)).fetchone()[0]
            camb = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Gate','entrance') returning id",(tb,sb)).fetchone()[0]

            # initial context = not captured, defaults present
            ctx0 = as_ok(ua,"select wl_my_site_context(%s)",sa)[0]
            step(ctx0["captured"] is False and "restricted_purposes" in ctx0, "defaults returned before capture")

            up = as_ok(ua,"""select wl_upsert_site_context(%s,'office','09:00'::time,'18:00'::time,false,
                          array[1,2,3,4,5],%s::uuid[],null,null,%s::uuid[],null,null,null)""",
                       sa, [str(cam)], [str(cam)])[0]
            step(up.get("ok") is True, "captured business context on own site")
            ctx = as_ok(ua,"select wl_my_site_context(%s)",sa)[0]
            step(ctx["captured"] is True and ctx["site_type"]=="office" and str(ctx["open_time"]).startswith("09:00"),
                 "context reads back", ctx["site_type"])

            step(as_raises(ua,"select wl_upsert_site_context(%s,'office',null,null,null,null,null,null,null,null,null,null,null)",sb),
                 "cannot write context on another tenant's site")
            step(as_raises(ua,"select wl_my_site_context(%s)",sb), "cannot read another tenant's context")
            step(as_raises(ua,"select wl_upsert_site_context(%s,'not_a_type',null,null,null,null,null,null,null,null,null,null,null)",sa),
                 "invalid site_type rejected")
            step(as_raises(ua,"select wl_upsert_site_context(%s,'office',null,null,null,null,%s::uuid[],null,null,null,null,null,null)",sa,[str(camb)]),
                 "cross-site camera id rejected")

            oc = as_ok(ua,"select wl_onboarding_status(%s)",sa)[0]
            keys = {s["key"]: s["done"] for s in oc["steps"]}
            step(keys.get("business_context") is True and keys.get("connect_agent") is False,
                 "checklist composes derived + business (context done, no agent)")
            as_ok(ua,"select wl_onboarding_advance(%s,'cameras_mapped',true)",sa)
            oc2 = as_ok(ua,"select wl_onboarding_status(%s)",sa)[0]
            step({s["key"]:s["done"] for s in oc2["steps"]}.get("map_cameras") is True, "onboarding step persists (resume)")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

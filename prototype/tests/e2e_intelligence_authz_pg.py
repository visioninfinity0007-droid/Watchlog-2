#!/usr/bin/env python3
"""Role / tenant-isolation for the new intelligence RPC surface (items 24 + 21 guards).

Self-contained, rolled back (nothing persists to the live DB): bootstraps two real tenants
via wl_bootstrap_tenant and, acting as each tenant's authenticated user, proves:

  * a tenant reads its OWN site's daily/monthly/journeys/policies, and
  * can NEVER target another tenant's site or policy (every cross-tenant call raises),
  * an unauthenticated caller is rejected,
  * the raw service_role builders are NOT executable by 'authenticated' or 'anon'.

    python prototype/tests/e2e_intelligence_authz_pg.py
"""
from __future__ import annotations

import json
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

MIGS = [ROOT / "supabase" / "migrations" / m for m in (
    "0065_intelligence_pipeline.sql", "0067_daily_intelligence_dataset.sql",
    "0069_monthly_rollup.sql", "0070_journeys.sql", "0071_custom_analytics_authoring.sql")]

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    LO, HI = "2026-06-01 00:00+05", "2026-06-01 23:59+05"

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            for p in MIGS:
                cur.execute(p.read_text(encoding="utf-8"))

            def claims(uid):
                return json.dumps({"sub": str(uid), "role": "authenticated"})

            def as_user_ok(uid, sql, *p):
                cur.execute("savepoint sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                row = cur.execute(sql, p or None).fetchone()
                cur.execute("reset role")
                cur.execute("release savepoint sp")
                return row

            def as_user_raises(uid, sql, *p):
                cur.execute("savepoint sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                raised = False
                try:
                    cur.execute(sql, p or None).fetchone()
                except psycopg.Error:
                    raised = True
                cur.execute("rollback to savepoint sp")
                return raised

            def bootstrap(email, company, site_name):
                uid = cur.execute("insert into auth.users (id, email) values (gen_random_uuid(), %s) returning id", (email,)).fetchone()[0]
                boot = as_user_ok(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                tenant = boot["tenant_id"]
                cur.execute("select id from sites where tenant_id=%s order by created_at limit 1", (tenant,))
                site = cur.fetchone()[0]
                return uid, tenant, site

            ua, ta, sa = bootstrap("authz-a@watchlog.test", "Tenant A", "Site A")
            ub, tb, sb = bootstrap("authz-b@watchlog.test", "Tenant B", "Site B")
            step(ta != tb and sa != sb, "two independent tenants bootstrapped")

            own = as_user_ok(ua, "select wl_my_daily_intelligence(%s, %s::date)", sa, "2026-06-01")[0]
            step((own or {}).get("schema") == "daily_intelligence.v1", "tenant A reads its OWN daily dataset")

            step(as_user_raises(ua, "select wl_my_daily_intelligence(%s, %s::date)", sb, "2026-06-01"),
                 "tenant A CANNOT read tenant B's daily dataset")
            step(as_user_raises(ua, "select wl_my_monthly_rollup(%s, %s::date)", sb, "2026-06-01"),
                 "tenant A CANNOT read tenant B's monthly rollup")
            step(as_user_raises(ua, "select wl_my_site_journeys(%s,%s::timestamptz,%s::timestamptz)", sb, LO, HI),
                 "tenant A CANNOT read tenant B's journeys")

            pol = as_user_ok(ua, """select wl_upsert_incident_policy(%s,'armory after hours','after_hours_armory',
                             'critical',null,'%%armory%%',null,true,null,true,null)""", sa)[0]
            step(pol.get("ok") is True, "tenant A authors an incident policy on its OWN site")
            lst = as_user_ok(ua, "select wl_my_incident_policies(%s)", sa)[0]
            step(isinstance(lst, list) and len(lst) == 1, "tenant A lists its policy", str(len(lst or [])))

            step(as_user_raises(ua, """select wl_upsert_incident_policy(%s,'x','y','warning',
                             null,null,null,false,null,true,null)""", sb),
                 "tenant A CANNOT author a policy on tenant B's site")
            step(as_user_raises(ub, "select wl_my_incident_policies(%s)", sa),
                 "tenant B CANNOT list tenant A's policies")
            step(as_user_raises(ub, "select wl_delete_incident_policy(%s)", lst[0]["id"]),
                 "tenant B CANNOT delete tenant A's policy")
            step(as_user_ok(ua, "select wl_delete_incident_policy(%s)", lst[0]["id"])[0].get("deleted") is True,
                 "tenant A deletes its own policy")

            # Unauthenticated: empty claims -> wl_my_tenant() null -> rejected.
            cur.execute("savepoint spu")
            cur.execute("select set_config('request.jwt.claims', '{}', true)")
            cur.execute("set local role authenticated")
            unauth = False
            try:
                cur.execute("select wl_my_daily_intelligence(%s, %s::date)", (sa, "2026-06-01")).fetchone()
            except psycopg.Error:
                unauth = True
            cur.execute("rollback to savepoint spu")
            step(unauth, "unauthenticated caller is rejected")

            # Structural: raw builders locked to service_role, never authenticated/anon.
            def priv(role, sig):
                cur.execute("select has_function_privilege(%s, %s, 'execute')", (role, sig))
                return cur.fetchone()[0]
            step(not priv("authenticated", "public.wl_daily_intelligence(uuid,date,boolean)")
                 and not priv("authenticated", "public.wl_monthly_rollup(uuid,date)")
                 and not priv("authenticated", "public.wl_site_journeys(uuid,timestamptz,timestamptz)"),
                 "raw builders NOT executable by 'authenticated'")
            step(not priv("anon", "public.wl_my_daily_intelligence(uuid,date)")
                 and not priv("anon", "public.wl_upsert_incident_policy(uuid,text,text,text,text,text,text,boolean,numeric,boolean,uuid)"),
                 "portal wrappers NOT executable by 'anon'")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Site-neutral period facts (0143): real execution + tenant isolation on the disposable Postgres.

Self-contained and rolled back. Proves:
  * wl_site_day_facts reads only site-neutral keys (an 'office' brief in the input changes nothing),
  * wl_site_period returns site-period-v1 for warehouse/factory/retail/office and never office-period-v1,
  * restaurant and unprofiled site types are not routed through it (enabled:false + reason),
  * wl_office_period is unchanged: still office-only,
  * each authenticated tenant can query its own site and never the other tenant's; anon and an
    unauthenticated non-service caller are rejected,
  * service_role is refused EXECUTE on wl_site_period and the helpers (no server caller needs it), while
    the existing privileged wl_assert_my_site service_role contract (0122) is left exactly as it is,
  * the exact post-migration EXECUTE ACL (pg_proc.proacl): wl_site_period -> owner + authenticated only;
    internal helpers -> owner only.

    python prototype/tests/e2e_site_period_pg.py
"""
from __future__ import annotations

import json
import os
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
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
          "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

MIG = ROOT / "supabase" / "migrations" / "0143_site_period_facts.sql"

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


DAY = {
    "coverage": {"coverage_ratio": 0.9, "classes": {"live_seconds": 77760, "recovered_seconds": 0, "unverified_seconds": 8640}},
    "attention": {"incidents_total": 2, "critical": 1, "warning": 1},
    "after_hours": {"count": 3, "verified": True},
    "day_boundaries": {"opening_at": "08:05", "closing_at": "18:10", "confidence": "high"},
    "meta": {"partial_day": False},
    "access_windows": [
        {"camera": "Dock 1", "purpose": "loading_dock", "object_class": "person", "start": "08:10", "end": "09:00", "detections": 7},
        {"camera": "Dock 1", "purpose": "loading_dock", "object_class": "vehicle", "start": "08:20", "end": "08:50", "detections": 3},
        {"camera": "Gate", "purpose": "gate", "object_class": "person", "start": "14:00", "end": "14:05", "detections": 2},
        {"camera": "Yard", "purpose": "", "object_class": "person", "start": "15:30", "end": "15:31", "detections": 1},
    ],
}


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG.read_text(encoding="utf-8"))

            def claims(uid):
                return json.dumps({"sub": str(uid), "role": "authenticated"})

            def as_ok(uid, sql, *p):
                cur.execute("savepoint sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                row = cur.execute(sql, p or None).fetchone()
                cur.execute("reset role")
                cur.execute("release savepoint sp")
                return row

            def as_raises(uid, sql, *p):
                cur.execute("savepoint sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid) if uid else "{}",))
                cur.execute("set local role authenticated")
                raised = False
                try:
                    cur.execute(sql, p or None).fetchone()
                except psycopg.Error:
                    raised = True
                cur.execute("rollback to savepoint sp")
                return raised

            # --- pure day facts -------------------------------------------------------------
            f = cur.execute("select wl_site_day_facts(%s::jsonb)", (json.dumps(DAY),)).fetchone()[0]
            step(f["activity_episodes"] == 3 and f["vehicle_episodes"] == 1 and f["activity_detections"] == 10,
                 "day facts count person and vehicle episodes separately", json.dumps({k: f[k] for k in ("activity_episodes", "vehicle_episodes", "activity_detections")}))
            step(f["first_observed"] == "08:10" and f["last_observed"] == "15:31", "first/last observed from governed episodes")
            purposes = {p["purpose"]: p for p in f["by_purpose"]}
            step(set(purposes) == {"loading_dock", "gate", "unassigned"} and purposes["loading_dock"]["vehicle_episodes"] == 1,
                 "facts grouped by configured purpose; blank purpose stays 'unassigned'")
            step(f["coverage_classes"]["unverified_seconds"] == 8640 and f["after_hours_verified"] is True,
                 "coverage classes and verified after-hours carried through")
            with_office = dict(DAY, office={"coverage": {"person_events": 999}, "peak_hour": {"hour": 3, "count": 999}})
            f2 = cur.execute("select wl_site_day_facts(%s::jsonb)", (json.dumps(with_office),)).fetchone()[0]
            step(f2 == f, "an office brief in the input changes nothing (office semantics never read)")
            empty = cur.execute("select wl_site_day_facts('{}'::jsonb)").fetchone()[0]
            step(empty["activity_episodes"] == 0 and empty["coverage_ratio"] == 0 and empty["first_observed"] is None,
                 "no data -> zero counts with unknown first observation, not invented values")
            summ = cur.execute("select wl_site_period_summary(%s::jsonb)", (json.dumps([f, empty]),)).fetchone()[0]
            step(summ["days"] == 2 and summ["observed_days"] == 1 and summ["activity_episodes"] == 3,
                 "summary counts an unobserved day as a day, not as an observed zero")
            cc = summ["coverage_classes"]
            step(float(cc["live_seconds"]) == 77760 and float(cc["recovered_seconds"]) == 0 and float(cc["unverified_seconds"]) == 8640,
                 "LIVE / RECOVERED / UNVERIFIED stay separate sums at period level", json.dumps(cc))

            # --- tenants ---------------------------------------------------------------------
            def bootstrap(email, company, site_name, site_type):
                uid = cur.execute("insert into auth.users (id, email) values (gen_random_uuid(), %s) returning id", (email,)).fetchone()[0]
                boot = as_ok(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute("select id from sites where tenant_id=%s order by created_at limit 1", (boot["tenant_id"],)).fetchone()[0]
                up = as_ok(uid, """select wl_upsert_site_context(%s,%s,'08:00'::time,'18:00'::time,false,
                              array[1,2,3,4,5,6],null,null,null,null,null,null,null)""", site, site_type)[0]
                return uid, site, up

            ua, sa, upa = bootstrap("period-a@watchlog.test", "Tenant A", "Warehouse A", "warehouse")
            ub, sb, upb = bootstrap("period-b@watchlog.test", "Tenant B", "Store B", "retail")
            uc, sc, _ = bootstrap("period-c@watchlog.test", "Tenant C", "Restaurant C", "restaurant")
            ud, sd, _ = bootstrap("period-d@watchlog.test", "Tenant D", "Clinic D", "clinic")
            step(upa.get("ok") is True and upb.get("ok") is True, "warehouse and retail business contexts captured through the governed upsert")

            pa = as_ok(ua, "select wl_site_period(%s,7,true)", sa)[0]
            step(pa.get("enabled") is True and pa.get("schema") == "site-period-v1" and pa.get("site_type") == "warehouse",
                 "warehouse gets site-period-v1", f"{pa.get('schema')}/{pa.get('site_type')}")
            step(len(pa.get("daily") or []) == 7 and "comparison" in pa and "office" not in json.dumps(pa["measurement_notes"]).lower(),
                 "7 governed days with comparison facts and site-neutral notes")
            step(pa["summary"]["observed_days"] == 0 and pa["summary"]["activity_episodes"] == 0,
                 "a site without monitoring shows unobserved days, not activity")
            step(as_ok(ua, "select wl_office_period(%s,7,true)", sa)[0].get("enabled") is False,
                 "wl_office_period is unchanged: warehouse still not routed through office semantics")

            pb = as_ok(ub, "select wl_site_period(%s,7,true)", sb)[0]
            step(pb.get("enabled") is True and pb.get("schema") == "site-period-v1" and pb.get("site_type") == "retail",
                 "authenticated tenant B CAN query its own (retail) site", f"{pb.get('schema')}/{pb.get('site_type')}")
            pc = as_ok(uc, "select wl_site_period(%s,7,true)", sc)[0]
            step(pc.get("enabled") is False and pc.get("reason") == "restaurant_period", "restaurant keeps its own governed period")
            pd = as_ok(ud, "select wl_site_period(%s,7,true)", sd)[0]
            step(pd.get("enabled") is False and pd.get("reason") == "site_type_not_profiled", "unprofiled site type is not interpreted")

            step(as_raises(ua, "select wl_site_period(%s,7,true)", sb), "tenant A CANNOT read tenant B's period facts")
            step(as_raises(ub, "select wl_site_period(%s,7,true)", sa), "tenant B CANNOT read tenant A's period facts")
            step(as_raises(uc, "select wl_site_period(%s,7,true)", sa), "tenant C CANNOT read tenant A's period facts")
            step(as_raises(None, "select wl_site_period(%s,7,true)", sa), "unauthenticated non-service caller is rejected")

            # Role-level execution (not just static SQL): anon is refused by the ACL itself.
            def as_role_raises(role, sql, *p, jwt="{}"):
                cur.execute("savepoint sr")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (jwt,))
                cur.execute(f"set local role {role}")
                raised, msg = False, ""
                try:
                    cur.execute(sql, p or None).fetchone()
                except psycopg.Error as e:
                    raised, msg = True, str(e).splitlines()[0]
                cur.execute("rollback to savepoint sr")
                return raised, msg
            raised, msg = as_role_raises("anon", "select wl_site_period(%s,7,true)", sa)
            step(raised and "permission denied" in msg.lower(), "anon is refused EXECUTE on wl_site_period", msg)
            for helper in ("select wl_site_day_facts('{}'::jsonb)", "select wl_site_period_summary('[]'::jsonb)"):
                for role in ("anon", "authenticated", "service_role"):
                    raised, msg = as_role_raises(role, helper)
                    step(raised and "permission denied" in msg.lower(), f"{role} is refused EXECUTE on internal helper", helper.split("(")[0][7:])

            # service_role: the existing contract (0122) is privileged on purpose -- wl_assert_my_site returns
            # the owner of ANY site for auth.role()='service_role'. That is exactly why this customer RPC is
            # not granted to service_role: no server runtime calls it, so it never gets a cross-tenant path.
            service_jwt = json.dumps({"role": "service_role"})
            raised, msg = as_role_raises("service_role", "select wl_site_period(%s,7,true)", sa, jwt=service_jwt)
            step(raised and "permission denied" in msg.lower(), "service_role is refused EXECUTE on wl_site_period (not granted)", msg)
            cur.execute("savepoint sv")
            cur.execute("select set_config('request.jwt.claims', %s, true)", (service_jwt,))
            cur.execute("set local role service_role")
            owner_b = cur.execute("select wl_assert_my_site(%s)", (sb,)).fetchone()[0]
            cur.execute("rollback to savepoint sv")
            tenant_b = cur.execute("select tenant_id from sites where id=%s", (sb,)).fetchone()[0]
            step(owner_b == tenant_b, "existing wl_assert_my_site service_role contract unchanged (privileged, any site)")
            step(as_raises(ua, "select wl_site_period(%s,1,true)", sa), "out-of-range window is rejected")

            # Exact EXECUTE ACL after the migration (catches default privileges or a stray grant).
            def execute_grantees(sig):
                rows = cur.execute("""
                    select case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end, p.proowner::regrole::text
                      from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                     where p.oid = %s::regprocedure and a.privilege_type = 'EXECUTE'""", (sig,)).fetchall()
                owner = rows[0][1] if rows else None
                return {g for g, _ in rows if g != owner}
            got = execute_grantees("public.wl_site_period(uuid,integer,boolean)")
            step(got == {"authenticated"}, "wl_site_period EXECUTE ACL is exactly {authenticated} (+owner)", str(sorted(got)))
            for sig in ("public.wl_site_day_facts(jsonb)", "public.wl_site_period_summary(jsonb)"):
                got = execute_grantees(sig)
                step(got == set(), f"{sig} EXECUTE ACL is owner only", str(sorted(got)))
            sec = cur.execute("select prosecdef, proconfig from pg_proc where proname='wl_site_period'").fetchone()
            step(sec[0] is True and any("search_path=public" in c for c in (sec[1] or [])), "SECURITY DEFINER with pinned search_path")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

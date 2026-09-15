#!/usr/bin/env python3
"""0106 AI routing — per-site egress policy + route audit on real Postgres (disposable CI DB only,
never production). Proves the security model the Phase-5 router depends on:

  * a site is LOCAL-ONLY by default (no row => external_egress_allowed false);
  * only the site's own tenant owner/admin can relax it (a viewer is refused; another tenant is refused) —
    so no platform admin / model can flip a site to external;
  * the route audit is service-role WRITE only (an authenticated user is refused);
  * the route audit is platform-admin READ only (a tenant is refused), and it carries provider/model
    for Admin+audit but NEVER a key;
  * an unconfigured mode resolves to configured=false (=> the edge function uses guided_fallback, and
    no local benchmark model is silently customer-facing).

Assumes 0001..0106 are already applied (the CI integration job does this first).

    python prototype/tests/e2e_ai_routing_pg.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

FUNCS = ("wl_ai_site_egress", "wl_ai_set_site_egress", "wl_ai_log_route", "wl_ai_route_audit")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD") if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_ai_routing_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> int:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()  # superuser seeding  # noqa: E731

    def claims(uid, role):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": (str(uid) if uid else None), "role": role}),))

    def as_user(uid, sql, *p):
        claims(uid, "authenticated"); conn.execute("set role authenticated")
        try: return conn.execute(sql, p or None).fetchone()
        finally: conn.execute("reset role")

    def as_service(sql, *p):
        claims(None, "service_role")  # SECURITY DEFINER fn; auth.role() reads this claim
        return conn.execute(sql, p or None).fetchone()

    def bootstrap(email, company, site_name):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
        tenant = uuid.UUID(boot["tenant_id"])
        site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
        return uid, tenant, site

    def add_member(tenant, email, role):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        conn.execute("insert into memberships (user_id, tenant_id, role) values (%s,%s,%s)", (uid, tenant, role))
        return uid

    sfx = uuid.uuid4().hex[:8]
    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0106 did not apply")

    owner, tenant, site = bootstrap(f"route-owner-{sfx}@watchlog.test", "Route Co", "Route Site")
    viewer = add_member(tenant, f"route-viewer-{sfx}@watchlog.test", "viewer")
    owner_b, tenant_b, site_b = bootstrap(f"route-otherowner-{sfx}@watchlog.test", "Other Co", "Other Site")

    # 1. default local-only (no row)
    eg = as_user(owner, "select wl_ai_site_egress(%s)", site)[0]
    step(eg.get("external_egress_allowed") is False, "site is LOCAL-ONLY by default", str(eg))

    # 2. owner may relax it; read reflects it
    as_user(owner, "select wl_ai_set_site_egress(%s, true)", site)
    eg = as_user(owner, "select wl_ai_site_egress(%s)", site)[0]
    step(eg.get("external_egress_allowed") is True, "owner can allow external egress")

    # 3. owner may re-restrict to local-only
    as_user(owner, "select wl_ai_set_site_egress(%s, false)", site)
    eg = as_user(owner, "select wl_ai_site_egress(%s)", site)[0]
    step(eg.get("external_egress_allowed") is False, "owner can re-restrict to local-only")

    # 4. a viewer cannot change the policy
    denied = False
    try: as_user(viewer, "select wl_ai_set_site_egress(%s, true)", site)
    except psycopg.Error: denied = True
    finally: conn.execute("reset role")
    step(denied, "a viewer cannot change the egress policy")

    # 5. another tenant cannot even read this site's policy
    cross = False
    try: as_user(owner_b, "select wl_ai_site_egress(%s)", site)
    except psycopg.Error: cross = True
    finally: conn.execute("reset role")
    step(cross, "another tenant cannot read this site's policy (42501)")

    # 6. service_role writes a route audit row (provider/model present, NO key)
    envelope = {"tenant_id": str(tenant), "site_id": str(site), "user_id": str(owner),
                "mode": "instant", "route": "ai_primary", "provider_id": str(uuid.uuid4()),
                "provider_name": "VI Ollama", "model": "qwen3:8b", "used_fallback": False,
                "egress": "local", "latency_ms": 812, "candidates_tried": 1,
                "tool_calls": ["daily_intelligence"], "outcome": "ok"}
    as_service("select wl_ai_log_route(%s::jsonb)", json.dumps(envelope))
    n = q("select count(*) from ai_route_audit where site_id=%s", site)[0]
    step(n == 1, "service_role logs a route decision", f"rows={n}")

    # 7. an authenticated user cannot write the audit
    blocked_write = False
    try: as_user(owner, "select wl_ai_log_route(%s::jsonb)", json.dumps(envelope))
    except psycopg.Error: blocked_write = True
    finally: conn.execute("reset role")
    step(blocked_write, "authenticated cannot write route audit (service-role only)")

    # 8. platform admin reads it; provider/model visible; NO key material
    padmin = q("insert into auth.users (email) values (%s) returning id", f"route-padmin-{sfx}@watchlog.test")[0]
    q("insert into platform_admins(user_id, role) values (%s,'platform_admin') "
      "on conflict (user_id) do update set role=excluded.role", padmin)
    rows = as_user(padmin, "select wl_ai_route_audit(100, %s)", site)[0][0]
    blob = json.dumps(rows)
    step(isinstance(rows, list) and len(rows) == 1 and rows[0]["provider_name"] == "VI Ollama"
         and rows[0]["model"] == "qwen3:8b" and "sk-" not in blob and "api_key" not in blob,
         "platform admin reads audit; provider/model shown; no key")

    # 9. a tenant cannot read the route audit
    blocked_read = False
    try: as_user(owner, "select wl_ai_route_audit(100, null)")
    except psycopg.Error: blocked_read = True
    finally: conn.execute("reset role")
    step(blocked_read, "a tenant cannot read the route audit (platform-admin only)")

    # 10. an unconfigured mode resolves to configured=false => guided_fallback (no silent 4B Instant)
    md = as_service("select wl_ai_resolve_mode('instant', false)")[0]
    step(md.get("configured") is False, "unconfigured mode => configured=false (guided_fallback floor)")

    ok = all(STEPS) and len(STEPS) == 10
    print(("OK — " if ok else "FAIL — ") + f"{sum(STEPS)}/{len(STEPS)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

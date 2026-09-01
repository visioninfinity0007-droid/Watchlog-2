#!/usr/bin/env python3
"""Offline contract gate for the WatchLog platform admin control plane.

This test deliberately does not need database credentials. It verifies the
migration keeps platform administration separate from tenant membership and
that every cross-tenant write has a reason/audit path. Live PostgREST denial
checks are run after the migration is deployed.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "prototype/supabase/migrations/0029_platform_admin.sql").read_text(encoding="utf-8")

CHECKS = []
def check(name, ok): CHECKS.append((name, bool(ok)))

check("dedicated platform_admins table", "create table if not exists public.platform_admins" in SQL)
check("three explicit platform roles", all(x in SQL for x in ("platform_owner","platform_admin","platform_support")))
check("platform tables have RLS", SQL.count("enable row level security") >= 2)
check("platform tables revoked from tenant roles", "revoke all on public.platform_admins from anon, authenticated" in SQL)
check("normal users only get platform_me probe", "grant execute on function public.wl_platform_me() to authenticated" in SQL)
check("platform role helper is not client-callable", "revoke all on function public.wl_platform_role() from public,anon,authenticated" in SQL)
check("support is not in trial write role list", "wl_platform_require(array['platform_owner','platform_admin'])" in SQL)
check("paid override is platform_owner only", "wl_platform_require(array['platform_owner'])" in SQL and "manual_subscription_override" in SQL)
check("paid override calls authoritative billing writer", "perform wl_billing_set_subscription(p_tenant_id,p_plan,p_status)" in SQL)
check("cross-tenant writes require reason", all(fn in SQL for fn in ("wl_platform_grant_trial","wl_platform_set_subscription","wl_platform_set_admin","wl_platform_remove_admin")) and SQL.count("p_reason text") >= 4)
check("audit writer records actor", all(x in SQL for x in ("actor_user_id","actor_role","reason","before_json","after_json")))
check("tenant deletion is not exposed", "delete from tenants" not in SQL.lower())
check("agent secrets are not returned", "agent_key_hash" not in re.sub(r"--.*", "", SQL))

# Platform RPCs can be granted to authenticated because each call performs a
# platform-role check internally. They must never be granted to anon.
for fn in ["wl_platform_overview","wl_platform_tenants","wl_platform_tenant","wl_platform_operations",
           "wl_platform_billing","wl_platform_grant_trial","wl_platform_set_subscription",
           "wl_platform_audit","wl_platform_admins","wl_platform_set_admin","wl_platform_remove_admin"]:
    check(f"{fn} revoked from anon", re.search(rf"revoke all on function public\.{fn}\([^;]*?\) from public,anon", SQL, re.S) is not None)


def run():
    failed=[]
    print("Platform admin contract gate")
    print("="*64)
    for name,ok in CHECKS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok: failed.append(name)
    print("="*64)
    print(f"  {len(CHECKS)-len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def test_platform_admin_contract(): assert run()==0

if __name__=="__main__": sys.exit(run())

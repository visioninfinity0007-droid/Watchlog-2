import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "prototype/supabase/migrations/0038_customer_account_lifecycle.sql"


def block(sql: str, name: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{re.escape(name)}\b.*?\$\$;",
        sql,
        re.I | re.S,
    )
    if not match:
        raise AssertionError(f"missing lifecycle RPC: {name}")
    return match.group(0).lower()


def main():
    sql = MIG.read_text(encoding="utf-8")
    low = sql.lower()
    problems = []

    if "add column if not exists account_status" not in low:
        problems.append("tenants.account_status missing")
    if "check (account_status in ('active','suspended'))" not in low:
        problems.append("account_status constraint missing")

    for name in [
        "wl_is_member",
        "wl_my_tenant",
        "wl_my_account",
        "wl_reporting_enabled",
        "wl_platform_customer_lifecycles",
        "wl_platform_customer_lifecycle",
        "wl_platform_set_account_status",
    ]:
        try:
            fn = block(sql, name)
        except AssertionError as exc:
            problems.append(str(exc))
            continue
        if "security definer" not in fn:
            problems.append(f"{name}: must be SECURITY DEFINER")
        if "set search_path = public" not in fn:
            problems.append(f"{name}: search_path must be pinned")
        if f"grant execute on function public.{name}" not in low:
            problems.append(f"{name}: authenticated grant missing")
        if f"revoke all on function public.{name}" not in low:
            problems.append(f"{name}: public/anon revoke missing")

    member = block(sql, "wl_is_member")
    tenant = block(sql, "wl_my_tenant")
    reporting = block(sql, "wl_reporting_enabled")
    setter = block(sql, "wl_platform_set_account_status")

    if "account_status='active'" not in member:
        problems.append("suspended users are not blocked by wl_is_member")
    if "account_status='active'" not in tenant:
        problems.append("suspended users are not blocked by wl_my_tenant")
    if "account_status <> 'active' then false" not in reporting:
        problems.append("reporting does not stop for suspended accounts")
    if "wl_platform_require(array['platform_owner','platform_admin'])" not in setter:
        problems.append("account lifecycle mutation is not owner/admin limited")
    if "wl_platform_write_audit" not in setter:
        problems.append("account lifecycle mutation is not audited")
    for forbidden in ["delete from memberships", "update memberships", "insert into memberships", "delete from auth.users", "update auth.users"]:
        if forbidden in setter:
            problems.append(f"account suspension performs forbidden identity/data mutation: {forbidden}")

    shell = (ROOT / "portal/app/shell.js").read_text(encoding="utf-8")
    entry = (ROOT / "portal/app/page.js").read_text(encoding="utf-8")
    onboarding = (ROOT / "portal/app/onboarding/page.js").read_text(encoding="utf-8")
    suspended = ROOT / "portal/app/account-suspended/page.js"
    customer360 = (ROOT / "portal/app/admin/tenants/page.js").read_text(encoding="utf-8")

    if not suspended.exists():
        problems.append("missing suspended-account customer page")
    for rel, text in [("shell", shell), ("entry", entry), ("onboarding", onboarding)]:
        if 'account_status==="suspended"' not in text and 'account_status === "suspended"' not in text:
            problems.append(f"{rel}: suspended account routing missing")
        if "/account-suspended/" not in text:
            problems.append(f"{rel}: suspended route target missing")
    for phrase in ["Suspend customer", "Reactivate customer", "wl_platform_set_account_status", "Customer access"]:
        if phrase not in customer360:
            problems.append(f"Customer 360 lifecycle control missing: {phrase}")

    if problems:
        raise SystemExit("Customer lifecycle contract failed:\n- " + "\n- ".join(problems))
    print("Customer lifecycle contract: PASS")


if __name__ == "__main__":
    main()

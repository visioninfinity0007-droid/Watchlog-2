import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "prototype/supabase/migrations/0037_saas_operations.sql"

TABLES = [
    "customer_billing_profiles",
    "platform_support_notes",
    "platform_support_sessions",
    "commercial_invoices",
    "commercial_invoice_items",
    "commercial_contracts",
    "commercial_manual_payments",
]

PUBLIC_RPCS = [
    "wl_platform_commercial",
    "wl_customer_documents",
    "wl_platform_invoice",
    "wl_platform_contract",
    "wl_platform_set_billing_profile",
    "wl_platform_add_support_note",
    "wl_platform_start_support_mode",
    "wl_platform_support_session",
    "wl_platform_end_support_mode",
    "wl_platform_create_invoice",
    "wl_platform_set_invoice_status",
    "wl_platform_record_payment",
    "wl_platform_create_contract",
    "wl_platform_set_contract_status",
]

ADMIN_PAGES = [
    "portal/app/admin/tenants/page.js",
    "portal/app/admin/support/page.js",
    "portal/app/admin/invoices/page.js",
    "portal/app/admin/contracts/page.js",
    "portal/app/admin/billing/page.js",
]


def function_block(sql: str, name: str) -> str:
    # Functions in this migration use $$ bodies and are followed by revoke.
    pattern = re.compile(
        rf"create\s+or\s+replace\s+function\s+public\.{re.escape(name)}\b.*?\$\$;",
        re.I | re.S,
    )
    match = pattern.search(sql)
    if not match:
        raise AssertionError(f"missing RPC: {name}")
    return match.group(0)


def main():
    if not MIGRATION.exists():
        raise SystemExit("missing migration 0037_saas_operations.sql")
    sql = MIGRATION.read_text(encoding="utf-8")
    low = sql.lower()
    problems = []

    for table in TABLES:
        if f"create table if not exists public.{table}" not in low:
            problems.append(f"missing table {table}")
        if f"alter table public.{table} enable row level security" not in low:
            problems.append(f"RLS not enabled on {table}")
        if f"revoke all on public.{table} from anon, authenticated" not in low:
            problems.append(f"direct client privileges not revoked on {table}")

    for name in PUBLIC_RPCS:
        try:
            block = function_block(sql, name).lower()
        except AssertionError as exc:
            problems.append(str(exc))
            continue
        if "security definer" not in block:
            problems.append(f"{name}: must be SECURITY DEFINER")
        if "set search_path = public" not in block:
            problems.append(f"{name}: search_path must be pinned")
        signature_start = block.split("returns", 1)[0]
        if f"grant execute on function public.{name}" not in low:
            problems.append(f"{name}: authenticated grant missing")
        if f"revoke all on function public.{name}" not in low:
            problems.append(f"{name}: public/anon revoke missing")

    # Every mutating platform workflow must write the existing platform audit.
    for name in [
        "wl_platform_set_billing_profile",
        "wl_platform_add_support_note",
        "wl_platform_start_support_mode",
        "wl_platform_end_support_mode",
        "wl_platform_create_invoice",
        "wl_platform_set_invoice_status",
        "wl_platform_record_payment",
        "wl_platform_create_contract",
        "wl_platform_set_contract_status",
    ]:
        try:
            block = function_block(sql, name).lower()
        except AssertionError:
            continue
        if "wl_platform_write_audit" not in block:
            problems.append(f"{name}: platform audit write missing")

    # Support Mode must never rewrite auth identity or tenant memberships.
    support = "\n".join(
        function_block(sql, n).lower()
        for n in ["wl_platform_start_support_mode", "wl_platform_support_session", "wl_platform_end_support_mode"]
    )
    forbidden_support = [
        "update auth.users",
        "insert into auth.users",
        "delete from auth.users",
        "insert into memberships",
        "update memberships",
        "delete from memberships",
        "set local role",
    ]
    for token in forbidden_support:
        if token in support:
            problems.append(f"Support Mode performs forbidden identity mutation: {token}")

    customer_docs = function_block(sql, "wl_customer_documents").lower()
    if "wl_my_tenant()" not in customer_docs or "wl_my_role()" not in customer_docs:
        problems.append("customer documents must derive tenant and role server-side")
    if "v_role <> 'owner'" not in customer_docs:
        problems.append("customer documents must be Owner-only")
    if "platform_support_notes" in customer_docs or "platform_support_sessions" in customer_docs:
        problems.append("customer documents must not expose internal support data")

    for rel in ADMIN_PAGES:
        if not (ROOT / rel).exists():
            problems.append(f"missing SaaS operations page: {rel}")

    customer360 = (ROOT / "portal/app/admin/tenants/page.js").read_text(encoding="utf-8")
    for phrase in ["Customer 360", "Change customer plan", "Open Support Mode", "Generate invoice", "Generate contract", "Record payment"]:
        if phrase not in customer360:
            problems.append(f"Customer 360 action missing: {phrase}")

    if problems:
        raise SystemExit("SaaS operations contract failed:\n- " + "\n- ".join(problems))
    print("SaaS operations contract: PASS")


if __name__ == "__main__":
    main()

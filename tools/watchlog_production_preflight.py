#!/usr/bin/env python3
"""WatchLog production preflight with a hard read-only database transaction.

This tool is deliberately incapable of applying migrations. It exists to prove
that an operator is connected to the intended WatchLog Supabase project and to
capture the schema/data boundary required before any production DDL.

Required environment:
  SUPABASE_PROJECT_REF
  SUPABASE_DB_HOST
  SUPABASE_DB_USER
  SUPABASE_DB_PASSWORD

Optional:
  SUPABASE_DB_PORT (default 5432)
  SUPABASE_DB_NAME (default postgres)

Example:
  python tools/watchlog_production_preflight.py --admin-email admin@example.com

The report never prints database credentials, API keys, or passwords.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

EXPECTED_PROJECT_REF = "oyvgubyxmjlijiczjona"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def validate_target(project_ref: str, db_host: str, db_user: str) -> None:
    """Fail closed unless the supplied connection metadata identifies WatchLog."""
    if project_ref != EXPECTED_PROJECT_REF:
        raise RuntimeError(
            f"refusing target {project_ref!r}; expected WatchLog project "
            f"{EXPECTED_PROJECT_REF!r}"
        )

    # Direct Supabase database hosts embed the project ref. Pooler hosts do not.
    host = db_host.lower()
    if host.startswith("db.") and host.endswith(".supabase.co"):
        expected_host = f"db.{EXPECTED_PROJECT_REF}.supabase.co"
        if host != expected_host:
            raise RuntimeError(
                f"database host does not match WatchLog project: {db_host!r}"
            )

    # Session-pooler users commonly use postgres.<project-ref>. A plain
    # `postgres` user is also valid for some direct connection paths.
    user = db_user.strip()
    if user.startswith("postgres.") and user != f"postgres.{EXPECTED_PROJECT_REF}":
        raise RuntimeError(
            f"database user does not match WatchLog project: {db_user!r}"
        )


def classify_boundary(markers: dict[str, Any]) -> str:
    """Return a conservative schema-boundary classification from read markers."""
    has_0023 = bool(markers.get("entitlement_0023")) and bool(
        markers.get("reporting_0023")
    )

    later_keys = (
        "monitoring_rules_0024",
        "site_type_0024",
        "camera_purpose_0024",
        "platform_admins_0029",
        "platform_me_0029",
        "analytic_key_0030",
        "recipient_destinations_0031",
        "site_health_0033",
        "billing_owner_policies_0036",
    )
    later = [bool(markers.get(key)) for key in later_keys]

    if has_0023 and all(later):
        return "0036_candidate_requires_authz_smoke"
    if has_0023 and not any(later):
        return "0023_exact_candidate"
    if has_0023 and any(later):
        return "partial_after_0023_stop_and_reconcile"
    if any(later):
        return "inconsistent_or_unknown_stop_and_reconcile"
    return "before_0023_or_unknown_stop_and_reconcile"


def _collect_report(admin_email: str | None = None) -> dict[str, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required: python -m pip install 'psycopg[binary]'"
        ) from exc

    project_ref = _required_env("SUPABASE_PROJECT_REF")
    db_host = _required_env("SUPABASE_DB_HOST")
    db_user = _required_env("SUPABASE_DB_USER")
    db_password = _required_env("SUPABASE_DB_PASSWORD")
    db_port = int(os.environ.get("SUPABASE_DB_PORT", "5432"))
    db_name = os.environ.get("SUPABASE_DB_NAME", "postgres").strip() or "postgres"

    validate_target(project_ref, db_host, db_user)

    with psycopg.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        dbname=db_name,
        connect_timeout=20,
        autocommit=True,
        row_factory=dict_row,
    ) as conn:
        conn.execute("begin transaction read only")
        try:
            identity = conn.execute(
                """
                select
                  current_database() as database_name,
                  current_user as database_user,
                  current_setting('server_version') as server_version,
                  current_setting('transaction_read_only') as transaction_read_only
                """
            ).fetchone()
            if identity["transaction_read_only"] != "on":
                raise RuntimeError("database transaction is not read-only; aborting preflight")

            markers = conn.execute(
                """
                select
                  to_regclass('public.tenants') is not null as tenants_core,
                  to_regclass('public.sites') is not null as sites_core,
                  to_regclass('public.cameras') is not null as cameras_core,
                  to_regprocedure('public.wl_entitlement()') is not null as entitlement_0023,
                  to_regprocedure('public.wl_reporting_enabled(uuid)') is not null as reporting_0023,
                  to_regclass('public.monitoring_rules') is not null as monitoring_rules_0024,
                  exists (
                    select 1 from information_schema.columns
                    where table_schema='public' and table_name='sites' and column_name='site_type'
                  ) as site_type_0024,
                  exists (
                    select 1 from information_schema.columns
                    where table_schema='public' and table_name='cameras' and column_name='purpose'
                  ) as camera_purpose_0024,
                  to_regclass('public.platform_admins') is not null as platform_admins_0029,
                  to_regprocedure('public.wl_platform_me()') is not null as platform_me_0029,
                  exists (
                    select 1 from information_schema.columns
                    where table_schema='public' and table_name='monitoring_rules' and column_name='analytic_key'
                  ) as analytic_key_0030,
                  exists (
                    select 1 from information_schema.columns
                    where table_schema='public' and table_name='report_recipients'
                      and column_name='whatsapp_destination'
                  ) and exists (
                    select 1 from information_schema.columns
                    where table_schema='public' and table_name='report_recipients'
                      and column_name='email_destination'
                  ) as recipient_destinations_0031,
                  to_regprocedure('public.wl_site_health_details(integer)') is not null as site_health_0033,
                  (
                    select count(*) = 4
                    from pg_policies
                    where schemaname='public'
                      and policyname in (
                        'owner_read_billing_customers',
                        'owner_read_subscriptions',
                        'owner_read_payment_transactions',
                        'owner_read_billing_checkouts'
                      )
                      and cmd='SELECT'
                      and coalesce(qual, '') like '%wl_my_tenant%'
                      and coalesce(qual, '') like '%wl_my_role%'
                  ) as billing_owner_policies_0036
                """
            ).fetchone()

            counts: dict[str, int | None] = {
                "auth_users": conn.execute("select count(*) as n from auth.users").fetchone()["n"],
                "tenants": None,
                "sites": None,
                "cameras": None,
            }
            for table in ("tenants", "sites", "cameras"):
                if markers[f"{table}_core"]:
                    # Table names are fixed constants, never user input.
                    counts[table] = conn.execute(
                        f"select count(*) as n from public.{table}"
                    ).fetchone()["n"]

            ledger_present = conn.execute(
                "select to_regclass('public.schema_migrations') is not null as present"
            ).fetchone()["present"]
            ledger_columns: list[str] = []
            ledger_shape_ok = False
            ledger: list[dict[str, Any]] = []
            if ledger_present:
                ledger_columns = [
                    row["column_name"]
                    for row in conn.execute(
                        """
                        select column_name
                        from information_schema.columns
                        where table_schema='public' and table_name='schema_migrations'
                        order by ordinal_position
                        """
                    ).fetchall()
                ]
                ledger_shape_ok = {"filename", "sha256", "applied_at"}.issubset(
                    set(ledger_columns)
                )
                if ledger_shape_ok:
                    ledger = conn.execute(
                        """
                        select filename, sha256, applied_at::text as applied_at
                        from public.schema_migrations
                        order by filename
                        """
                    ).fetchall()

            admin: dict[str, Any] | None = None
            if admin_email:
                auth_row = conn.execute(
                    """
                    select id::text as user_id,
                           email_confirmed_at is not null as email_confirmed
                    from auth.users
                    where lower(email)=lower(%s)
                    limit 1
                    """,
                    (admin_email,),
                ).fetchone()
                admin = {
                    "auth_user_exists": auth_row is not None,
                    "email_confirmed": bool(auth_row and auth_row["email_confirmed"]),
                    "platform_role": None,
                }
                if auth_row and markers["platform_admins_0029"]:
                    role_row = conn.execute(
                        "select role from public.platform_admins where user_id=%s::uuid",
                        (auth_row["user_id"],),
                    ).fetchone()
                    admin["platform_role"] = role_row["role"] if role_row else None

            boundary = classify_boundary(dict(markers))
            needs_reconcile = "stop_and_reconcile" in boundary or (
                bool(ledger_present) and not ledger_shape_ok
            )
            report = {
                "tool": "watchlog_production_preflight",
                "project_ref": project_ref,
                "identity": dict(identity),
                "read_only_proven": True,
                "markers": dict(markers),
                "boundary": boundary,
                "row_counts": counts,
                "migration_ledger": {
                    "present": bool(ledger_present),
                    "shape_ok": ledger_shape_ok if ledger_present else None,
                    "columns": ledger_columns,
                    "rows": ledger,
                },
                "admin": admin,
                "next_action": (
                    "No DDL. Reconcile partial/unknown schema or migration-ledger shape first."
                    if needs_reconcile
                    else "Use the guarded production parity runbook; this report alone never authorizes DDL."
                ),
            }
            return report
        finally:
            conn.execute("rollback")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only WatchLog production schema/data preflight"
    )
    parser.add_argument(
        "--admin-email",
        help="Optionally verify that this Auth user exists and inspect its platform role",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output file. Secrets are never included.",
    )
    args = parser.parse_args()

    try:
        report = _collect_report(args.admin_email)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(payload)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

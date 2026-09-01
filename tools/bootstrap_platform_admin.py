#!/usr/bin/env python3
"""Bootstrap or promote a WatchLog platform administrator safely.

The script never stores credentials and never prints passwords or secret keys.
It supports two production-safe paths:

1. Promote an existing Supabase Auth user by email using the Postgres owner
   connection used for migrations.
2. Optionally create a confirmed Auth user first when SUPABASE_SECRET_KEY and
   PLATFORM_ADMIN_PASSWORD are supplied, then promote that user.

Required environment for promotion:
  SUPABASE_DB_HOST
  SUPABASE_DB_PORT (default 5432)
  SUPABASE_DB_USER
  SUPABASE_DB_PASSWORD
  SUPABASE_DB_NAME (default postgres)

Required environment for --create-user:
  SUPABASE_URL
  SUPABASE_SECRET_KEY
  PLATFORM_ADMIN_PASSWORD

Examples:
  python tools/bootstrap_platform_admin.py --email admin@example.com
  python tools/bootstrap_platform_admin.py --email admin@example.com --create-user

The default role is platform_owner. Other roles can be supplied explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROLES = ("platform_owner", "platform_admin", "platform_support")


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def create_auth_user(email: str) -> None:
    url = env_required("SUPABASE_URL").rstrip("/")
    secret = env_required("SUPABASE_SECRET_KEY")
    password = env_required("PLATFORM_ADMIN_PASSWORD")
    if len(password) < 12:
        raise RuntimeError("PLATFORM_ADMIN_PASSWORD must be at least 12 characters")

    body = json.dumps({
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"watchlog_platform_admin": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/auth/v1/admin/users",
        data=body,
        method="POST",
        headers={
            "apikey": secret,
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"Auth user creation returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        # If the account already exists, promotion can continue safely.
        if exc.code in (400, 409) and "already" in detail.lower():
            return
        raise RuntimeError(f"Auth user creation failed with HTTP {exc.code}: {detail}") from exc


def promote(email: str, role: str) -> tuple[str, str]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required: python -m pip install psycopg[binary]") from exc

    host = env_required("SUPABASE_DB_HOST")
    port = int(os.environ.get("SUPABASE_DB_PORT", "5432"))
    user = env_required("SUPABASE_DB_USER")
    password = env_required("SUPABASE_DB_PASSWORD")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")

    with psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        connect_timeout=20,
        autocommit=False,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.platform_admins')")
            if cur.fetchone()[0] is None:
                raise RuntimeError("platform_admins does not exist; apply migration 0029 first")

            cur.execute(
                "select id::text, email from auth.users where lower(email)=lower(%s) limit 1",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(
                    "no Supabase Auth user with that email; sign up first or use --create-user"
                )
            user_id, canonical_email = row

            cur.execute(
                """
                insert into public.platform_admins(user_id, role, created_by)
                values (%s::uuid, %s, null)
                on conflict (user_id) do update set role=excluded.role
                returning role
                """,
                (user_id, role),
            )
            assigned_role = cur.fetchone()[0]
            conn.commit()
            return canonical_email, assigned_role


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a WatchLog platform administrator")
    parser.add_argument("--email", required=True, help="Supabase Auth email to promote")
    parser.add_argument("--role", choices=ROLES, default="platform_owner")
    parser.add_argument(
        "--create-user",
        action="store_true",
        help="Create a confirmed Auth user first using SUPABASE_SECRET_KEY",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        print("ERROR: invalid email", file=sys.stderr)
        return 2

    try:
        if args.create_user:
            create_auth_user(email)
        canonical_email, role = promote(email, args.role)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {canonical_email} is {role}")
    print("Next: sign in through the normal WatchLog login page; platform users route to /admin/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

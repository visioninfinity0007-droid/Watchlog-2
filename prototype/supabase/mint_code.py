#!/usr/bin/env python3
"""
Mint a one-time enrollment code.

One code per machine. A code is single-use and expiring — that is the
whole point: it is the only secret that ever travels with an installer,
and it is worthless the moment it has been redeemed.

    python supabase/mint_code.py                    # 1 code, 7 days
    python supabase/mint_code.py -n 5 --days 1
    python supabase/mint_code.py --list             # show unused codes

Reads credentials from projects/watchlog/.env (Secrets Gate).
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]

TENANT_ID = "00000000-0000-4000-8000-000000000001"
SITE_ID = "00000000-0000-4000-8000-000000000002"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no O/0, no I/1 — read aloud


def load_env() -> dict:
    p = ROOT / ".env"
    if not p.exists():
        sys.exit(f"FATAL: no .env at {p}")
    return {k.strip(): v.strip() for k, _, v in
            (l.partition("=") for l in p.read_text(encoding="utf-8").splitlines())
            if k.strip() and not k.startswith("#") and _}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=1, help="how many codes")
    ap.add_argument("--days", type=int, default=7, help="validity in days")
    ap.add_argument("--site", default=SITE_ID)
    ap.add_argument("--tenant", default=TENANT_ID)
    ap.add_argument("--list", action="store_true", help="list unused codes")
    args = ap.parse_args()

    env = load_env()
    with psycopg.connect(host=env["SUPABASE_DB_HOST"],
                         port=int(env.get("SUPABASE_DB_PORT", 5432)),
                         user=env["SUPABASE_DB_USER"],
                         password=env["SUPABASE_DB_PASSWORD"],
                         dbname=env.get("SUPABASE_DB_NAME", "postgres"),
                         autocommit=True) as conn:
        if args.list:
            rows = conn.execute(
                "select code, expires_at from enrollment_codes "
                "where used_at is null and expires_at > now() order by expires_at"
            ).fetchall()
            if not rows:
                print("  no unused codes — mint one")
            for code, exp in rows:
                print(f"  {code}   expires {exp:%Y-%m-%d %H:%M} UTC")
            return

        for _ in range(args.n):
            code = "WL-" + "".join(secrets.choice(ALPHABET) for _ in range(4)) \
                   + "-" + "".join(secrets.choice(ALPHABET) for _ in range(4))
            conn.execute(
                "insert into enrollment_codes (code, tenant_id, site_id, expires_at) "
                "values (%s, %s, %s, now() + make_interval(days => %s))",
                (code, args.tenant, args.site, args.days))
            print(f"  {code}   valid {args.days} day(s)")


if __name__ == "__main__":
    main()

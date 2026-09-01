#!/usr/bin/env python3
"""
Entitlement gate tests — the trial/subscription rule that decides whether a
tenant's daily reports go out. Exercises wl_reporting_enabled across every
policy case on a DISPOSABLE tenant (created + deleted here; no real tenant
touched). Live (needs the DB), so not in the offline CI suite.

    python prototype/tests/test_entitlement.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
ENV = {}
for line in (ROOT / ".env").read_text(errors="ignore").splitlines():
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV[m.group(1)] = m.group(2).strip().strip('"').strip("'")
DSN = dict(host=ENV["SUPABASE_DB_HOST"], port=ENV["SUPABASE_DB_PORT"],
           user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
           dbname=ENV["SUPABASE_DB_NAME"], connect_timeout=30, autocommit=True)

# (label, status, trial_started_offset_days, trial_days, expected_enabled)
CASES = [
    ("active",             "active",   0,   14, True),
    ("past_due (grace)",   "past_due", 0,   14, True),
    ("trial valid",        "trialing", -2,  14, True),
    ("trial expired",      "trialing", -20, 14, False),
    ("cancelled",          "cancelled", 0,  14, False),
    ("expired",            "expired",   0,  14, False),
]


def run() -> int:
    conn = psycopg.connect(**DSN); cur = conn.cursor()
    tid = None
    ok = True
    try:
        tid = cur.execute(
            "insert into tenants (name, plan, subscription_status, trial_started_at, trial_days) "
            "values ('ENTITLEMENT-TEST', 'starter', 'trialing', now(), 14) returning id").fetchone()[0]
        print("Entitlement gate (disposable tenant)")
        for label, status, off, days, expected in CASES:
            cur.execute(
                "update tenants set subscription_status=%s, trial_started_at=now() + make_interval(days=>%s), "
                "trial_days=%s where id=%s", (status, off, days, tid))
            got = cur.execute("select wl_reporting_enabled(%s)", (tid,)).fetchone()[0]
            good = got == expected
            ok = ok and good
            print(f"  {'PASS' if good else 'FAIL'}  {label:20} -> reporting_enabled={got} (expected {expected})")
        print("PASS" if ok else "FAILED")
        return 0 if ok else 1
    finally:
        if tid:
            cur.execute("delete from tenants where id=%s", (tid,))
            print("  cleanup: disposable tenant removed")
        conn.close()


def test_entitlement():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())

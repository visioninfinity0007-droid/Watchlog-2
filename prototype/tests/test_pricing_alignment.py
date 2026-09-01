#!/usr/bin/env python3
"""
Pricing alignment gate — the website and the billing config must never show
different numbers.

Fails if the Starter/Growth prices published on the marketing site (the WP
theme templates) diverge from the effective billing plan prices declared by
the SQL migration chain. Enterprise is quoted ("Talk to us") and is not a
self-serve price, so it is excluded from the numeric check but is asserted to
be contact-only on both sides.

Offline (reads repo files only), so it runs in CI.

    python prototype/tests/test_pricing_alignment.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WP = ROOT / "deploy" / "wordpress" / "themes" / "watchlog"
MIG = ROOT / "prototype" / "supabase" / "migrations"


def website_prices():
    """{'starter': 6000, 'growth': 12000} parsed from the WP pricing UIs."""
    out = {}
    for f in [WP / "page-pricing.php", WP / "front-page.php"]:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        # find each plan heading, then the first "PKR N,NNN" after it
        for plan in ("Starter", "Growth"):
            m = re.search(rf"{plan}.*?PKR\s*([\d,]+)", text, re.S | re.I)
            if m:
                val = int(m.group(1).replace(",", ""))
                out.setdefault(f.name, {})[plan.lower()] = val
    return out


def billing_prices():
    """Effective {'starter': paisa, 'growth': paisa, 'enterprise_active': bool}
    after applying every migration that touches billing_plans, in order."""
    amt = {}
    active = {"starter": True, "growth": True, "enterprise": True}
    for f in sorted(MIG.glob("*.sql")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        # seed rows:  ('starter',    250000, 'PKR')
        for plan, a in re.findall(r"\(\s*'(starter|growth|enterprise)'\s*,\s*(\d+)\s*,", t):
            amt[plan] = int(a)
        # updates: set amount_minor = 600000 ... where plan = 'starter'
        for a, plan in re.findall(r"set\s+amount_minor\s*=\s*(\d+).*?where\s+plan\s*=\s*'(starter|growth|enterprise)'", t, re.S | re.I):
            amt[plan] = int(a)
        for val, plan in re.findall(r"set\s+active\s*=\s*(true|false).*?where\s+plan\s*=\s*'(starter|growth|enterprise)'", t, re.S | re.I):
            active[plan] = (val.lower() == "true")
    return amt, active


def main() -> int:
    web = website_prices()
    amt, active = billing_prices()
    ok = True
    print("website prices:", web)
    print("billing paisa:", amt, "| active:", active)

    if not web:
        print("FAIL: could not parse website prices"); return 1

    # every WP surface must agree with itself
    surfaces = list(web.values())
    for plan in ("starter", "growth"):
        vals = {s.get(plan) for s in surfaces if plan in s}
        if len(vals) > 1:
            print(f"FAIL: website surfaces disagree on {plan}: {vals}"); ok = False

    ref = surfaces[0]
    for plan in ("starter", "growth"):
        web_pkr = ref.get(plan)
        db_pkr = amt.get(plan, 0) / 100
        if web_pkr is None:
            print(f"FAIL: no website price for {plan}"); ok = False
        elif web_pkr != db_pkr:
            print(f"FAIL: {plan} website PKR {web_pkr} != billing PKR {db_pkr}"); ok = False
        else:
            print(f"  OK  {plan}: PKR {web_pkr} (website == billing)")

    # Enterprise is quoted on the site and must be contact-only in billing
    if active.get("enterprise", True):
        print("FAIL: Enterprise is a self-serve plan in billing but 'Talk to us' on the site"); ok = False
    else:
        print("  OK  enterprise: contact-only on both sides")

    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


def test_pricing_alignment():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Pricing alignment gate: the website and billing config must never show
different numbers.

Fails if Starter/Growth prices published by the WordPress theme diverge from
the effective billing plan prices declared by the SQL migration chain.
Enterprise is quoted (Talk to us), so it is excluded from the numeric check
but must remain contact-only in billing.

Offline: reads repository files only.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WP = ROOT / "deploy" / "wordpress" / "themes" / "watchlog"
MIG = ROOT / "prototype" / "supabase" / "migrations"


def visible_text(raw: str) -> str:
    """Collapse PHP/HTML into searchable visible text.

    Pricing markup intentionally separates the PKR label from the amount with
    spans. The gate must validate what a visitor reads, not depend on whether
    `PKR` and `6,000` happen to be adjacent bytes in the template.
    """
    raw = re.sub(r"<\?php.*?\?>", " ", raw, flags=re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def website_prices():
    out = {}
    for f in [WP / "page-pricing.php", WP / "front-page.php"]:
        if not f.exists():
            continue
        text = visible_text(f.read_text(encoding="utf-8", errors="ignore"))
        for plan in ("Starter", "Growth"):
            m = re.search(rf"\b{plan}\b.*?\bPKR\s*([\d,]+)", text, re.I)
            if m:
                out.setdefault(f.name, {})[plan.lower()] = int(m.group(1).replace(",", ""))
    return out


def billing_prices():
    amt = {}
    active = {"starter": True, "growth": True, "enterprise": True}
    for f in sorted(MIG.glob("*.sql")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        for plan, amount in re.findall(r"\(\s*'(starter|growth|enterprise)'\s*,\s*(\d+)\s*,", t):
            amt[plan] = int(amount)
        for amount, plan in re.findall(r"set\s+amount_minor\s*=\s*(\d+).*?where\s+plan\s*=\s*'(starter|growth|enterprise)'", t, re.S | re.I):
            amt[plan] = int(amount)
        for value, plan in re.findall(r"set\s+active\s*=\s*(true|false).*?where\s+plan\s*=\s*'(starter|growth|enterprise)'", t, re.S | re.I):
            active[plan] = value.lower() == "true"
    return amt, active


def main() -> int:
    web = website_prices()
    amt, active = billing_prices()
    ok = True
    print("website prices:", web)
    print("billing paisa:", amt, "| active:", active)
    if not web:
        print("FAIL: could not parse website prices")
        return 1

    surfaces = list(web.values())
    for plan in ("starter", "growth"):
        vals = {surface.get(plan) for surface in surfaces if plan in surface}
        if len(vals) > 1:
            print(f"FAIL: website surfaces disagree on {plan}: {vals}")
            ok = False

    ref = surfaces[0]
    for plan in ("starter", "growth"):
        web_pkr = ref.get(plan)
        db_pkr = amt.get(plan, 0) / 100
        if web_pkr is None:
            print(f"FAIL: no website price for {plan}")
            ok = False
        elif web_pkr != db_pkr:
            print(f"FAIL: {plan} website PKR {web_pkr} != billing PKR {db_pkr}")
            ok = False
        else:
            print(f"  OK  {plan}: PKR {web_pkr} (website == billing)")

    if active.get("enterprise", True):
        print("FAIL: Enterprise is self-serve in billing but contact-only on the site")
        ok = False
    else:
        print("  OK  enterprise: contact-only on both sides")

    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


def test_pricing_alignment():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

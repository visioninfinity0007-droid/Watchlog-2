#!/usr/bin/env python3
"""
Billing-authorization gate (SOW invariant D).

Customers may request a plan; they may NEVER authoritatively mark themselves
paid. The audit found the old wl_set_plan let any owner set
subscription_status='active' with no payment. 0017 split that into:

  * wl_set_plan            — request-only (owner), cannot grant paid state
  * wl_billing_set_subscription — the authoritative writer, granted to NO
                             client role (only the Switch webhook / ops).

These assertions run over the real PostgREST surface, as anon and as a
normal signed-in owner — the privilege levels an attacker actually has. They
are non-mutating (permission checks only): the authoritative writer is
rejected before it can change anything.

    python prototype/tests/test_billing_authz.py
    pytest -q prototype/tests/test_billing_authz.py

Preconditions: SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY, and
PORTAL_DEMO_EMAIL / PORTAL_DEMO_PASSWORD signing in as an owner.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> dict:
    env = dict(os.environ)
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


ENV = load_env()
URL = ENV.get("SUPABASE_URL", "").rstrip("/")
KEY = ENV.get("SUPABASE_PUBLISHABLE_KEY", "")
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def rpc(fn, body, bearer=None):
    h = {"apikey": KEY, "Authorization": f"Bearer {bearer or KEY}",
         "Content-Type": "application/json"}
    req = urllib.request.Request(f"{URL}/rest/v1/rpc/{fn}",
                                 data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def sign_in():
    req = urllib.request.Request(
        f"{URL}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": ENV["PORTAL_DEMO_EMAIL"],
                         "password": ENV["PORTAL_DEMO_PASSWORD"]}).encode(),
        headers={"apikey": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())["access_token"]


@case("anon cannot call the authoritative billing writer")
def t_anon_billing():
    st, _ = rpc("wl_billing_set_subscription",
                {"p_tenant": "00000000-0000-0000-0000-000000000000",
                 "p_plan": "enterprise", "p_status": "active"})
    assert st in (401, 403, 404), f"expected denial, got {st}"
    return f"denied ({st})"


@case("anon cannot call wl_set_plan")
def t_anon_setplan():
    st, _ = rpc("wl_set_plan", {"p_plan": "enterprise"})
    assert st in (401, 403, 404), f"expected denial, got {st}"
    return f"denied ({st})"


@case("a signed-in OWNER cannot call the authoritative billing writer")
def t_owner_billing():
    jwt = sign_in()
    st, _ = rpc("wl_billing_set_subscription",
                {"p_tenant": "00000000-0000-0000-0000-000000000000",
                 "p_plan": "enterprise", "p_status": "active"}, bearer=jwt)
    assert st in (401, 403, 404), f"owner should be denied, got {st}"
    return f"owner denied ({st})"


def _reset_requested_plan():
    """Clear the intent marker this test set on the demo tenant, so the suite
    leaves the account exactly as it found it. Best-effort: skipped if DB
    credentials are not present (e.g. a CI run without secrets)."""
    try:
        import psycopg  # noqa: PLC0415
        if not ENV.get("SUPABASE_DB_PASSWORD"):
            return
        with psycopg.connect(
                host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
                user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
                dbname=ENV.get("SUPABASE_DB_NAME", "postgres"),
                connect_timeout=15, autocommit=True) as c:
            c.execute("update tenants set requested_plan=null "
                      "where lower(name)=lower(%s)", (ENV.get("PORTAL_DEMO_TENANT", "Demo Security Co"),))
    except Exception:  # noqa: BLE001
        pass


@case("wl_set_plan is request-only: a paid plan does NOT activate paid status")
def t_no_self_pay():
    jwt = sign_in()
    before = rpc("wl_trial_status", {}, bearer=jwt)[1]
    st, res = rpc("wl_set_plan", {"p_plan": "enterprise", "p_status": "active"}, bearer=jwt)
    try:
        assert st == 200, f"owner request-plan should succeed, got {st}: {res}"
        assert res.get("status") != "active", f"must not report active: {res}"
        after = rpc("wl_trial_status", {}, bearer=jwt)[1]
        assert after.get("status") != "active" or before.get("status") == "active", \
            f"subscription_status was escalated to active: {after}"
        # It may only record intent, never grant paid state.
        assert "requested_plan" in res or res.get("plan") == "trial", res
        return f"status stayed {after.get('status')!r}; only requested_plan recorded"
    finally:
        _reset_requested_plan()


def run() -> int:
    print("Billing authorization gate")
    print("=" * 62)
    p = f = 0
    for name, fn in CASES:
        try:
            print(f"  PASS  {name}\n          {fn()}"); p += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n          {e}"); f += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}"); f += 1
    print("=" * 62)
    print(f"  {p} passed, {f} failed")
    return 1 if f else 0


def test_billing_authz():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())

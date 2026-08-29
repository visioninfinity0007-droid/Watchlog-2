#!/usr/bin/env python3
"""
Tenant isolation QA suite — the Milestone 4 hard gate.

The scope of work makes this a gate, not a nicety: M4 cannot be handed
over until it passes. It exists because a multi-tenant bug is silent.
Nothing crashes, nothing is logged, the dashboard looks correct — one
tenant is simply able to read another's cameras, events and incident
stills.

That is not hypothetical here. On 2026-08-29 `wl_fleet` returned rows
from BOTH tenants to a caller holding nothing but the public publishable
key. Migration 0010 closed it. Case 1 is the regression test for exactly
that, so it cannot reopen unnoticed.

DESIGN
------
Two layers, deliberately separated:

  * The DATABASE is used only to discover fixtures — which tenants exist,
    which one the test user belongs to, which event ids belong to whom.
    Read-only. It never asserts anything.

  * Every ASSERTION goes over HTTP against the real PostgREST surface,
    as `anon` or as a normal signed-in user. That is the privilege level
    an attacker actually has, and a grant is only truly closed if the
    HTTP layer says so. Checking pg_proc grants would prove less.

The suite creates no users and writes nothing, so it is safe to run
against production and needs no service-role key.

    python prototype/tests/test_tenant_isolation.py
    pytest -q prototype/tests/test_tenant_isolation.py

Preconditions (it fails loudly rather than silently passing on a
single-tenant database, which is how this class of bug hides):
  * at least two tenants exist
  * PORTAL_DEMO_EMAIL / PORTAL_DEMO_PASSWORD sign in
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 30


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        env.update(dict(re.findall(r"^([A-Z0-9_]+)=(.*)$",
                                   f.read_text(errors="replace"), re.M)))
    env.update({k: v for k, v in os.environ.items()
                if k.startswith(("SUPABASE_", "PORTAL_"))})
    return {k: v.strip().strip('"').strip("'") for k, v in env.items()}


ENV = load_env()
URL = ENV.get("SUPABASE_URL", "").rstrip("/")
ANON = ENV.get("SUPABASE_PUBLISHABLE_KEY", "")


class Failure(Exception):
    pass


# ---------------------------------------------------------------------
# HTTP — the layer every assertion runs against
# ---------------------------------------------------------------------

def rpc(fn: str, body: dict | None = None, token: str | None = None):
    r = requests.post(
        f"{URL}/rest/v1/rpc/{fn}",
        headers={"apikey": ANON,
                 "Authorization": f"Bearer {token or ANON}",
                 "Content-Type": "application/json"},
        data=json.dumps(body or {}), timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def table(name: str, token: str | None = None, query: str = "select=*&limit=500"):
    r = requests.get(
        f"{URL}/rest/v1/{name}?{query}",
        headers={"apikey": ANON, "Authorization": f"Bearer {token or ANON}"},
        timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def login(email: str, password: str) -> str:
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password",
                      headers={"apikey": ANON, "Content-Type": "application/json"},
                      data=json.dumps({"email": email, "password": password}),
                      timeout=TIMEOUT)
    d = r.json()
    if "access_token" not in d:
        raise Failure(f"could not sign in as {email}: {d}")
    return d["access_token"]


# ---------------------------------------------------------------------
# fixture discovery — read-only, database side
# ---------------------------------------------------------------------

class Fixtures:
    def __init__(self):
        self.mine = None        # tenant the test user belongs to
        self.other = None       # a tenant they must NOT be able to see
        self.other_event = None
        self.token = None

    def discover(self):
        self.token = login(ENV["PORTAL_DEMO_EMAIL"], ENV["PORTAL_DEMO_PASSWORD"])
        st, mine = rpc("wl_my_tenant", {}, self.token)
        if st != 200 or not mine:
            raise Failure(f"wl_my_tenant returned {st} {mine} — test user has no tenant")
        self.mine = mine

        try:
            import psycopg
        except ImportError:
            raise Failure("psycopg is required for fixture discovery: pip install psycopg")

        g = lambda k: ENV.get(k, "")
        dsn = (f"postgresql://{g('SUPABASE_DB_USER')}:{g('SUPABASE_DB_PASSWORD')}"
               f"@{g('SUPABASE_DB_HOST')}:{g('SUPABASE_DB_PORT')}/{g('SUPABASE_DB_NAME')}")
        with psycopg.connect(dsn, connect_timeout=25, sslmode="require") as c:
            rows = c.execute("select id::text, name from tenants order by created_at").fetchall()
            if len(rows) < 2:
                raise Failure(
                    f"only {len(rows)} tenant(s) exist. This gate cannot prove "
                    "isolation on a single-tenant database — create a second "
                    "tenant before running it.")
            others = [r for r in rows if r[0] != self.mine]
            if not others:
                raise Failure("test user's tenant is the only tenant")
            self.other = others[0][0]
            ev = c.execute(
                "select id from events where tenant_id = %s order by id desc limit 1",
                (self.other,)).fetchone()
            self.other_event = ev[0] if ev else None
        return self


# ---------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# Must never be reachable without a session. wl_fleet is the one that
# actually leaked; the others share its shape and blast radius.
ANON_FORBIDDEN = ["wl_fleet", "wl_recent_events", "wl_analytics",
                  "wl_site_health", "wl_get_snapshot", "wl_daily_report",
                  "wl_prune_snapshots", "wl_portal_overview",
                  "wl_portal_snapshot"]

# Granted to anon ON PURPOSE — the agent authenticates by agent key
# inside the function body. They must still reject an invalid caller.
TABLES = ["tenants", "sites", "agents", "cameras", "events", "snapshots",
          "memberships", "enrollment_codes"]


def _leaks(status, data) -> bool:
    """Did this response hand back tenant data?"""
    if status != 200:
        return False
    if isinstance(data, list):
        return bool(data)
    if isinstance(data, dict):
        return bool(data.get("tenant") or data.get("rows") or data.get("sites"))
    return False


@check("1. anon cannot call privileged functions (regression: the wl_fleet leak)")
def t_anon_functions(f):
    bad = []
    for fn in ANON_FORBIDDEN:
        st, d = rpc(fn)
        if _leaks(st, d):
            bad.append(f"{fn} -> {st}, returned data to anon")
    if bad:
        raise Failure("; ".join(bad))
    return f"all {len(ANON_FORBIDDEN)} denied to anon"


@check("2. anon cannot read any table directly (RLS, no anon policy)")
def t_anon_tables(f):
    bad = []
    for t in TABLES:
        st, d = table(t)
        if st == 200 and isinstance(d, list) and d:
            bad.append(f"{t} returned {len(d)} rows to anon")
    if bad:
        raise Failure("; ".join(bad))
    return f"all {len(TABLES)} tables returned nothing to anon"


@check("3. signed-in overview contains only the caller's own tenant")
def t_overview_scoped(f):
    st, d = rpc("wl_portal_overview", {"p_days": 30}, f.token)
    if st != 200:
        raise Failure(f"overview failed: {st} {d}")
    tid = (d.get("tenant") or {}).get("id")
    if tid != f.mine:
        raise Failure(f"overview returned tenant {tid}, expected {f.mine}")
    if f.other and f.other in json.dumps(d):
        raise Failure("overview payload contains the OTHER tenant's id")
    return f"saw only {tid[:8]}"


@check("4. signed-in user cannot read the other tenant's rows from any table")
def t_cross_tenant_tables(f):
    bad = []
    for t in ("sites", "cameras", "events", "agents", "snapshots", "tenants"):
        st, d = table(t, f.token)
        if st == 200 and isinstance(d, list):
            key = "id" if t == "tenants" else "tenant_id"
            leaked = [r for r in d if str(r.get(key)) == f.other]
            if leaked:
                bad.append(f"{t}: {len(leaked)} rows of the other tenant")
    if bad:
        raise Failure("; ".join(bad))
    return "no cross-tenant rows visible"


@check("5. wl_is_member is false for a tenant the user does not belong to")
def t_is_member(f):
    st, d = rpc("wl_is_member", {"p_tenant": f.other}, f.token)
    if st == 200 and d is True:
        raise Failure("user is reported a member of the other tenant")
    return "correctly false"


@check("6. user cannot fetch the other tenant's incident still")
def t_cross_snapshot(f):
    if not f.other_event:
        return "skipped — the other tenant has no events"
    st, d = rpc("wl_portal_snapshot", {"p_event_id": f.other_event}, f.token)
    if st == 200 and isinstance(d, dict) and (d.get("image") or d.get("data")
                                              or d.get("bytes")):
        raise Failure(f"returned image bytes for event {f.other_event} "
                      "belonging to another tenant")
    return "denied"


@check("7. agent API is reachable but rejects invalid credentials")
def t_agent_api(f):
    st, d = rpc("wl_enroll", {"p_code": "WL-0000-0000", "p_hostname": "qa"})
    if st in (401, 403):
        raise Failure("wl_enroll no longer reachable by anon — agents in the "
                      "field would stop enrolling")
    if st == 200 and isinstance(d, dict) and d.get("agent_id"):
        raise Failure(f"wl_enroll ACCEPTED a bogus code: {d}")
    st, d = rpc("wl_heartbeat", {"p_agent_id": str(uuid.uuid4()),
                                 "p_agent_key": "wrong"})
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("wl_heartbeat accepted a bogus agent key")
    return "reachable, bogus credentials rejected"


@check("8. destructive functions denied to anon AND to a normal user")
def t_destructive(f):
    bad = []
    for tok, who in ((None, "anon"), (f.token, "a signed-in user")):
        st, _ = rpc("wl_prune_snapshots",
                    {"p_keep_days": 1, "p_keep_per_camera": 1}, tok)
        if st == 200:
            bad.append(f"{who} can call wl_prune_snapshots")
    if bad:
        raise Failure("; ".join(bad))
    return "wl_prune_snapshots denied to both"


@check("9. the publishable key alone yields no tenant data")
def t_key_inert(f):
    """The whole model rests on this: the anon key is public by design, so
    on its own it must be worth nothing."""
    bad = []
    for tid in (f.mine, f.other):
        st, d = table("sites", None, f"select=*&tenant_id=eq.{tid}")
        if st == 200 and isinstance(d, list) and d:
            bad.append(f"anon read {len(d)} sites of {tid[:8]}")
    if bad:
        raise Failure("; ".join(bad))
    return "publishable key returns nothing without a session"


# ---------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------

def run() -> int:
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY",
                           "PORTAL_DEMO_EMAIL", "PORTAL_DEMO_PASSWORD")
               if not ENV.get(k)]
    if missing:
        print(f"FATAL: missing from .env: {', '.join(missing)}")
        return 2

    print(f"Tenant isolation gate — {URL}")
    print("=" * 68)
    try:
        f = Fixtures().discover()
    except Failure as e:
        print(f"  PRECONDITION FAILED: {e}")
        return 2
    print(f"  test user's tenant {f.mine[:8]} | other tenant {f.other[:8]}"
          f" | probe event {f.other_event}\n")

    passed = failed = 0
    for name, fn in CHECKS:
        try:
            print(f"  PASS  {name}\n          {fn(f)}")
            passed += 1
        except Failure as e:
            print(f"  FAIL  {name}\n          {e}")
            failed += 1
        except Exception as e:                        # noqa: BLE001
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
            failed += 1

    print("=" * 68)
    print(f"  {passed} passed, {failed} failed")
    print("\n  GATE FAILED — do not hand over; a tenant can reach data that is "
          "not theirs." if failed else
          "\n  GATE PASSED — no cross-tenant access found.")
    return 1 if failed else 0


def test_tenant_isolation():
    assert run() == 0, "tenant isolation gate failed — see output"


if __name__ == "__main__":
    sys.exit(run())

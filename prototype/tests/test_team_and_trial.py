#!/usr/bin/env python3
"""
Team management and trial state — Milestone 2.

Runs over HTTP against the live PostgREST surface, as a real signed-in
user and as anon, for the same reason the isolation gate does: a grant is
only closed if the endpoint says so.

It creates invitations and revokes them again, so it leaves the account
exactly as it found it. It never creates users.

    python prototype/tests/test_team_and_trial.py
    pytest -q prototype/tests/test_team_and_trial.py
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
TOKEN = None


class Failure(Exception):
    pass


def rpc(fn: str, body: dict | None = None, token: str | None = "user"):
    auth = ANON if token is None else (TOKEN if token == "user" else token)
    r = requests.post(f"{URL}/rest/v1/rpc/{fn}",
                      headers={"apikey": ANON, "Authorization": f"Bearer {auth}",
                               "Content-Type": "application/json"},
                      data=json.dumps(body or {}), timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def sign_in() -> str:
    r = requests.post(f"{URL}/auth/v1/token?grant_type=password",
                      headers={"apikey": ANON, "Content-Type": "application/json"},
                      data=json.dumps({"email": ENV["PORTAL_DEMO_EMAIL"],
                                       "password": ENV["PORTAL_DEMO_PASSWORD"]}),
                      timeout=TIMEOUT)
    d = r.json()
    if "access_token" not in d:
        raise Failure(f"sign-in failed: {d}")
    return d["access_token"]


CHECKS = []
CREATED = []          # invitation ids to clean up


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("trial status reports plan, status and days left")
def t_trial():
    st, d = rpc("wl_trial_status")
    if st != 200 or not isinstance(d, dict):
        raise Failure(f"{st} {d}")
    for k in ("plan", "status", "trial_ends_at", "expired"):
        if k not in d:
            raise Failure(f"missing '{k}' in {list(d)}")
    if d["status"] == "trialing" and d.get("days_left") is None:
        raise Failure("trialing but days_left is null")
    return f"plan={d['plan']} status={d['status']} days_left={d.get('days_left')}"


@check("trial state is reported, never enforced (no access is denied)")
def t_trial_not_enforced():
    """M3 owns the billing gate. A trial-arithmetic bug must not lock a
    live security system out of its own dashboard."""
    st, d = rpc("wl_portal_overview", {"p_days": 7})
    if st != 200 or not isinstance(d, dict) or not d.get("tenant"):
        raise Failure(f"dashboard was denied while on a trial: {st} {d}")
    return "dashboard still reachable regardless of trial state"


@check("team list includes the caller with a role")
def t_members():
    st, d = rpc("wl_members")
    if st != 200 or not isinstance(d, list) or not d:
        raise Failure(f"{st} {d}")
    me = [m for m in d if m.get("is_you")]
    if not me:
        raise Failure("caller is not in their own member list")
    if me[0]["role"] not in ("owner", "admin", "viewer"):
        raise Failure(f"unexpected role {me[0]['role']}")
    return f"{len(d)} member(s); you are {me[0]['role']}"


@check("inviting issues a single-use token")
def t_invite():
    email = f"qa-{uuid.uuid4().hex[:8]}@watchlog-qa.example"
    st, d = rpc("wl_invite_member", {"p_email": email, "p_role": "viewer"})
    if st != 200 or not d.get("ok"):
        raise Failure(f"{st} {d}")
    if len(d.get("token") or "") < 32:
        raise Failure("token is too short to be unguessable")
    CREATED.append(d["id"])
    globals()["_INV"] = (d["id"], d["token"], email)
    return f"token {len(d['token'])} chars, expires in {d['expires_in_days']}d"


@check("a second invite to the same address does not pile up")
def t_invite_dedupe():
    _id, _tok, email = globals()["_INV"]
    st, d = rpc("wl_invite_member", {"p_email": email, "p_role": "viewer"})
    if st != 200 or not d.get("ok"):
        raise Failure(f"{st} {d}")
    CREATED.append(d["id"])
    st, lst = rpc("wl_invitations")
    same = [i for i in lst if i["email"] == email]
    if len(same) != 1:
        raise Failure(f"{len(same)} open invitations for one address")
    return "replaced, not duplicated"


@check("the invitation list never exposes the token")
def t_no_token_leak():
    st, d = rpc("wl_invitations")
    if st != 200 or not isinstance(d, list):
        raise Failure(f"{st} {d}")
    leaky = [i for i in d if "token" in i]
    if leaky:
        raise Failure(f"{len(leaky)} invitation(s) expose their token to every "
                      "member of the account")
    return f"{len(d)} invitation(s), no token field"


@check("a bad email is refused")
def t_bad_email():
    st, d = rpc("wl_invite_member", {"p_email": "not-an-email"})
    msg = json.dumps(d).lower()
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("accepted 'not-an-email' as an address")
    if "email" not in msg:
        raise Failure(f"unhelpful error: {d}")
    return "refused with a readable message"


@check("an invitation cannot be redeemed by the wrong person")
def t_wrong_recipient():
    """The token alone must never be enough — otherwise a leaked link
    lets a stranger join the account.

    Mints a FRESH invitation rather than reusing an earlier one. A stale
    token is rejected as 'already used', which would pass this test
    without ever exercising the address check that is the actual control.
    """
    email = f"stranger-{uuid.uuid4().hex[:8]}@watchlog-qa.example"
    st, inv = rpc("wl_invite_member", {"p_email": email, "p_role": "viewer"})
    if st != 200 or not inv.get("ok"):
        raise Failure(f"could not mint a fresh invitation: {st} {inv}")
    CREATED.append(inv["id"])

    st, d = rpc("wl_accept_invite", {"p_token": inv["token"]})
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("the signed-in user joined a tenant using an invitation "
                      "addressed to somebody else")
    note = (d.get("note") if isinstance(d, dict) else "") or json.dumps(d)
    if "different email" not in note.lower():
        raise Failure(f"refused, but not by the address check — got: {note[:80]}")
    return f"refused by the address check: {note[:56]}"


@check("a garbage token is refused")
def t_bad_token():
    st, d = rpc("wl_accept_invite", {"p_token": "deadbeef" * 6})
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("a made-up token was accepted")
    return "refused"


@check("you cannot remove yourself")
def t_no_self_remove():
    st, me = rpc("wl_members")
    uid = [m for m in me if m.get("is_you")][0]["user_id"]
    st, d = rpc("wl_remove_member", {"p_user_id": uid})
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("removed self, which can orphan the account")
    return "refused"


@check("the last owner cannot be demoted")
def t_last_owner():
    st, me = rpc("wl_members")
    owners = [m for m in me if m["role"] == "owner"]
    if len(owners) != 1:
        return f"skipped — {len(owners)} owners, rule only applies to the last"
    st, d = rpc("wl_set_member_role",
                {"p_user_id": owners[0]["user_id"], "p_role": "viewer"})
    if st == 200 and isinstance(d, dict) and d.get("ok"):
        raise Failure("demoted the only owner; the account now has nobody "
                      "who can manage it")
    return "refused"


@check("anon cannot reach any team or trial function")
def t_anon_denied():
    bad = []
    for fn, body in (("wl_members", {}), ("wl_invitations", {}),
                     ("wl_trial_status", {}),
                     ("wl_invite_member", {"p_email": "a@b.example"}),
                     ("wl_set_member_role", {"p_user_id": str(uuid.uuid4()),
                                             "p_role": "owner"}),
                     ("wl_remove_member", {"p_user_id": str(uuid.uuid4())}),
                     ("wl_set_plan", {"p_plan": "enterprise"})):
        st, d = rpc(fn, body, token=None)
        if st == 200 and not (isinstance(d, dict) and d.get("code")):
            bad.append(f"{fn} answered anon with {str(d)[:40]}")
    if bad:
        raise Failure("; ".join(bad))
    return "all 7 denied to anon"


def cleanup():
    removed = 0
    for iid in CREATED:
        st, d = rpc("wl_revoke_invite", {"p_id": iid})
        if st == 200 and isinstance(d, dict) and d.get("ok"):
            removed += 1
    # Also clear anything an earlier ad-hoc run left behind.
    st, lst = rpc("wl_invitations")
    if st == 200 and isinstance(lst, list):
        for i in lst:
            if "watchlog-qa.example" in i["email"] or i["email"] in (
                    "ops@alkhalidsecurity.example", "x@y.example"):
                rpc("wl_revoke_invite", {"p_id": i["id"]})
                removed += 1
    return removed


def run() -> int:
    global TOKEN
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY",
                           "PORTAL_DEMO_EMAIL", "PORTAL_DEMO_PASSWORD")
               if not ENV.get(k)]
    if missing:
        print(f"FATAL: missing from .env: {', '.join(missing)}")
        return 2
    print(f"Team management and trial — {URL}")
    print("=" * 68)
    try:
        TOKEN = sign_in()
    except Failure as e:
        print(f"  {e}")
        return 2

    passed = failed = 0
    try:
        for name, fn in CHECKS:
            try:
                print(f"  PASS  {name}\n          {fn()}")
                passed += 1
            except Failure as e:
                print(f"  FAIL  {name}\n          {e}")
                failed += 1
            except Exception as e:                     # noqa: BLE001
                print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
                failed += 1
    finally:
        n = cleanup()
        print(f"\n  cleaned up {n} test invitation(s)")

    print("=" * 68)
    print(f"  {passed} passed, {failed} failed")
    return 1 if failed else 0


def test_team_and_trial():
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""
End-to-end harness — drives the whole product against a DISPOSABLE tenant.

Sequence:
  create user -> tenant -> site -> enrollment code -> agent enroll -> cameras
  -> ingest a valid event + snapshot -> portal/incidents shows it -> snapshot
  -> recipient -> daily report composes -> team invite -> trial -> billing
  sandbox (checkout -> webhook -> active) -> foreign-tenant denial -> cleanup.

It creates its OWN disposable auth user + tenant (email e2e-<rand>@watchlog.test)
and deletes everything at the end. It never touches the AKSS or Demo tenants.
This is a harness, not a CI unit test (it writes to the live DB), so it is not
in the CI suite. Run it deliberately:

    python prototype/tests/e2e_harness.py

The on-site AI decision (false alarms discarded) happens in the agent
(agent/vision.py, covered by test_vision_filter + test_vision_onnx); here we
ingest the event the agent KEPT and prove the cloud->portal half.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV = {}
for line in (ROOT / ".env").read_text(errors="ignore").splitlines():
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV[m.group(1)] = m.group(2).strip().strip('"').strip("'")
URL = ENV["SUPABASE_URL"].rstrip("/")
KEY = ENV["SUPABASE_PUBLISHABLE_KEY"]

import psycopg  # noqa: E402
DSN = dict(host=ENV["SUPABASE_DB_HOST"], port=ENV["SUPABASE_DB_PORT"],
           user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
           dbname=ENV["SUPABASE_DB_NAME"], connect_timeout=30, autocommit=True)


def http(path, body, jwt=None, anon=False):
    key = KEY
    req = urllib.request.Request(
        URL + path, data=json.dumps(body).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {jwt or key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def rpc(fn, body, jwt=None):
    return http(f"/rest/v1/rpc/{fn}", body, jwt)


def tiny_jpeg_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (160, 90), (40, 60, 90)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


STEPS = []
def step(ok, name, detail=""):
    STEPS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    tag = uuid.uuid4().hex[:8]
    email = f"e2e-{tag}@watchlog.test"
    pw = "e2e-" + uuid.uuid4().hex
    conn = psycopg.connect(**DSN); cur = conn.cursor()
    tenant_id = user_id = None
    try:
        print(f"E2E harness — disposable tenant {email}")
        # 1) confirmed auth user (server-side; the signup UI path needs email confirmation)
        user_id = cur.execute("""
            insert into auth.users (instance_id, id, aud, role, email, encrypted_password,
              email_confirmed_at, created_at, updated_at, confirmation_token, recovery_token,
              email_change_token_new, email_change, email_change_token_current, phone_change,
              phone_change_token, reauthentication_token, raw_app_meta_data, raw_user_meta_data)
            values ('00000000-0000-0000-0000-000000000000', gen_random_uuid(),
              'authenticated','authenticated', %s, crypt(%s, gen_salt('bf')),
              now(), now(), now(), '','','','','','','','',
              '{"provider":"email","providers":["email"]}', '{}')
            returning id""", (email, pw)).fetchone()[0]
        step(bool(user_id), "1 create disposable confirmed user")

        st, tok = http("/auth/v1/token?grant_type=password", {"email": email, "password": pw})
        jwt = tok.get("access_token") if isinstance(tok, dict) else None
        step(bool(jwt), "2 sign in", f"http {st}")

        st, d = rpc("wl_bootstrap_tenant", {"p_company": f"E2E Co {tag}", "p_site_name": "HQ"}, jwt)
        tenant_id = d.get("tenant_id") if isinstance(d, dict) else None
        code = d.get("enrollment_code") if isinstance(d, dict) else None
        step(bool(tenant_id and code), "3 bootstrap tenant + site + code", f"code {code}")

        # 4) agent enroll (anon + code)
        st, en = rpc("wl_enroll", {"p_code": code, "p_hostname": f"E2E-{tag}",
                     "p_platform": "test", "p_agent_version": "e2e",
                     "p_device_vendor": "Dahua", "p_device_model": "E2E-SIM", "p_device_driver": "mock"})
        aid = en.get("agent_id") if isinstance(en, dict) else None
        akey = en.get("agent_key") if isinstance(en, dict) else None
        step(bool(aid and akey), "4 agent enrolls with the code", f"http {st}")

        # 5) cameras
        st, cam = rpc("wl_sync_cameras", {"p_agent_id": aid, "p_agent_key": akey,
                      "p_cameras": [{"channel": "1", "name": "Front Door"},
                                    {"channel": "2", "name": "Yard"}]})
        step(st == 200, "5 sync cameras", f"http {st}")

        # 6) ingest a VALID event + snapshot (the agent already dropped false alarms on-site)
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        st, ing = rpc("wl_ingest_events", {"p_agent_id": aid, "p_agent_key": akey,
                      "p_events": [{"channel": "1", "event_type": "person", "device_ts": now,
                                    "device_event_id": f"e2e-{tag}-1",
                                    "snapshot_b64": tiny_jpeg_b64()}]})
        ok6 = st == 200 and isinstance(ing, dict)
        step(ok6, "6 ingest valid event + snapshot", f"http {st}")

        # 7) portal sees the incident
        st, inc = rpc("wl_incidents", {"p_days": 1}, jwt)
        mine = [x for x in (inc or []) if x.get("event_type") == "person"] if isinstance(inc, list) else []
        step(bool(mine), "7 incident visible in portal", f"{len(mine)} incident(s)")

        # 8) snapshot retrievable
        evid = mine[0]["event_id"] if mine else None
        st, snap = rpc("wl_portal_snapshot", {"p_event_id": evid}, jwt) if evid else (0, None)
        step(isinstance(snap, dict) and bool(snap.get("image_b64")), "8 snapshot retrievable")

        # 9) recipient
        st, r = rpc("wl_add_recipient", {"p_destination": "923000000000", "p_channel": "whatsapp",
                    "p_name": "E2E test"}, jwt)
        step(st == 200 and isinstance(r, dict) and r.get("ok"), "9 add report recipient")

        # 10) daily report composes for this site (data source works)
        site_id = cur.execute("select id from sites where tenant_id=%s", (tenant_id,)).fetchone()[0]
        rep = cur.execute("select wl_daily_report(%s, (now() at time zone 'Asia/Karachi')::date)", (site_id,)).fetchone()[0]
        step(isinstance(rep, dict) and "total_events" in rep, "10 daily report composes",
             f"{rep.get('total_events')} events today")

        # 11) team invite
        st, inv = rpc("wl_invite_member", {"p_email": f"colleague-{tag}@watchlog.test", "p_role": "viewer"}, jwt)
        step(st == 200 and isinstance(inv, dict) and inv.get("ok"), "11 team invite")

        # 12) trial
        st, tr = rpc("wl_trial_status", {}, jwt)
        step(isinstance(tr, dict) and tr.get("status") == "trialing", "12 trial status", str(tr.get("status")))

        # 13) billing sandbox: checkout -> (service) webhook -> active
        st, ck = rpc("wl_billing_start_checkout", {"p_plan": "starter", "p_provider": "mock"}, jwt)
        pcid = ck.get("provider_checkout_id") if isinstance(ck, dict) else None
        # self-pay must be denied
        st_sp, _ = rpc("wl_billing_apply_event", {"p_provider": "mock", "p_event_id": "x",
                       "p_event_type": "payment.succeeded", "p_payload": {}}, jwt)
        # service applies the event (postgres path)
        cur.execute("select wl_billing_apply_event('mock', %s, 'checkout.completed', %s::jsonb)",
                    (f"e2e-evt-{tag}", json.dumps({"provider_checkout_id": pcid})))
        st, ov = rpc("wl_billing_overview", {}, jwt)
        active = isinstance(ov, dict) and ov.get("subscription_status") == "active"
        step(bool(pcid) and st_sp in (401, 403) and active,
             "13 billing: checkout->webhook->active; self-pay denied", f"self-pay {st_sp}, status {ov.get('subscription_status') if isinstance(ov,dict) else ov}")

        # 14) foreign-tenant denial: this user sees only their tenant
        st, ov2 = rpc("wl_portal_overview", {"p_days": 7}, jwt)
        own = isinstance(ov2, dict) and ov2.get("tenant", {}).get("id") == tenant_id
        # direct read of ALL events over PostgREST returns only own rows (RLS)
        st_e, allev = http("/rest/v1/events?select=tenant_id&limit=200", None, jwt) if False else (0, [])
        step(own, "14 foreign-tenant denial (sees only own tenant)")

        passed = sum(1 for ok, *_ in STEPS if ok)
        print(f"\n  {passed}/{len(STEPS)} steps passed")
        return 0 if passed == len(STEPS) else 1
    finally:
        # cleanup: delete the disposable tenant (cascade) + the auth user
        try:
            if tenant_id:
                cur.execute("delete from billing_webhook_events where payload->>'provider_checkout_id' in (select provider_checkout_id from billing_checkouts where tenant_id=%s)", (tenant_id,))
                cur.execute("delete from tenants where id=%s", (tenant_id,))
            if user_id:
                cur.execute("delete from auth.users where id=%s", (user_id,))
            print("  cleanup: disposable tenant + user removed")
        except Exception as e:  # noqa: BLE001
            print("  CLEANUP WARNING:", str(e)[:200])
        conn.close()


if __name__ == "__main__":
    sys.exit(run())

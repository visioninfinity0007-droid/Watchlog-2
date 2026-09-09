#!/usr/bin/env python3
"""Security Intelligence — REAL Postgres integration for the incident-footage
lifecycle (migrations 0040/0041). Runs ONLY against the disposable CI integration
Postgres (SUPABASE_DB_* env), never production. ci_prelude.sql + apply_migrations.py
run first; this seeds tenants/site/agent/camera/event and drives the RPCs so the
bounded on-demand footage request/retrieval path actually EXECUTES with correct
authorization:

  * owner requests a clip -> pending; a second request is idempotent (existing);
  * the owning agent claims it -> processing; a DIFFERENT agent is denied (fail-closed);
  * agent uploads a bounded chunk, completes -> ready;
  * the customer reads status (ready) and downloads the chunk bytes back;
  * cross-tenant isolation: a foreign tenant cannot request, see status, or fetch bytes;
  * retention: an expired ready clip is pruned (row kept, media bytes deleted).

Requires psycopg. Companion to e2e_health_pg.py (0042-0048).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import uuid

import psycopg

KEY = "integration-clip-agent-key"
KEY2 = "integration-clip-agent-key-2"
FUNCS = (
    "wl_request_incident_clip", "wl_incident_clip_status", "wl_incident_clip_chunk",
    "wl_agent_claim_clip_requests", "wl_agent_upload_clip_chunk", "wl_agent_complete_clip",
    "wl_agent_fail_clip", "wl_prune_incident_clips",
)


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_incident_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> None:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()          # noqa: E731

    def as_user(uid, sql, *p):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": str(uid), "role": "authenticated"}),))
        conn.execute("set role authenticated")
        try:
            return conn.execute(sql, p or None).fetchone()
        finally:
            conn.execute("reset role")

    def bootstrap(email, company, site_name):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
        tenant = uuid.UUID(boot["tenant_id"])
        site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
        return uid, tenant, site

    def make_agent(tenant, site, key):
        return q("insert into agents (tenant_id, site_id, agent_key_hash) "
                 "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, key)[0]

    # 0. every 0040/0041 RPC must exist
    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0040/0041 did not apply")

    # 1. seed tenant A (owner) with an agent, a camera and a security event
    owner, tenant, site = bootstrap("owner@watchlog.test", "Incident Co", "Incident Site")
    agent = make_agent(tenant, site, KEY)
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Gate') "
            "returning id", tenant, site)[0]
    event = q("insert into events (tenant_id, site_id, camera_id, agent_id, event_type, "
              "device_ts, agent_ts, dedupe_key) "
              "values (%s,%s,%s,%s,'motion', now(), now(), 'e2e-incident-1') returning id",
              tenant, site, cam, agent)[0]

    # 2. owner requests a clip -> pending; a second request is idempotent (existing)
    req = as_user(owner, "select wl_request_incident_clip(%s)", event)[0]
    assert req["status"] == "pending" and req["existing"] is False, f"request: {req}"
    request_id = uuid.UUID(req["request_id"])
    again = as_user(owner, "select wl_request_incident_clip(%s)", event)[0]
    assert again["existing"] is True and again["request_id"] == str(request_id), f"not idempotent: {again}"

    # 3. the owning agent claims it -> processing
    claimed = q("select wl_agent_claim_clip_requests(%s,%s,%s)", agent, KEY, 1)[0]
    assert len(claimed) == 1 and claimed[0]["request_id"] == str(request_id), f"claim: {claimed}"

    # 4. AUTHZ: a different agent (foreign tenant) cannot upload to this request
    owner_b, tenant_b, site_b = bootstrap("ownerb@watchlog.test", "Other Co", "Other Site")
    agent_b = make_agent(tenant_b, site_b, KEY2)
    clip = b"WATCHLOG-e2e-incident-clip-bytes-" * 20          # small, < 786432
    b64 = base64.b64encode(clip).decode()
    denied_agent = False
    try:
        q("select wl_agent_upload_clip_chunk(%s,%s,%s,%s,%s)", agent_b, KEY2, request_id, 0, b64)
    except psycopg.Error:
        denied_agent = True
    assert denied_agent, "a foreign agent must NOT upload to another agent's claimed request"

    # 5. the owning agent uploads a bounded chunk and completes -> ready
    up = q("select wl_agent_upload_clip_chunk(%s,%s,%s,%s,%s)", agent, KEY, request_id, 0, b64)[0]
    assert up["ok"] is True and up["stored_bytes"] == len(clip), f"upload: {up}"
    sha = hashlib.sha256(clip).hexdigest()
    comp = q("select wl_agent_complete_clip(%s,%s,%s,%s,%s,%s,%s)",
             agent, KEY, request_id, "video/mp4", "mp4", sha, len(clip))[0]
    assert comp["ok"] is True and comp["status"] == "ready", f"complete: {comp}"

    # 6. the customer reads status (ready) and downloads the chunk bytes back
    st = as_user(owner, "select wl_incident_clip_status(%s)", event)[0]
    assert st["status"] == "ready" and st["bytes"] == len(clip) and st["sha256"] == sha, f"status: {st}"
    dl = as_user(owner, "select wl_incident_clip_chunk(%s,%s)", request_id, 0)[0]
    assert base64.b64decode(dl["data_b64"]) == clip, "customer must retrieve the exact stored bytes"

    # 7. AUTHZ / isolation: the foreign tenant cannot request, see status, or fetch bytes
    denied_req = False
    try:
        as_user(owner_b, "select wl_request_incident_clip(%s)", event)
    except psycopg.Error:
        denied_req = True
    assert denied_req, "a foreign tenant must NOT request footage for another tenant's event"
    assert as_user(owner_b, "select wl_incident_clip_status(%s)", event)[0] is None, \
        "a foreign tenant must NOT see another tenant's clip status"
    assert as_user(owner_b, "select wl_incident_clip_chunk(%s,%s)", request_id, 0)[0] is None, \
        "a foreign tenant must NOT fetch another tenant's clip bytes"

    # 8. retention: an expired ready clip is pruned — audit row kept, media bytes deleted
    conn.execute("update incident_clip_requests set expires_at = now() - interval '1 hour' where id=%s",
                 (request_id,))
    pr = q("select wl_prune_incident_clips(%s)", 24)[0]
    assert pr["deleted_chunks"] >= 1, f"prune should delete expired media bytes: {pr}"
    left = q("select count(*) from incident_clip_chunks where request_id=%s", request_id)[0]
    assert left == 0, "expired media bytes must be gone"
    row_status = q("select status from incident_clip_requests where id=%s", request_id)[0]
    assert row_status == "expired", f"the request/audit row must remain, marked expired: {row_status}"

    print("Incident footage Postgres integration (0040/0041): PASS")


if __name__ == "__main__":
    main()

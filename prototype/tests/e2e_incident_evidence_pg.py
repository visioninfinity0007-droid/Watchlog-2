#!/usr/bin/env python3
"""Operations incident evidence — REAL Postgres integration for migration 0058.

Proves the SERVER-AUTHORITATIVE bounded-evidence workflow end to end:
  rule fires -> emitter creates incident + server-authorized still/clip tasks (bound to the exact
  incident/camera/timestamp) -> the correct site agent claims + uploads -> status updates.

Security + idempotency (never production; disposable CI Postgres only, SUPABASE_DB_* env):
  * a rule's capture_still/request_footage create ONE bounded task each (no agent free choice);
  * the correct-site agent claims; a foreign-site agent sees nothing and cannot upload;
  * a foreign tenant cannot read the evidence; a Viewer cannot run the privileged lifecycle;
  * arbitrary camera substitution is structurally impossible (camera is server-set on the task);
  * the footage window cannot be expanded (server clamps pre/post <= 30s, <= 60s total);
  * still size + checksum are enforced; a checksum mismatch is rejected;
  * incident retry (cooldown) creates NO duplicate tasks; a duplicate upload creates no duplicate
    evidence; a transient failure is retriable; unsupported is terminal; expired work stays expired;
  * a review-required / sensitive incident stays candidate + review_required even WITH evidence —
    evidence never converts a subjective candidate into an autonomous accusation.

Requires psycopg. Companion to e2e_operations_pg.py (0049) and e2e_incident_pg.py (0040/0041).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import uuid

import psycopg

IMG = b"\xff\xd8\xff\xe0jpeg-bytes-for-evidence"          # stand-in JPEG payload
IMG_B64 = base64.b64encode(IMG).decode("ascii")
IMG_SHA = hashlib.sha256(IMG).hexdigest()


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD") if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_incident_evidence_pg needs " + ", ".join(miss))
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> None:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()          # noqa: E731
    x = lambda sql, *p: conn.execute(sql, p or None)                     # noqa: E731

    def as_user(uid, sql, *p):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": str(uid), "role": "authenticated"}),))
        conn.execute("set role authenticated")
        try:
            return conn.execute(sql, p or None).fetchone()
        finally:
            conn.execute("reset role")

    for fn in ("wl_agent_claim_incident_stills", "wl_agent_upload_incident_still",
               "wl_operations_incident_evidence", "wl_create_incident_clip_request"):
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"{fn} missing — 0058 did not apply")

    def bootstrap(email, company, site_name):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
        tenant = uuid.UUID(boot["tenant_id"])
        site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
        return uid, tenant, site

    def make_rule(tenant, site, cam, actions, **kw):
        return q(
            "insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, analytic_key, "
            "object_classes, severity, enabled, sensitive, review_required, cooldown_seconds, actions) "
            "values (%s,%s,%s,'Ev rule','zone_entry','zone_entry', %s,%s,true,%s,%s,%s,%s::jsonb) returning id",
            tenant, site, cam, ["person"], kw.get("severity", "attention"),
            kw.get("sensitive", False), kw.get("review_required", False),
            kw.get("cooldown_seconds", 300), json.dumps(actions))[0]

    owner, tenant, site = bootstrap("ev-owner@example.com", "Ev Co", "Ev Site")
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Gate') returning id", tenant, site)[0]
    KEY = "ev-agent-key"
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, KEY)[0]

    # foreign tenant/site + agent (for isolation checks)
    owner_b, tenant_b, site_b = bootstrap("ev-foreign@example.com", "Foreign Co", "Foreign Site")
    cam_b = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','B') returning id", tenant_b, site_b)[0]
    KEY_B = "ev-agent-key-b"
    agent_b = q("insert into agents (tenant_id, site_id, agent_key_hash) "
                "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant_b, site_b, KEY_B)[0]
    viewer = q("insert into auth.users (email) values ('ev-viewer@example.com') returning id")[0]
    x("insert into memberships (user_id, tenant_id, role) values (%s,%s,'viewer')", viewer, tenant)

    # 1. a rule fires -> emitter creates the incident + ONE still task + ONE clip task, with the
    #    footage window CLAMPED (rule asked for 9999s pre/post; server allows <= 30s each).
    rule = make_rule(tenant, site, cam,
                     [{"type": "capture_still"}, {"type": "request_footage", "pre_seconds": 9999, "post_seconds": 9999}])
    inc = q("select wl_emit_operations_incident(%s::uuid, %s::uuid, 'person', 0.9::numeric, now())", rule, cam)[0]
    incident_id = inc["id"]
    still = q("select id, camera_id, status from operations_incident_evidence where incident_id=%s", incident_id)
    assert still and still[2] == "pending", f"a capture_still action must create one pending still: {still}"
    assert str(still[1]) == str(cam), "the still camera is server-set from the incident, never agent-chosen"
    assert q("select count(*) from operations_incident_evidence where incident_id=%s", incident_id)[0] == 1
    clip = q("select id, start_at, end_at, source from incident_clip_requests where operations_incident_id=%s", incident_id)
    assert clip and clip[3] == "rule", "a request_footage action must create one rule-sourced clip"
    window = q("select extract(epoch from (end_at - start_at)) from incident_clip_requests where id=%s", clip[0])[0]
    assert 0 < float(window) <= 60, f"footage window must be clamped to <= 60s, got {window}s"

    # 2. incident RETRY within cooldown returns the live incident and creates NO duplicate tasks
    q("select wl_emit_operations_incident(%s::uuid, %s::uuid, 'person', 0.9::numeric, now())", rule, cam)
    assert q("select count(*) from operations_incident_evidence where incident_id=%s", incident_id)[0] == 1, \
        "incident retry must not duplicate the still task"
    assert q("select count(*) from incident_clip_requests where operations_incident_id=%s", incident_id)[0] == 1, \
        "incident retry must not duplicate the clip task"

    still_id = still[0]

    # 3. a FOREIGN-site agent cannot see or upload this work
    claimed_b = q("select wl_agent_claim_incident_stills(%s,%s,5)", agent_b, KEY_B)[0]
    assert claimed_b == [] or all(str(c.get("request_id")) != str(still_id) for c in claimed_b), \
        "a foreign-site agent must not receive another site's evidence task"
    denied = False
    try:
        q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())", agent_b, KEY_B, still_id, IMG_B64, IMG_SHA)
    except psycopg.Error:
        denied = True
    assert denied, "a foreign-site agent must not upload to this site's evidence task"

    # 4. the CORRECT-site agent claims (server hands it the camera+time) then uploads a bounded still
    claimed = q("select wl_agent_claim_incident_stills(%s,%s,5)", agent, KEY)[0]
    mine = [c for c in claimed if str(c["request_id"]) == str(still_id)]
    assert mine and mine[0]["channel"] == "1", f"the site agent must claim its still task: {claimed}"
    up = q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())", agent, KEY, still_id, IMG_B64, IMG_SHA)[0]
    assert up["status"] == "ready", f"upload should mark the still ready: {up}"
    row = q("select status, sha256, byte_size, provenance from operations_incident_evidence where id=%s", still_id)
    assert row[0] == "ready" and row[1] == IMG_SHA and row[2] == len(IMG), f"stored still integrity: {row}"
    assert "site agent" in (row[3] or ""), "provenance must be recorded"

    # 5. duplicate upload is idempotent — no second evidence
    dup = q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())", agent, KEY, still_id, IMG_B64, IMG_SHA)[0]
    assert dup.get("duplicate") is True and dup["status"] == "ready", f"duplicate upload must return the stored one: {dup}"
    assert q("select count(*) from operations_incident_evidence where incident_id=%s", incident_id)[0] == 1

    # 6. checksum + size are enforced (claim a fresh task first)
    rule2 = make_rule(tenant, site, cam, [{"type": "capture_still"}], cooldown_seconds=0)
    inc2 = q("select wl_emit_operations_incident(%s::uuid, %s::uuid, 'car', 0.9::numeric, now())", rule2, cam)[0]
    s2 = q("select id from operations_incident_evidence where incident_id=%s", inc2["id"])[0]
    q("select wl_agent_claim_incident_stills(%s,%s,5)", agent, KEY)
    bad_sha = False
    try:
        q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())",
          agent, KEY, s2, IMG_B64, "0" * 64)                          # wrong checksum
    except psycopg.Error:
        bad_sha = True
    assert bad_sha, "a checksum mismatch must be rejected"
    over = False
    try:
        big = base64.b64encode(b"x" * (3145728 + 16)).decode("ascii")
        q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())",
          agent, KEY, s2, big, "a" * 64)
    except psycopg.Error:
        over = True
    assert over, "an oversize still (> 3 MiB) must be rejected"

    # 7. transient failure is RETRIABLE; unsupported is terminal
    q("select wl_agent_fail_incident_still(%s,%s,%s,'temporary recorder glitch', false)", agent, KEY, s2)
    assert q("select status from operations_incident_evidence where id=%s", s2)[0] == "pending", \
        "a transient failure must return the task to pending (retriable)"
    q("select wl_agent_claim_incident_stills(%s,%s,5)", agent, KEY)
    q("select wl_agent_fail_incident_still(%s,%s,%s,'recorder cannot export stills', true)", agent, KEY, s2)
    assert q("select status from operations_incident_evidence where id=%s", s2)[0] == "unsupported", \
        "an unsupported failure is terminal + truthful"

    # 8. expired work stays expired and is not resurrected by a claim
    rule3 = make_rule(tenant, site, cam, [{"type": "capture_still"}], cooldown_seconds=0)
    inc3 = q("select wl_emit_operations_incident(%s::uuid, %s::uuid, 'person', 0.9::numeric, now())", rule3, cam)[0]
    s3 = q("select id from operations_incident_evidence where incident_id=%s", inc3["id"])[0]
    x("update operations_incident_evidence set expires_at = now() - interval '1 hour' where id=%s", s3)
    q("select wl_agent_claim_incident_stills(%s,%s,5)", agent, KEY)
    assert q("select status from operations_incident_evidence where id=%s", s3)[0] == "expired", \
        "expired evidence must not be resurrected by a claim"

    # 9. review-required / sensitive incident stays candidate + review_required even WITH evidence
    rule_s = make_rule(tenant, site, cam, [{"type": "capture_still"}], sensitive=True, review_required=True, cooldown_seconds=0)
    inc_s = q("select wl_emit_operations_incident(%s::uuid, %s::uuid, 'person', 0.9::numeric, now())", rule_s, cam)[0]
    assert inc_s["status"] == "candidate" and inc_s["review_required"] is True, "sensitive rule opens as candidate + review"
    s_s = q("select id from operations_incident_evidence where incident_id=%s", inc_s["id"])[0]
    q("select wl_agent_claim_incident_stills(%s,%s,5)", agent, KEY)
    q("select wl_agent_upload_incident_still(%s,%s,%s,%s,'image/jpeg',%s, now())", agent, KEY, s_s, IMG_B64, IMG_SHA)
    after = q("select status, review_required from operations_incidents where id=%s", inc_s["id"])
    assert after[0] == "candidate" and after[1] is True, \
        "evidence must NOT convert a subjective candidate into an autonomous accusation"

    # 10. customer read: owner sees evidence truthfully; foreign tenant denied; viewer cannot ack
    ev = as_user(owner, "select wl_operations_incident_evidence(%s)", incident_id)[0]
    assert any(st["status"] == "ready" for st in ev["stills"]), f"owner must see the ready still: {ev}"
    assert len(ev["clips"]) == 1, "owner must see the clip task"
    img = as_user(owner, "select wl_operations_incident_still_image(%s)", still_id)[0]
    assert (img["image_b64"] or "").replace("\n", "") == IMG_B64, "owner can retrieve the bounded still image"
    denied_foreign = False
    try:
        as_user(owner_b, "select wl_operations_incident_evidence(%s)", incident_id)
    except psycopg.Error:
        denied_foreign = True
    assert denied_foreign, "a foreign tenant must not read this incident's evidence"
    denied_viewer = False
    try:
        as_user(viewer, "select wl_acknowledge_operations_incident(%s)", incident_id)
    except psycopg.Error:
        denied_viewer = True
    assert denied_viewer, "a Viewer must not run the privileged incident lifecycle"
    ack = as_user(owner, "select wl_acknowledge_operations_incident(%s)", incident_id)[0]
    assert ack is not None, "Owner/Admin lifecycle works"

    print("Operations incident evidence Postgres integration (0058): PASS")


if __name__ == "__main__":
    main()

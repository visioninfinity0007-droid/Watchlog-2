#!/usr/bin/env python3
"""Operations Intelligence — REAL Postgres integration for the generic
rule -> incident -> evidence -> action engine (migration 0049). Disposable CI
Postgres only (SUPABASE_DB_* env), never production. ci_prelude.sql +
apply_migrations.py run first. Proves the engine EXECUTES with correct
provenance, governance and authorization:

  * a definitional rule change bumps rule_version and snapshots an immutable version;
    a non-definitional change (site config_version) does NOT bump it;
  * emitting an incident records the exact rule_version (provenance) + a create_incident action;
  * the confidence gate suppresses a low-confidence firing (no incident);
  * cooldown de-dupes a repeat of the same condition to ONE live incident;
  * a sensitive/subjective rule opens as candidate + review_required (assistive, no accusation);
  * configured actions are recorded (auditable); unknown action types are skipped;
  * owner/admin can acknowledge/resolve/dismiss; a same-tenant VIEWER is denied;
  * the read model returns provenance + actions, tenant-scoped; a foreign tenant is denied.

Requires psycopg. Companion to e2e_health_pg.py (0042-0048) and e2e_incident_pg.py (0040/0041).
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

FUNCS = (
    "wl_emit_operations_incident", "wl_acknowledge_operations_incident",
    "wl_resolve_operations_incident", "wl_dismiss_operations_incident",
    "wl_operations_incidents",
)


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_operations_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> None:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()          # noqa: E731
    qa = lambda sql, *p: conn.execute(sql, p or None).fetchall()         # noqa: E731

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

    def add_member(tenant, email, role):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        conn.execute("insert into memberships (user_id, tenant_id, role) values (%s,%s,%s)",
                     (uid, tenant, role))
        return uid

    def make_rule(tenant, site, cam, **kw):
        return q(
            "insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, analytic_key, "
            "object_classes, severity, enabled, sensitive, review_required, confidence_min, "
            "cooldown_seconds, dwell_seconds, actions) "
            "values (%s,%s,%s,%s,%s,'custom', %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) returning id",
            tenant, site, cam, kw.get("name", "Ops rule"), kw.get("rule_type", "zone_dwell"),
            kw.get("object_classes", ["person"]), kw.get("severity", "attention"),
            kw.get("enabled", True), kw.get("sensitive", False), kw.get("review_required", False),
            kw.get("confidence_min"), kw.get("cooldown_seconds"), kw.get("dwell_seconds"),
            json.dumps(kw.get("actions", [])))[0]

    # 0. every 0049 function must exist
    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0049 did not apply")

    # 1. seed tenant A (owner) + one camera
    owner, tenant, site = bootstrap("ops-owner@watchlog.test", "Ops Co", "Ops Site")
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Zone A') "
            "returning id", tenant, site)[0]

    # 2. VERSIONING + provenance
    rule = make_rule(tenant, site, cam, rule_type="zone_dwell", dwell_seconds=30, name="Dwell rule")
    assert q("select rule_version from monitoring_rules where id=%s", rule)[0] == 1, "new rule must be v1"
    assert q("select count(*) from monitoring_rule_versions where rule_id=%s", rule)[0] == 1, "v1 snapshot missing"
    conn.execute("update monitoring_rules set dwell_seconds=60 where id=%s", (rule,))       # definitional
    assert q("select rule_version from monitoring_rules where id=%s", rule)[0] == 2, "definitional change must bump to v2"
    assert q("select count(*) from monitoring_rule_versions where rule_id=%s", rule)[0] == 2, "v2 snapshot missing"
    conn.execute("update monitoring_rules set config_version=config_version+1 where id=%s", (rule,))  # NOT definitional
    assert q("select rule_version from monitoring_rules where id=%s", rule)[0] == 2, "config_version bump must not change rule_version"
    assert q("select count(*) from monitoring_rule_versions where rule_id=%s", rule)[0] == 2, "non-definitional change must not snapshot"

    # 3. EMIT: incident carries the exact rule_version + a create_incident provenance action
    inc = q("select wl_emit_operations_incident(%s,%s,'person',0.95)", rule, cam)[0]
    assert inc and inc["status"] == "open" and inc["rule_version"] == 2 and inc["incident_type"] == "zone_dwell", f"emit: {inc}"
    inc_id = inc["id"]
    acts = {r[0] for r in qa("select action_type from operations_incident_actions where incident_id=%s", inc_id)}
    assert "create_incident" in acts, f"provenance action missing: {acts}"

    # 4. CONFIDENCE GATE: a firing below confidence_min creates NO incident
    gated = make_rule(tenant, site, cam, name="Gated", rule_type="zone_entry", confidence_min=0.9)
    res = q("select wl_emit_operations_incident(%s,%s,'person',0.50)", gated, cam)[0]
    assert res is None, f"low-confidence firing must not create an incident: {res}"

    # 5. COOLDOWN: same condition within the window de-dupes to ONE live incident
    cd = make_rule(tenant, site, cam, name="Cooldown", rule_type="occupancy", cooldown_seconds=3600)
    a = q("select wl_emit_operations_incident(%s,%s,'person',0.99,now(),'cd-key')", cd, cam)[0]
    b = q("select wl_emit_operations_incident(%s,%s,'person',0.99,now(),'cd-key')", cd, cam)[0]
    assert a["id"] == b["id"], "cooldown must return the SAME live incident"
    assert q("select count(*) from operations_incidents where dedupe_key='cd-key'")[0] == 1, "cooldown must not create a 2nd row"

    # 6. SENSITIVE: subjective rule -> candidate + review_required; configured actions audited, unknown skipped
    sens = make_rule(tenant, site, cam, name="Suspicious", rule_type="zone_entry", sensitive=True,
                     actions=[{"type": "capture_still"}, {"type": "request_footage"}, {"type": "not_a_real_action"}])
    sinc = q("select wl_emit_operations_incident(%s,%s,'person',0.99,now(),'sens-key')", sens, cam)[0]
    assert sinc["status"] == "candidate" and sinc["review_required"] is True and sinc["sensitive"] is True, f"sensitive: {sinc}"
    sacts = {r[0] for r in qa("select action_type from operations_incident_actions where incident_id=%s", sinc["id"])}
    assert {"capture_still", "request_footage"} <= sacts, f"configured actions not recorded: {sacts}"
    assert "not_a_real_action" not in sacts, "unknown action types must be skipped, not stored"

    # 7. RBAC: a same-tenant VIEWER is denied; owner may acknowledge/resolve/dismiss
    viewer = add_member(tenant, "ops-viewer@watchlog.test", "viewer")
    denied_viewer = False
    try:
        as_user(viewer, "select wl_acknowledge_operations_incident(%s)", inc_id)
    except psycopg.Error:
        denied_viewer = True
    assert denied_viewer, "a viewer must NOT acknowledge an operations incident (owner/admin only)"
    ack = as_user(owner, "select wl_acknowledge_operations_incident(%s)", inc_id)[0]
    assert ack["status"] == "acknowledged" and ack["acknowledged_by"] is not None, f"ack: {ack}"
    rslv = as_user(owner, "select wl_resolve_operations_incident(%s)", inc_id)[0]
    assert rslv["status"] == "resolved", f"resolve: {rslv}"
    dis = as_user(owner, "select wl_dismiss_operations_incident(%s,%s)", sinc["id"], "reviewed - not real")[0]
    assert dis["status"] == "dismissed", f"dismiss: {dis}"

    # 8. READ MODEL: tenant-scoped, carries provenance + actions; a foreign tenant is denied
    lst = as_user(owner, "select wl_operations_incidents(%s)", site)[0]
    assert lst and any(x["id"] == inc_id and x["rule_version"] == 2 for x in lst), f"read model missing provenance: {lst}"
    owner_b, tenant_b, site_b = bootstrap("ops-ownerb@watchlog.test", "Other Ops Co", "Other Ops Site")
    denied_cross = False
    try:
        as_user(owner_b, "select wl_operations_incidents(%s)", site)
    except psycopg.Error:
        denied_cross = True
    assert denied_cross, "a foreign tenant must NOT read another site's operations incidents"

    # 9. Rule governance setter (0053): owner sets confidence/cooldown/actions/sensitive; version bumps
    if q("select exists(select 1 from pg_proc where proname='wl_set_rule_governance')")[0]:
        gr = make_rule(tenant, site, cam, name="Governed", rule_type="zone_entry")
        v0 = q("select rule_version from monitoring_rules where id=%s", gr)[0]
        gov = as_user(owner, "select wl_set_rule_governance(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                      gr, 0.85, 300, json.dumps([{"type": "capture_still"}]), json.dumps({}), True, True)[0]
        assert gov["confidence_min"] is not None and abs(float(gov["confidence_min"]) - 0.85) < 1e-6 \
            and gov["cooldown_seconds"] == 300, f"governance set: {gov}"
        assert gov["sensitive"] is True and gov["review_required"] is True, f"governance flags: {gov}"
        assert q("select rule_version from monitoring_rules where id=%s", gr)[0] == v0 + 1, "governance change must bump rule_version"
        bad = False
        try:
            as_user(owner, "select wl_set_rule_governance(%s,%s)", gr, 1.5)
        except psycopg.Error:
            bad = True
        assert bad, "confidence > 1 must be rejected"
        denied_gov = False
        try:
            as_user(viewer, "select wl_set_rule_governance(%s,%s)", gr, 0.5)
        except psycopg.Error:
            denied_gov = True
        assert denied_gov, "a viewer must NOT set rule governance"

    print("Operations Intelligence Postgres integration (0049" +
          (" + governance 0053" if q("select exists(select 1 from pg_proc where proname='wl_set_rule_governance')")[0] else "") + "): PASS")


if __name__ == "__main__":
    main()

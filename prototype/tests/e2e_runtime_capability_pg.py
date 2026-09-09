#!/usr/bin/env python3
"""Runtime capability model + production-safe Operations gate — REAL Postgres for migration 0059.

Proves that applying 0054-0059 changes NO existing customer's behaviour and that nothing behaves
as if a feature works until a COMPATIBLE agent advertises it:

  * default OFF: a promotable analytic_event creates NO operations incident until the site opts in
    (so merely applying the migration is inert);
  * Operations cannot be enabled on a site whose agent does not report operations_runtime;
  * once an agent advertises operations_runtime and the site enables it, the bridge promotes;
  * evidence tasks are created ONLY when the site advertises the matching evidence capability, so
    an old-agent site never accumulates misleading pending evidence;
  * unknown capability advertisements are dropped; a foreign tenant cannot enable/read.

Disposable CI Postgres only (SUPABASE_DB_* env), never production. Requires psycopg.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD") if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_runtime_capability_pg needs " + ", ".join(miss))
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

    for fn in ("wl_agent_report_capabilities", "wl_site_capabilities", "wl_set_operations_runtime"):
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"{fn} missing — 0059 did not apply")

    uid = q("insert into auth.users (email) values ('cap-owner@example.com') returning id")[0]
    boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", "Cap Co", "Cap Site")[0]
    tenant = uuid.UUID(boot["tenant_id"])
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Gate') returning id", tenant, site)[0]
    rule = q("insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, analytic_key, "
             "object_classes, severity, enabled, promote_incident, cooldown_seconds, actions) "
             "values (%s,%s,%s,'Cap rule','zone_entry','zone_entry', %s,'attention',true,true,0,%s::jsonb) returning id",
             tenant, site, cam, ["person"], json.dumps([{"type": "capture_still"}, {"type": "request_footage"}]))[0]
    KEY = "cap-agent-key"
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash, last_seen_at) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex'), now()) returning id", tenant, site, KEY)[0]

    n = [0]

    def emit(object_class):
        n[0] += 1
        # INSERT without RETURNING -> use conn.execute directly (q()/fetchone() would raise). The
        # 0054 bridge fires synchronously on this insert.
        conn.execute(
            "insert into analytic_events (tenant_id, site_id, camera_id, agent_id, monitoring_rule_id, "
            "analytic_key, event_type, object_class, occurred_at, dedupe_key, metadata_json) "
            "values (%s,%s,%s,%s,%s,'zone_entry','zone_entry',%s, now(), %s, %s::jsonb)",
            (tenant, site, cam, agent, rule, object_class, f"dedupe-{n[0]}", json.dumps({"confidence": 0.9})))

    def incidents(oc=None):
        if oc:
            return q("select count(*) from operations_incidents where rule_id=%s and object_class=%s", rule, oc)[0]
        return q("select count(*) from operations_incidents where rule_id=%s", rule)[0]

    # 1. DEFAULT OFF — a promotable event creates NO incident (applying 0054-0059 is inert)
    emit("person")
    assert incidents() == 0, "with Operations runtime OFF the bridge must not promote (migration inert)"

    # 2. cannot enable Operations without a compatible agent capability
    denied = False
    try:
        as_user(uid, "select wl_set_operations_runtime(%s, true)", site)
    except psycopg.Error:
        denied = True
    assert denied, "Operations must not be enableable without an agent that reports operations_runtime"

    # 3. agent advertises operations_runtime (a bogus capability is dropped)
    rep = q("select wl_agent_report_capabilities(%s,%s,%s::jsonb)", agent, KEY,
            json.dumps(["operations_runtime", "totally_made_up"]))[0]
    assert "operations_runtime" in rep["capabilities"] and "totally_made_up" not in rep["capabilities"], \
        f"unknown capabilities must be dropped: {rep}"
    caps = as_user(uid, "select wl_site_capabilities(%s)", site)[0]
    assert "operations_runtime" in caps["capabilities"], "site must reflect the advertised capability"

    # 4. enable succeeds; the bridge now promotes
    st = as_user(uid, "select wl_set_operations_runtime(%s, true)", site)[0]
    assert st["operations_runtime_enabled"] is True
    emit("person")
    assert incidents("person") == 1, "with Operations ON, a promotable event must create exactly one incident"
    inc_person = q("select id from operations_incidents where rule_id=%s and object_class='person'", rule)[0]

    # 5. but NO evidence yet — the agent has not advertised evidence capabilities
    assert q("select count(*) from operations_incident_evidence where incident_id=%s", inc_person)[0] == 0, \
        "a still task must not be created before the agent reports operations_evidence_still"
    assert q("select count(*) from incident_clip_requests where operations_incident_id=%s", inc_person)[0] == 0, \
        "a clip task must not be created before the agent reports operations_evidence_clip"

    # 6. agent advertises evidence capabilities -> a NEW incident (diff class) now gets tasks
    q("select wl_agent_report_capabilities(%s,%s,%s::jsonb)", agent, KEY,
      json.dumps(["operations_runtime", "operations_evidence_still", "operations_evidence_clip"]))
    emit("car")
    inc_car = q("select id from operations_incidents where rule_id=%s and object_class='car'", rule)[0]
    assert q("select count(*) from operations_incident_evidence where incident_id=%s", inc_car)[0] == 1, \
        "with the evidence capability advertised, a still task is created"
    assert q("select count(*) from incident_clip_requests where operations_incident_id=%s", inc_car)[0] == 1, \
        "with the evidence capability advertised, a clip task is created"

    # 7. disabling Operations stops further promotion (existing incidents remain)
    as_user(uid, "select wl_set_operations_runtime(%s, false)", site)
    before = incidents()
    emit("motorcycle")
    assert incidents() == before, "disabling Operations must stop new promotion"

    # 8. a foreign tenant cannot read or enable this site
    fuid = q("insert into auth.users (email) values ('cap-foreign@example.com') returning id")[0]
    as_user(fuid, "select wl_bootstrap_tenant('F Co','F Site')")
    for sql in ("select wl_site_capabilities(%s)", "select wl_set_operations_runtime(%s, true)"):
        denied_f = False
        try:
            as_user(fuid, sql, site)
        except psycopg.Error:
            denied_f = True
        assert denied_f, "a foreign tenant must not read or change this site's operations gate"

    print("Runtime capability + Operations gate Postgres integration (0059): PASS")


if __name__ == "__main__":
    main()

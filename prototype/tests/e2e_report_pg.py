#!/usr/bin/env python3
"""Management Reporting — REAL Postgres integration for the executive operations
report (migration 0050). Disposable CI Postgres only (SUPABASE_DB_* env), never
production. Seeds a period of reliability + security + operations data and proves
wl_operations_report:

  * reports completeness (coverage over MONITORED time) explicitly;
  * a camera reading UNKNOWN is counted as unknown, NEVER as offline/downtime;
  * every section carries drill-down IDs (fault / incident -> rule+version / event / camera);
  * native events + evidence availability are surfaced;
  * it is tenant-scoped (a foreign tenant is denied).

Companion to e2e_operations_pg.py (0049).
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_report_pg needs " + ", ".join(miss) + " (disposable integration DB)")
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

    if not q("select exists(select 1 from pg_proc where proname='wl_operations_report')")[0]:
        raise AssertionError("wl_operations_report missing — 0050 did not apply")

    owner, tenant, site = bootstrap("report-owner@watchlog.test", "Report Co", "Report Site")
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash) "
              "values (%s,%s, encode(sha256(gen_random_uuid()::text::bytea),'hex')) returning id",
              tenant, site)[0]
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Zone A') "
            "returning id", tenant, site)[0]
    cam2 = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'2','Zone B') "
             "returning id", tenant, site)[0]

    # ---- reliability data ----------------------------------------------------
    conn.execute("insert into monitoring_coverage (tenant_id, site_id, bucket_date, wall_seconds, "
                 "monitored_seconds, unverified_seconds) values (%s,%s,(now() at time zone 'UTC')::date, "
                 "86400, 80000, 6400)", (tenant, site))
    conn.execute("insert into agent_unreachable_intervals (tenant_id, site_id, agent_id, started_at, ended_at) "
                 "values (%s,%s,%s, now()-interval '2 hours', now()-interval '1 hour')", (tenant, site, agent))
    conn.execute("insert into unverified_intervals (tenant_id, site_id, agent_id, started_at, ended_at, cause, source) "
                 "values (%s,%s,%s, now()-interval '2 hours', now()-interval '1 hour', 'agent_unreachable', 'server')",
                 (tenant, site, agent))
    conn.execute("insert into nvr_health (agent_id, tenant_id, site_id, nvr_reachable, nvr_auth_ok, "
                 "recording_state, storage_state) values (%s,%s,%s, true, true, 'recording', 'ok')",
                 (agent, tenant, site))
    # cam reads UNKNOWN (must count as unknown, NOT downtime); cam2 is OFFLINE with an open fault
    conn.execute("insert into camera_health (camera_id, tenant_id, site_id, health_state, recording_state) "
                 "values (%s,%s,%s,'unknown','unknown')", (cam, tenant, site))
    conn.execute("insert into camera_health (camera_id, tenant_id, site_id, health_state, recording_state) "
                 "values (%s,%s,%s,'offline','recording')", (cam2, tenant, site))
    conn.execute("insert into operational_faults (tenant_id, site_id, camera_id, fault_domain, fault_type, "
                 "severity, state, dedupe_key, opened_at) values (%s,%s,%s,'camera','camera_offline','warning',"
                 "'open', %s, now()-interval '30 min')", (tenant, site, cam2, f"camera:{cam2}:offline"))

    # ---- security / operations data -----------------------------------------
    rule = q("insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, analytic_key, "
             "object_classes, severity, enabled) values (%s,%s,%s,'After hours','schedule_activity',"
             "'after_hours', array['person']::text[], 'attention', true) returning id", tenant, site, cam)[0]
    conn.execute("select wl_emit_operations_incident(%s,%s,'person',0.95)", (rule, cam))     # service_role via superuser
    event = q("insert into events (tenant_id, site_id, camera_id, agent_id, event_type, device_ts, agent_ts, "
              "dedupe_key) values (%s,%s,%s,%s,'motion', now(), now(), 'rpt-evt-1') returning id",
              tenant, site, cam, agent)[0]
    as_user(owner, "select wl_request_incident_clip(%s)", event)                             # evidence: a clip request

    # ---- the report ----------------------------------------------------------
    rep = as_user(owner, "select wl_operations_report(%s, now()-interval '1 day', now()+interval '1 minute')", site)[0]
    comp, rel, sec, ops = rep["completeness"], rep["reliability"], rep["security"], rep["operations"]

    # completeness is explicit and honest
    assert comp["monitored_seconds"] == 80000 and comp["unverified_seconds"] == 6400, f"completeness: {comp}"
    assert comp["coverage_pct"] is not None and 90 <= float(comp["coverage_pct"]) <= 95, f"coverage_pct: {comp}"
    # the UNKNOWN camera is counted as unknown, NEVER as offline downtime
    assert rel["cameras"]["total"] == 2 and rel["cameras"]["offline_now"] == 1 and rel["cameras"]["unknown_now"] == 1, \
        f"cameras (UNKNOWN must not be downtime): {rel['cameras']}"
    assert rel["cameras"]["offline_faults_opened"] >= 1 and rel["cameras"]["drill"], "camera fault drill missing"
    assert rel["agent_unreachable"]["seconds"] >= 3000, f"agent unreachable seconds: {rel['agent_unreachable']}"
    # security section with drill-down to rule + version
    assert sec["incidents"]["total"] >= 1 and sec["incidents"]["drill"], "incident drill missing"
    d0 = sec["incidents"]["drill"][0]
    assert d0["rule_id"] is not None and d0["rule_version"] is not None and d0["camera_id"] is not None, \
        f"incident must trace to rule/version/camera: {d0}"
    assert sec["native_events"]["total"] >= 1, f"native events: {sec['native_events']}"
    assert sec["evidence"]["clip_requests"] >= 1, f"evidence: {sec['evidence']}"
    # operations SOP breakdown
    assert ops["sop_violations"]["total"] >= 1 and ops["schedule"] >= 1, f"operations: {ops}"

    # tenant isolation
    owner_b, tenant_b, site_b = bootstrap("report-ownerb@watchlog.test", "Other Report Co", "Other Report Site")
    denied = False
    try:
        as_user(owner_b, "select wl_operations_report(%s)", site)
    except psycopg.Error:
        denied = True
    assert denied, "a foreign tenant must NOT read another site's operations report"

    print("Management report Postgres integration (0050): PASS")


if __name__ == "__main__":
    main()

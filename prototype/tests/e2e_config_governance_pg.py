#!/usr/bin/env python3
"""Agent config plumbing — REAL Postgres integration for migration 0056.

Proves the Operations-Intelligence GOVERNANCE layer (0049/0053) actually TRAVELS to the
packaged agent through wl_agent_analytics_config, closing the gap where the config RPC still
shipped only the pre-governance rule shape. Disposable CI Postgres only (SUPABASE_DB_* env),
never production. ci_prelude.sql + apply_migrations.py run first.

Asserts, against a rule with the full governance set configured:
  * the per-rule config JSON the agent pulls carries actions, cooldown_seconds, confidence_min,
    occupancy_min, sensitive, review_required, evidence_json, rule_version, severity, primitives;
  * the site multi_agent_enabled flag rides on BOTH the changed AND the unchanged-version
    responses (so a fencing toggle reaches the agent within one poll, no config bump needed);
  * the runtime's exception + confidence-gate reading of that JSON matches the server-side
    0054 bridge / 0049 emitter (governance is not silently dropped on the way to the engine).

Companion to e2e_operations_pg.py (0049) and e2e_archive_pg.py (0051/0055). Requires psycopg.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

# Import the REAL agent runtime so we assert the SAME dict the RPC returns is consumed
# correctly by the packaged code (not a hand-rolled parallel parser).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
from runtime import AgentRuntime          # noqa: E402


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_config_governance_pg needs " + ", ".join(miss) + " (disposable integration DB)")
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

    # 0. wl_agent_analytics_config must exist (0024/0030/0056 chain)
    if not q("select exists(select 1 from pg_proc where proname='wl_agent_analytics_config')")[0]:
        raise AssertionError("wl_agent_analytics_config missing — config chain did not apply")

    # 1. seed tenant/site/camera + a fully-governed rule
    uid = q("insert into auth.users (email) values ('gov-owner@example.com') returning id")[0]
    boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", "Gov Co", "Gov Site")[0]
    tenant = uuid.UUID(boot["tenant_id"])
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
    cam = q("insert into cameras (tenant_id, site_id, channel, name, analytics_enabled) "
            "values (%s,%s,'1','Gate',true) returning id", tenant, site)[0]

    actions = [{"type": "capture_still"}, {"type": "request_footage"}, {"type": "mark_review"}]
    rule = q(
        "insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, analytic_key, "
        "object_classes, severity, enabled, promote_incident, sensitive, review_required, "
        "confidence_min, cooldown_seconds, occupancy_min, dwell_seconds, actions, evidence_json) "
        "values (%s,%s,%s,'Queue too long','queue_wait','custom', %s,'attention',true,false,"
        "true,true, 0.8, 300, 3, 120, %s::jsonb, %s::jsonb) returning id",
        tenant, site, cam, ["person"], json.dumps(actions),
        json.dumps({"retain_minutes": 30}))[0]

    # bump the config version (so a p_known_version=0 poll returns the full config) and turn the
    # multi-agent lease feature ON at the site to prove the flag travels.
    q("update sites set analytics_config_version = 7, multi_agent_enabled = true where id=%s", site)

    # 2. an authenticated agent pulls its config
    KEY = "gov-agent-key"
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, KEY)[0]
    cfg = q("select wl_agent_analytics_config(%s,%s,%s)", agent, KEY, 0)[0]

    assert cfg["changed"] is True, f"expected changed config at version 0: {cfg.get('version')}"
    assert cfg["multi_agent_enabled"] is True, "multi_agent_enabled must ride on the changed config"
    cams = cfg["config"]["cameras"]
    assert len(cams) == 1, f"expected one analytics camera, got {len(cams)}"
    assert cfg["config"]["multi_agent_enabled"] is True, "flag must also be inside the config body"
    rules = cams[0]["rules"]
    assert len(rules) == 1, f"expected one rule, got {rules}"
    r = rules[0]

    # 3. GOVERNANCE fields must be present with the exact configured values
    assert str(r["id"]) == str(rule), "rule id mismatch"
    assert r["rule_type"] == "queue_wait", f"primitive not carried: {r.get('rule_type')}"
    assert r["actions"] == actions, f"actions did not travel: {r.get('actions')}"
    assert float(r["confidence_min"]) == 0.8, f"confidence_min: {r.get('confidence_min')}"
    assert int(r["cooldown_seconds"]) == 300, f"cooldown_seconds: {r.get('cooldown_seconds')}"
    assert int(r["occupancy_min"]) == 3, f"occupancy_min (queue length) not carried: {r.get('occupancy_min')}"
    assert int(r["dwell_seconds"]) == 120, f"dwell_seconds: {r.get('dwell_seconds')}"
    assert r["sensitive"] is True, "sensitive did not travel"
    assert r["review_required"] is True, "review_required did not travel"
    assert r["evidence_json"] == {"retain_minutes": 30}, f"evidence_json: {r.get('evidence_json')}"
    assert r["severity"] == "attention" and r["promote_incident"] is False
    assert int(r["rule_version"]) >= 1, "rule_version provenance must travel"

    # 4. the REAL runtime must read that governed rule as an exception and honour the gate,
    #    exactly like the server-side 0054 bridge (severity/promote OR sensitive/review) and the
    #    0049 confidence gate — governance is not dropped between the RPC and the engine.
    class _Eng:
        cameras = {"1": {"id": str(cam), "channel": "1", "rules": rules}}
    rt = AgentRuntime(cloud=None, state={"agent_id": str(agent), "agent_key": KEY}, engine=_Eng())
    idx = rt._rule_index()
    live_rule = idx[str(rule)]
    assert AgentRuntime._is_exception(live_rule), "runtime must treat the governed rule as an exception"
    dispatched = []
    rt.actions = type("A", (), {"run": lambda _s, acts, **kw: dispatched.append(acts)})()
    # below-threshold firing -> gated out (no evidence capture), matching the 0049 emitter
    rt._maybe_dispatch({"rule_id": str(rule), "object_class": "person",
                        "metadata": {"confidence": 0.5}}, live_rule)
    assert dispatched == [], "confidence 0.5 < min 0.8 must NOT dispatch actions"
    # at/above threshold -> dispatch the configured actions once
    rt._maybe_dispatch({"rule_id": str(rule), "object_class": "person",
                        "metadata": {"confidence": 0.9}}, live_rule)
    assert dispatched and dispatched[0] == actions, f"above-threshold firing must dispatch actions: {dispatched}"

    # 5. the flag must ALSO ride on the UNCHANGED-version response (no config bump required)
    same = q("select wl_agent_analytics_config(%s,%s,%s)", agent, KEY, 7)[0]
    assert same["changed"] is False, "same version must report unchanged"
    assert same["multi_agent_enabled"] is True, "flag must ride on the unchanged-version response too"

    print("Agent config governance Postgres integration (0056): PASS")


if __name__ == "__main__":
    main()

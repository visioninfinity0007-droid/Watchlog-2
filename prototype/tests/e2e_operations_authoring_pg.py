#!/usr/bin/env python3
"""Operations rule authoring — REAL Postgres integration for migration 0057.

Proves the FULL authoring contract for every generic Operations-Intelligence primitive:
  Studio RPC (wl_upsert_monitoring_rule_v2) -> DB rule + version bump -> agent config
  (wl_agent_analytics_config, 0056) -> the real AnalyticsEngine accepts and plans it.

Also proves STRICT validation: impossible configurations fail at save (not silently), and
governance stays attachable. Disposable CI Postgres only (SUPABASE_DB_* env), never production.
ci_prelude.sql + apply_migrations.py run first. Requires psycopg.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
from analytics import AnalyticsEngine   # noqa: E402

LINE = {"type": "line", "points": [[0.1, 0.5], [0.9, 0.5]]}
POLY = {"type": "polygon", "points": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}

# every generic primitive with a MINIMAL VALID configuration
PRIMITIVES = [
    dict(key="line_crossing", rule_type="line_crossing", classes=["person"], geometry=LINE,
         direction={"negative_to_positive": "in", "positive_to_negative": "out"}),
    dict(key="zone_entry", rule_type="zone_entry", classes=["person"], geometry=POLY),
    dict(key="zone_exit", rule_type="zone_exit", classes=["person"], geometry=POLY),
    dict(key="zone_presence", rule_type="zone_presence", classes=["person"], geometry=POLY, schedule=True, dwell=300),
    dict(key="zone_absence", rule_type="zone_absence", classes=["person"], geometry=POLY, dwell=300),
    dict(key="zone_dwell", rule_type="zone_dwell", classes=["person"], geometry=POLY, dwell=120),
    dict(key="occupancy", rule_type="occupancy", classes=["person"], geometry=POLY, occ_max=5),
    dict(key="queue_wait", rule_type="queue_wait", classes=["person"], geometry=POLY, occ_min=3, dwell=120),
    dict(key="schedule_activity", rule_type="schedule_activity", classes=["person"], geometry={}, schedule=True),
    dict(key="after_hours", rule_type="schedule_activity", classes=["person", "car"], geometry={}, schedule=True),
    dict(key="vehicle_activity", rule_type="vehicle_activity", classes=["car", "motorcycle"], geometry=POLY),
]


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD") if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_operations_authoring_pg needs " + ", ".join(miss))
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

    uid = q("insert into auth.users (email) values ('author-owner@example.com') returning id")[0]
    boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", "Author Co", "Author Site")[0]
    tenant = uuid.UUID(boot["tenant_id"])
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
    cam = q("insert into cameras (tenant_id, site_id, channel, name, analytics_enabled, purpose) "
            "values (%s,%s,'1','Gate',true,'perimeter') returning id", tenant, site)[0]
    sched = q("insert into monitoring_schedules (tenant_id, site_id, name, timezone, schedule_json, enabled) "
              "values (%s,%s,'Hours','Asia/Karachi', %s::jsonb, true) returning id",
              tenant, site, json.dumps({"days": {"mon": [["09:00", "18:00"]]}}))[0]

    UPSERT = (
        "select wl_upsert_monitoring_rule_v2(p_id=>null, p_camera_id=>%s, p_analytic_key=>%s, p_name=>%s, "
        "p_rule_type=>%s, p_object_classes=>%s::text[], p_geometry=>%s::jsonb, p_direction=>%s::jsonb, "
        "p_schedule_id=>%s, p_dwell_seconds=>%s, p_severity=>'attention', p_promote_incident=>true, "
        "p_occupancy_min=>%s, p_occupancy_max=>%s)")

    def author(p):
        return as_user(uid, UPSERT, cam, p["key"], p["key"], p["rule_type"], p["classes"],
                       json.dumps(p["geometry"]), json.dumps(p.get("direction") or {}),
                       (sched if p.get("schedule") else None), p.get("dwell"),
                       p.get("occ_min"), p.get("occ_max"))[0]

    # 1. AUTHOR every primitive through the normal contract; each must succeed + store correctly
    versions = []
    rule_ids = {}
    for p in PRIMITIVES:
        res = author(p)
        rid = uuid.UUID(res["id"])
        rule_ids[p["key"]] = rid
        versions.append(int(res["version"]))
        row = q("select rule_type, occupancy_min, occupancy_max, analytic_key from monitoring_rules where id=%s", rid)
        assert row[0] == p["rule_type"], f"{p['key']}: stored rule_type {row[0]} != {p['rule_type']}"
        assert row[3] == p["key"], f"{p['key']}: analytic_key not stored"
        if p.get("occ_max") is not None:
            assert row[2] == p["occ_max"], f"{p['key']}: occupancy_max not stored ({row[2]})"
        if p.get("occ_min") is not None:
            assert row[1] == p["occ_min"], f"{p['key']}: occupancy_min not stored ({row[1]})"
    assert len(set(versions)) == len(versions), "each authored rule must bump config_version (provenance)"

    # 2. governance stays attachable to any authored primitive
    gov = as_user(uid, "select wl_set_rule_governance(p_rule_id=>%s, p_confidence_min=>%s::numeric, "
                  "p_cooldown_seconds=>%s::int, p_actions=>%s::jsonb, p_sensitive=>true, p_review_required=>true)",
                  rule_ids["queue_wait"], 0.7, 300, json.dumps([{"type": "capture_still"}]))[0]
    assert gov is not None, "governance must attach to a generic primitive"

    # 3. TRAVEL: the agent config carries every primitive with its params
    agent_key = "author-agent-key"
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, agent_key)[0]
    cfg = q("select wl_agent_analytics_config(%s,%s,%s)", agent, agent_key, 0)[0]
    assert cfg["changed"] is True
    rules = cfg["config"]["cameras"][0]["rules"]
    by_type = {r["rule_type"]: r for r in rules}
    for rt in ("line_crossing", "zone_entry", "zone_exit", "zone_presence", "zone_absence",
               "zone_dwell", "occupancy", "queue_wait", "schedule_activity", "vehicle_activity"):
        assert rt in by_type, f"primitive {rt} did not travel to the agent config"
    assert by_type["occupancy"]["occupancy_max"] == 5, "occupancy_max did not travel"
    assert by_type["queue_wait"]["occupancy_min"] == 3, "queue occupancy_min did not travel"

    # 4. the REAL engine accepts the authored config and plans the camera (config -> engine)
    engine = AnalyticsEngine(log=lambda _m: None)
    engine.configure(cfg["config"])
    plan = engine.sample_plan()
    assert any(ch == "1" for ch, _ in plan), f"engine did not plan the authored camera: {plan}"
    engine.process("1", [], (1000, 1000))          # must not raise for any authored primitive

    # 5. STRICT validation — impossible configs fail at SAVE, not silently
    def rejects(label, p):
        bad = False
        try:
            author(p)
        except psycopg.Error:
            bad = True
        assert bad, f"invalid config must be rejected at save: {label}"

    rejects("occupancy without min or max",
            dict(key="occupancy", rule_type="occupancy", classes=["person"], geometry=POLY))
    rejects("queue_wait without occupancy_min",
            dict(key="queue_wait", rule_type="queue_wait", classes=["person"], geometry=POLY, dwell=120))
    rejects("expected presence without schedule",
            dict(key="zone_presence", rule_type="zone_presence", classes=["person"], geometry=POLY, dwell=300))
    rejects("vehicle activity with people",
            dict(key="vehicle_activity", rule_type="vehicle_activity", classes=["person"], geometry=POLY))
    rejects("occupancy min greater than max",
            dict(key="occupancy", rule_type="occupancy", classes=["person"], geometry=POLY, occ_min=9, occ_max=2))
    rejects("line crossing with a 4-point polygon",
            dict(key="line_crossing", rule_type="line_crossing", classes=["person"], geometry=POLY))
    rejects("absence without a duration",
            dict(key="zone_absence", rule_type="zone_absence", classes=["person"], geometry=POLY))

    print("Operations rule authoring Postgres integration (0057): PASS")


if __name__ == "__main__":
    main()

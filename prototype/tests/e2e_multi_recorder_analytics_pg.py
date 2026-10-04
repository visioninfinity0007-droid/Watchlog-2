#!/usr/bin/env python3
"""Multi-recorder analytics attribution (0151): real Postgres execution.

Rolled back after execution. Proves:
- Recorder A ch1 and Recorder B ch1 analytic events map to different cameras/rules.
- Promoted canonical events retain the exact recorder_id.
- Same channel/time across recorders does not cross-attribute.
- Explicit foreign recorder identity fails closed.
- Legacy analytics payload without recorder_id works only for a singleton site.
- Final multi-recorder contract is v4 and advertises analytics + continuity.
- RPC ACLs remain the intended Agent surface.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ENV = {}
env_path = ROOT.parent / ".env"
for line in env_path.read_text(errors="ignore").splitlines() if env_path.exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
          "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

STEPS: list[bool] = []


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=ENV.get("SUPABASE_DB_NAME", "postgres"),
        connect_timeout=30,
        autocommit=False,
    )

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            def claims(uid):
                return json.dumps({"sub": str(uid), "role": "authenticated"})

            def as_auth(uid, sql, *params):
                cur.execute("savepoint auth_sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")
                return row

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            def as_anon_raises(sql, *params):
                cur.execute("savepoint anon_err")
                cur.execute("set local role anon")
                raised, message = False, ""
                try:
                    cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    raised, message = True, str(exc).splitlines()[0]
                cur.execute("rollback to savepoint anon_err")
                return raised, message

            def bootstrap(email, company, site_name):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (email,),
                ).fetchone()[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.0.27',now()
                       ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}"),
                ).fetchone()[0]

            def sync_recorders(agent_id, key, rows):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps(rows),
                )[0]

            def sync_camera(agent_id, key, recorder_id, channel="1"):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{
                        "channel": channel,
                        "name": f"Camera {channel}",
                        "is_configured": True,
                    }]),
                )[0][channel]

            def add_rule(tenant_id, site_id, camera_id, suffix, promote=True):
                return cur.execute(
                    """insert into public.monitoring_rules(
                         tenant_id,site_id,camera_id,name,rule_type,
                         analytic_key,enabled,severity,promote_incident
                       ) values (
                         %s,%s,%s,%s,'line_crossing',
                         'line_crossing',true,'attention',%s
                       ) returning id""",
                    (tenant_id, site_id, camera_id, f"Rule {suffix}", promote),
                ).fetchone()[0]

            # --------------------------------------------------------------
            # Tenant A: two recorders, both Channel 1.
            # --------------------------------------------------------------
            ua, ta, sa = bootstrap(
                "analytics-a@watchlog.test", "Analytics A", "Warehouse A"
            )
            key_a = "analytics-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")
            recs = sync_recorders(agent_a, key_a, [
                {
                    "local_key": "rec-a",
                    "display_name": "Recorder A",
                    "is_primary": True,
                    "is_configured": True,
                },
                {
                    "local_key": "rec-b",
                    "display_name": "Recorder B",
                    "is_primary": False,
                    "is_configured": True,
                },
            ])
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cam_a = sync_camera(agent_a, key_a, rec_a, "1")
            cam_b = sync_camera(agent_a, key_a, rec_b, "1")
            rule_a = add_rule(ta, sa, cam_a, "A")
            rule_b = add_rule(ta, sa, cam_b, "B")

            occurred = "2026-10-03T09:00:00Z"
            payload = [
                {
                    "recorder_id": str(rec_a),
                    "rule_id": str(rule_a),
                    "channel": "1",
                    "event_type": "line_crossing",
                    "object_class": "person",
                    "occurred_at": occurred,
                    "dedupe_key": "analytics-rec-a-ch1",
                    "metadata": {"probe": "A"},
                },
                {
                    "recorder_id": str(rec_b),
                    "rule_id": str(rule_b),
                    "channel": "1",
                    "event_type": "line_crossing",
                    "object_class": "person",
                    "occurred_at": occurred,
                    "dedupe_key": "analytics-rec-b-ch1",
                    "metadata": {"probe": "B"},
                },
            ]
            res = as_anon(
                "select wl_ingest_analytic_events(%s,%s,%s::jsonb)",
                agent_a, key_a, json.dumps(payload),
            )[0]
            step(
                res["received"] == 2 and res["inserted"] == 2
                and res["promoted_incidents"] == 2,
                "two recorder-scoped analytic events insert and promote independently",
                json.dumps(res, default=str),
            )

            rows = cur.execute(
                """select recorder_id,camera_id,monitoring_rule_id,dedupe_key
                     from public.analytic_events
                    where agent_id=%s
                      and dedupe_key in ('analytics-rec-a-ch1','analytics-rec-b-ch1')
                    order by dedupe_key""",
                (agent_a,),
            ).fetchall()
            by_key = {r[3]: (str(r[0]), str(r[1]), str(r[2])) for r in rows}
            step(
                by_key["analytics-rec-a-ch1"] == (
                    str(rec_a), str(cam_a), str(rule_a)
                )
                and by_key["analytics-rec-b-ch1"] == (
                    str(rec_b), str(cam_b), str(rule_b)
                ),
                "same Channel 1 resolves exact recorder/camera/rule",
                str(by_key),
            )

            promoted = cur.execute(
                """select recorder_id,camera_id,dedupe_key
                     from public.events
                    where agent_id=%s
                      and dedupe_key in (
                        'analytics:analytics-rec-a-ch1',
                        'analytics:analytics-rec-b-ch1'
                      )
                    order by dedupe_key""",
                (agent_a,),
            ).fetchall()
            promoted_by_key = {
                r[2]: (str(r[0]), str(r[1])) for r in promoted
            }
            step(
                promoted_by_key["analytics:analytics-rec-a-ch1"] == (
                    str(rec_a), str(cam_a)
                )
                and promoted_by_key["analytics:analytics-rec-b-ch1"] == (
                    str(rec_b), str(cam_b)
                ),
                "promoted canonical events preserve recorder provenance",
                str(promoted_by_key),
            )

            raised, msg = as_anon_raises(
                "select wl_ingest_analytic_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{
                    "rule_id": str(rule_a),
                    "channel": "1",
                    "event_type": "line_crossing",
                    "occurred_at": "2026-10-03T09:01:00Z",
                    "dedupe_key": "legacy-ambiguous-analytics",
                }]),
            )
            step(
                raised and "ambiguous" in msg.lower(),
                "legacy analytics payload fails closed on multi-recorder site",
                msg,
            )

            # --------------------------------------------------------------
            # Tenant B: foreign recorder identity is rejected.
            # --------------------------------------------------------------
            ub, tb, sb = bootstrap(
                "analytics-b@watchlog.test", "Analytics B", "Retail B"
            )
            key_b = "analytics-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_c = sync_recorders(agent_b, key_b, [{
                "local_key": "rec-c",
                "display_name": "Recorder C",
                "is_primary": True,
                "is_configured": True,
            }])["rec-c"]
            cam_c = sync_camera(agent_b, key_b, rec_c, "1")
            rule_c = add_rule(tb, sb, cam_c, "C", promote=False)

            raised, msg = as_anon_raises(
                "select wl_ingest_analytic_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{
                    "recorder_id": str(rec_c),
                    "rule_id": str(rule_a),
                    "channel": "1",
                    "event_type": "line_crossing",
                    "occurred_at": "2026-10-03T09:02:00Z",
                    "dedupe_key": "foreign-recorder-analytics",
                }]),
            )
            step(
                raised and "not configured for this agent site" in msg.lower(),
                "foreign-tenant recorder analytics fails closed",
                msg,
            )

            # Singleton legacy payload remains compatible.
            legacy = as_anon(
                "select wl_ingest_analytic_events(%s,%s,%s::jsonb)",
                agent_b, key_b,
                json.dumps([{
                    "rule_id": str(rule_c),
                    "channel": "1",
                    "event_type": "line_crossing",
                    "object_class": "person",
                    "occurred_at": "2026-10-03T09:03:00Z",
                    "dedupe_key": "singleton-legacy-analytics",
                }]),
            )[0]
            legacy_row = cur.execute(
                """select recorder_id,camera_id
                     from analytic_events
                    where tenant_id=%s and dedupe_key='singleton-legacy-analytics'""",
                (tb,),
            ).fetchone()
            step(
                legacy["inserted"] == 1
                and str(legacy_row[0]) == str(rec_c)
                and str(legacy_row[1]) == str(cam_c),
                "legacy analytics payload remains compatible on singleton site",
            )

            # The complete migration chain ends at contract v4; analytics/job
            # routing remain required alongside immutable recorder continuity.
            contract = as_anon(
                "select wl_multi_recorder_agent_contract(%s,%s)",
                agent_a, key_a,
            )[0]
            features = set(contract.get("features") or [])
            step(
                contract.get("ok") is True
                and int(contract.get("version") or 0) == 4
                and "recorder_analytics" in features
                and "recorder_job_routing" in features
                and "recorder_continuity" in features,
                "multi-recorder contract v4 advertises analytics + job routing + continuity",
                json.dumps(contract, default=str),
            )

            # Existing analytic rows are required to have deterministic recorder lineage.
            nulls = cur.execute(
                "select count(*) from analytic_events where recorder_id is null"
            ).fetchone()[0]
            step(nulls == 0, "analytic_events recorder_id is fully backfilled and non-null")

            # A trusted/internal insert that predates recorder_id takes the
            # recorder of its own camera (Recorder B here, not the primary).
            direct = cur.execute(
                """insert into analytic_events(
                     tenant_id,site_id,camera_id,analytic_key,event_type,occurred_at,dedupe_key
                   ) values (%s,%s,%s,'custom','line_crossing',now(),'analytics-direct-b')
                   returning recorder_id""",
                (ta, sa, cam_b),
            ).fetchone()[0]
            cur.execute("savepoint foreign_camera_sp")
            foreign_blocked = False
            try:
                cur.execute(
                    """insert into analytic_events(
                         tenant_id,site_id,camera_id,analytic_key,event_type,occurred_at,dedupe_key
                       ) values (%s,%s,%s,'custom','line_crossing',now(),'analytics-direct-foreign')""",
                    (tb, sb, cam_b),
                )
            except psycopg.Error:
                foreign_blocked = True
            cur.execute("rollback to savepoint foreign_camera_sp")
            step(str(direct) == str(rec_b) and foreign_blocked,
                 "legacy direct analytic insert derives its camera's recorder; a foreign camera fails closed",
                 str(direct))

            # Exact execute ACLs.
            def execute_grantees(sig):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC'
                                    else a.grantee::regrole::text end,
                              p.proowner::regrole::text
                         from pg_proc p,
                              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                        where p.oid=%s::regprocedure
                          and a.privilege_type='EXECUTE'""",
                    (sig,),
                ).fetchall()
                owner = rows[0][1] if rows else None
                return {g for g, _ in rows if g != owner}

            ingest_acl = execute_grantees(
                "public.wl_ingest_analytic_events(uuid,text,jsonb)"
            )
            step(
                ingest_acl == {"anon", "authenticated"},
                "analytics ingest keeps exact deployed Agent ACL",
                str(sorted(ingest_acl)),
            )
            contract_acl = execute_grantees(
                "public.wl_multi_recorder_agent_contract(uuid,text)"
            )
            step(
                contract_acl == {"anon"},
                "multi-recorder contract remains Agent-only via anon RPC surface",
                str(sorted(contract_acl)),
            )

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

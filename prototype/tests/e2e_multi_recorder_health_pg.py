#!/usr/bin/env python3
"""Multi-recorder health/recovery foundation (0145): real Postgres execution.

Self-contained and rolled back. Proves:
- recorder health/inventory is scoped to recorder+channel, not site+channel;
- two recorders with channel 1 get independent camera health;
- legacy health APIs keep working for one recorder and fail closed for >1;
- recorder recovery intervals can share the same time window without collision;
- recovery camera/recorder lineage is enforced;
- cross-tenant recorder writes are denied;
- recorder-health RLS is tenant-isolated;
- recorder-specific recovery cannot promote the site-wide RECOVERED metric;
- exact new RPC/helper ACLs are narrow.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations" / "0145_multi_recorder_health_recovery.sql"

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
            cur.execute(MIG.read_text(encoding="utf-8"))

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

            # Multi-recorder tenant A.
            ua, ta, sa = bootstrap(
                "health-a@watchlog.test", "Health A", "Warehouse A"
            )
            key_a = "health-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")
            recs = sync_recorders(agent_a, key_a, [
                {
                    "local_key": "rec-a",
                    "display_name": "Recorder A",
                    "vendor": "Hikvision",
                    "model": "TEST-A",
                    "driver": "onvif",
                    "is_primary": True,
                    "is_configured": True,
                },
                {
                    "local_key": "rec-b",
                    "display_name": "Recorder B",
                    "vendor": "Dahua",
                    "model": "TEST-B",
                    "driver": "onvif",
                    "is_primary": False,
                    "is_configured": True,
                },
            ])
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cam_a = sync_camera(agent_a, key_a, rec_a, "1")
            cam_b = sync_camera(agent_a, key_a, rec_b, "1")

            health_a = {
                "nvr": {
                    "reachable": True,
                    "auth_ok": True,
                    "reason": "ok",
                    "state": "operational",
                    "vendor": "Hikvision",
                    "model": "TEST-A",
                    "firmware": "A1",
                },
                "channels": {
                    "enumerated": True,
                    "reported": [{"channel": "1", "enabled": True}],
                },
            }
            health_b = {
                "nvr": {
                    "reachable": False,
                    "auth_ok": None,
                    "reason": "nvr_unreachable",
                    "state": "offline",
                    "vendor": "Dahua",
                    "model": "TEST-B",
                    "firmware": "B1",
                },
                "channels": {
                    "enumerated": False,
                    "reported": [],
                },
            }

            ra = as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(health_a),
            )[0]
            rb = as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, json.dumps(health_b),
            )[0]
            step(ra["present"] == 1 and ra["missing"] == 0,
                 "Recorder A inventory sees only Recorder A channel 1")
            step(rb["unknown"] == 1 and rb["present"] == 0,
                 "unreachable Recorder B makes only its camera inventory unknown")

            inv = {
                str(r[0]): r[1]
                for r in cur.execute(
                    """select ci.camera_id,ci.inventory_state
                         from camera_inventory ci
                        where ci.camera_id in (%s,%s)""",
                    (cam_a, cam_b),
                ).fetchall()
            }
            step(inv[str(cam_a)] == "present" and inv[str(cam_b)] == "unknown",
                 "same channel number has independent recorder-scoped inventory", str(inv))

            ca = as_anon(
                "select wl_report_recorder_camera_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a,
                json.dumps({"cameras": [{
                    "channel": "1", "health": "operational", "reason": "ok"
                }]}),
            )[0]
            cb = as_anon(
                "select wl_report_recorder_camera_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b,
                json.dumps({"cameras": [{
                    "channel": "1", "health": "offline", "reason": "nvr_unreachable"
                }]}),
            )[0]
            states = {
                str(r[0]): r[1]
                for r in cur.execute(
                    "select camera_id,health_state from camera_health where camera_id in (%s,%s)",
                    (cam_a, cam_b),
                ).fetchall()
            }
            step(ca["operational"] == 1 and cb["offline"] == 1
                 and states[str(cam_a)] == "operational"
                 and states[str(cam_b)] == "offline",
                 "camera current health resolves by recorder+channel", str(states))

            unknown_auth_report = {
                "nvr": {
                    "reachable": True,
                    "auth_ok": None,
                    "reason": "unknown",
                    "state": "unknown",
                },
                "channels": {"enumerated": False, "reported": []},
            }
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(unknown_auth_report),
            )
            unknown_inv = cur.execute(
                "select inventory_state from camera_inventory where camera_id=%s",
                (cam_a,),
            ).fetchone()[0]
            unknown_health = cur.execute(
                """select nvr_reachable,nvr_auth_ok,last_ok_at
                     from recorder_health
                    where recorder_id=%s and agent_id=%s""",
                (rec_a, agent_a),
            ).fetchone()
            step(
                unknown_inv == "unknown"
                and unknown_health[0] is True
                and unknown_health[1] is None,
                "reachable recorder with unknown auth does not verify camera inventory",
                str((unknown_inv, unknown_health)),
            )
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(health_a),
            )

            rows = cur.execute(
                """select recorder_id,nvr_reachable,nvr_auth_ok
                     from recorder_health
                    where site_id=%s and agent_id=%s
                    order by recorder_id""",
                (sa, agent_a),
            ).fetchall()
            step(len(rows) == 2 and {str(r[0]) for r in rows} == {str(rec_a), str(rec_b)},
                 "recorder current health has one row per recorder for the Agent")

            raised, msg = as_anon_raises(
                "select wl_report_health(%s,%s,%s::jsonb)",
                agent_a, key_a, json.dumps(health_a),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy recorder health fails closed on multi-recorder site", msg)

            raised, msg = as_anon_raises(
                "select wl_report_camera_health(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps({"cameras": [{
                    "channel": "1", "health": "operational", "reason": "ok"
                }]}),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy camera health fails closed on multi-recorder site", msg)

            # Same recovery window may exist once per recorder.
            start = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
            end = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
            open_a = as_anon(
                "select wl_open_recorder_recovery_interval(%s,%s,%s,%s,%s,array[%s::text])",
                agent_a, key_a, rec_a, start, end, "1",
            )[0]
            open_b = as_anon(
                "select wl_open_recorder_recovery_interval(%s,%s,%s,%s,%s,array[%s::text])",
                agent_a, key_a, rec_b, start, end, "1",
            )[0]
            step(open_a["id"] != open_b["id"],
                 "same outage window can be opened independently for two recorders")

            duplicate_a = as_anon(
                "select wl_open_recorder_recovery_interval(%s,%s,%s,%s,%s,array[%s::text])",
                agent_a, key_a, rec_a, start, end, "1",
            )[0]
            step(duplicate_a.get("duplicate") is True
                 and str(duplicate_a["id"]) == str(open_a["id"]),
                 "same recorder recovery window remains idempotent")

            raised, msg = as_anon_raises(
                "select wl_open_recorder_recovery_interval(%s,%s,%s,%s,%s,array[%s::text])",
                agent_a, key_a, rec_a,
                datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 2, 10, 5, tzinfo=timezone.utc),
                "999",
            )
            step(raised and "does not belong" in msg.lower(),
                 "recovery refuses channel not owned by recorder", msg)

            claimed_a = as_anon(
                "select wl_agent_claim_recorder_recovery(%s,%s,%s,1,900)",
                agent_a, key_a, rec_a,
            )[0]
            step(len(claimed_a) == 1
                 and str(claimed_a[0]["id"]) == str(open_a["id"])
                 and str(claimed_a[0]["recorder_id"]) == str(rec_a)
                 and claimed_a[0]["channels"] == ["1"],
                 "Recorder A claim cannot take Recorder B recovery work")

            complete_a = as_anon(
                "select wl_complete_recorder_recovery(%s,%s,%s,%s,'recovered',1,'{}'::jsonb,'{}'::jsonb)",
                agent_a, key_a, rec_a, open_a["id"],
            )[0]
            step(complete_a["ok"] is True and complete_a["status"] == "recovered",
                 "recorder-scoped recovery completes only under its recorder identity")

            raised, msg = as_anon_raises(
                "select wl_open_recovery_interval(%s,%s,%s,%s,array[%s::uuid])",
                agent_a, key_a,
                datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 2, 11, 5, tzinfo=timezone.utc),
                cam_a,
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy recovery opening fails closed on multi-recorder site", msg)

            # Tenant B: cross-tenant writes denied and RLS isolates reads.
            ub, tb, sb = bootstrap(
                "health-b@watchlog.test", "Health B", "Retail B"
            )
            key_b = "health-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_c = sync_recorders(agent_b, key_b, [{
                "local_key": "rec-c",
                "display_name": "Recorder C",
                "is_primary": True,
                "is_configured": True,
            }])["rec-c"]
            cam_c = sync_camera(agent_b, key_b, rec_c, "1")

            raised, msg = as_anon_raises(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_c, json.dumps(health_a),
            )
            step(raised and "not configured for this agent site" in msg.lower(),
                 "Agent A cannot write recorder health for tenant B", msg)

            seen_a = as_auth(
                ua, "select count(*) from recorder_health"
            )[0]
            actual_a = cur.execute(
                "select count(*) from recorder_health where tenant_id=%s", (ta,)
            ).fetchone()[0]
            seen_b = as_auth(
                ub, "select count(*) from recorder_health"
            )[0]
            actual_b = cur.execute(
                "select count(*) from recorder_health where tenant_id=%s", (tb,)
            ).fetchone()[0]
            step(seen_a == actual_a and seen_b == actual_b,
                 "recorder_health direct reads are tenant-isolated by RLS")

            # Single-recorder tenant B retains legacy RPC behavior.
            legacy = as_anon(
                "select wl_report_health(%s,%s,%s::jsonb)",
                agent_b, key_b, json.dumps({
                    "nvr": {
                        "reachable": True, "auth_ok": True,
                        "reason": "ok", "state": "operational",
                    },
                    "channels": {
                        "enumerated": True,
                        "reported": [{"channel": "1", "enabled": True}],
                    },
                }),
            )[0]
            step(legacy["ok"] is True and legacy["present"] == 1,
                 "legacy health RPC remains compatible on one-recorder site")
            legacy_nvr = cur.execute(
                "select nvr_reachable,nvr_auth_ok from nvr_health where agent_id=%s",
                (agent_b,),
            ).fetchone()
            step(legacy_nvr == (True, True),
                 "legacy wrapper keeps nvr_health current for existing fault/read models")

            legacy_open = as_anon(
                "select wl_open_recovery_interval(%s,%s,%s,%s,array[%s::uuid])",
                agent_b, key_b,
                datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 2, 12, 5, tzinfo=timezone.utc),
                cam_c,
            )[0]
            legacy_recorder = cur.execute(
                "select recorder_id from recovery_intervals where id=%s",
                (legacy_open["id"],),
            ).fetchone()[0]
            step(legacy_recorder is None,
                 "legacy single-recorder recovery keeps NULL recorder_id for old site-wide coverage semantics")

            # Site-wide coverage function must explicitly ignore recorder-specific
            # recovery. This is a truth guard: no partial-recorder recovery can
            # promote the whole site's coverage.
            coverage_def = cur.execute(
                """select pg_get_functiondef(p.oid)
                     from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                    where n.nspname='public'
                      and p.proname='wl_site_coverage_report_classes'
                    limit 1"""
            ).fetchone()[0]
            step("ri.recorder_id is null" in coverage_def,
                 "site-wide RECOVERED coverage excludes recorder-specific recovery")

            # Exact EXECUTE ACLs.
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

            for sig in (
                "public.wl_report_recorder_health(uuid,text,uuid,jsonb)",
                "public.wl_report_recorder_camera_health(uuid,text,uuid,jsonb)",
                "public.wl_open_recorder_recovery_interval(uuid,text,uuid,timestamptz,timestamptz,text[])",
                "public.wl_agent_claim_recorder_recovery(uuid,text,uuid,integer,integer)",
                "public.wl_complete_recorder_recovery(uuid,text,uuid,uuid,text,integer,jsonb,jsonb)",
            ):
                got = execute_grantees(sig)
                step(got == {"anon"}, f"{sig} EXECUTE ACL is exactly anon (+owner)", str(sorted(got)))

            for sig in (
                "public.wl_report_recorder_health_core(uuid,uuid,uuid,uuid,jsonb)",
                "public.wl_report_recorder_camera_health_core(uuid,uuid,uuid,uuid,jsonb)",
            ):
                got = execute_grantees(sig)
                step(got == set(), f"{sig} EXECUTE ACL is owner only", str(sorted(got)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

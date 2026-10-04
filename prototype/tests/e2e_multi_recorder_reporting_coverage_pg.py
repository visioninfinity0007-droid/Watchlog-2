#!/usr/bin/env python3
"""Multi-recorder reporting coverage bridge (0155): real Postgres gate.

Runs only against disposable/test Postgres and rolls back.

Proves:
- multi-recorder effective coverage uses camera-time, never legacy site-Agent 100%;
- recorder-specific recovery restores only that recorder's camera-time;
- wall-clock impact windows retain affected-camera counts;
- a window beginning before recorder tracking is Unknown, never legacy fallback;
- wl_my_daily_intelligence inherits the same effective coverage source;
- wl_my_site_diagnosis / wl_ai_context inherit the same source;
- direct coverage-classes access is tenant scoped;
- true single-recorder sites keep the legacy classes contract.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
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

            def as_auth_raises(uid, sql, *params):
                cur.execute("savepoint auth_err")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                raised, message = False, ""
                try:
                    cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    raised, message = True, str(exc).splitlines()[0]
                cur.execute("rollback to savepoint auth_err")
                return raised, message

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

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
                cur.execute("update sites set timezone='UTC' where id=%s", (site,))
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.1.0',now()
                       ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}"),
                ).fetchone()[0]

            def sync_recorders(agent_id, key, rows):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps(rows),
                )[0]

            def sync_cameras(agent_id, key, recorder_id, rows):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id, json.dumps(rows),
                )[0]

            ua, ta, sa = bootstrap(
                "reporting-coverage-a@watchlog.test",
                "Reporting Coverage A",
                "Warehouse A",
            )
            key_a = "reporting-coverage-agent-a"
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
            cams_a = sync_cameras(agent_a, key_a, rec_a, [
                {"channel": "1", "name": "A1", "is_configured": True},
                {"channel": "2", "name": "A2", "is_configured": True},
            ])
            cams_b = sync_cameras(agent_a, key_a, rec_b, [
                {"channel": "1", "name": "B1", "is_configured": True},
            ])
            step(
                len({str(cams_a["1"]), str(cams_b["1"])}) == 2,
                "overlapping Channel 1 remains distinct before coverage assertions",
            )

            # Deterministic 3-hour governed window:
            # Recorder A = 2 cameras, one 60m outage, 15m recovered.
            # Recorder B = 1 camera, no outage.
            start = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
            gap_start = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
            rec_start = datetime(2026, 10, 2, 10, 30, tzinfo=timezone.utc)
            rec_end = datetime(2026, 10, 2, 10, 45, tzinfo=timezone.utc)
            gap_end = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
            end = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

            cur.execute(
                "update recorders set coverage_tracking_started_at=%s where site_id=%s",
                (start, sa),
            )
            cur.execute("delete from recorder_coverage_intervals where site_id=%s", (sa,))
            cur.execute(
                """insert into recorder_coverage_intervals(
                     tenant_id,site_id,recorder_id,started_at,ended_at,cause,source
                   ) values (%s,%s,%s,%s,%s,'nvr_unreachable','test')""",
                (ta, sa, rec_a, gap_start, gap_end),
            )
            cur.execute(
                """insert into recovery_intervals(
                     tenant_id,site_id,agent_id,recorder_id,
                     started_at,ended_at,status,cameras,recovered_count
                   ) values (%s,%s,%s,%s,%s,%s,'recovered',%s::uuid[],2)""",
                (
                    ta, sa, agent_a, rec_a, rec_start, rec_end,
                    [str(cams_a["1"]), str(cams_a["2"])],
                ),
            )

            cov = as_auth(
                ua,
                "select wl_site_coverage_report_classes(%s,%s,%s)",
                sa, start, end,
            )[0]
            step(
                cov["schema"] == "multi-recorder-effective-coverage-v1"
                and cov["coverage_basis"] == "camera_time"
                and cov["complete_window"] is True
                and cov["known"] is True,
                "multi-recorder coverage selects the camera-time contract",
                json.dumps(cov, default=str),
            )
            step(
                abs(float(cov["coverage_ratio"]) - 0.8333) < 0.0001
                and abs(float(cov["camera_coverage_ratio"]) - 0.8333) < 0.0001,
                "effective ratio is camera-time coverage, not legacy site connectivity",
                str(cov["coverage_ratio"]),
            )
            step(
                "classes" not in cov,
                "camera-time seconds are not relabelled as legacy wall-clock coverage classes",
            )
            step(
                round(float(cov["any_unverified_seconds"])) == 2700
                and cov["max_affected_cameras"] == 2
                and len(cov["gaps"]) == 2
                and all(x["affected_camera_count"] == 2 for x in cov["gaps"]),
                "wall-clock impact says some cameras were unverified without whole-site downtime",
                json.dumps(cov["gaps"], default=str),
            )

            # Rollout boundary: no reconstruction before recorder tracking start.
            cur.execute(
                "update recorders set coverage_tracking_started_at=%s where site_id=%s",
                (gap_start, sa),
            )
            unknown = as_auth(
                ua,
                "select wl_site_coverage_report_classes(%s,%s,%s)",
                sa, start, end,
            )[0]
            step(
                unknown["complete_window"] is False
                and unknown["known"] is False
                and unknown["coverage_ratio"] is None
                and unknown["gaps"] == [],
                "pre-tracking part of a multi-recorder window stays Unknown, never legacy 100%",
                json.dumps(unknown, default=str),
            )

            # Current owner/daily context uses the exact same source. Make the
            # current UTC day fully governed and open one Recorder B gap.
            today_start = cur.execute("select date_trunc('day',now())").fetchone()[0]
            cur.execute(
                "update recorders set coverage_tracking_started_at=%s where site_id=%s",
                (today_start, sa),
            )
            cur.execute("delete from recorder_coverage_intervals where site_id=%s", (sa,))
            cur.execute(
                """insert into recorder_coverage_intervals(
                     tenant_id,site_id,recorder_id,started_at,ended_at,cause,source
                   ) values (
                     %s,%s,%s,greatest(%s::timestamptz,now()-interval '10 minutes'),
                     null,'nvr_unreachable','test'
                   )""",
                (ta, sa, rec_b, today_start),
            )

            diagnosis = as_auth(
                ua, "select wl_my_site_diagnosis(%s)", sa
            )[0]
            dcov = diagnosis["coverage"]
            step(
                dcov["coverage_basis"] == "camera_time"
                and dcov["complete_window"] is True
                and float(dcov["coverage_ratio"]) < 1.0,
                "owner diagnosis cannot report fully verified while Recorder B has a current gap",
                json.dumps(dcov, default=str),
            )

            daily = as_auth(
                ua,
                "select wl_my_daily_intelligence(%s,(now() at time zone 'UTC')::date)",
                sa,
            )[0]
            step(
                daily["coverage"]["coverage_basis"] == "camera_time"
                and float(daily["coverage"]["coverage_ratio"]) < 1.0,
                "daily intelligence inherits recorder-aware coverage for Phase-28 gating",
                json.dumps(daily["coverage"], default=str),
            )

            ai = as_auth(ua, "select wl_ai_context(%s)", sa)[0]
            step(
                ai["coverage"]["coverage_basis"] == "camera_time"
                and float(ai["coverage"]["coverage_ratio"]) < 1.0,
                "WatchLog AI context shares the same recorder-aware owner coverage truth",
            )

            # Direct coverage classes are now tenant/site authorized.
            ub, tb, sb = bootstrap(
                "reporting-coverage-b@watchlog.test",
                "Reporting Coverage B",
                "Office B",
            )
            raised, msg = as_auth_raises(
                ub,
                "select wl_site_coverage_report_classes(%s,%s,%s)",
                sa, start, end,
            )
            step(
                raised and ("authorized" in msg.lower() or "site" in msg.lower()),
                "another tenant cannot read recorder-aware coverage classes",
                msg,
            )

            # True singleton site remains on the exact legacy 3-class contract.
            key_b = "reporting-coverage-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_c = sync_recorders(agent_b, key_b, [{
                "local_key": "rec-c",
                "display_name": "Recorder C",
                "is_primary": True,
                "is_configured": True,
            }])["rec-c"]
            sync_cameras(agent_b, key_b, rec_c, [
                {"channel": "1", "name": "C1", "is_configured": True},
            ])
            singleton = as_auth(
                ub,
                "select wl_site_coverage_report_classes(%s,%s,%s)",
                sb, start, end,
            )[0]
            step(
                "classes" in singleton
                and "live_seconds" in singleton["classes"]
                and "recovered_seconds" in singleton["classes"]
                and "unverified_seconds" in singleton["classes"]
                and singleton.get("coverage_basis") is None,
                "single-recorder coverage remains on the legacy wall-clock classes contract",
                json.dumps(singleton, default=str),
            )

            # Helper must remain owner-only; customer entry point keeps existing ACL.
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

            step(
                execute_grantees(
                    "public.wl_effective_site_coverage(uuid,timestamptz,timestamptz)"
                ) == set(),
                "effective coverage helper is owner-only",
            )
            step(
                execute_grantees(
                    "public.wl_site_coverage_report_classes(uuid,timestamptz,timestamptz)"
                ) == {"authenticated", "service_role"},
                "coverage classes keeps authenticated/service-role API surface",
            )

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

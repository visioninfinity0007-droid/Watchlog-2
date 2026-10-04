#!/usr/bin/env python3
"""Multi-recorder coverage truth (0153): real Postgres execution.

Runs only against disposable/test Postgres and rolls back.

Proves:
- newly configured recorders start UNKNOWN until health is verified;
- healthy recorder reports close only their own coverage interval;
- a Recorder A outage affects only Recorder A cameras;
- recorder-specific RECOVERED restores only that recorder's camera-time;
- partial vs fully-unverified wall-clock impact is deterministic;
- the site coverage compatibility point switches to camera-time truth;
- customer wrapper is tenant-isolated;
- internal coverage helpers remain owner-only.
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

            def sync_cameras(agent_id, key, recorder_id, rows):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id, json.dumps(rows),
                )[0]

            def healthy_report(channels):
                return {
                    "nvr": {
                        "reachable": True,
                        "auth_ok": True,
                        "reason": "ok",
                        "state": "operational",
                    },
                    "channels": {
                        "enumerated": True,
                        "reported": [
                            {"channel": str(ch), "enabled": True}
                            for ch in channels
                        ],
                    },
                }

            ua, ta, sa = bootstrap(
                "coverage-a@watchlog.test", "Coverage A", "Warehouse A"
            )
            key_a = "coverage-agent-a"
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

            # Recorder registry trigger starts every new recorder UNKNOWN.
            open_unknown = cur.execute(
                """select recorder_id,cause
                     from recorder_coverage_intervals
                    where site_id=%s and ended_at is null
                    order by recorder_id""",
                (sa,),
            ).fetchall()
            step(
                len(open_unknown) == 2
                and {str(r[0]) for r in open_unknown} == {str(rec_a), str(rec_b)}
                and {r[1] for r in open_unknown} == {"unknown"},
                "new configured recorders start with independent UNKNOWN coverage",
                str(open_unknown),
            )

            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report(["1", "2"])),
            )
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, json.dumps(healthy_report(["1"])),
            )
            step(
                cur.execute(
                    "select count(*) from recorder_coverage_intervals where site_id=%s and ended_at is null",
                    (sa,),
                ).fetchone()[0] == 0,
                "healthy recorder reports close only their current UNKNOWN intervals",
            )

            unknown_auth = {
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
                agent_a, key_a, rec_a, json.dumps(unknown_auth),
            )
            unknown_open = cur.execute(
                """select recorder_id,cause
                     from recorder_coverage_intervals
                    where site_id=%s and ended_at is null""",
                (sa,),
            ).fetchall()
            step(
                len(unknown_open) == 1
                and str(unknown_open[0][0]) == str(rec_a)
                and unknown_open[0][1] == "unknown",
                "reachable recorder with unknown auth remains recorder-scoped UNVERIFIED",
                str(unknown_open),
            )
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report(["1", "2"])),
            )
            step(
                cur.execute(
                    "select count(*) from recorder_coverage_intervals where site_id=%s and ended_at is null",
                    (sa,),
                ).fetchone()[0] == 0,
                "verified authentication closes the unknown-auth coverage interval",
            )

            # Deterministic 3-hour window:
            # Recorder A has 2 cameras and one 60m gap.
            # 15m of that gap is RECOVERED.
            # Recorder B has 1 camera and no gap.
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

            facts = cur.execute(
                "select wl_site_recorder_coverage_facts(%s,%s,%s)",
                (sa, start, end),
            ).fetchone()[0]

            step(
                facts["enabled"] is True
                and facts["recorder_count"] == 2
                and facts["camera_count"] == 3
                and facts["complete_window"] is True,
                "recorder coverage window is fully governed for all three cameras",
                json.dumps(facts, default=str),
            )
            step(
                round(float(facts["fully_verified_seconds"])) == 8100
                and round(float(facts["partial_unverified_seconds"])) == 2700
                and round(float(facts["fully_unverified_seconds"])) == 0
                and facts["max_affected_cameras"] == 2,
                "one-recorder outage is PARTIAL, not a whole-site outage",
                json.dumps({
                    "fully_verified": facts["fully_verified_seconds"],
                    "partial": facts["partial_unverified_seconds"],
                    "full": facts["fully_unverified_seconds"],
                    "max_affected": facts["max_affected_cameras"],
                }),
            )
            step(
                round(float(facts["camera_time_seconds"])) == 32400
                and round(float(facts["live_camera_seconds"])) == 25200
                and round(float(facts["recovered_camera_seconds"])) == 1800
                and round(float(facts["unverified_camera_seconds"])) == 5400
                and abs(float(facts["camera_coverage_ratio"]) - 0.8333) < 0.0001,
                "camera-time coverage restores only Recorder A recovered camera-time",
                json.dumps({
                    "total": facts["camera_time_seconds"],
                    "live": facts["live_camera_seconds"],
                    "recovered": facts["recovered_camera_seconds"],
                    "unverified": facts["unverified_camera_seconds"],
                    "ratio": facts["camera_coverage_ratio"],
                }),
            )

            by_name = {r["name"]: r for r in facts["recorders"]}
            step(
                abs(float(by_name["Recorder A"]["coverage_ratio"]) - 0.75) < 0.0001
                and abs(float(by_name["Recorder B"]["coverage_ratio"]) - 1.0) < 0.0001
                and round(float(by_name["Recorder A"]["unverified_seconds"])) == 2700
                and round(float(by_name["Recorder B"]["unverified_seconds"])) == 0,
                "each recorder keeps independent coverage truth",
                json.dumps(by_name, default=str),
            )

            impact = facts["impact_windows"]
            step(
                len(impact) == 2
                and all(x["state"] == "partial_unverified" for x in impact)
                and all(x["affected_camera_count"] == 2 for x in impact)
                and all(x["total_camera_count"] == 3 for x in impact),
                "impact windows identify exactly two affected cameras",
                json.dumps(impact, default=str),
            )

            # Final chain (0155): a site with more than one configured recorder
            # gets camera-time coverage. Camera-time seconds are never relabelled
            # as the legacy wall-clock LIVE/RECOVERED/UNVERIFIED classes.
            classes = as_auth(
                ua,
                "select wl_site_coverage_report_classes(%s,%s,%s)",
                sa, start, end,
            )[0]
            step(
                "classes" not in classes
                and classes["multi_recorder"] is True
                and classes["coverage_basis"] == "camera_time"
                and "coverage_ratio" in classes
                and classes["recorder_coverage"]["schema"] == "multi-recorder-coverage-v1",
                "multi-recorder site coverage is camera-time, never relabelled legacy classes",
                json.dumps({k: classes.get(k) for k in
                            ("multi_recorder", "coverage_basis", "coverage_ratio")},
                           default=str),
            )

            # Authenticated wrapper: tenant A can read; tenant B cannot.
            own = as_auth(
                ua,
                "select wl_my_site_recorder_coverage(%s,%s,%s)",
                sa, start, end,
            )[0]
            step(
                own["camera_count"] == 3
                and own["max_affected_cameras"] == 2,
                "owning tenant can read recorder coverage",
            )

            ub, tb, sb = bootstrap(
                "coverage-b@watchlog.test", "Coverage B", "Retail B"
            )
            raised, msg = as_auth_raises(
                ub,
                "select wl_my_site_recorder_coverage(%s,%s,%s)",
                sa, start, end,
            )
            step(
                raised and ("authorized" in msg.lower() or "site" in msg.lower()),
                "another tenant cannot read recorder coverage",
                msg,
            )

            # Current-state trigger: B becomes unavailable, A remains healthy.
            cur.execute("delete from recorder_coverage_intervals where site_id=%s", (sa,))
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report(["1", "2"])),
            )
            down_b = {
                "nvr": {
                    "reachable": False,
                    "auth_ok": None,
                    "reason": "nvr_unreachable",
                    "state": "offline",
                },
                "channels": {"enumerated": False, "reported": []},
            }
            as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, json.dumps(down_b),
            )
            opens = cur.execute(
                """select recorder_id,cause
                     from recorder_coverage_intervals
                    where site_id=%s and ended_at is null""",
                (sa,),
            ).fetchall()
            step(
                len(opens) == 1
                and str(opens[0][0]) == str(rec_b)
                and opens[0][1] == "nvr_unreachable",
                "Recorder B failure opens coverage only for Recorder B",
                str(opens),
            )

            # Exact ACLs.
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

            got = execute_grantees(
                "public.wl_my_site_recorder_coverage(uuid,timestamptz,timestamptz)"
            )
            step(
                got == {"authenticated"},
                "customer recorder-coverage RPC is authenticated only",
                str(sorted(got)),
            )
            for sig in (
                "public.wl_site_recorder_coverage_facts(uuid,timestamptz,timestamptz)",
                "public.wl_set_recorder_coverage_state(uuid,uuid,uuid,boolean,text,timestamptz)",
                "public.wl_site_coverage_legacy_classes(uuid,timestamptz,timestamptz)",
            ):
                got = execute_grantees(sig)
                step(got == set(), f"{sig} is owner-only", str(sorted(got)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

#!/usr/bin/env python3
"""Agent runtime status (0162): real Postgres, rolled back.

wl_report_agent_runtime(uuid,text,jsonb) stores what each Agent worker is doing, and
wl_site_agent_runtime(uuid) lets the site's tenant read it. Proves:
- grants: the report RPC is anon-only (Agents use the publishable key), the read RPC is
  authenticated-only, the helpers and the three tables are closed to every client role;
- authentication: a wrong Agent key is refused (28000);
- the report upserts worker, recorder and agent-level rows; unknown worker names and states,
  malformed ids, another tenant's recorder and duplicate rows are skipped and listed under
  "rejected" while the rest is stored; a failed worker reported healthy is stored unhealthy;
  an error is capped at 500 characters; a future timestamp is clamped to now();
- a later report replaces the set: changed rows update, a worker no longer reported is gone;
- bounds: a non-object payload (22023), over 64 KiB or over 200 workers (54000);
- the agent-level verdict is computed from the stored critical rows, not taken from the payload;
- the read RPC returns the site's runtime to its tenant; another tenant's member gets 42501
  and anon cannot execute it;
- "healthy" needs fresh evidence: rows reported more than 180 s ago read unhealthy and their
  event stream reads unknown;
- wl_agent_report_capabilities keeps agent_runtime_status_v1.
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


def worker(name, state="running", healthy=True, recorder=None, critical=False, **extra):
    row = {"worker": name, "state": state, "healthy": healthy, "enabled": state != "disabled",
           "critical": critical, "recorder_id": recorder, "restart_count": 0,
           "last_success_at": None, "last_error": None, "detail": {}}
    row.update(extra)
    return row


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("refusing: set WATCHLOG_CI_PLAIN_POSTGRES=1 (disposable CI Postgres only)")
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
            def as_auth(uid, sql, *params):
                cur.execute("savepoint auth_sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)",
                            (json.dumps({"sub": str(uid), "role": "authenticated"}),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")
                return row

            def as_role_raises(role, sql, *params, uid=None):
                """(raised, sqlstate) of running sql as role, rolled back to a savepoint."""
                cur.execute("savepoint err_sp")
                if uid is not None:
                    cur.execute("select set_config('request.jwt.claims', %s, true)",
                                (json.dumps({"sub": str(uid), "role": "authenticated"}),))
                cur.execute(f"set local role {role}")
                raised, state = False, None
                try:
                    cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    raised, state = True, exc.sqlstate
                cur.execute("rollback to savepoint err_sp")
                cur.execute("reset role")
                return raised, state

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            def tenant(label):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (f"{label}@watchlog.test",)).fetchone()[0]
                t = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                            f"{label} Tenant", f"{label} Site")[0]["tenant_id"]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (t,)).fetchone()[0]
                return uid, t, site

            def add_agent(tenant_id, site_id, key, suffix):
                return cur.execute(
                    """insert into public.agents(tenant_id,site_id,agent_key_hash,hostname,
                                                 platform,agent_version,last_seen_at)
                       values (%s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),%s,
                               'windows','5.1.2',now()) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}")).fetchone()[0]

            def recorders(agent_id, key, rows):
                return as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)",
                               agent_id, key, json.dumps(rows))[0]

            def report(agent_id, key, status):
                return as_anon("select wl_report_agent_runtime(%s,%s,%s::jsonb)",
                               agent_id, key, json.dumps(status))[0]

            # ---------------------------------------------------------- grants
            report_sig = "public.wl_report_agent_runtime(uuid,text,jsonb)"
            read_sig = "public.wl_site_agent_runtime(uuid)"
            priv = lambda role, sig: cur.execute(  # noqa: E731
                "select has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)).fetchone()[0]
            step(priv("anon", report_sig) and not priv("authenticated", report_sig),
                 "report RPC: anon only (Agents call with the publishable key)")
            step(priv("authenticated", read_sig) and not priv("anon", read_sig),
                 "read RPC: authenticated only")
            for helper in ("public.wl_agent_runtime_workers()", "public.wl_agent_runtime_ts(text)",
                           "public.wl_agent_runtime_bool(jsonb,boolean)",
                           "public.wl_agent_runtime_int(jsonb)"):
                step(not priv("anon", helper) and not priv("authenticated", helper),
                     f"helper {helper} closed to client roles")
            for table in ("agent_runtime_status", "agent_runtime_recorders",
                          "agent_runtime_summary"):
                open_ = cur.execute(
                    """select has_table_privilege('anon', %s, 'SELECT')
                              or has_table_privilege('authenticated', %s, 'SELECT')
                              or has_table_privilege('anon', %s, 'INSERT')""",
                    (f"public.{table}",) * 3).fetchone()[0]
                step(not open_, f"table {table} closed to client roles")

            # ---------------------------------------------------------- fixtures
            uid_a, tenant_a, site_a = tenant("runtime-a")
            uid_b, tenant_b, site_b = tenant("runtime-b")
            key_a, key_b = "runtime-agent-a", "runtime-agent-b"
            agent_a = add_agent(tenant_a, site_a, key_a, "a")
            agent_b = add_agent(tenant_b, site_b, key_b, "b")
            recs_a = recorders(agent_a, key_a, [
                {"local_key": "rt-a1", "display_name": "A1", "is_primary": True,
                 "is_configured": True},
                {"local_key": "rt-a2", "display_name": "A2", "is_primary": False,
                 "is_configured": True}])
            rec_a1, rec_a2 = recs_a["rt-a1"], recs_a["rt-a2"]
            rec_b1 = recorders(agent_b, key_b, [
                {"local_key": "rt-b1", "display_name": "B1", "is_primary": True,
                 "is_configured": True}])["rt-b1"]

            # ---------------------------------------------------------- auth
            raised, state = as_role_raises(
                "anon", "select wl_report_agent_runtime(%s,%s,%s::jsonb)",
                agent_a, "wrong-key", json.dumps({"workers": []}))
            step(raised and state == "28000", "a wrong Agent key is refused (28000)", str(state))

            # ---------------------------------------------------------- first report
            first = {
                "schema": "agent_runtime_status_v1",
                "agent_version": "5.1.2",
                "workers": [
                    worker("collector", recorder=rec_a1, critical=True,
                           last_success_at="2099-01-01T00:00:00+00:00"),
                    worker("collector", "stalled", False, recorder=rec_a2, critical=True,
                           last_error="stalled: no progress for 999s"),
                    worker("health", "failed", True, recorder=rec_a1, critical=True,
                           restart_count=6, last_error="E" * 2000),
                    worker("analytics", "disabled", False, critical=True),
                    worker("bitcoin_miner"),
                    worker("site_control", "zombie"),
                    worker("recovery", recorder="not-a-uuid"),
                    worker("recovery", recorder=rec_b1),
                    worker("collector", recorder=rec_a1, critical=True),
                ],
                "recorders": [
                    {"recorder_id": rec_a1, "event_stream": "up", "credential_unavailable": False,
                     "spool_depth": 3, "spool_overflow": False, "upload_degraded": False},
                    {"recorder_id": rec_a2, "event_stream": "down", "credential_unavailable": True,
                     "spool_depth": None, "spool_overflow": None, "upload_degraded": True},
                    {"recorder_id": rec_b1, "event_stream": "up"},
                    {"recorder_id": rec_a1, "event_stream": "sideways"},
                ],
                "agent": {"spool_depth": 3, "spool_capacity": 400000, "spool_overflow": False,
                          "cloud_upload_last_success_at": "2026-10-07T10:00:00+00:00",
                          "heartbeat_last_success_at": "garbage", "healthy": True},
            }
            res = report(agent_a, key_a, first)
            reasons = sorted(r["reason"] for r in res["rejected"])
            step(res["ok"] is True and res["workers_accepted"] == 4
                 and res["recorders_accepted"] == 2,
                 "valid worker and recorder rows are stored", json.dumps(res)[:300])
            step(reasons == sorted(["unknown worker", "unknown state", "invalid recorder_id",
                                    "recorder is not on this site", "duplicate row",
                                    "recorder is not on this site", "unknown event_stream"]),
                 "unknown names/states, bad ids, another tenant's recorder and duplicates "
                 "are rejected, not stored", json.dumps(reasons))
            rows = {(r[0], str(r[1]) if r[1] else None): r for r in cur.execute(
                """select worker, recorder_id, state, healthy, enabled, critical,
                          last_success_at <= now(), char_length(last_error), restart_count,
                          tenant_id, site_id
                     from agent_runtime_status where agent_id=%s""", (agent_a,)).fetchall()}
            step(set(rows) == {("collector", str(rec_a1)), ("collector", str(rec_a2)),
                               ("health", str(rec_a1)), ("analytics", None)},
                 "one row per (recorder, worker), the site-level worker with no recorder",
                 str(sorted(rows)))
            step(rows[("health", str(rec_a1))][3] is False,
                 "a failed worker reported healthy is stored unhealthy")
            step(rows[("health", str(rec_a1))][7] == 500 and rows[("health", str(rec_a1))][8] == 6,
                 "the error is capped at 500 characters; restart count kept")
            step(rows[("collector", str(rec_a1))][6] is True,
                 "a future last_success_at is clamped to now()")
            step(rows[("analytics", None)][4] is False, "a disabled worker is stored disabled")
            step(all(r[9] == tenant_a and r[10] == site_a for r in rows.values()),
                 "rows carry the Agent's own tenant and site")
            recs = {str(r[0]): r for r in cur.execute(
                """select recorder_id, event_stream, credential_unavailable, spool_depth,
                          upload_degraded from agent_runtime_recorders where agent_id=%s""",
                (agent_a,)).fetchall()}
            step(recs[str(rec_a1)][1:4] == ("up", False, 3)
                 and recs[str(rec_a2)][1:5] == ("down", True, None, True),
                 "per-recorder event stream, login availability, queue and upload state stored")
            summary = cur.execute(
                """select healthy, critical_unhealthy, workers_total, spool_depth,
                          cloud_upload_last_success_at is not null,
                          heartbeat_last_success_at is null, agent_version
                     from agent_runtime_summary where agent_id=%s""", (agent_a,)).fetchone()
            step(summary == (False, 2, 4, 3, True, True, "5.1.2"),
                 "agent-level verdict computed from the stored critical rows, not the payload",
                 str(summary))

            # ---------------------------------------------------------- second report (replace)
            second = {"agent_version": "5.1.2", "workers": [
                worker("collector", recorder=rec_a1, critical=True),
                worker("collector", recorder=rec_a2, critical=True),
                worker("health", recorder=rec_a1, critical=True, restart_count=7),
            ], "recorders": [{"recorder_id": rec_a1, "event_stream": "up"}],
                "agent": {"spool_depth": 0}}
            report(agent_a, key_a, second)
            after = {(r[0], str(r[1]) if r[1] else None): r[2:] for r in cur.execute(
                """select worker, recorder_id, healthy, restart_count
                     from agent_runtime_status where agent_id=%s""", (agent_a,)).fetchall()}
            step(("analytics", None) not in after and len(after) == 3,
                 "a worker no longer reported is removed (the report is the complete set)")
            step(after[("health", str(rec_a1))] == (True, 7), "a changed row is updated in place")
            step(cur.execute("select count(*) from agent_runtime_recorders where agent_id=%s",
                             (agent_a,)).fetchone()[0] == 1,
                 "a recorder no longer reported is removed")
            step(cur.execute("select healthy, critical_unhealthy from agent_runtime_summary "
                             "where agent_id=%s", (agent_a,)).fetchone() == (True, 0),
                 "every enabled critical worker healthy -> the Agent is healthy")

            # ---------------------------------------------------------- bounds
            for payload, want, name in (
                    ([1, 2], "22023", "a non-object payload is refused"),
                    ({"workers": [], "pad": "x" * 70000}, "54000", "a payload over 64 KiB is refused"),
                    ({"workers": [worker("health")] * 201}, "54000",
                     "more than 200 worker rows are refused")):
                raised, state = as_role_raises(
                    "anon", "select wl_report_agent_runtime(%s,%s,%s::jsonb)",
                    agent_a, key_a, json.dumps(payload))
                step(raised and state == want, name, str(state))

            # ---------------------------------------------------------- tenant-scoped read
            own = as_auth(uid_a, "select wl_site_agent_runtime(%s)", site_a)[0]
            agents = own["agents"]
            step(len(agents) == 1 and agents[0]["agent_id"] == str(agent_a)
                 and agents[0]["fresh"] is True and agents[0]["healthy"] is True
                 and len(agents[0]["workers"]) == 3 and len(agents[0]["recorders"]) == 1,
                 "the site's owner reads its Agent runtime", json.dumps(own)[:300])
            raised, state = as_role_raises("authenticated", "select wl_site_agent_runtime(%s)",
                                           site_a, uid=uid_b)
            step(raised and state == "42501",
                 "another tenant's member cannot read this site's runtime (42501)", str(state))
            raised, state = as_role_raises("anon", "select wl_site_agent_runtime(%s)", site_a)
            step(raised and state == "42501", "anon cannot read runtime status", str(state))
            other = as_auth(uid_b, "select wl_site_agent_runtime(%s)", site_b)[0]
            step(other["agents"] == [], "a tenant without reports reads an empty list")

            # ---------------------------------------------------------- freshness
            cur.execute("update agent_runtime_summary set reported_at=now()-interval '10 minutes' "
                        "where agent_id=%s", (agent_a,))
            cur.execute("update agent_runtime_status set reported_at=now()-interval '10 minutes' "
                        "where agent_id=%s", (agent_a,))
            cur.execute("update agent_runtime_recorders set reported_at=now()-interval '10 minutes' "
                        "where agent_id=%s", (agent_a,))
            stale = as_auth(uid_a, "select wl_site_agent_runtime(%s)", site_a)[0]["agents"][0]
            step(stale["fresh"] is False and stale["healthy"] is False
                 and stale["reported_healthy"] is True
                 and all(w["healthy"] is False for w in stale["workers"])
                 and all(r["event_stream"] == "unknown" for r in stale["recorders"]),
                 "a stale report is never read as healthy, and its stream reads unknown")

            # ---------------------------------------------------------- capability
            caps = as_anon("select wl_agent_report_capabilities(%s,%s,%s::jsonb)", agent_a, key_a,
                           json.dumps(["agent_runtime_status_v1", "made_up"]))[0]
            step("agent_runtime_status_v1" in caps["capabilities"]
                 and "made_up" not in caps["capabilities"],
                 "wl_agent_report_capabilities keeps agent_runtime_status_v1",
                 json.dumps(caps["capabilities"]))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

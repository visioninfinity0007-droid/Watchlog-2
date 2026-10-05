#!/usr/bin/env python3
"""0156 production-truth hotfix — REAL Postgres (disposable CI database only).

Proves, against the applied migration chain:

  1. NEW-L1: wl_known_capabilities() contains 'config_snapshot_requests' and is
     otherwise byte-for-byte the body production runs today (pinned by the
     production md5(prosrc) read on 2026-10-05), so wl_agent_report_capabilities
     keeps the capability instead of stripping it, and the 0125 restaurant
     scheduler selects an Agent that advertises it.
  2. wl_agent_semver_triplet() equals the production body exactly and parses
     '5.0.27' as {5,0,27} under standard_conforming_strings=on, so a current Agent
     keeps site_control_runtime / remote_update_v1 and an older one does not.
  3. NEW-L6: wl_expire_stale_snapshot_requests() closes snapshot requests that no
     Agent completed within a bounded window, marks them expired (never as a
     delivered image), leaves fresh requests alone, frees the camera for a new
     request, removes them from the Agent's config poll, is idempotent, and is
     callable by service_role only.

    python prototype/tests/e2e_production_hotfix_0156_pg.py

Requires SUPABASE_DB_* pointing at a disposable Postgres with the chain applied
and WATCHLOG_CI_PLAIN_POSTGRES=1. Never production.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

import psycopg

# Production prosrc of wl_known_capabilities() and wl_agent_semver_triplet(text),
# read with pg_get_functiondef / prosrc on 2026-10-05 (project oyvgubyxmjlijiczjona).
PROD_KNOWN_CAPS_SRC = (
    "\n  select array[\n"
    "    'operations_runtime',\n"
    "    'operations_extended_primitives',\n"
    "    'operations_evidence_still',\n"
    "    'operations_evidence_clip',\n"
    "    'archive_processing',\n"
    "    'multi_agent_fencing',\n"
    "    'recorder_probe_v2',\n"
    "    'site_control_runtime',\n"
    "    'remote_update_v1'\n"
    "  ]\n"
)
PROD_KNOWN_CAPS_MD5 = "1d4e5e4acc69645dd01143cf90115b41"
PROD_SEMVER_SRC = (
    "\ndeclare\n  m text[];\nbegin\n"
    "  m := regexp_match(coalesce(p_version,''), '^([0-9]+)\\.([0-9]+)\\.([0-9]+)');\n"
    "  if m is null then return array[0,0,0]; end if;\n"
    "  return array[m[1]::int,m[2]::int,m[3]::int];\nend\n"
)
PROD_SEMVER_MD5 = "8a4f5c64381d1080dbc565c409b84a27"
# The 0156 body: the production body plus exactly one appended element.
EXPECTED_KNOWN_CAPS_SRC = PROD_KNOWN_CAPS_SRC.replace(
    "    'remote_update_v1'\n",
    "    'remote_update_v1',\n    'config_snapshot_requests'\n",
)

STEPS: list[tuple[bool, str, str]] = []


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  - {detail}" if detail else ""))


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def connect():
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("refusing: set WATCHLOG_CI_PLAIN_POSTGRES=1 (disposable CI Postgres only)")
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_production_hotfix_0156_pg needs " + ", ".join(miss))
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> int:
    # Self-check of the pinned production text before touching the database.
    step(md5(PROD_KNOWN_CAPS_SRC) == PROD_KNOWN_CAPS_MD5,
         "pinned production wl_known_capabilities text matches production md5")
    step(md5(PROD_SEMVER_SRC) == PROD_SEMVER_MD5,
         "pinned production wl_agent_semver_triplet text matches production md5")

    conn = connect()

    def q(sql, *p):
        return conn.execute(sql, p or None).fetchone()

    def as_role(role, sql, *p):
        conn.execute(f"set role {role}")
        try:
            return conn.execute(sql, p or None).fetchone()
        finally:
            conn.execute("reset role")

    def as_user(uid, sql, *p):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": str(uid), "role": "authenticated"}),))
        return as_role("authenticated", sql, *p)

    def proc(sig):
        return q("select prosrc, prosecdef, array_to_string(proconfig, ';'), "
                 "has_function_privilege('anon', oid, 'execute'), "
                 "has_function_privilege('authenticated', oid, 'execute'), "
                 "has_function_privilege('service_role', oid, 'execute') "
                 "from pg_proc where oid = to_regprocedure(%s)", sig)

    # ---------------------------------------------------------------- 1. NEW-L1
    row = proc("public.wl_known_capabilities()")
    step(row is not None, "wl_known_capabilities() exists")
    caps_src = row[0] if row else ""
    step(caps_src == EXPECTED_KNOWN_CAPS_SRC,
         "wl_known_capabilities body = production body + 'config_snapshot_requests' only",
         f"md5 {md5(caps_src)} want {md5(EXPECTED_KNOWN_CAPS_SRC)}")
    step(row is not None and row[2] == "search_path=public",
         "wl_known_capabilities keeps search_path=public", str(row and row[2]))
    step(bool(q("select 'config_snapshot_requests' = any(public.wl_known_capabilities())")[0]),
         "config_snapshot_requests is a known capability")

    # ------------------------------------------------------------- 2. semver
    row = proc("public.wl_agent_semver_triplet(text)")
    step(row is not None and row[0] == PROD_SEMVER_SRC,
         "wl_agent_semver_triplet body equals production exactly",
         f"md5 {md5(row[0]) if row else None} want {PROD_SEMVER_MD5}")
    step(row is not None and row[2] == "search_path=public" and row[1] is False,
         "wl_agent_semver_triplet keeps search_path=public, not security definer")
    scs = q("select current_setting('standard_conforming_strings')")[0]
    for version, want in (("5.0.27", [5, 0, 27]), ("5.0.22", [5, 0, 22]),
                          ("10.2.3-beta", [10, 2, 3]), ("e2e", [0, 0, 0]), (None, [0, 0, 0])):
        got = q("select public.wl_agent_semver_triplet(%s)", version)[0]
        step(got == want, f"semver triplet({version!r}) = {want}",
             f"got {got} (standard_conforming_strings={scs})")

    # ------------------------------------------- disposable tenant / site / agent
    tag = uuid.uuid4().hex[:8]
    uid = q("insert into auth.users (email) values (%s) returning id", f"hotfix-{tag}@example.com")[0]
    boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", f"Hotfix Co {tag}", "Hotfix Site")[0]
    tenant = uuid.UUID(boot["tenant_id"])
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
    cam_a = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Dining') "
              "returning id", tenant, site)[0]
    cam_b = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'2','Till') "
              "returning id", tenant, site)[0]
    cam_c = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'3','Door') "
              "returning id", tenant, site)[0]
    key = "hotfix-agent-key-" + tag
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash, agent_version, last_seen_at) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex'), '5.0.27', now()) returning id",
              tenant, site, key)[0]

    advertised = ["operations_runtime", "config_snapshot_requests", "site_control_runtime",
                  "remote_update_v1", "not_a_real_capability"]
    rep = q("select wl_agent_report_capabilities(%s,%s,%s::jsonb)",
            agent, key, json.dumps(advertised))[0]
    stored = q("select capabilities from agents where id=%s", agent)[0] or []
    step("config_snapshot_requests" in rep.get("capabilities", []) and
         "config_snapshot_requests" in stored,
         "config_snapshot_requests survives wl_agent_report_capabilities", json.dumps(stored))
    step("not_a_real_capability" not in stored, "unknown capabilities are still dropped")
    step("site_control_runtime" in stored and "remote_update_v1" in stored,
         "5.0.27 Agent keeps site_control_runtime and remote_update_v1 (>= 5.0.22 gate)")

    key_old = "hotfix-old-agent-key-" + tag
    agent_old = q("insert into agents (tenant_id, site_id, agent_key_hash, agent_version, last_seen_at) "
                  "values (%s,%s, encode(sha256(%s::bytea),'hex'), '5.0.21', now() - interval '1 hour') "
                  "returning id", tenant, site, key_old)[0]
    q("select wl_agent_report_capabilities(%s,%s,%s::jsonb)", agent_old, key_old, json.dumps(advertised))
    stored_old = q("select capabilities from agents where id=%s", agent_old)[0] or []
    step("site_control_runtime" not in stored_old and "remote_update_v1" not in stored_old
         and "config_snapshot_requests" in stored_old,
         "5.0.21 Agent loses only the version-gated capabilities", json.dumps(stored_old))
    # Keep one Agent per site for the rest of the run (later chains gate the config
    # poll on the current site authority).
    q("delete from agents where id=%s returning id", agent_old)

    # ---------------------------------------- 1b. scheduler selects the Agent
    q("insert into site_business_context (site_id, tenant_id, site_type, open_time, close_time, "
      "working_days) values (%s,%s,'restaurant',null,null,'{}') "
      "on conflict (site_id) do update set open_time=null, close_time=null, working_days='{}' "
      "returning site_id", site, tenant)
    q("insert into restaurant_camera_profiles (camera_id, tenant_id, site_id, analytics_role, "
      "sampling_mode, interval_seconds, enabled) values (%s,%s,%s,'dining_floor','interval',60,true) "
      "returning camera_id", cam_a, tenant, site)
    sched = q("select public.wl_restaurant_schedule_snapshot_requests()")[0]
    n_rest = q("select count(*) from camera_snapshot_requests where camera_id=%s "
               "and request_source='restaurant_analytics' and completed_at is null", cam_a)[0]
    step(n_rest == 1 and int(sched.get("requested", 0)) >= 1,
         "restaurant scheduler selects an Agent advertising config_snapshot_requests",
         json.dumps(sched))

    # Control: with the capability gone, the scheduler must not select the site.
    q("update agents set capabilities='[\"operations_runtime\"]'::jsonb where site_id=%s "
      "returning id", site)
    q("update camera_snapshot_requests set completed_at=now() - interval '1 hour' "
      "where camera_id=%s and completed_at is null returning id", cam_a)
    sched2 = q("select public.wl_restaurant_schedule_snapshot_requests()")[0]
    step(int(sched2.get("requested", -1)) == 0,
         "scheduler does not select a site whose Agent lacks the capability", json.dumps(sched2))
    q("select wl_agent_report_capabilities(%s,%s,%s::jsonb)", agent, key, json.dumps(advertised))

    # ------------------------------------------------------------ 3. NEW-L6
    row = proc("public.wl_expire_stale_snapshot_requests(integer,integer)")
    step(row is not None, "wl_expire_stale_snapshot_requests(integer,integer) exists")
    if row is not None:
        step(row[1] is True and row[2] == "search_path=public, pg_temp",
             "expiry function is security definer with pinned search_path", str(row[2]))
        step(row[3] is False and row[4] is False and row[5] is True,
             "expiry function is executable by service_role only",
             f"anon={row[3]} authenticated={row[4]} service_role={row[5]}")
    col = q("select data_type from information_schema.columns where table_schema='public' "
            "and table_name='camera_snapshot_requests' and column_name='expired_at'")
    step(col is not None and col[0] == "timestamp with time zone",
         "camera_snapshot_requests.expired_at exists")

    if row is not None and col is not None:
        # The expiry is global; close open requests left by other tests or earlier runs
        # on this disposable database so the counts below are exact.
        q("update camera_snapshot_requests set completed_at=now() "
          "where completed_at is null and tenant_id<>%s returning id", tenant)
        old_manual =q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, "
                       "requested_at, request_source) values (%s,%s,%s, now() - interval '13 days', "
                       "'manual') returning id", tenant, site, cam_b)[0]
        fresh_manual = q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, "
                         "requested_at, request_source) values (%s,%s,%s, now() - interval '30 minutes', "
                         "'manual') returning id", tenant, site, cam_c)[0]
        old_rest = q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, "
                     "requested_at, request_source) values (%s,%s,%s, now() - interval '45 minutes', "
                     "'restaurant_analytics') returning id", tenant, site, cam_a)[0]
        delivered = q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, "
                      "requested_at, completed_at, request_source) values "
                      "(%s,%s,%s, now() - interval '20 days', now() - interval '20 days', 'manual') "
                      "returning id", tenant, site, cam_b)[0]

        poll = q("select wl_agent_analytics_config(%s,%s,0)", agent, key)[0]
        ids_before = {r["request_id"] for r in poll.get("snapshot_requests", [])}
        step(str(old_manual) in ids_before, "stale manual request is in the Agent poll before expiry")

        for role in ("anon", "authenticated"):
            denied = False
            try:
                as_role(role, "select public.wl_expire_stale_snapshot_requests()")
            except psycopg.Error:
                denied = True
            step(denied, f"{role} cannot run the expiry")

        n = as_role("service_role", "select public.wl_expire_stale_snapshot_requests()")[0]
        step(n == 2, "default run expires the 13-day manual and 45-minute analytics requests",
             f"expired {n}")

        def state(rid):
            return q("select completed_at is not null, expired_at is not null, "
                     "expired_at is not distinct from completed_at "
                     "from camera_snapshot_requests where id=%s", rid)

        step(state(old_manual) == (True, True, True), "stale manual request closed and marked expired")
        step(state(old_rest) == (True, True, True), "stale analytics request closed and marked expired")
        step(state(fresh_manual) == (False, False, True), "30-minute manual request untouched")
        step(state(delivered)[1] is False, "delivered request is never marked expired")

        poll2 = q("select wl_agent_analytics_config(%s,%s,0)", agent, key)[0]
        ids_after = {r["request_id"] for r in poll2.get("snapshot_requests", [])}
        step(str(old_manual) not in ids_after and str(old_rest) not in ids_after
             and str(fresh_manual) in ids_after,
             "expired requests leave the Agent poll; fresh ones stay")

        again = as_role("service_role", "select public.wl_expire_stale_snapshot_requests()")[0]
        step(again == 0, "second run is a no-op (idempotent)", f"expired {again}")

        renewed = q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, request_source) "
                    "values (%s,%s,%s,'manual') returning id", tenant, site, cam_b)
        step(renewed is not None, "camera with an expired request accepts a new request")

        # Bounds: a tiny or null window is clamped, never expires a fresh request.
        fresh_rest = q("insert into camera_snapshot_requests (tenant_id, site_id, camera_id, "
                       "requested_at, request_source) values (%s,%s,%s, now() - interval '3 minutes', "
                       "'restaurant_analytics') returning id", tenant, site, cam_a)[0]
        n_clamped = as_role("service_role",
                            "select public.wl_expire_stale_snapshot_requests(0, 0)")[0]
        step(n_clamped == 0 and state(fresh_manual)[0] is False and state(fresh_rest)[0] is False,
             "windows are clamped (manual >= 60 min, analytics >= 5 min); (0,0) expires nothing fresh",
             f"expired {n_clamped}")
        n_null = as_role("service_role",
                         "select public.wl_expire_stale_snapshot_requests(null, null)")[0]
        step(n_null == 0, "null windows fall back to the defaults", f"expired {n_null}")
        q("update camera_snapshot_requests set requested_at=now() - interval '90 minutes' "
          "where id=%s returning id", fresh_manual)
        n_tight = as_role("service_role",
                          "select public.wl_expire_stale_snapshot_requests(60, 5)")[0]
        step(n_tight == 1 and state(fresh_manual) == (True, True, True)
             and state(fresh_rest)[0] is False,
             "explicit 60-minute manual window expires a 90-minute request only", f"expired {n_tight}")

        if q("select to_regclass('cron.job') is not null")[0]:
            job = q("select schedule, command from cron.job "
                    "where jobname='watchlog-expire-stale-snapshot-requests'")
            step(job is not None and "wl_expire_stale_snapshot_requests" in (job[1] or ""),
                 "expiry is scheduled server-side (cron shim)", str(job))

    failed = [s for s in STEPS if not s[0]]
    print(f"0156 production hotfix integration: {len(STEPS) - len(failed)} pass, {len(failed)} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

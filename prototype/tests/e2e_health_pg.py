#!/usr/bin/env python3
"""Phase A — REAL Postgres integration for the health foundation (0042-0048).

Unlike the static contract tests (which regex the migration SQL), this DRIVES the migrations against a
real engine — the disposable CI integration Postgres, never production. It applies nothing itself;
ci_prelude.sql + apply_migrations.py run first. Here it seeds a tenant/site/agent/camera + current
health state, then proves the increment-7 fault lifecycle and the customer read model actually EXECUTE
and produce the right rows:

  * an OFFLINE camera opens exactly one camera_offline fault (deduped);
  * a DOWN recorder SUPPRESSES the camera fault (resolves it) and opens nvr_unreachable — the "never
    fabricate a fault you cannot observe" invariant, proven end to end;
  * recovery resolves the recorder fault and RE-OPENS the still-offline camera (the partial-unique
    dedupe index frees the key);
  * wl_sweep_faults() reconciles all sites;
  * wl_site_health_snapshot() returns the per-layer state + open faults to an AUTHENTICATED caller.

Connection comes from SUPABASE_DB_* env vars (set by the CI integration job). Requires psycopg.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

KEY = "integration-agent-key"


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_health_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> None:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()          # noqa: E731
    qa = lambda sql, *p: conn.execute(sql, p or None).fetchall()         # noqa: E731

    # 0. every increment-7 function must exist and be callable
    for fn in ("wl_reconcile_site_faults", "wl_sweep_faults", "wl_ack_fault", "wl_site_health_snapshot"):
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0048 did not apply")

    # 1. authed seed: a signed-in user bootstraps a tenant + site (reuses the real RPC path)
    user = q("insert into auth.users (email) values ('integ@watchlog.test') returning id")[0]
    conn.execute("select set_config('request.jwt.claims', %s, false)",
                 (json.dumps({"sub": str(user), "role": "authenticated"}),))
    conn.execute("set role authenticated")
    boot = q("select wl_bootstrap_tenant('Integration Co', 'Integration Site')")[0]
    conn.execute("reset role")
    tenant = uuid.UUID(boot["tenant_id"])          # jsonb string -> uuid (compared against uuid columns)
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]

    # 2. a recorder/agent + one camera, and their CONFIRMED current health (camera OFFLINE, recorder up)
    agent = q("insert into agents (tenant_id, site_id, agent_key_hash) "
              "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, KEY)[0]
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Front door') "
            "returning id", tenant, site)[0]
    conn.execute("insert into camera_health (camera_id, tenant_id, site_id, health_state, recording_state) "
                 "values (%s,%s,%s,'offline','recording')", (cam, tenant, site))
    conn.execute("insert into nvr_health (agent_id, tenant_id, site_id, nvr_reachable, nvr_auth_ok, storage_state) "
                 "values (%s,%s,%s, true, true, 'ok')", (agent, tenant, site))

    def open_keys():
        return {r[0] for r in qa("select dedupe_key from operational_faults "
                                 "where site_id=%s and state<>'resolved'", site)}

    cam_off = f"camera:{cam}:offline"
    nvr_down = f"nvr:{agent}:unreachable"

    # 3. reconcile: the offline camera opens exactly one deduped fault; the healthy recorder opens none
    q("select wl_reconcile_site_faults(%s)", site)
    assert open_keys() == {cam_off}, f"expected only {cam_off}, got {open_keys()}"
    # idempotent: a second reconcile changes nothing
    r2 = q("select wl_reconcile_site_faults(%s)", site)[0]
    assert r2["opened"] == 0 and r2["resolved"] == 0, f"reconcile not idempotent: {r2}"

    # 4. the recorder goes DOWN — the camera reading is now UNKNOWN, so its fault must be SUPPRESSED
    #    (resolved) and only nvr_unreachable open. This is the core "don't fabricate" invariant, live.
    conn.execute("update nvr_health set nvr_reachable=false where agent_id=%s", (agent,))
    q("select wl_reconcile_site_faults(%s)", site)
    assert open_keys() == {nvr_down}, f"observer-down must suppress camera fault; got {open_keys()}"

    # 5. recovery: the recorder fault resolves and the STILL-offline camera re-opens (dedupe freed)
    conn.execute("update nvr_health set nvr_reachable=true where agent_id=%s", (agent,))
    q("select wl_reconcile_site_faults(%s)", site)
    assert open_keys() == {cam_off}, f"recovery must re-open the camera fault; got {open_keys()}"
    n_offline_rows = q("select count(*) from operational_faults where dedupe_key=%s", cam_off)[0]
    assert n_offline_rows == 2, f"re-open should be a NEW row (old resolved), got {n_offline_rows}"

    # 6. the cron entrypoint reconciles every site without error
    swept = q("select wl_sweep_faults()")[0]
    assert swept["sites"] >= 1, f"sweep should cover >=1 site, got {swept}"

    # 7. the customer read model returns the per-layer state + open faults to an AUTHENTICATED caller
    conn.execute("set role authenticated")
    snap = q("select wl_site_health_snapshot(%s)", site)[0]
    conn.execute("reset role")
    cams = snap["cameras"]
    assert cams and cams[0]["health_state"] == "offline", f"snapshot camera health wrong: {cams}"
    assert cams[0]["inventory_state"] in ("present", "unknown"), "inventory must be distinct from health"
    assert snap["summary"]["offline"] >= 1, f"snapshot summary wrong: {snap['summary']}"
    assert any(f["fault_type"] == "camera_offline" for f in snap["faults"]), "snapshot must list the open fault"
    assert snap["faults_open"] >= 1, "faults_open must reflect the live fault"

    # 8. tenant isolation: a DIFFERENT authenticated user cannot read this site's snapshot
    other = q("insert into auth.users (email) values ('other@watchlog.test') returning id")[0]
    conn.execute("select set_config('request.jwt.claims', %s, false)",
                 (json.dumps({"sub": str(other), "role": "authenticated"}),))
    conn.execute("set role authenticated")
    denied = False
    try:
        q("select wl_site_health_snapshot(%s)", site)
    except psycopg.Error:
        denied = True
    conn.execute("reset role")
    assert denied, "a foreign tenant must NOT read another site's health snapshot"

    print("Health foundation Postgres integration (0042-0048): PASS")


if __name__ == "__main__":
    main()

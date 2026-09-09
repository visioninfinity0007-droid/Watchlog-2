#!/usr/bin/env python3
"""Multi-agent architecture — REAL Postgres integration for the single-authority
ownership lease + fencing token (migration 0052). Disposable CI Postgres only.
Proves, end to end:

  * feature OFF (default): single-agent mode — trivially authoritative, generation 0;
  * feature ON: one primary holds the lease; a second agent is standby;
  * the primary renews without changing the fencing generation;
  * on lease expiry a standby takes over and the generation BUMPS (failover);
  * the superseded generation is FENCED OUT (no dual-active ingestion);
  * clean recovery: release -> the other agent re-acquires, generation bumps again.

Companion to the other e2e_*_pg.py harnesses.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

KEY_A = "ma-agent-key-a"
KEY_B = "ma-agent-key-b"
FUNCS = ("wl_agent_acquire_lease", "wl_agent_is_fenced_authority", "wl_agent_release_lease")


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_multiagent_pg needs " + ", ".join(miss) + " (disposable integration DB)")
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

    def make_agent(tenant, site, key):
        return q("insert into agents (tenant_id, site_id, agent_key_hash) "
                 "values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id", tenant, site, key)[0]

    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0052 did not apply")

    uid = q("insert into auth.users (email) values "
            "('ma-'||gen_random_uuid()::text||'@watchlog.test') returning id")[0]
    boot = as_user(uid, "select wl_bootstrap_tenant('MultiAgent Co','MultiAgent Site')")[0]
    tenant = uuid.UUID(boot["tenant_id"])
    site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
    A = make_agent(tenant, site, KEY_A)
    B = make_agent(tenant, site, KEY_B)

    # 1. feature OFF (default): single-agent mode, trivially authoritative
    off = q("select wl_agent_acquire_lease(%s,%s)", A, KEY_A)[0]
    assert off["granted"] is True and off["generation"] == 0 and off["mode"] == "single_agent", f"off: {off}"
    assert q("select wl_agent_is_fenced_authority(%s,%s)", A, 0)[0] is True, "single-agent must be authoritative"

    # turn the feature ON for this site
    conn.execute("update sites set multi_agent_enabled=true where id=%s", (site,))

    # 2. A acquires the lease -> primary, generation 1
    a1 = q("select wl_agent_acquire_lease(%s,%s,%s)", A, KEY_A, 90)[0]
    assert a1["granted"] is True and a1["generation"] == 1 and a1["mode"] == "primary", f"a1: {a1}"
    assert q("select wl_agent_is_fenced_authority(%s,%s)", A, 1)[0] is True

    # 3. B is standby while A's lease is live
    b1 = q("select wl_agent_acquire_lease(%s,%s,%s)", B, KEY_B, 90)[0]
    assert b1["granted"] is False and b1["mode"] == "standby" and str(b1["holder_agent_id"]) == str(A), f"b1: {b1}"

    # 4. A renews -> generation unchanged (fencing token stable for a stable holder)
    a2 = q("select wl_agent_acquire_lease(%s,%s,%s)", A, KEY_A, 90)[0]
    assert a2["granted"] is True and a2["generation"] == 1, f"renew must keep generation: {a2}"

    # 5. A's lease expires -> B takes over, generation BUMPS (failover)
    conn.execute("update site_agent_leases set lease_expires_at = now() - interval '1 second' where site_id=%s", (site,))
    b2 = q("select wl_agent_acquire_lease(%s,%s,%s)", B, KEY_B, 90)[0]
    assert b2["granted"] is True and b2["generation"] == 2 and b2["mode"] == "primary", f"takeover: {b2}"

    # 6. fencing: B(gen 2) authoritative; A(gen 1) is FENCED OUT -> no dual-active ingestion
    assert q("select wl_agent_is_fenced_authority(%s,%s)", B, 2)[0] is True, "new primary must be authoritative"
    assert q("select wl_agent_is_fenced_authority(%s,%s)", A, 1)[0] is False, "superseded generation must be fenced out"
    a3 = q("select wl_agent_acquire_lease(%s,%s,%s)", A, KEY_A, 90)[0]
    assert a3["granted"] is False and a3["mode"] == "standby", f"a3 should be standby: {a3}"

    # 7. clean recovery: B releases -> A re-acquires, generation bumps again
    rel = q("select wl_agent_release_lease(%s,%s)", B, KEY_B)[0]
    assert rel["released"] is True, f"release: {rel}"
    a4 = q("select wl_agent_acquire_lease(%s,%s,%s)", A, KEY_A, 90)[0]
    assert a4["granted"] is True and a4["generation"] == 3 and a4["mode"] == "primary", f"recovery: {a4}"

    print("Multi-agent lease/fencing Postgres integration (0052): PASS")


if __name__ == "__main__":
    main()

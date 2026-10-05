#!/usr/bin/env python3
"""Site Control write roles (MNVR-050): real Postgres execution.

Rolled back after execution. Proves the recommend/approve tiers that
wl_my_site_diagnosis advertises are enforced by the database itself:
- a viewer cannot propose a recorder write (recommend or managed);
- a viewer cannot approve a proposed write, and the command stays proposed;
- an admin can propose, and an owner or admin can approve;
- another tenant's owner still cannot approve;
- the propose/approve EXECUTE ACLs stay authenticated-facing.
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

# The only recorder model whose safe writes are graded FIELD_VERIFIED in 0061.
VENDOR, MODEL = "Dahua", "DH-XVR1B08-I"
FIRMWARE, FINGERPRINT = "authz-fw-1", "authz-fingerprint-1"


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
                cur.execute("reset role")
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

            def new_user(email):
                return cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (email,),
                ).fetchone()[0]

            def bootstrap(email, company, site_name):
                uid = new_user(email)
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def member(tenant_id, email, role):
                uid = new_user(email)
                cur.execute(
                    "insert into memberships(user_id,tenant_id,role) values (%s,%s,%s)",
                    (uid, tenant_id, role),
                )
                return uid

            def add_agent(tenant_id, site_id, key):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         'authz-agent','windows','5.0.27',now()
                       ) returning id""",
                    (tenant_id, site_id, key),
                ).fetchone()[0]

            owner, tenant, site = bootstrap(
                "authz-owner@watchlog.test", "Authz Tenant", "Authz Site"
            )
            admin = member(tenant, "authz-admin@watchlog.test", "admin")
            viewer = member(tenant, "authz-viewer@watchlog.test", "viewer")
            other_owner, _other_tenant, _other_site = bootstrap(
                "authz-other@watchlog.test", "Other Tenant", "Other Site"
            )

            key = "authz-agent-key"
            agent = add_agent(tenant, site, key)
            recorder = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent, key, json.dumps([{
                    "local_key": "rec-a",
                    "display_name": "Recorder A",
                    "vendor": VENDOR,
                    "model": MODEL,
                    "firmware": FIRMWARE,
                    "identity_fingerprint": FINGERPRINT,
                    "driver": "dahua-cgi",
                    "is_primary": True,
                    "is_configured": True,
                }]),
            )[0]["rec-a"]
            cur.execute("update sites set site_control_enabled=true where id=%s", (site,))

            propose_sql = (
                "select wl_site_command_propose_write("
                "%s,'configure_time',%s::jsonb,%s,'test','role test')"
            )
            params = json.dumps({"recorder_id": str(recorder), "ntp_enabled": True})

            # ----------------------------------------------------------
            # Viewer: neither recommend nor managed.
            # ----------------------------------------------------------
            for mode in ("recommend", "managed"):
                raised, msg = as_auth_raises(viewer, propose_sql, site, params, mode)
                step(
                    raised and "role" in msg.lower(),
                    f"viewer cannot propose a recorder write ({mode})",
                    msg,
                )

            # ----------------------------------------------------------
            # Admin proposes; viewer cannot approve; owner can.
            # ----------------------------------------------------------
            proposed = as_auth(admin, propose_sql, site, params, "recommend")[0]
            step(
                proposed["status"] == "proposed",
                "admin can recommend a FIELD-VERIFIED safe write",
                json.dumps(proposed, default=str),
            )
            cmd = proposed["id"]

            raised, msg = as_auth_raises(
                viewer, "select wl_site_command_approve(%s,'viewer')", cmd
            )
            status = cur.execute(
                "select status from site_commands where id=%s", (cmd,)
            ).fetchone()[0]
            step(
                raised and "role" in msg.lower() and status == "proposed",
                "viewer cannot approve a proposed write; it stays proposed",
                f"{msg} / status={status}",
            )

            raised, msg = as_auth_raises(
                other_owner, "select wl_site_command_approve(%s,'other')", cmd
            )
            step(raised, "another tenant's owner cannot approve", msg)

            approved = as_auth(owner, "select wl_site_command_approve(%s,'owner')", cmd)[0]
            status = cur.execute(
                "select status, detail->>'approved_by' from site_commands where id=%s",
                (cmd,),
            ).fetchone()
            step(
                approved.get("ok") is True and status == ("queued", "owner"),
                "owner can approve a proposed write",
                f"{json.dumps(approved, default=str)} / {status}",
            )

            second = as_auth(owner, propose_sql, site, params, "recommend")[0]
            approved = as_auth(admin, "select wl_site_command_approve(%s,'admin')", second["id"])[0]
            step(approved.get("ok") is True, "admin can approve a proposed write",
                 json.dumps(approved, default=str))

            # ----------------------------------------------------------
            # ACLs stay authenticated-facing (no anon).
            # ----------------------------------------------------------
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
                owner_role = rows[0][1] if rows else None
                return {g for g, _ in rows if g != owner_role}

            for sig in (
                "public.wl_site_command_propose_write(uuid,text,jsonb,text,text,text)",
                "public.wl_site_command_approve(uuid,text)",
            ):
                got = execute_grantees(sig)
                step(
                    "anon" not in got and "PUBLIC" not in got and "authenticated" in got,
                    f"{sig} is authenticated-facing only",
                    str(sorted(got)),
                )

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

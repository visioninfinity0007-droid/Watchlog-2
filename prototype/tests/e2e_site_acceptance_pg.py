#!/usr/bin/env python3
"""Remote full acceptance test (0163): real Postgres execution.

Rolled back after execution. Proves, in the database itself:
- wl_site_run_acceptance needs owner/admin of the site's own account: a viewer, another
  account's owner and anon are refused; it fails closed while Site Control is off, without a
  current Site Agent, and for a recorder that is not this site's;
- a whole-site run is anchored to the primary configured recorder with all_recorders=true;
  a recorder run targets exactly that recorder;
- the Agent claims it through the unchanged claim RPC, and completing it as succeeded
  records one acceptance_runs row (hardware, summary, checks, agent version) through the
  trigger, while a failed run, or any other succeeded command, records nothing;
- wl_site_acceptance_result is tenant-scoped (members incl. viewers; never another
  account) and only answers for acceptance commands;
- acceptance_runs is RLS-scoped per account and not writable by clients;
- wl_site_command_enqueue accepts the new maintenance read actions, still refuses every
  deny-listed action and every non-read tier.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
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

MAINTENANCE_ACTIONS = (
    "run_full_acceptance_test", "run_recording_check", "run_archive_check",
    "collect_diagnostics", "refresh_inventory", "refresh_capabilities",
    "reconnect_recorder", "restart_agent",
)


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def acceptance_result_doc() -> dict:
    """What the Agent's full_acceptance.run_suite returns (abridged)."""
    checks = [
        {"name": "Agent", "scope": "agent", "recorder_id": None, "channel": None,
         "status": "PASS", "supported": True, "enabled": True, "healthy": True,
         "last_success_at": "2026-10-07T10:00:00Z", "last_error": None,
         "evidence": {"version": "5.1.2"}, "duration_ms": 1},
        {"name": "Recording", "scope": "camera", "recorder_id": None, "channel": "2",
         "status": "FAIL", "supported": True, "enabled": True, "healthy": False,
         "last_success_at": None, "last_error": "no recording found in the last 15 min",
         "evidence": {"segments_found": 0}, "duration_ms": 900},
    ]
    return {
        "schema": "watchlog.acceptance.v1",
        "summary": {"passed": 1, "failed": 1, "unknown": 0, "unsupported": 0, "total": 2,
                    "hardware": {"vendor": "Dahua", "model": "DH-XVR1B08-I",
                                 "firmware": "4.001", "serial": "ACCEPT-SERIAL-1"},
                    "agent": {"version": "5.1.2", "build_sha": "c151d52d"},
                    "tested_at": "2026-10-07T10:00:00Z", "recorders": 2},
        "recorders": [{"recorder_id": None, "name": "Recorder A", "primary": True}],
        "checks": checks,
    }


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
                raised, message, code = False, "", None
                try:
                    cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    raised, message = True, str(exc).splitlines()[0]
                    code = getattr(exc, "sqlstate", None)
                cur.execute("rollback to savepoint auth_err")
                cur.execute("reset role")
                return raised, message, code

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
                cur.execute("reset role")
                return raised, message

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
                         'acceptance-agent','windows','5.1.2',now()
                       ) returning id""",
                    (tenant_id, site_id, key),
                ).fetchone()[0]

            owner, tenant, site = bootstrap(
                "accept-owner@watchlog.test", "Accept Tenant", "Accept Site")
            admin = member(tenant, "accept-admin@watchlog.test", "admin")
            viewer = member(tenant, "accept-viewer@watchlog.test", "viewer")
            other_owner, _other_tenant, other_site = bootstrap(
                "accept-other@watchlog.test", "Accept Other", "Accept Other Site")

            key = "acceptance-agent-key"
            agent = add_agent(tenant, site, key)
            mapping = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent, key, json.dumps([
                    {"local_key": "rec-a", "display_name": "Recorder A", "vendor": "Dahua",
                     "model": "DH-XVR1B08-I", "driver": "dahua-cgi", "is_primary": True,
                     "is_configured": True},
                    {"local_key": "rec-b", "display_name": "Recorder B",
                     "vendor": "Hikvision", "model": "DS-7608NI-Q1",
                     "driver": "hikvision-isapi", "is_primary": False,
                     "is_configured": True},
                ]),
            )[0]
            rec_a, rec_b = str(mapping["rec-a"]), str(mapping["rec-b"])
            tenant, site, agent = str(tenant), str(site), str(agent)
            run_sql = "select wl_site_run_acceptance(%s,%s)"

            # ----------------------------------------------------------
            # Gates before anything is queued.
            # ----------------------------------------------------------
            raised, msg, code = as_auth_raises(owner, run_sql, site, None)
            step(raised and code == "55000" and "not enabled" in msg,
                 "fails closed while Site Control is off for the site", f"{code} {msg}")

            cur.execute("update sites set site_control_enabled=true where id in (%s,%s)",
                        (site, other_site))

            raised, msg, code = as_auth_raises(viewer, run_sql, site, None)
            step(raised and "role" in msg.lower(), "a viewer cannot run the acceptance test",
                 msg)
            raised, msg, code = as_auth_raises(other_owner, run_sql, site, None)
            step(raised and code == "42501", "another account's owner cannot run it", msg)
            raised, msg = as_anon_raises(run_sql, site, None)
            step(raised, "anon cannot run it", msg)
            raised, msg, code = as_auth_raises(other_owner, run_sql, other_site, None)
            step(raised and code == "55000" and "Site Agent" in msg,
                 "fails closed for a site without a current Site Agent", msg)
            raised, msg, code = as_auth_raises(owner, run_sql, site, str(uuid.uuid4()))
            step(raised and code == "42501",
                 "a recorder that is not this site's is refused", msg)
            queued = cur.execute(
                "select count(*) from site_commands where site_id=%s", (site,)).fetchone()[0]
            step(queued == 0, "no command was queued by a refused call", str(queued))

            # ----------------------------------------------------------
            # Owner (whole site) and admin (one recorder) can run it.
            # ----------------------------------------------------------
            cmd_all = as_auth(owner, run_sql, site, None)[0]
            row = cur.execute(
                "select action,tier,status,recorder_id,params,created_by from site_commands "
                "where id=%s", (cmd_all,)).fetchone()
            step(row[0] == "run_full_acceptance_test" and row[1] == "read"
                 and row[2] == "queued" and str(row[3]) == rec_a
                 and row[4].get("all_recorders") is True
                 and str(row[5]).startswith("acceptance:"),
                 "owner whole-site run: anchored to the primary recorder, all_recorders",
                 json.dumps(row, default=str))

            cmd_b = as_auth(admin, run_sql, site, rec_b)[0]
            row = cur.execute("select recorder_id,params from site_commands where id=%s",
                              (cmd_b,)).fetchone()
            step(str(row[0]) == rec_b and row[1].get("recorder_id") == rec_b
                 and row[1].get("all_recorders") is False,
                 "admin recorder run targets exactly that recorder", json.dumps(row, default=str))

            # ----------------------------------------------------------
            # The Agent claims and completes; the trigger records the run.
            # ----------------------------------------------------------
            claimed = as_anon("select wl_agent_claim_command(%s,%s)", agent, key)[0]
            command = claimed.get("command") or {}
            step(command.get("id") == str(cmd_all)
                 and command.get("action") == "run_full_acceptance_test"
                 and command.get("recorder_id") == rec_a
                 and (command.get("params") or {}).get("all_recorders") is True,
                 "the current Agent claims it through the unchanged claim RPC",
                 json.dumps(command, default=str))

            done = as_anon("select wl_agent_complete_command(%s,%s,%s,'succeeded',%s::jsonb,null)",
                           agent, key, cmd_all, json.dumps(acceptance_result_doc()))[0]
            run_row = cur.execute(
                "select tenant_id,site_id,recorder_id,agent_id,agent_version,all_recorders,"
                "hardware,summary,jsonb_array_length(checks) from acceptance_runs "
                "where command_id=%s", (cmd_all,)).fetchone()
            step(done.get("ok") is True and run_row is not None
                 and str(run_row[0]) == tenant and str(run_row[1]) == site
                 and run_row[2] is None and str(run_row[3]) == agent
                 and run_row[4] == "5.1.2" and run_row[5] is True
                 and run_row[6].get("serial") == "ACCEPT-SERIAL-1"
                 and run_row[7].get("failed") == 1 and run_row[8] == 2,
                 "a succeeded run is recorded in acceptance_runs",
                 json.dumps(run_row, default=str))

            claimed = as_anon("select wl_agent_claim_command(%s,%s)", agent, key)[0]
            step((claimed.get("command") or {}).get("id") == str(cmd_b),
                 "the recorder run is claimed next", json.dumps(claimed, default=str))
            as_anon("select wl_agent_complete_command(%s,%s,%s,'failed',null,'RuntimeError')",
                    agent, key, cmd_b)
            n = cur.execute("select count(*) from acceptance_runs where command_id=%s",
                            (cmd_b,)).fetchone()[0]
            step(n == 0, "a failed run (the suite could not run) records nothing", str(n))

            # A succeeded non-acceptance command records nothing.
            diag = as_auth(owner, "select wl_site_command_enqueue(%s,'collect_diagnostics',"
                                  "%s::jsonb,'read','test')",
                           site, json.dumps({"recorder_id": rec_a}))[0]
            claimed = as_anon("select wl_agent_claim_command(%s,%s)", agent, key)[0]
            as_anon("select wl_agent_complete_command(%s,%s,%s,'succeeded',%s::jsonb,null)",
                    agent, key, diag, json.dumps({"summary": {"total": 1}}))
            n = cur.execute("select count(*) from acceptance_runs where command_id=%s",
                            (diag,)).fetchone()[0]
            step((claimed.get("command") or {}).get("id") == str(diag) and n == 0,
                 "a succeeded collect_diagnostics command records no acceptance run", str(n))

            # ----------------------------------------------------------
            # Result read: tenant-scoped.
            # ----------------------------------------------------------
            res_sql = "select wl_site_acceptance_result(%s)"
            res = as_auth(owner, res_sql, cmd_all)[0]
            step(res["status"] == "succeeded" and res["recorded"] is True
                 and res["summary"]["passed"] == 1 and len(res["checks"]) == 2
                 and res["all_recorders"] is True and res["agent_version"] == "5.1.2",
                 "owner reads the recorded result", json.dumps(res, default=str)[:300])
            res = as_auth(viewer, res_sql, cmd_all)[0]
            step(res["status"] == "succeeded", "a viewer of the account can read the result")
            res = as_auth(viewer, res_sql, cmd_b)[0]
            step(res["status"] == "failed" and res["recorded"] is False
                 and res["error"] == "RuntimeError", "a failed run reads as failed",
                 json.dumps(res, default=str)[:300])
            raised, msg, code = as_auth_raises(other_owner, res_sql, cmd_all)
            step(raised and code == "42501", "another account cannot read the result", msg)
            raised, msg, code = as_auth_raises(owner, res_sql, diag)
            step(raised and code == "22023", "only acceptance commands are answered", msg)
            raised, msg = as_anon_raises(res_sql, cmd_all)
            step(raised, "anon cannot read results", msg)

            # ----------------------------------------------------------
            # acceptance_runs: RLS per account, no client writes.
            # ----------------------------------------------------------
            seen = as_auth(viewer, "select count(*) from acceptance_runs")[0]
            step(seen == 1, "members see their account's runs", str(seen))
            seen = as_auth(other_owner, "select count(*) from acceptance_runs")[0]
            step(seen == 0, "another account sees none of them", str(seen))
            raised, msg, code = as_auth_raises(
                owner, "insert into acceptance_runs(tenant_id,site_id,command_id) "
                       "values (%s,%s,%s)", tenant, site, cmd_b)
            step(raised and code == "42501", "clients cannot write acceptance_runs", msg)

            # ----------------------------------------------------------
            # Catalog: new read actions accepted; deny-list and tier gate unchanged.
            # ----------------------------------------------------------
            enqueue = "select wl_site_command_enqueue(%s,%s,%s::jsonb,%s,'test')"
            target = json.dumps({"recorder_id": rec_b})
            accepted = []
            for action in MAINTENANCE_ACTIONS:
                row = as_auth(owner, enqueue, site, action, target, "read")
                accepted.append(bool(row and row[0]))
            step(all(accepted), "every maintenance read action is in the read catalog",
                 str(dict(zip(MAINTENANCE_ACTIONS, accepted))))
            for action in ("factory_reset", "reboot_recorder", "format_disk",
                           "firmware_upgrade", "set_password", "delete_rec"):
                raised, msg, code = as_auth_raises(owner, enqueue, site, action, target, "read")
                step(raised and "prohibited action" in msg,
                     f"deny-list still refuses {action}", msg)
            raised, msg, code = as_auth_raises(
                owner, enqueue, site, "run_full_acceptance_test", target, "managed")
            step(raised and "read tier" in msg, "a non-read tier is still refused", msg)
            raised, msg, code = as_auth_raises(
                owner, enqueue, site, "run_full_acceptance_test", "{}", "read")
            step(raised and "recorder target required" in msg,
                 "multi-recorder routing still needs an explicit recorder", msg)

            # ----------------------------------------------------------
            # ACLs: authenticated-facing only.
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

            for sig in ("public.wl_site_run_acceptance(uuid,uuid)",
                        "public.wl_site_acceptance_result(uuid)",
                        "public.wl_site_command_enqueue(uuid,text,jsonb,text,text)"):
                got = execute_grantees(sig)
                step("anon" not in got and "PUBLIC" not in got and "authenticated" in got,
                     f"{sig} is authenticated-facing only", str(sorted(got)))
            got = execute_grantees("public.wl_record_acceptance_run()")
            step(not ({"anon", "authenticated", "PUBLIC"} & got),
                 "the recording trigger function is not client-callable", str(sorted(got)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

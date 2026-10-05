#!/usr/bin/env python3
"""Recorder-scoped Site Control write eligibility (MNVR-049): real Postgres.

Rolled back after execution. Two recorders of the SAME vendor and model sit on
one site. Field evidence exists only for Recorder A. Proves:
- field evidence on one unit never makes a sibling of the same model
  FIELD_VERIFIED; model-only field evidence is capped below FIELD_VERIFIED;
- FIELD_VERIFIED write eligibility needs evidence bound to this recorder's
  id, identity fingerprint AND firmware; a firmware or identity change drops it;
- a proposal approved later is re-checked against the recorder's current
  evidence;
- managed pre-authorization is recorder-scoped; a legacy site-wide row never
  auto-queues a write;
- a recorder with no reported vendor is refused (no vendor default);
- the recorder-scoped resolver is owner-only.
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

# The only model with FIELD_VERIFIED safe-write rows in 0061 (from one unit).
VENDOR, MODEL = "Dahua", "DH-XVR1B08-I"


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

            def as_auth_try(uid, sql, *params):
                """(row, error message) without aborting the outer transaction."""
                cur.execute("savepoint auth_try")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_try")
                    return row, ""
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint auth_try")
                    cur.execute("reset role")
                    return None, str(exc).splitlines()[0]

            def try_sql(sql, *params):
                cur.execute("savepoint try_sp")
                try:
                    row = cur.execute(sql, params or None)
                    out = row.fetchone() if cur.description else None
                    cur.execute("release savepoint try_sp")
                    return out, ""
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint try_sp")
                    return None, str(exc).splitlines()[0]

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            uid = cur.execute(
                "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                ("eligibility@watchlog.test",),
            ).fetchone()[0]
            boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                           "Eligibility Tenant", "Eligibility Site")[0]
            tenant = boot["tenant_id"]
            site = cur.execute(
                "select id from sites where tenant_id=%s order by created_at limit 1",
                (tenant,),
            ).fetchone()[0]
            key = "eligibility-agent-key"
            agent = cur.execute(
                """insert into public.agents(
                     tenant_id,site_id,agent_key_hash,hostname,platform,
                     agent_version,last_seen_at
                   ) values (
                     %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                     'eligibility-agent','windows','5.1.0',now()
                   ) returning id""",
                (tenant, site, key),
            ).fetchone()[0]
            recs = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent, key, json.dumps([
                    {"local_key": "rec-a", "display_name": "Recorder A",
                     "vendor": VENDOR, "model": MODEL, "firmware": "fw-a",
                     "identity_fingerprint": "fp-a", "driver": "dahua-cgi",
                     "is_primary": True, "is_configured": True},
                    {"local_key": "rec-b", "display_name": "Recorder B",
                     "vendor": VENDOR, "model": MODEL, "firmware": "fw-b",
                     "identity_fingerprint": "fp-b", "driver": "dahua-cgi",
                     "is_primary": False, "is_configured": True},
                ]),
            )[0]
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cams = {}
            for name, rec in (("a", rec_a), ("b", rec_b)):
                cams[name] = as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent, key, rec,
                    json.dumps([{"channel": "1", "name": f"{name} cam",
                                 "is_configured": True}]),
                )[0]["1"]
            cur.execute("update sites set site_control_enabled=true where id=%s", (site,))

            def add_evidence(eid, recorder, capability, firmware, fingerprint,
                             operation="read_write", read_back=True):
                return try_sql(
                    """insert into recorder_field_evidence(
                         id,site_id,recorder_id,identity_fingerprint,vendor,model,
                         firmware,capability,operation,result,evidence_class,
                         test_date,read_back_verified
                       ) values (
                         %s,%s,%s,%s,%s,%s,%s,%s,%s,'write applied and read back',
                         'FIELD_VERIFIED',current_date,%s
                       )""",
                    eid, site, recorder, fingerprint, VENDOR, MODEL, firmware,
                    capability, operation, read_back,
                )

            _, err = add_evidence("TEST-ELIG-A-TITLE", rec_a, "channel_title", "fw-a", "fp-a")
            step(err == "", "field evidence can be bound to one recorder identity", err)

            rename = (
                "select wl_site_command_propose_write("
                "%s,'rename_channel',%s::jsonb,%s,'test','eligibility')"
            )

            def rename_params(cam):
                return json.dumps({"camera_id": str(cam), "name": "Front Door"})

            # ----------------------------------------------------------
            # Sibling of the same model with no evidence of its own.
            # ----------------------------------------------------------
            out, err = as_auth_try(uid, rename, site, rename_params(cams["b"]), "recommend")
            step(
                out is None and "field-verified" in err.lower(),
                "same-model sibling without its own evidence cannot be proposed a write",
                err or json.dumps(out[0] if out else None, default=str),
            )

            out, err = as_auth_try(uid, rename, site, rename_params(cams["a"]), "recommend")
            proposal_a = out[0] if out else None
            step(
                proposal_a is not None
                and proposal_a["status"] == "proposed"
                and proposal_a["evidence"] == "FIELD_VERIFIED",
                "recorder with its own matching evidence can be proposed a write",
                err or json.dumps(proposal_a, default=str),
            )

            out, err = try_sql(
                """select wl_recorder_capability_for_recorder(%s,'channel_title'),
                          wl_recorder_capability_for_recorder(%s,'time_ntp_config'),
                          wl_recorder_capability_for_recorder(%s,'channel_title')""",
                rec_b, rec_b, rec_a,
            )
            b_title, b_time, a_title = out if out else (None, None, None)
            step(
                b_title is not None
                and b_title["evidence_class"] == "IMPLEMENTED_UNVERIFIED"
                and b_time["evidence_class"] == "OFFICIAL_DOCUMENTED"
                and a_title["evidence_class"] == "FIELD_VERIFIED"
                and a_title["evidence_scope"] == "recorder",
                "model-only field evidence is capped below FIELD_VERIFIED",
                err or json.dumps([b_title, b_time, a_title], default=str)[:600],
            )

            out, err = try_sql(
                """select wl_recorder_profile_for_recorder(%s),
                          wl_recorder_profile_for_recorder(%s)""",
                rec_a, rec_b,
            )
            prof_a, prof_b = out if out else ([], [])
            by_cap_a = {x["capability"]: x for x in prof_a or []}
            by_cap_b = {x["capability"]: x for x in prof_b or []}
            step(
                by_cap_a.get("channel_title", {}).get("evidence_class") == "FIELD_VERIFIED"
                and by_cap_b.get("channel_title", {}).get("evidence_class")
                == "IMPLEMENTED_UNVERIFIED"
                and all(x["evidence_class"] != "FIELD_VERIFIED" for x in prof_b or []),
                "recorder profile shows FIELD_VERIFIED only from that recorder's evidence",
                err or json.dumps(
                    {k: v.get("evidence_class") for k, v in by_cap_b.items()}
                ),
            )

            # ----------------------------------------------------------
            # Firmware and identity must still match the evidence.
            # ----------------------------------------------------------
            cur.execute("update recorders set firmware='fw-a2' where id=%s", (rec_a,))
            out, err = as_auth_try(uid, rename, site, rename_params(cams["a"]), "recommend")
            step(out is None and "field-verified" in err.lower(),
                 "a firmware change drops recorder write eligibility",
                 err or json.dumps(out[0] if out else None, default=str))

            if proposal_a:
                out, err = as_auth_try(
                    uid, "select wl_site_command_approve(%s,'owner')", proposal_a["id"]
                )
                status = cur.execute(
                    "select status from site_commands where id=%s", (proposal_a["id"],)
                ).fetchone()[0]
                step(
                    (out is None or out[0].get("ok") is False) and status == "proposed",
                    "approval re-checks the recorder's current evidence",
                    f"{err or json.dumps(out[0] if out else None, default=str)} / {status}",
                )
            else:
                step(False, "approval re-checks the recorder's current evidence",
                     "no proposal to approve")

            cur.execute(
                "update recorders set firmware='fw-a', identity_fingerprint='fp-other' where id=%s",
                (rec_a,),
            )
            out, err = as_auth_try(uid, rename, site, rename_params(cams["a"]), "recommend")
            step(out is None and "field-verified" in err.lower(),
                 "a different recorder identity drops write eligibility",
                 err or json.dumps(out[0] if out else None, default=str))
            cur.execute(
                "update recorders set identity_fingerprint='fp-a' where id=%s", (rec_a,)
            )

            _, err = add_evidence("TEST-ELIG-A-READ", rec_a, "time_ntp_config",
                                  "fw-a", "fp-a", operation="read", read_back=False)
            time_params = json.dumps({"recorder_id": str(rec_a), "ntp_enabled": True})
            out, err2 = as_auth_try(
                uid,
                "select wl_site_command_propose_write("
                "%s,'configure_time',%s::jsonb,'recommend','test','read only')",
                site, time_params,
            )
            step(err == "" and out is None and "field-verified" in err2.lower(),
                 "read-only field evidence does not make a write eligible",
                 err or err2 or json.dumps(out[0] if out else None, default=str))

            # ----------------------------------------------------------
            # Managed pre-authorization is recorder-scoped.
            # ----------------------------------------------------------
            _, err = add_evidence("TEST-ELIG-B-TIME", rec_b, "time_ntp_config", "fw-b", "fp-b")
            step(err == "", "Recorder B gets its own time evidence", err)
            cur.execute(
                "insert into site_managed_actions(site_id,action,allowed_by) "
                "values (%s,'configure_time','legacy site-wide')",
                (site,),
            )
            managed = (
                "select wl_site_command_propose_write("
                "%s,'configure_time',%s::jsonb,'managed','test','managed')"
            )
            b_time_params = json.dumps({"recorder_id": str(rec_b), "ntp_enabled": True})
            out, err = as_auth_try(uid, managed, site, b_time_params)
            step(
                out is not None and out[0]["status"] == "proposed",
                "a legacy site-wide managed row never auto-queues a recorder write",
                err or json.dumps(out[0] if out else None, default=str),
            )

            _, err = try_sql(
                "insert into site_managed_actions(site_id,recorder_id,action,allowed_by) "
                "values (%s,%s,'configure_time','owner for A')",
                site, rec_a,
            )
            out_b, err_b = as_auth_try(uid, managed, site, b_time_params)
            step(
                err == "" and out_b is not None and out_b[0]["status"] == "proposed",
                "managed authorization for Recorder A does not auto-queue Recorder B",
                err or err_b or json.dumps(out_b[0] if out_b else None, default=str),
            )
            _, err = try_sql(
                "insert into site_managed_actions(site_id,recorder_id,action,allowed_by) "
                "values (%s,%s,'configure_time','owner for B')",
                site, rec_b,
            )
            out_b, err_b = as_auth_try(uid, managed, site, b_time_params)
            step(
                err == "" and out_b is not None and out_b[0]["status"] == "queued",
                "managed authorization for Recorder B auto-queues Recorder B",
                err or err_b or json.dumps(out_b[0] if out_b else None, default=str),
            )

            # ----------------------------------------------------------
            # No vendor default: an unreported vendor is refused.
            # ----------------------------------------------------------
            cur.execute("update recorders set vendor=null where id=%s", (rec_b,))
            out, err = as_auth_try(uid, managed, site, b_time_params)
            step(out is None and "identity unknown" in err.lower(),
                 "a recorder without a reported vendor is refused, never defaulted",
                 err or json.dumps(out[0] if out else None, default=str))

            # ----------------------------------------------------------
            # ACL: the resolver is an owner-only helper.
            # ----------------------------------------------------------
            for fn in ("wl_recorder_capability_for_recorder",
                       "wl_recorder_profile_for_recorder"):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC'
                                    else a.grantee::regrole::text end,
                              p.proowner::regrole::text
                         from pg_proc p,
                              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                        where p.proname=%s
                          and a.privilege_type='EXECUTE'""",
                    (fn,),
                ).fetchall()
                grantees = {g for g, owner in rows if g != owner}
                exists = cur.execute(
                    "select count(*) from pg_proc where proname=%s", (fn,)
                ).fetchone()[0]
                step(exists == 1 and grantees == set(),
                     f"{fn} is owner-only", str(sorted(grantees)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

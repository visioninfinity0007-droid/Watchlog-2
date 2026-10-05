#!/usr/bin/env python3
"""Recorder-scoped current truth (current proof, faults, recorder events): real Postgres.

Self-contained and rolled back. Two recorders at one site both have a
channel 1. Proves:
- recording/storage current proof is applied per recorder: Recorder A
  recording never marks Recorder B's channel 1 recording, and each
  recorder keeps its own storage proof (MNVR-013);
- the legacy site-scoped current-proof RPC fails closed on a multi-recorder
  site and still works, mirrored to the recorder, on a one-recorder site;
- only the current site Agent can write recorder current proof;
- exact EXECUTE ACLs of the current-proof RPCs.
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


def proof(evidence: str, storage_state: str, storage_reason: str, channels: list) -> str:
    return json.dumps({
        "recording_evidence": evidence,
        "storage": {"state": storage_state, "reason": storage_reason},
        "recording": {"channels": channels},
    })


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
            def one(sql, *params):
                return cur.execute(sql, params or None).fetchone()

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

            def as_anon(sql, *params):
                """(row, message): a database error never aborts the transaction."""
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_sp")
                    return None, str(exc).splitlines()[0]
                cur.execute("reset role")
                cur.execute("release savepoint anon_sp")
                return row, ""

            def bootstrap(email, company, site_name):
                uid = one("insert into auth.users(id,email) values (gen_random_uuid(),%s) "
                          "returning id", email)[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = one("select id from sites where tenant_id=%s order by created_at limit 1",
                           boot["tenant_id"])[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix, seen="now()"):
                return one(
                    f"""insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,
                          agent_version,last_seen_at
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          %s,'windows','5.1.0',{seen}
                        ) returning id""",
                    tenant_id, site_id, key, f"agent-{suffix}")[0]

            def sync_camera(agent_id, key, recorder_id, channel="1"):
                row, msg = as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{"channel": channel, "name": f"Camera {channel}",
                                 "is_configured": True}]))
                assert row, msg
                return row[0][channel]

            def rec_current(camera_id):
                return one("""select rec_current_state,rec_current_evidence
                                from camera_health where camera_id=%s""",
                           camera_id) or (None, None)

            def recorder_storage(recorder_id, agent_id):
                cur.execute("savepoint sto_sp")
                try:
                    row = one("""select sto_current_state,sto_current_reason_code
                                   from recorder_health
                                  where recorder_id=%s and agent_id=%s""",
                              recorder_id, agent_id)
                    cur.execute("release savepoint sto_sp")
                except psycopg.Error:
                    cur.execute("rollback to savepoint sto_sp")
                    row = None
                return row or (None, None)

            # ---------------- multi-recorder site ----------------
            ua, ta, sa = bootstrap("faults-a@watchlog.test", "Faults A", "Warehouse A")
            key_a = "faults-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")
            recs, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent_a, key_a,
                                json.dumps([
                                    {"local_key": "rec-a", "display_name": "Recorder A",
                                     "is_primary": True, "is_configured": True},
                                    {"local_key": "rec-b", "display_name": "Recorder B",
                                     "is_primary": False, "is_configured": True},
                                ]))
            assert recs, msg
            rec_a, rec_b = recs[0]["rec-a"], recs[0]["rec-b"]
            cam_a = sync_camera(agent_a, key_a, rec_a, "1")
            cam_b = sync_camera(agent_a, key_a, rec_b, "1")

            # Recorder A proves recording from its archive; Recorder B is offline.
            ra, msg = as_anon(
                "select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a,
                proof("archive_search", "ok", "ok",
                      [{"channel": "1", "state": "recording", "reason": "ok"}]))
            rb, msg_b = as_anon(
                "select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, proof("unknown", "unknown", "nvr_unreachable", []))
            step(bool(ra) and ra[0].get("ok") is True and ra[0].get("cameras_refreshed") == 1
                 and str(ra[0].get("recorder_id")) == str(rec_a)
                 and bool(rb) and rb[0].get("ok") is True and rb[0].get("cameras_refreshed") == 0,
                 "recorder current proof is accepted per recorder",
                 msg or msg_b or json.dumps([ra and ra[0], rb and rb[0]], default=str))
            a_state, b_state = rec_current(cam_a), rec_current(cam_b)
            step(a_state == ("recording", "archive_search")
                 and b_state[0] != "recording",
                 "Recorder A recording never marks Recorder B's channel 1 recording",
                 str((a_state, b_state)))
            sto_a, sto_b = recorder_storage(rec_a, agent_a), recorder_storage(rec_b, agent_a)
            step(sto_a == ("ok", "ok") and sto_b == ("unknown", "nvr_unreachable"),
                 "each recorder keeps its own storage proof",
                 str((sto_a, sto_b)))

            # A storage fault on B lands on B only.
            as_anon("select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)",
                    agent_a, key_a, rec_b,
                    proof("archive_search", "fault", "disk_error",
                          [{"channel": "1", "state": "storage_fault", "reason": "disk_error"}]))
            a_state, b_state = rec_current(cam_a), rec_current(cam_b)
            sto_a, sto_b = recorder_storage(rec_a, agent_a), recorder_storage(rec_b, agent_a)
            step(a_state[0] == "recording" and b_state[0] == "storage_fault"
                 and sto_a[0] == "ok" and sto_b == ("fault", "disk_error"),
                 "Recorder B's storage fault never flips onto Recorder A",
                 str((a_state, b_state, sto_a, sto_b)))

            # The legacy site-scoped RPC cannot tell the recorders apart.
            legacy, msg = as_anon(
                "select wl_report_recording_storage_current(%s,%s,%s::jsonb)",
                agent_a, key_a,
                proof("archive_search", "ok", "ok",
                      [{"channel": "1", "state": "recording", "reason": "ok"}]))
            step(legacy is None and "ambiguous" in msg.lower()
                 and rec_current(cam_b)[0] == "storage_fault",
                 "legacy current-proof RPC fails closed on a multi-recorder site",
                 msg or json.dumps(legacy and legacy[0], default=str))

            # Only the current site Agent may write recorder current proof.
            key_old = "faults-agent-old"
            agent_old = add_agent(ta, sa, key_old, "old", "now()-interval '2 hours'")
            stale, msg = as_anon(
                "select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)",
                agent_old, key_old, rec_a, proof("unknown", "fault", "disk_error", []))
            step(stale is None and "not current site authority" in msg.lower()
                 and recorder_storage(rec_a, agent_a)[0] == "ok",
                 "a stale Agent cannot write recorder current proof", msg)

            # ---------------- tenant isolation + one-recorder site ----------------
            ub, tb, sb = bootstrap("faults-b@watchlog.test", "Faults B", "Retail B")
            key_b = "faults-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            recs_b, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent_b, key_b,
                                  json.dumps([{"local_key": "rec-c",
                                               "display_name": "Recorder C",
                                               "is_primary": True, "is_configured": True}]))
            assert recs_b, msg
            rec_c = recs_b[0]["rec-c"]
            cam_c = sync_camera(agent_b, key_b, rec_c, "1")

            foreign, msg = as_anon(
                "select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_c, proof("archive_search", "ok", "ok",
                                             [{"channel": "1", "state": "recording"}]))
            step(foreign is None and "not configured for this agent site" in msg.lower(),
                 "an Agent cannot write current proof for another tenant's recorder", msg)

            single, msg = as_anon(
                "select wl_report_recording_storage_current(%s,%s,%s::jsonb)",
                agent_b, key_b,
                proof("archive_search", "degraded", "disk_full",
                      [{"channel": "1", "state": "recording", "reason": "ok"}]))
            nvr = one("""select sto_current_state,sto_current_reason_code
                           from nvr_health where agent_id=%s""", agent_b)
            step(bool(single) and single[0].get("ok") is True
                 and str(single[0].get("recorder_id")) == str(rec_c)
                 and rec_current(cam_c) == ("recording", "archive_search")
                 and nvr == ("degraded", "disk_full")
                 and recorder_storage(rec_c, agent_b) == ("degraded", "disk_full"),
                 "legacy current-proof RPC still works on a one-recorder site and is "
                 "mirrored to its recorder",
                 msg or str((single and single[0], nvr, recorder_storage(rec_c, agent_b))))

            # ---------------- exact EXECUTE ACLs ----------------
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

            for sig, want in (
                ("public.wl_report_recorder_recording_storage_current(uuid,text,uuid,jsonb)",
                 {"anon"}),
                ("public.wl_report_recording_storage_current(uuid,text,jsonb)",
                 {"anon", "authenticated"}),
                ("public.wl_report_recorder_recording_storage_current_core("
                 "uuid,uuid,uuid,uuid,jsonb)", set()),
            ):
                try:
                    cur.execute("savepoint acl_sp")
                    got = execute_grantees(sig)
                    cur.execute("release savepoint acl_sp")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint acl_sp")
                    got = {f"missing: {str(exc).splitlines()[0]}"}
                step(got == want, f"{sig} EXECUTE ACL is exactly {sorted(want) or 'owner only'}",
                     str(sorted(got)))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

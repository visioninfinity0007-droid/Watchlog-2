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
- operational faults are recorder-aware (MNVR-014): an unplugged recorder
  raises 'nvr:<recorder>:...' faults, camera faults follow their own
  recorder's observability, and a missing or frozen nvr_health row neither
  suppresses nor freezes faults; a one-recorder site keeps its Agent-keyed
  fault identity; the cron sweep reaches recorder-health-only sites;
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

            # ---------------- recorder-aware operational faults (MNVR-014) ----------------
            def report_health(agent_id, key, recorder_id, reachable, auth_ok, reason):
                verified = bool(reachable and auth_ok)
                body = {"nvr": {"reachable": reachable, "auth_ok": auth_ok, "reason": reason},
                        "channels": {"enumerated": verified,
                                     "reported": [{"channel": "1", "enabled": True}]
                                     if verified else []}}
                row, msg = as_anon("select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                                   agent_id, key, recorder_id, json.dumps(body))
                assert row, msg

            def camera_offline(recorder_id):
                row, msg = as_anon(
                    "select wl_report_recorder_camera_health(%s,%s,%s,%s::jsonb)",
                    agent_a, key_a, recorder_id,
                    json.dumps({"cameras": [{"channel": "1", "health": "offline",
                                             "reason": "video_loss"}]}))
                assert row, msg

            def open_faults(site):
                return {r[0]: (r[1], str(r[2]) if r[2] else None, str(r[3]) if r[3] else None)
                        for r in cur.execute(
                            """select dedupe_key,reason_code,agent_id,camera_id
                                 from operational_faults
                                where site_id=%s and state<>'resolved'""",
                            (site,)).fetchall()}

            def reconcile(site):
                return one("select wl_reconcile_site_faults(%s)", site)[0]

            report_health(agent_a, key_a, rec_a, True, True, "ok")
            report_health(agent_a, key_a, rec_b, False, None, "nvr_unreachable")
            camera_offline(rec_a)
            camera_offline(rec_b)
            no_mirror = one("select count(*) from nvr_health where agent_id=%s", agent_a)[0]
            reconcile(sa)
            faults = open_faults(sa)
            b_down = f"nvr:{rec_b}:unreachable"
            step(no_mirror == 0 and b_down in faults
                 and faults[b_down] == ("nvr_unreachable", str(agent_a), None),
                 "an unplugged Recorder B raises its own recorder fault with no nvr_health row",
                 str(sorted(faults)))
            step(f"camera:{cam_a}:offline" in faults
                 and f"camera:{cam_b}:offline" not in faults
                 and f"camera:{cam_b}:recording" not in faults
                 and not any(k.startswith(f"nvr:{agent_a}") for k in faults),
                 "camera faults follow their own recorder's observability: A's open, B's "
                 "are withheld while B cannot be observed",
                 str(sorted(faults)))

            # A row left in nvr_health from before the cut-over must neither raise
            # a fault nor hide the cameras of an observable recorder.
            cur.execute("""insert into nvr_health(agent_id,tenant_id,site_id,
                                                  nvr_reachable,nvr_auth_ok,reason_code)
                           values (%s,%s,%s,false,null,'nvr_unreachable')""",
                        (agent_a, ta, sa))
            reconcile(sa)
            faults = open_faults(sa)
            step(f"nvr:{agent_a}:unreachable" not in faults
                 and f"camera:{cam_a}:offline" in faults and b_down in faults,
                 "a frozen nvr_health row neither raises nor suppresses multi-recorder faults",
                 str(sorted(faults)))

            report_health(agent_a, key_a, rec_b, True, True, "ok")
            reconcile(sa)
            faults = open_faults(sa)
            step(b_down not in faults
                 and f"camera:{cam_b}:offline" in faults
                 and faults.get(f"nvr:{rec_b}:storage", (None,))[0] == "storage_fault"
                 and f"camera:{cam_b}:recording" in faults
                 and f"nvr:{rec_a}:storage" not in faults,
                 "Recorder B back: its outage resolves and its own storage and camera "
                 "faults open on B only",
                 str(sorted(faults)))

            report_health(agent_a, key_a, rec_a, True, False, "nvr_auth_failed")
            reconcile(sa)
            faults = open_faults(sa)
            step(f"nvr:{rec_a}:auth" in faults and f"camera:{cam_a}:offline" not in faults
                 and f"camera:{cam_b}:offline" in faults,
                 "Recorder A sign-in failure raises A's auth fault and withholds only A's cameras",
                 str(sorted(faults)))

            cur.execute("""insert into agent_unreachable_intervals(
                             tenant_id,site_id,agent_id,started_at)
                           values (%s,%s,%s,now())""", (ta, sa, agent_a))
            reconcile(sa)
            faults = open_faults(sa)
            step(set(faults) == {f"agent:{agent_a}:unreachable"},
                 "an unreachable Agent raises one Agent fault and withholds every recorder fault",
                 str(sorted(faults)))

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

            # One-recorder site: faults keep their historical Agent-keyed identity,
            # and a bound Agent that reports through the recorder RPC (no
            # nvr_health mirror) is not hidden behind a stale nvr_health row.
            def legacy_health(reachable):
                row, msg = as_anon(
                    "select wl_report_health(%s,%s,%s::jsonb)", agent_b, key_b,
                    json.dumps({"nvr": {"reachable": reachable, "auth_ok": True if reachable
                                        else None,
                                        "reason": "ok" if reachable else "nvr_unreachable"},
                                "channels": {"enumerated": reachable,
                                             "reported": [{"channel": "1", "enabled": True}]
                                             if reachable else []}}))
                assert row, msg

            legacy_health(False)
            reconcile(sb)
            faults = open_faults(sb)
            step(f"nvr:{agent_b}:unreachable" in faults
                 and not any(str(rec_c) in k for k in faults),
                 "one-recorder site keeps the Agent-keyed recorder fault identity",
                 str(sorted(faults)))
            legacy_health(True)
            reconcile(sb)
            faults = open_faults(sb)
            step(f"nvr:{agent_b}:unreachable" not in faults
                 and faults.get(f"nvr:{agent_b}:storage", (None,))[0] == "disk_full",
                 "one-recorder site: recovery resolves it and storage proof raises its fault",
                 str(sorted(faults)))
            report_health(agent_b, key_b, rec_c, False, None, "nvr_unreachable")
            reconcile(sb)
            faults = open_faults(sb)
            step(f"nvr:{agent_b}:unreachable" in faults,
                 "one-recorder site: a recorder-RPC outage report is not hidden by a stale "
                 "nvr_health row",
                 str(sorted(faults)))

            # The cron sweep must also reach a site whose only health rows are
            # recorder_health (multi-recorder Agent, no camera reported yet).
            ud, td, sd = bootstrap("faults-d@watchlog.test", "Faults D", "Depot D")
            key_d = "faults-agent-d"
            agent_d = add_agent(td, sd, key_d, "d")
            recs_d, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent_d, key_d,
                                  json.dumps([
                                      {"local_key": "rec-d1", "display_name": "Recorder D1",
                                       "is_primary": True, "is_configured": True},
                                      {"local_key": "rec-d2", "display_name": "Recorder D2",
                                       "is_primary": False, "is_configured": True},
                                  ]))
            assert recs_d, msg
            report_health(agent_d, key_d, recs_d[0]["rec-d2"], False, None, "nvr_unreachable")
            only_recorder_rows = one(
                """select (select count(*) from nvr_health where site_id=%s)
                        + (select count(*) from camera_health where site_id=%s)""", sd, sd)[0]
            one("select wl_sweep_faults()")
            step(only_recorder_rows == 0
                 and f"nvr:{recs_d[0]['rec-d2']}:unreachable" in open_faults(sd),
                 "the fault sweep reaches a site that only has recorder health",
                 str(sorted(open_faults(sd))))

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

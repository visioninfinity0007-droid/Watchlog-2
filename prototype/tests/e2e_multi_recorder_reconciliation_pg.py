#!/usr/bin/env python3
"""Multi-recorder durable reconciliation (0146): real Postgres execution.

Rolled back after execution. Proves:
- camera health reconciliation resolves recorder+channel;
- recorder-scoped checkpoints persist their recorder provenance;
- camera recording + NVR storage reconcile independently per recorder;
- ACK/disposition + replay idempotence remain intact;
- malformed/foreign recorder ids are rejected per-row, not batch-poisoning;
- legacy payloads keep working on one-recorder sites;
- legacy payloads fail closed once recorder identity is ambiguous;
- legacy storage continues to update nvr_health;
- new storage current state advances in recorder_health.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations" / "0146_multi_recorder_reconciliation.sql"

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
            cur.execute(MIG.read_text(encoding="utf-8"))

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
                return raised, message

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

            def sync_camera(agent_id, key, recorder_id, channel="1"):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{
                        "channel": channel,
                        "name": f"Camera {channel}",
                        "is_configured": True,
                    }]),
                )[0][channel]

            # --------------------------------------------------------------
            # Multi-recorder site A.
            # --------------------------------------------------------------
            ua, ta, sa = bootstrap(
                "reconcile-a@watchlog.test", "Reconcile A", "Warehouse A"
            )
            key_a = "reconcile-agent-a"
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
            cam_a = sync_camera(agent_a, key_a, rec_a, "1")
            cam_b = sync_camera(agent_a, key_a, rec_b, "1")

            epoch = "epoch-multi-a"
            health_tx = [
                {
                    "id": f"{agent_a}:{epoch}:1",
                    "store_epoch": epoch,
                    "seq": 1,
                    "recorder_id": str(rec_a),
                    "layer": "camera",
                    "entity": "1",
                    "from": "unknown",
                    "to": "operational",
                    "reason": "ok",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:00:00Z",
                },
                {
                    "id": f"{agent_a}:{epoch}:2",
                    "store_epoch": epoch,
                    "seq": 2,
                    "recorder_id": str(rec_b),
                    "layer": "camera",
                    "entity": "1",
                    "from": "unknown",
                    "to": "offline",
                    "reason": "nvr_unreachable",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:00:01Z",
                },
            ]
            checkpoints = [
                {
                    "id": f"{agent_a}:{epoch}:cp:3",
                    "store_epoch": epoch,
                    "seq": 3,
                    "recorder_id": str(rec_a),
                    "device_ts": "2026-10-02T13:00:02Z",
                    "nvr_state": "operational",
                    "cameras_observed": 1,
                    "cycle_ok": True,
                },
                {
                    "id": f"{agent_a}:{epoch}:cp:4",
                    "store_epoch": epoch,
                    "seq": 4,
                    "recorder_id": str(rec_b),
                    "device_ts": "2026-10-02T13:00:03Z",
                    "nvr_state": "offline",
                    "cameras_observed": 1,
                    "cycle_ok": True,
                },
            ]

            hres = as_anon(
                "select wl_reconcile_health(%s,%s,%s::jsonb,%s::jsonb,300)",
                agent_a, key_a, json.dumps(health_tx), json.dumps(checkpoints),
            )[0]
            step(
                hres["transitions_applied"] == 2
                and set(hres["accepted_ids"]) == {x["id"] for x in health_tx}
                and hres["checkpoints_applied"] == 2
                and set(hres["checkpoints_accepted_ids"]) == {x["id"] for x in checkpoints},
                "recorder-scoped health transitions/checkpoints are accepted independently",
                json.dumps(hres, default=str),
            )

            states = {
                str(r[0]): r[1]
                for r in cur.execute(
                    "select camera_id,health_state from camera_health where camera_id in (%s,%s)",
                    (cam_a, cam_b),
                ).fetchall()
            }
            step(
                states[str(cam_a)] == "operational"
                and states[str(cam_b)] == "offline",
                "same Channel 1 reconciles to the correct camera per recorder",
                str(states),
            )

            ck_recorders = {
                str(r[0])
                for r in cur.execute(
                    """select recorder_id
                         from local_monitoring_checkpoints
                        where agent_id=%s and checkpoint_id like %s""",
                    (agent_a, f"{agent_a}:{epoch}:cp:%"),
                ).fetchall()
            }
            step(
                ck_recorders == {str(rec_a), str(rec_b)},
                "local monitoring checkpoints retain recorder provenance",
                str(ck_recorders),
            )

            replay = as_anon(
                "select wl_reconcile_health(%s,%s,%s::jsonb,%s::jsonb,300)",
                agent_a, key_a, json.dumps(health_tx), json.dumps(checkpoints),
            )[0]
            step(
                replay["transitions_applied"] == 0
                and set(replay["duplicate_ids"]) == {x["id"] for x in health_tx}
                and replay["checkpoints_applied"] == 0
                and set(replay["checkpoints_duplicate_ids"]) == {x["id"] for x in checkpoints},
                "health replay remains idempotent with exact duplicate dispositions",
            )

            raised, msg = as_anon_raises(
                "select wl_reconcile_health(%s,%s,%s::jsonb,'[]'::jsonb,300)",
                agent_a, key_a,
                json.dumps([{
                    "id": f"{agent_a}:{epoch}:legacy",
                    "store_epoch": epoch,
                    "seq": 9,
                    "layer": "camera",
                    "entity": "1",
                    "to": "offline",
                    "reason": "probe_timeout",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:01:00Z",
                }]),
            )
            step(
                raised and "ambiguous" in msg.lower(),
                "legacy health reconciliation fails closed on multi-recorder site",
                msg,
            )

            # Per-row malformed recorder identity is rejected, not a SQL cast crash.
            malformed = as_anon(
                "select wl_reconcile_health(%s,%s,%s::jsonb,'[]'::jsonb,300)",
                agent_a, key_a,
                json.dumps([{
                    "id": f"{agent_a}:{epoch}:bad-recorder",
                    "store_epoch": epoch,
                    "seq": 10,
                    "recorder_id": "not-a-uuid",
                    "layer": "camera",
                    "entity": "1",
                    "to": "offline",
                    "reason": "unknown",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:01:01Z",
                }]),
            )[0]
            step(
                malformed["transitions_rejected"] == 1
                and malformed["rejected"][0]["reason"] == "invalid_recorder_id",
                "malformed recorder UUID is quarantinable per-row instead of poisoning the batch",
                json.dumps(malformed, default=str),
            )

            # --------------------------------------------------------------
            # Recorder-aware recording + storage reconciliation.
            # --------------------------------------------------------------
            rs_epoch = "epoch-rs-a"
            rs_tx = [
                {
                    "id": f"{agent_a}:{rs_epoch}:1",
                    "store_epoch": rs_epoch,
                    "seq": 1,
                    "recorder_id": str(rec_a),
                    "layer": "camera_recording",
                    "entity": "1",
                    "from": "unknown",
                    "to": "recording",
                    "reason": "ok",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:10:00Z",
                },
                {
                    "id": f"{agent_a}:{rs_epoch}:2",
                    "store_epoch": rs_epoch,
                    "seq": 2,
                    "recorder_id": str(rec_b),
                    "layer": "camera_recording",
                    "entity": "1",
                    "from": "unknown",
                    "to": "not_recording",
                    "reason": "not_recording",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:10:01Z",
                },
                {
                    "id": f"{agent_a}:{rs_epoch}:3",
                    "store_epoch": rs_epoch,
                    "seq": 3,
                    "recorder_id": str(rec_a),
                    "layer": "nvr_storage",
                    "entity": "nvr",
                    "from": "unknown",
                    "to": "ok",
                    "reason": "ok",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:10:02Z",
                },
                {
                    "id": f"{agent_a}:{rs_epoch}:4",
                    "store_epoch": rs_epoch,
                    "seq": 4,
                    "recorder_id": str(rec_b),
                    "layer": "nvr_storage",
                    "entity": "nvr",
                    "from": "unknown",
                    "to": "fault",
                    "reason": "disk_error",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:10:03Z",
                },
            ]
            rsres = as_anon(
                "select wl_reconcile_recording_storage(%s,%s,%s::jsonb,300)",
                agent_a, key_a, json.dumps(rs_tx),
            )[0]
            step(
                rsres["transitions_applied"] == 4
                and set(rsres["accepted_ids"]) == {x["id"] for x in rs_tx},
                "camera recording + NVR storage transitions accept recorder identity",
                json.dumps(rsres, default=str),
            )

            rec_states = {
                str(r[0]): r[1]
                for r in cur.execute(
                    "select camera_id,recording_state from camera_health where camera_id in (%s,%s)",
                    (cam_a, cam_b),
                ).fetchall()
            }
            step(
                rec_states[str(cam_a)] == "recording"
                and rec_states[str(cam_b)] == "not_recording",
                "recording current state resolves same Channel 1 independently",
                str(rec_states),
            )

            storage_states = {
                str(r[0]): r[1]
                for r in cur.execute(
                    """select recorder_id,storage_state
                         from recorder_health
                        where agent_id=%s and recorder_id in (%s,%s)""",
                    (agent_a, rec_a, rec_b),
                ).fetchall()
            }
            step(
                storage_states[str(rec_a)] == "ok"
                and storage_states[str(rec_b)] == "fault",
                "NVR storage current state advances independently per recorder",
                str(storage_states),
            )

            storage_ledger = {
                str(r[0])
                for r in cur.execute(
                    """select recorder_id
                         from storage_transitions
                        where agent_id=%s and store_epoch=%s""",
                    (agent_a, rs_epoch),
                ).fetchall()
            }
            step(
                storage_ledger == {str(rec_a), str(rec_b)},
                "storage ledger persists recorder provenance",
                str(storage_ledger),
            )

            rs_replay = as_anon(
                "select wl_reconcile_recording_storage(%s,%s,%s::jsonb,300)",
                agent_a, key_a, json.dumps(rs_tx),
            )[0]
            step(
                rs_replay["transitions_applied"] == 0
                and set(rs_replay["duplicate_ids"]) == {x["id"] for x in rs_tx},
                "recording/storage replay stays idempotent",
            )

            raised, msg = as_anon_raises(
                "select wl_reconcile_recording_storage(%s,%s,%s::jsonb,300)",
                agent_a, key_a,
                json.dumps([{
                    "id": f"{agent_a}:{rs_epoch}:legacy",
                    "store_epoch": rs_epoch,
                    "seq": 20,
                    "layer": "nvr_storage",
                    "entity": "nvr",
                    "to": "fault",
                    "reason": "disk_error",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:11:00Z",
                }]),
            )
            step(
                raised and "ambiguous" in msg.lower(),
                "legacy recording/storage reconciliation fails closed on multi-recorder site",
                msg,
            )

            # --------------------------------------------------------------
            # Tenant B: foreign recorder ID rejected per row.
            # --------------------------------------------------------------
            ub, tb, sb = bootstrap(
                "reconcile-b@watchlog.test", "Reconcile B", "Retail B"
            )
            key_b = "reconcile-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_c = sync_recorders(agent_b, key_b, [{
                "local_key": "rec-c",
                "display_name": "Recorder C",
                "is_primary": True,
                "is_configured": True,
            }])["rec-c"]
            cam_c = sync_camera(agent_b, key_b, rec_c, "1")

            foreign = as_anon(
                "select wl_reconcile_health(%s,%s,%s::jsonb,'[]'::jsonb,300)",
                agent_a, key_a,
                json.dumps([{
                    "id": f"{agent_a}:{epoch}:foreign",
                    "store_epoch": epoch,
                    "seq": 30,
                    "recorder_id": str(rec_c),
                    "layer": "camera",
                    "entity": "1",
                    "to": "operational",
                    "reason": "ok",
                    "source": "probe",
                    "device_ts": "2026-10-02T13:12:00Z",
                }]),
            )[0]
            step(
                foreign["transitions_rejected"] == 1
                and foreign["rejected"][0]["reason"] == "invalid_recorder",
                "foreign-tenant recorder is rejected per-row by reconciliation",
                json.dumps(foreign, default=str),
            )

            # --------------------------------------------------------------
            # Singleton compatibility.
            # --------------------------------------------------------------
            legacy_epoch = "epoch-legacy-b"
            legacy_health = [{
                "id": f"{agent_b}:{legacy_epoch}:1",
                "store_epoch": legacy_epoch,
                "seq": 1,
                "layer": "camera",
                "entity": "1",
                "from": "unknown",
                "to": "operational",
                "reason": "ok",
                "source": "probe",
                "device_ts": "2026-10-02T14:00:00Z",
            }]
            legacy_cp = [{
                "id": f"{agent_b}:{legacy_epoch}:cp:2",
                "store_epoch": legacy_epoch,
                "seq": 2,
                "device_ts": "2026-10-02T14:00:01Z",
                "nvr_state": "operational",
                "cameras_observed": 1,
                "cycle_ok": True,
            }]
            legacy_hres = as_anon(
                "select wl_reconcile_health(%s,%s,%s::jsonb,%s::jsonb,300)",
                agent_b, key_b, json.dumps(legacy_health), json.dumps(legacy_cp),
            )[0]
            step(
                legacy_hres["transitions_applied"] == 1
                and legacy_hres["checkpoints_applied"] == 1,
                "legacy health payload remains accepted on singleton site",
            )
            cp_rid = cur.execute(
                "select recorder_id from local_monitoring_checkpoints where checkpoint_id=%s",
                (legacy_cp[0]["id"],),
            ).fetchone()[0]
            step(
                cp_rid is None,
                "legacy checkpoint keeps NULL recorder_id for historical site-wide semantics",
            )

            legacy_rs = [{
                "id": f"{agent_b}:{legacy_epoch}:3",
                "store_epoch": legacy_epoch,
                "seq": 3,
                "layer": "nvr_storage",
                "entity": "nvr",
                "from": "unknown",
                "to": "ok",
                "reason": "ok",
                "source": "probe",
                "device_ts": "2026-10-02T14:00:02Z",
            }]
            legacy_rsres = as_anon(
                "select wl_reconcile_recording_storage(%s,%s,%s::jsonb,300)",
                agent_b, key_b, json.dumps(legacy_rs),
            )[0]
            legacy_nvr = cur.execute(
                "select storage_state from nvr_health where agent_id=%s",
                (agent_b,),
            ).fetchone()
            legacy_storage_rid = cur.execute(
                "select recorder_id from storage_transitions where dedupe_key=%s",
                (legacy_rs[0]["id"],),
            ).fetchone()[0]
            step(
                legacy_rsres["transitions_applied"] == 1
                and legacy_nvr is not None and legacy_nvr[0] == "ok"
                and legacy_storage_rid is None,
                "legacy storage keeps nvr_health + NULL recorder ledger compatibility",
            )

            # Exact execute ACLs remain the deployed surface.
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

            for sig in (
                "public.wl_reconcile_health(uuid,text,jsonb,jsonb,integer)",
                "public.wl_reconcile_recording_storage(uuid,text,jsonb,integer)",
            ):
                got = execute_grantees(sig)
                step(
                    got == {"anon", "authenticated"},
                    f"{sig} keeps exact deployed EXECUTE ACL",
                    str(sorted(got)),
                )

            got = execute_grantees("public.wl_try_uuid(text)")
            step(got == set(), "wl_try_uuid helper is owner-only", str(sorted(got)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

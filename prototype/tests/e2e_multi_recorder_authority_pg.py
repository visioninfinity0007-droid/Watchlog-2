#!/usr/bin/env python3
"""Multi-recorder current-Agent authority gate.

Runs after 0146-0154 on disposable Postgres and rolls back.

A valid but stale enrolled Agent must not be able to mutate recorder-aware state
or claim recorder-routed work. This is server-enforced; runtime lease fencing is
not treated as the security boundary.

A superseded Agent that keeps running (an old PC left on after a replacement was
enrolled) must not take site authority back with its own heartbeat while the
replacement is online. Failover to it still works once the replacement goes
offline, and the replacement regains authority when it reports again and keeps
it against the older Agent's later heartbeats.
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

            uid = cur.execute(
                "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                ("authority@watchlog.test",),
            ).fetchone()[0]
            boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                           "Authority Test", "Warehouse Authority")[0]
            tenant_id = boot["tenant_id"]
            site_id = cur.execute(
                "select id from sites where tenant_id=%s order by created_at limit 1",
                (tenant_id,),
            ).fetchone()[0]

            current_key = "current-agent-key"
            stale_key = "stale-agent-key"
            current_agent = cur.execute(
                """insert into agents(
                     tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
                     last_seen_at,enrolled_at
                   ) values (
                     %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                     'current-agent','windows','5.1.0',now(),now()
                   ) returning id""",
                (tenant_id, site_id, current_key),
            ).fetchone()[0]
            stale_agent = cur.execute(
                """insert into agents(
                     tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
                     last_seen_at,enrolled_at
                   ) values (
                     %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                     'stale-agent','windows','5.1.0',
                     '2000-01-01'::timestamptz,'2000-01-01'::timestamptz
                   ) returning id""",
                (tenant_id, site_id, stale_key),
            ).fetchone()[0]

            recorder_map = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                current_agent, current_key,
                json.dumps([
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
                ]),
            )[0]
            rec_a = recorder_map["rec-a"]
            cam_map = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                current_agent, current_key, rec_a,
                json.dumps([{"channel": "1", "name": "A1", "is_configured": True}]),
            )[0]
            camera_id = cam_map["1"]

            step(
                str(cur.execute("select wl_current_site_agent(%s)", (site_id,)).fetchone()[0])
                == str(current_agent),
                "current Agent is deterministic before stale probes",
            )

            health = json.dumps({
                "nvr": {"reachable": True, "auth_ok": True, "reason": "ok", "state": "operational"},
                "channels": {"enumerated": True, "reported": [{"channel": "1", "enabled": True}]},
            })
            camera_health = json.dumps({
                "cameras": [{"channel": "1", "health": "operational", "reason": "ok"}]
            })
            event = json.dumps([{
                "recorder_id": str(rec_a),
                "channel": "1",
                "event_type": "authority_probe",
                "device_ts": "2026-10-04T10:00:00Z",
                "agent_ts": "2026-10-04T10:00:00Z",
            }])
            health_tx = json.dumps([{
                "id": f"{stale_agent}:authority:1",
                "store_epoch": "authority",
                "seq": 1,
                "recorder_id": str(rec_a),
                "layer": "camera",
                "entity": "1",
                "from": "unknown",
                "to": "operational",
                "reason": "ok",
                "source": "probe",
                "device_ts": "2026-10-04T10:00:00Z",
            }])
            storage_tx = json.dumps([{
                "id": f"{stale_agent}:authority:2",
                "store_epoch": "authority",
                "seq": 2,
                "recorder_id": str(rec_a),
                "layer": "nvr_storage",
                "entity": "nvr",
                "from": "unknown",
                "to": "ok",
                "reason": "ok",
                "source": "probe",
                "device_ts": "2026-10-04T10:00:01Z",
            }])
            analytic = json.dumps([{
                "recorder_id": str(rec_a),
                "channel": "1",
                "event_type": "measurement",
                "occurred_at": "2026-10-04T10:00:00Z",
                "dedupe_key": "authority-analytic",
                "metadata": {},
            }])

            calls = [
                ("explicit event ingest",
                 "select wl_ingest_events(%s,%s,%s::jsonb)",
                 (stale_agent, stale_key, event)),
                ("recorder health",
                 "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                 (stale_agent, stale_key, rec_a, health)),
                ("recorder camera health",
                 "select wl_report_recorder_camera_health(%s,%s,%s,%s::jsonb)",
                 (stale_agent, stale_key, rec_a, camera_health)),
                ("open recorder recovery",
                 "select wl_open_recorder_recovery_interval(%s,%s,%s,%s::timestamptz,%s::timestamptz,array['1']::text[])",
                 (stale_agent, stale_key, rec_a, "2026-10-04T09:00:00Z", "2026-10-04T09:05:00Z")),
                ("claim recorder recovery",
                 "select wl_agent_claim_recorder_recovery(%s,%s,%s,1,900)",
                 (stale_agent, stale_key, rec_a)),
                ("complete recorder recovery",
                 "select wl_complete_recorder_recovery(%s,%s,%s,%s,'recovered',0,'{}'::jsonb,'{}'::jsonb)",
                 (stale_agent, stale_key, rec_a, str(uuid.uuid4()))),
                ("durable health reconciliation",
                 "select wl_reconcile_health(%s,%s,%s::jsonb,'[]'::jsonb,300)",
                 (stale_agent, stale_key, health_tx)),
                ("durable storage reconciliation",
                 "select wl_reconcile_recording_storage(%s,%s,%s::jsonb,300)",
                 (stale_agent, stale_key, storage_tx)),
                ("recorder analytics ingest",
                 "select wl_ingest_analytic_events(%s,%s,%s::jsonb)",
                 (stale_agent, stale_key, analytic)),
                ("incident clip claim",
                 "select wl_agent_claim_clip_requests(%s,%s,1)",
                 (stale_agent, stale_key)),
                ("incident still claim",
                 "select wl_agent_claim_incident_stills(%s,%s,1)",
                 (stale_agent, stale_key)),
                ("analytics config",
                 "select wl_agent_analytics_config(%s,%s,0)",
                 (stale_agent, stale_key)),
                ("config snapshot upload",
                 "select wl_upload_config_snapshot(%s,%s,%s,'YWJj','image/jpeg')",
                 (stale_agent, stale_key, camera_id)),
                ("archive scan claim",
                 "select wl_agent_claim_archive_scans(%s,%s,1)",
                 (stale_agent, stale_key)),
            ]

            for name, sql, params in calls:
                raised, message = as_anon_raises(sql, *params)
                step(
                    raised and "current site authority" in message.lower(),
                    f"stale Agent blocked: {name}",
                    message,
                )

            step(
                str(cur.execute("select wl_current_site_agent(%s)", (site_id,)).fetchone()[0])
                == str(current_agent),
                "stale attempts cannot promote themselves by advancing last_seen_at",
            )

            def current_site_agent():
                return str(cur.execute(
                    "select wl_current_site_agent(%s)", (site_id,)
                ).fetchone()[0])

            # The superseded Agent keeps heartbeating (5.0.x calls wl_heartbeat
            # every minute) while the replacement last reported 30 s ago.
            cur.execute(
                "update agents set last_seen_at=now()-interval '30 seconds' where id=%s",
                (current_agent,),
            )
            beat = as_anon(
                "select wl_heartbeat(%s,%s,%s)", stale_agent, stale_key, "5.0.27",
            )[0]
            stale_seen = cur.execute(
                "select last_seen_at=now() from agents where id=%s", (stale_agent,)
            ).fetchone()[0]
            step(beat.get("ok") is True and stale_seen is True,
                 "superseded Agent heartbeat still succeeds and records its liveness")
            step(current_site_agent() == str(current_agent),
                 "superseded Agent heartbeat cannot take site authority from the online replacement")
            raised, message = as_anon_raises(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                current_agent, current_key, rec_a, health,
            )
            step(not raised,
                 "current Agent's recorder RPC still succeeds after the stale heartbeat",
                 message)
            raised, message = as_anon_raises(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                stale_agent, stale_key, rec_a, health,
            )
            step(raised and "current site authority" in message.lower(),
                 "heartbeating superseded Agent is still blocked from recorder state", message)

            # Failover is preserved: once the replacement is offline, the older
            # Agent's heartbeat makes it the site authority...
            cur.execute(
                "update agents set last_seen_at=now()-interval '10 minutes' where id=%s",
                (current_agent,),
            )
            as_anon("select wl_heartbeat(%s,%s,%s)", stale_agent, stale_key, "5.0.27")
            step(current_site_agent() == str(stale_agent),
                 "older Agent takes over when the replacement has gone offline")
            # ...and the replacement takes it back as soon as it reports again.
            # The whole script shares one now(), so reports are aged to give each
            # heartbeat its own time: the older Agent last reported 30 s ago...
            cur.execute(
                "update agents set last_seen_at=now()-interval '30 seconds' where id=%s",
                (stale_agent,),
            )
            as_anon("select wl_heartbeat(%s,%s,%s)", current_agent, current_key, "5.1.0")
            step(current_site_agent() == str(current_agent),
                 "replacement regains site authority when it reports again")
            # ...and its next heartbeat lands 20 s after the replacement's.
            cur.execute(
                "update agents set last_seen_at=now()-interval '20 seconds' where id=%s",
                (current_agent,),
            )
            as_anon("select wl_heartbeat(%s,%s,%s)", stale_agent, stale_key, "5.0.27")
            step(current_site_agent() == str(current_agent),
                 "older Agent heartbeat after the replacement reports cannot take authority back")

            # Current authority still uses the new path.
            ok = as_anon(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                current_agent, current_key, rec_a, health,
            )[0]
            step(ok.get("ok") is True, "current Agent retains recorder-health access")

            # Internal assertion helper is not directly executable by Agent/browser roles.
            rows = cur.execute(
                """select case when a.grantee=0 then 'PUBLIC' else a.grantee::regrole::text end,
                          p.proowner::regrole::text
                     from pg_proc p,
                          aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                    where p.oid='public.wl_assert_current_agent_authority(uuid,uuid)'::regprocedure
                      and a.privilege_type='EXECUTE'"""
            ).fetchall()
            owner = rows[0][1] if rows else None
            external = {g for g, _ in rows if g != owner}
            step(external == set(), "authority assertion helper is owner-only", str(sorted(external)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

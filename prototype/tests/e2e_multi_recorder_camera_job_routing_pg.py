#!/usr/bin/env python3
"""Multi-recorder camera/job routing (0150): real Postgres execution.

Rolled back after execution. Proves:
- multi-recorder Site Control requires a deterministic recorder target;
- camera_id derives recorder_id and canonical channel;
- mismatched camera/recorder targets fail closed;
- clip and still claims return the recorder owning the camera;
- analytics config snapshot requests return recorder_id;
- restaurant/config snapshot visual_sample ingestion carries recorder_id;
- archive scan claims return explicit camera->recorder->channel targets;
- contract v2 advertises recorder_job_routing;
- public EXECUTE ACLs remain unchanged/narrow.
"""
from __future__ import annotations

import base64
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

            def add_agent(tenant_id, site_id, key):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         'job-routing-agent','windows','5.0.27',now()
                       ) returning id""",
                    (tenant_id, site_id, key),
                ).fetchone()[0]

            def sync_recorders(agent_id, key):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps([
                        {
                            "local_key": "rec-a",
                            "display_name": "Recorder A",
                            "vendor": "Hikvision",
                            "model": "TEST-A",
                            "driver": "onvif",
                            "is_primary": True,
                            "is_configured": True,
                        },
                        {
                            "local_key": "rec-b",
                            "display_name": "Recorder B",
                            "vendor": "Dahua",
                            "model": "TEST-B",
                            "driver": "onvif",
                            "is_primary": False,
                            "is_configured": True,
                        },
                    ]),
                )[0]

            def sync_camera(agent_id, key, recorder_id, name):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{
                        "channel": "1",
                        "name": name,
                        "is_configured": True,
                    }]),
                )[0]["1"]

            user, tenant, site = bootstrap(
                "routing@watchlog.test", "Routing Tenant", "Routing Site"
            )
            key = "routing-agent-key"
            agent = add_agent(tenant, site, key)
            recs = sync_recorders(agent, key)
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cam_a = sync_camera(agent, key, rec_a, "A Camera 1")
            cam_b = sync_camera(agent, key, rec_b, "B Camera 1")
            cur.execute(
                "update cameras set analytics_enabled=true where id in (%s,%s)",
                (cam_a, cam_b),
            )
            cur.execute("update sites set site_control_enabled=true where id=%s", (site,))

            # ------------------------------------------------------------------
            # Site Control: no target is ambiguous; camera target derives recorder.
            # ------------------------------------------------------------------
            raised, msg = as_auth_raises(
                user,
                "select wl_site_command_enqueue(%s,'get_channels','{}'::jsonb,'read','test')",
                site,
            )
            step(
                raised and "recorder target required" in msg.lower(),
                "multi-recorder Site Control refuses an untargeted command",
                msg,
            )

            cmd_b = as_auth(
                user,
                "select wl_site_command_enqueue(%s,'get_channels',%s::jsonb,'read','test')",
                site, json.dumps({"recorder_id": str(rec_b)}),
            )[0]
            stored = cur.execute(
                "select recorder_id from site_commands where id=%s", (cmd_b,)
            ).fetchone()[0]
            step(str(stored) == str(rec_b),
                 "explicit recorder Site Control target is persisted")

            cmd_a = as_auth(
                user,
                "select wl_site_command_enqueue(%s,'request_snapshot',%s::jsonb,'read','test')",
                site, json.dumps({"camera_id": str(cam_a)}),
            )[0]
            stored_a = cur.execute(
                "select recorder_id,params->>'channel' from site_commands where id=%s",
                (cmd_a,),
            ).fetchone()
            step(
                str(stored_a[0]) == str(rec_a) and stored_a[1] == "1",
                "camera target derives recorder and canonical channel",
                str(stored_a),
            )

            raised, msg = as_auth_raises(
                user,
                "select wl_site_command_enqueue(%s,'request_snapshot',%s::jsonb,'read','test')",
                site,
                json.dumps({
                    "camera_id": str(cam_a),
                    "recorder_id": str(rec_b),
                    "channel": "1",
                }),
            )
            step(
                raised and "do not match" in msg.lower(),
                "mismatched camera and recorder targets fail closed",
                msg,
            )

            claimed_cmd = as_anon(
                "select wl_agent_claim_command(%s,%s)", agent, key
            )[0]
            target = (claimed_cmd.get("command") or {}).get("recorder_id")
            step(
                str(target) in {str(rec_a), str(rec_b)},
                "claimed Site Control work returns recorder_id",
                json.dumps(claimed_cmd, default=str),
            )

            # ------------------------------------------------------------------
            # Incident footage: identical Channel 1 on A/B routes by camera UUID.
            # ------------------------------------------------------------------
            # A clip request links exactly one event or incident (0058
            # incident_clip_link_chk), so ingest one Channel 1 event per recorder.
            as_anon(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent, key, json.dumps([
                    {"recorder_id": str(rec), "channel": "1",
                     "event_type": "routing_clip_probe",
                     "device_ts": "2026-10-04T10:00:00Z",
                     "agent_ts": "2026-10-04T10:00:00Z"}
                    for rec in (rec_a, rec_b)
                ]),
            )
            clip_events = {
                str(r[0]): r[1]
                for r in cur.execute(
                    """select camera_id,id from events
                        where site_id=%s and event_type='routing_clip_probe'""",
                    (site,),
                ).fetchall()
            }
            clip_a = cur.execute(
                """insert into incident_clip_requests(
                     tenant_id,site_id,camera_id,event_id,start_at,end_at
                   ) values (
                     %s,%s,%s,%s,now()-interval '2 minutes',now()-interval '1 minute'
                   ) returning id""",
                (tenant, site, cam_a, clip_events[str(cam_a)]),
            ).fetchone()[0]
            clip_b = cur.execute(
                """insert into incident_clip_requests(
                     tenant_id,site_id,camera_id,event_id,start_at,end_at
                   ) values (
                     %s,%s,%s,%s,now()-interval '2 minutes',now()-interval '1 minute'
                   ) returning id""",
                (tenant, site, cam_b, clip_events[str(cam_b)]),
            ).fetchone()[0]
            clips = as_anon(
                "select wl_agent_claim_clip_requests(%s,%s,2)", agent, key
            )[0]
            clip_map = {
                str(x["request_id"]): (str(x["recorder_id"]), str(x["channel"]))
                for x in clips
            }
            step(
                clip_map[str(clip_a)] == (str(rec_a), "1")
                and clip_map[str(clip_b)] == (str(rec_b), "1"),
                "clip claims route overlapping Channel 1 by camera recorder",
                str(clip_map),
            )

            # ------------------------------------------------------------------
            # Incident stills.
            # ------------------------------------------------------------------
            incident_a = cur.execute(
                """insert into operations_incidents(
                     tenant_id,site_id,camera_id,agent_id,incident_type,
                     occurred_at,dedupe_key
                   ) values (%s,%s,%s,%s,'routing_test',now(),%s)
                   returning id""",
                (tenant, site, cam_a, agent, "routing-still-a"),
            ).fetchone()[0]
            incident_b = cur.execute(
                """insert into operations_incidents(
                     tenant_id,site_id,camera_id,agent_id,incident_type,
                     occurred_at,dedupe_key
                   ) values (%s,%s,%s,%s,'routing_test',now(),%s)
                   returning id""",
                (tenant, site, cam_b, agent, "routing-still-b"),
            ).fetchone()[0]
            still_a = cur.execute(
                """insert into operations_incident_evidence(
                     tenant_id,site_id,incident_id,camera_id,occurred_at,purpose
                   ) values (%s,%s,%s,%s,now(),'routing test') returning id""",
                (tenant, site, incident_a, cam_a),
            ).fetchone()[0]
            still_b = cur.execute(
                """insert into operations_incident_evidence(
                     tenant_id,site_id,incident_id,camera_id,occurred_at,purpose
                   ) values (%s,%s,%s,%s,now(),'routing test') returning id""",
                (tenant, site, incident_b, cam_b),
            ).fetchone()[0]
            stills = as_anon(
                "select wl_agent_claim_incident_stills(%s,%s,3)", agent, key
            )[0]
            still_map = {
                str(x["request_id"]): (str(x["recorder_id"]), str(x["channel"]))
                for x in stills
            }
            step(
                still_map[str(still_a)] == (str(rec_a), "1")
                and still_map[str(still_b)] == (str(rec_b), "1"),
                "still claims route overlapping Channel 1 by camera recorder",
                str(still_map),
            )

            # ------------------------------------------------------------------
            # Config snapshot request and upload.
            # ------------------------------------------------------------------
            req_b = cur.execute(
                """insert into camera_snapshot_requests(
                     tenant_id,site_id,camera_id,request_source
                   ) values (%s,%s,%s,'manual') returning id""",
                (tenant, site, cam_b),
            ).fetchone()[0]
            config = as_anon(
                "select wl_agent_analytics_config(%s,%s,-1)", agent, key
            )[0]
            requests = {
                str(x["request_id"]): str(x["recorder_id"])
                for x in config["snapshot_requests"]
            }
            cameras = {
                str(x["id"]): str(x["recorder_id"])
                for x in (config.get("config") or {}).get("cameras", [])
            }
            step(
                requests[str(req_b)] == str(rec_b)
                and cameras[str(cam_a)] == str(rec_a)
                and cameras[str(cam_b)] == str(rec_b),
                "analytics config and snapshot queue expose recorder identity",
            )

            up_b = as_anon(
                "select wl_upload_config_snapshot(%s,%s,%s,%s,'image/jpeg')",
                agent, key, cam_b, base64.b64encode(b"snapshot-b").decode("ascii"),
            )[0]
            completed = cur.execute(
                "select completed_at is not null from camera_snapshot_requests where id=%s",
                (req_b,),
            ).fetchone()[0]
            step(
                str(up_b["recorder_id"]) == str(rec_b) and completed,
                "config snapshot upload remains tied to the requested camera recorder",
                json.dumps(up_b, default=str),
            )

            # Restaurant-requested snapshot must ingest its visual_sample with recorder_id.
            cur.execute(
                """insert into restaurant_camera_profiles(
                     camera_id,tenant_id,site_id,analytics_role,sampling_mode,
                     interval_seconds,enabled,config
                   ) values (%s,%s,%s,'dining_floor','interval',60,true,'{}'::jsonb)
                   on conflict (camera_id) do update
                     set enabled=true,analytics_role='dining_floor'""",
                (cam_a, tenant, site),
            )
            req_a = cur.execute(
                """insert into camera_snapshot_requests(
                     tenant_id,site_id,camera_id,request_source
                   ) values (%s,%s,%s,'restaurant_analytics') returning id""",
                (tenant, site, cam_a),
            ).fetchone()[0]
            up_a = as_anon(
                "select wl_upload_config_snapshot(%s,%s,%s,%s,'image/jpeg')",
                agent, key, cam_a, base64.b64encode(b"snapshot-a").decode("ascii"),
            )[0]
            ev = cur.execute(
                """select recorder_id,camera_id
                     from events
                    where site_id=%s
                      and event_type='visual_sample'
                      and device_event_id=%s
                    order by id desc limit 1""",
                (site, f"restaurant-request-{req_a}"),
            ).fetchone()
            step(
                ev is not None
                and str(ev[0]) == str(rec_a)
                and str(ev[1]) == str(cam_a)
                and str(up_a["recorder_id"]) == str(rec_a),
                "restaurant visual_sample ingestion carries recorder_id",
                str(ev),
            )

            # ------------------------------------------------------------------
            # Archive scan: one owner request may span several recorders, but the
            # Agent receives deterministic per-camera targets.
            # ------------------------------------------------------------------
            scan_id = cur.execute(
                """insert into archive_scans(
                     tenant_id,site_id,camera_ids,from_ts,to_ts
                   ) values (
                     %s,%s,array[%s::uuid,%s::uuid],
                     now()-interval '1 hour',now()
                   ) returning id""",
                (tenant, site, cam_a, cam_b),
            ).fetchone()[0]
            scans = as_anon(
                "select wl_agent_claim_archive_scans(%s,%s,1)", agent, key
            )[0]
            scan = next(x for x in scans if str(x["scan_id"]) == str(scan_id))
            targets = {
                str(x["camera_id"]): (str(x["recorder_id"]), str(x["channel"]))
                for x in scan["camera_targets"]
            }
            step(
                targets[str(cam_a)] == (str(rec_a), "1")
                and targets[str(cam_b)] == (str(rec_b), "1"),
                "archive scan returns explicit camera-to-recorder targets",
                str(targets),
            )

            # ------------------------------------------------------------------
            # Contract gate + ACLs.
            # ------------------------------------------------------------------
            contract = as_anon(
                "select wl_multi_recorder_agent_contract(%s,%s)", agent, key
            )[0]
            # Final chain: 0154 contract v4 still advertises job routing.
            step(
                contract["version"] == 4
                and "recorder_job_routing" in set(contract["features"]),
                "multi-recorder contract v4 gates on recorder job routing",
                json.dumps(contract, default=str),
            )

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
                "public.wl_agent_claim_clip_requests(uuid,text,integer)",
                "public.wl_agent_claim_incident_stills(uuid,text,integer)",
                "public.wl_agent_claim_archive_scans(uuid,text,integer)",
                "public.wl_agent_analytics_config(uuid,text,bigint)",
                "public.wl_upload_config_snapshot(uuid,text,uuid,text,text)",
                "public.wl_agent_claim_command(uuid,text)",
            ):
                got = execute_grantees(sig)
                step(
                    got == {"anon", "authenticated", "service_role"},
                    f"{sig} keeps Agent-facing EXECUTE ACL",
                    str(sorted(got)),
                )

            for sig in (
                "public.wl_site_command_enqueue(uuid,text,jsonb,text,text)",
                "public.wl_site_command_propose_write(uuid,text,jsonb,text,text,text)",
            ):
                got = execute_grantees(sig)
                step(
                    got == {"authenticated", "service_role"},
                    f"{sig} keeps portal/operator EXECUTE ACL",
                    str(sorted(got)),
                )

            for sig in (
                "public.wl_resolve_site_recorder_target(uuid,uuid,jsonb)",
                "public.wl_site_command_params(uuid,uuid,text,jsonb)",
            ):
                got = execute_grantees(sig)
                step(got == set(), f"{sig} is owner-only", str(sorted(got)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

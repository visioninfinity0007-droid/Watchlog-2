#!/usr/bin/env python3
"""In-flight incident evidence after an Agent restart (MNVR-033): real Postgres.

Rolled back after execution. Proves:
- 0150's recorder-routed clip claim keeps the started_at lease semantics that
  the production 0142 finalizer (wl_finalize_stale_incident_clips) relies on:
  a fresh claim is left alone, an abandoned one is failed, its partial chunks
  are deleted and the owner can request footage again;
- a restarted Agent releases its OWN in-flight rows at startup, under current
  Agent authority, instead of waiting for the lease: clips fail the same way
  0142 does, stills go back to pending within their attempt budget;
- a stale Agent cannot release anything, and other Agents' rows are untouched;
- a still whose lease expired after its last attempt fails instead of being
  reclaimed forever;
- a clip or still that cannot be routed (no camera, or a blank channel) is
  failed at claim time instead of being skipped or handed out;
- with no Agent polling at all, the server-side still finalizer (0142 style,
  service_role only; its pg_cron job is checked here when pg_cron is
  installed, and its schedule source statically by
  test_incident_still_stale_recovery.py) returns a lapsed still to pending
  within its budget, fails a spent one, and leaves live and ready stills
  alone; in-flight stills past their expiry are left to the hourly 0107
  retention, which deletes them (the finalizer never marks them expired);
- the release RPC is Agent-facing only.
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
STALE_CLIP_TEXT = "Footage retrieval did not complete. Please retry."


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

            def as_anon_try(sql, *params):
                cur.execute("savepoint anon_try")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_try")
                    return row, ""
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_try")
                    cur.execute("reset role")
                    return None, str(exc).splitlines()[0]

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

            def add_agent(tenant_id, site_id, key, seen="now()"):
                return cur.execute(
                    f"""insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,
                          agent_version,last_seen_at
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          'lease-agent','windows','5.1.0',{seen}
                        ) returning id""",
                    (tenant_id, site_id, key),
                ).fetchone()[0]

            def recorders(agent_id, key, keys):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps([
                        {"local_key": k, "display_name": k, "vendor": "Hikvision",
                         "model": "LEASE-TEST", "driver": "onvif",
                         "is_primary": i == 0, "is_configured": True}
                        for i, k in enumerate(keys)
                    ]),
                )[0]

            def camera(agent_id, key, recorder_id, channel="1"):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{"channel": channel, "name": f"cam {channel}",
                                 "is_configured": True}]),
                )[0][channel]

            def event(tenant_id, site_id, agent_id, camera_id, tag):
                return cur.execute(
                    """insert into events(
                         tenant_id,site_id,camera_id,agent_id,event_type,
                         device_ts,agent_ts,dedupe_key
                       ) values (%s,%s,%s,%s,'motion',now(),now(),%s) returning id""",
                    (tenant_id, site_id, camera_id, agent_id, f"lease-{tag}"),
                ).fetchone()[0]

            def clip_request(tenant_id, site_id, camera_id, event_id, status="pending",
                             claimed_by=None, started="null"):
                return cur.execute(
                    f"""insert into incident_clip_requests(
                          tenant_id,site_id,camera_id,event_id,start_at,end_at,
                          status,claimed_by_agent_id,started_at
                        ) values (
                          %s,%s,%s,%s,now()-interval '2 minutes',now()-interval '1 minute',
                          %s,%s,{started}
                        ) returning id""",
                    (tenant_id, site_id, camera_id, event_id, status, claimed_by),
                ).fetchone()[0]

            def incident(tenant_id, site_id, agent_id, camera_id, tag):
                return cur.execute(
                    """insert into operations_incidents(
                         tenant_id,site_id,camera_id,agent_id,incident_type,
                         occurred_at,dedupe_key
                       ) values (%s,%s,%s,%s,'lease_test',now(),%s) returning id""",
                    (tenant_id, site_id, camera_id, agent_id, f"lease-{tag}"),
                ).fetchone()[0]

            def still(tenant_id, site_id, incident_id, camera_id, status="pending",
                      claimed_by=None, attempts=0, lease="null",
                      expires="now()+interval '7 days'"):
                return cur.execute(
                    f"""insert into operations_incident_evidence(
                          tenant_id,site_id,incident_id,camera_id,occurred_at,purpose,
                          status,claimed_by_agent_id,attempts,claim_expires_at,
                          expires_at
                        ) values (%s,%s,%s,%s,now(),'lease test',%s,%s,%s,{lease},
                                  {expires})
                        returning id""",
                    (tenant_id, site_id, incident_id, camera_id, status, claimed_by, attempts),
                ).fetchone()[0]

            def clip_row(request_id):
                return cur.execute(
                    """select status,claimed_by_agent_id,started_at,error_message,
                              completed_at,
                              (select count(*) from incident_clip_chunks c
                                where c.request_id=r.id)
                         from incident_clip_requests r where id=%s""",
                    (request_id,),
                ).fetchone()

            def still_row(request_id):
                return cur.execute(
                    """select status,claimed_by_agent_id,claim_expires_at,attempts,
                              error_message
                         from operations_incident_evidence where id=%s""",
                    (request_id,),
                ).fetchone()

            def upload_chunk(agent_id, key, request_id):
                data = base64.b64encode(b"partial-clip-bytes" * 8).decode("ascii")
                return as_anon(
                    "select wl_agent_upload_clip_chunk(%s,%s,%s,%s,%s)",
                    agent_id, key, request_id, 0, data,
                )[0]

            owner, tenant, site = bootstrap("lease@watchlog.test", "Lease Tenant", "Lease Site")
            stale_key = "lease-stale-agent"
            stale_agent = add_agent(tenant, site, stale_key, seen="'2000-01-01'::timestamptz")
            key = "lease-agent-key"
            agent = add_agent(tenant, site, key)
            recs = recorders(agent, key, ["rec-a", "rec-b"])
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cam_a = camera(agent, key, rec_a)
            cam_b = camera(agent, key, rec_b)

            # ----------------------------------------------------------
            # A. 0150 claim keeps the started_at lease the 0142 finalizer uses.
            # ----------------------------------------------------------
            ev_a = event(tenant, site, agent, cam_a, "a")
            req = as_auth(owner, "select wl_request_incident_clip(%s)", ev_a)[0]
            first = req["request_id"]
            claimed = as_anon("select wl_agent_claim_clip_requests(%s,%s,1)", agent, key)[0]
            row = clip_row(first)
            step(
                [c["request_id"] for c in claimed] == [first]
                and str(claimed[0]["recorder_id"]) == str(rec_a)
                and row[0] == "processing" and str(row[1]) == str(agent)
                and row[2] is not None,
                "recorder-routed clip claim starts the processing lease (started_at)",
                str(row[:3]),
            )
            upload_chunk(agent, key, first)
            fresh = cur.execute("select wl_finalize_stale_incident_clips(180)").fetchone()[0]
            step(fresh == 0 and clip_row(first)[0] == "processing",
                 "0142 finalizer leaves a freshly claimed clip alone", str(fresh))

            cur.execute(
                "update incident_clip_requests set started_at=now()-interval '10 minutes' where id=%s",
                (first,),
            )
            stale = cur.execute("select wl_finalize_stale_incident_clips(180)").fetchone()[0]
            row = clip_row(first)
            step(
                stale >= 1 and row[0] == "failed" and row[1] is None
                and row[3] == STALE_CLIP_TEXT and row[5] == 0,
                "0142 finalizer fails an abandoned recorder-routed clip and deletes chunks",
                str(row),
            )
            again = as_auth(owner, "select wl_request_incident_clip(%s)", ev_a)[0]
            step(again["existing"] is False and again["request_id"] != first,
                 "owner can request the footage again after the finalizer",
                 json.dumps(again, default=str))
            second = again["request_id"]

            # ----------------------------------------------------------
            # B. A restarted Agent releases its own in-flight rows.
            # ----------------------------------------------------------
            claimed = as_anon("select wl_agent_claim_clip_requests(%s,%s,1)", agent, key)[0]
            upload_chunk(agent, key, second)
            inc_b = incident(tenant, site, agent, cam_b, "b")
            still_b = still(tenant, site, inc_b, cam_b)
            inc_b2 = incident(tenant, site, agent, cam_b, "b2")
            still_spent = still(tenant, site, inc_b2, cam_b, status="processing",
                                claimed_by=agent, attempts=3,
                                lease="now()+interval '5 minutes'")
            stills = as_anon("select wl_agent_claim_incident_stills(%s,%s,3)", agent, key)[0]
            step(
                [c["request_id"] for c in claimed] == [second]
                and str(still_b) in {str(s["request_id"]) for s in stills},
                "the Agent holds a clip and a still in flight",
            )
            # The previous (stale) Agent identity still holds a clip of its own.
            ev_old = event(tenant, site, agent, cam_b, "old")
            old_clip = clip_request(tenant, site, cam_b, ev_old, status="processing",
                                    claimed_by=stale_agent, started="now()")

            out, err = as_anon_try(
                "select wl_agent_release_inflight_evidence(%s,%s)", stale_agent, stale_key
            )
            step(
                out is None and "current site authority" in err.lower()
                and clip_row(second)[0] == "processing"
                and clip_row(old_clip)[0] == "processing",
                "a stale Agent cannot release in-flight evidence",
                err or json.dumps(out[0] if out else None, default=str),
            )

            # Another tenant's Agent holds a clip too.
            owner_x, tenant_x, site_x = bootstrap("lease-x@watchlog.test", "Lease X", "Lease X Site")
            key_x = "lease-agent-x"
            agent_x = add_agent(tenant_x, site_x, key_x)
            rec_x = recorders(agent_x, key_x, ["rec-x"])["rec-x"]
            cam_x = camera(agent_x, key_x, rec_x)
            ev_x = event(tenant_x, site_x, agent_x, cam_x, "x")
            clip_x = clip_request(tenant_x, site_x, cam_x, ev_x, status="processing",
                                  claimed_by=agent_x, started="now()")

            out, err = as_anon_try(
                "select wl_agent_release_inflight_evidence(%s,%s)", agent, key
            )
            released = out[0] if out else None
            row = clip_row(second)
            step(
                released is not None and released.get("ok") is True
                and row[0] == "failed" and row[1] is None and row[3] == STALE_CLIP_TEXT
                and row[4] is not None and row[5] == 0,
                "release fails the Agent's own in-flight clip like 0142 and deletes chunks",
                err or f"{json.dumps(released, default=str)} / {row}",
            )
            srow = still_row(still_b)
            spent = still_row(still_spent)
            step(
                srow[0] == "pending" and srow[1] is None and srow[2] is None
                and spent[0] == "failed" and spent[1] is None,
                "release returns stills to pending within budget; a spent still fails",
                f"{srow} / {spent}",
            )
            step(
                clip_row(old_clip)[0] == "processing" and clip_row(clip_x)[0] == "processing",
                "release never touches another Agent's rows",
                f"{clip_row(old_clip)[0]} / {clip_row(clip_x)[0]}",
            )
            step(
                released is not None
                and released.get("clips_failed") == 1
                and released.get("stills_released") == 1
                and released.get("stills_failed") == 1,
                "release reports what it changed",
                json.dumps(released, default=str),
            )
            again = as_auth(owner, "select wl_request_incident_clip(%s)", ev_a)[0]
            step(again["existing"] is False,
                 "owner can request the footage again right after the restart",
                 json.dumps(again, default=str))
            # Config snapshot requests have no claim state to strand: an open
            # request is offered again on every poll until it is completed.
            snap = cur.execute(
                """insert into camera_snapshot_requests(tenant_id,site_id,camera_id,request_source)
                   values (%s,%s,%s,'manual') returning id""",
                (tenant, site, cam_a),
            ).fetchone()[0]
            polls = [
                {str(x["request_id"]) for x in as_anon(
                    "select wl_agent_analytics_config(%s,%s,-1)", agent, key
                )[0]["snapshot_requests"]}
                for _ in range(2)
            ]
            step(all(str(snap) in p for p in polls),
                 "an open config snapshot request is re-offered after a restart",
                 str(polls))

            # Leave no pending work from this section for the next claims.
            cur.execute(
                "update incident_clip_requests set status='expired' where site_id=%s "
                "and status in ('pending','processing') and claimed_by_agent_id is distinct from %s",
                (site, stale_agent),
            )
            cur.execute(
                "update operations_incident_evidence set status='expired' where site_id=%s "
                "and status in ('pending','processing')",
                (site,),
            )

            # ----------------------------------------------------------
            # C. A still lease that expired after its last attempt is bounded.
            # ----------------------------------------------------------
            inc_c = incident(tenant, site, agent, cam_a, "c")
            spent_c = still(tenant, site, inc_c, cam_a, status="processing",
                            claimed_by=agent, attempts=3,
                            lease="now()-interval '1 minute'")
            inc_d = incident(tenant, site, agent, cam_a, "d")
            retry_d = still(tenant, site, inc_d, cam_a, status="processing",
                            claimed_by=agent, attempts=1,
                            lease="now()-interval '1 minute'")
            stills = as_anon("select wl_agent_claim_incident_stills(%s,%s,3)", agent, key)[0]
            got = {str(s["request_id"]) for s in stills}
            c_row, d_row = still_row(spent_c), still_row(retry_d)
            step(
                str(spent_c) not in got and c_row[0] == "failed" and c_row[1] is None,
                "an expired still lease after the last attempt fails instead of re-claiming",
                f"{c_row} / claimed={sorted(got)}",
            )
            step(
                str(retry_d) in got and d_row[0] == "processing" and d_row[3] == 2,
                "an expired still lease within budget is still re-claimed",
                str(d_row),
            )
            cur.execute(
                "update operations_incident_evidence set status='expired' where site_id=%s "
                "and status in ('pending','processing')",
                (site,),
            )

            # ----------------------------------------------------------
            # D. Unroutable work fails at claim time instead of being skipped.
            # ----------------------------------------------------------
            blank_cam = cur.execute(
                """insert into cameras(tenant_id,site_id,recorder_id,channel,name,is_configured)
                   values (%s,%s,%s,'  ','blank channel',true) returning id""",
                (tenant, site, rec_b),
            ).fetchone()[0]
            ev_blank = event(tenant, site, agent, blank_cam, "blank")
            clip_blank = clip_request(tenant, site, blank_cam, ev_blank)
            ev_gone = event(tenant, site, agent, cam_b, "gone")
            clip_gone = clip_request(tenant, site, None, ev_gone)
            claimed = as_anon("select wl_agent_claim_clip_requests(%s,%s,2)", agent, key)[0]
            got = {str(c["request_id"]) for c in claimed}
            blank_row, gone_row = clip_row(clip_blank), clip_row(clip_gone)
            step(
                str(clip_blank) not in got and blank_row[0] == "failed"
                and blank_row[3] and "recorder input" in blank_row[3],
                "a clip whose camera has a blank channel fails at claim time",
                f"{blank_row} / claimed={sorted(got)}",
            )
            step(
                gone_row[0] == "failed" and gone_row[3] and "recorder input" in gone_row[3],
                "a clip whose camera no longer exists fails instead of waiting 24 h",
                str(gone_row),
            )

            inc_blank = incident(tenant, site, agent, blank_cam, "blank")
            still_blank = still(tenant, site, inc_blank, blank_cam)
            inc_gone = incident(tenant, site, agent, cam_b, "gone")
            still_gone = still(tenant, site, inc_gone, None)
            stills = as_anon("select wl_agent_claim_incident_stills(%s,%s,3)", agent, key)[0]
            got = {str(s["request_id"]) for s in stills}
            b_row, g_row = still_row(still_blank), still_row(still_gone)
            step(
                str(still_blank) not in got and b_row[0] == "failed"
                and g_row[0] == "failed"
                and all(r[4] and "recorder input" in r[4] for r in (b_row, g_row)),
                "unroutable stills fail at claim time instead of being skipped",
                f"{b_row} / {g_row} / claimed={sorted(got)}",
            )

            # ----------------------------------------------------------
            # E. Server-side still lease: no Agent polls (replaced, removed or
            #    offline). Only the service-role finalizer runs.
            # ----------------------------------------------------------
            cur.execute(
                "update operations_incident_evidence set status='expired' where site_id=%s "
                "and status in ('pending','processing')",
                (site,),
            )
            still_ids = {}
            for tag, kwargs, tenant_id, site_id, agent_id, cam_id in (
                ("spent", dict(status="processing", attempts=3,
                               lease="now()-interval '1 minute'"),
                 tenant, site, agent, cam_a),
                ("lapsed", dict(status="processing", attempts=1,
                                lease="now()-interval '1 minute'"),
                 tenant, site, agent, cam_a),
                ("fresh", dict(status="processing", attempts=1,
                               lease="now()+interval '4 minutes'"),
                 tenant, site, agent, cam_a),
                ("old-pending", dict(expires="now()-interval '1 minute'"),
                 tenant, site, agent, cam_a),
                ("old-processing", dict(status="processing", attempts=1,
                                        lease="now()-interval '1 minute'",
                                        expires="now()-interval '1 minute'"),
                 tenant, site, agent, cam_a),
                ("ready", dict(status="ready", attempts=1), tenant, site, agent, cam_a),
                ("other-site", dict(status="processing", attempts=2,
                                    lease="now()-interval '1 minute'"),
                 tenant_x, site_x, agent_x, cam_x),
            ):
                inc = incident(tenant_id, site_id, agent_id, cam_id, f"e-{tag}")
                if kwargs.get("status") == "processing":
                    kwargs["claimed_by"] = agent_id
                still_ids[tag] = still(tenant_id, site_id, inc, cam_id, **kwargs)

            cur.execute("savepoint finalize_sp")
            cur.execute("set local role service_role")
            out, err = None, ""
            try:
                out = cur.execute("select wl_finalize_stale_incident_stills()").fetchone()[0]
                cur.execute("reset role")
                cur.execute("release savepoint finalize_sp")
            except psycopg.Error as exc:
                err = str(exc).splitlines()[0]
                cur.execute("rollback to savepoint finalize_sp")
                cur.execute("reset role")
            rows = {tag: still_row(i) for tag, i in still_ids.items()}
            step(out is not None, "service role runs the still finalizer with no Agent call",
                 err or json.dumps(out, default=str))
            step(
                rows["spent"][0] == "failed" and rows["spent"][1] is None
                and rows["spent"][4] == "The camera view was not captured after several attempts.",
                "finalizer fails a lapsed still whose attempts are spent",
                str(rows["spent"]),
            )
            step(
                rows["lapsed"][0] == "pending" and rows["lapsed"][1] is None
                and rows["lapsed"][2] is None and rows["lapsed"][3] == 1
                and rows["other-site"][0] == "pending",
                "finalizer returns a lapsed still within budget to pending (any site)",
                f"{rows['lapsed']} / {rows['other-site']}",
            )
            step(
                rows["old-pending"][0] == "pending" and rows["old-processing"][0] == "processing",
                "finalizer leaves in-flight stills past their expiry to the 0107 retention",
                f"{rows['old-pending']} / {rows['old-processing']}",
            )
            step(
                rows["fresh"][0] == "processing" and str(rows["fresh"][1]) == str(agent)
                and rows["ready"][0] == "ready",
                "finalizer leaves a live lease and a ready still alone",
                f"{rows['fresh']} / {rows['ready']}",
            )
            step(
                out is not None and out.get("failed", 0) >= 1
                and out.get("released", 0) >= 2 and out.get("expired", 0) == 0,
                "finalizer reports what it changed",
                json.dumps(out, default=str),
            )

            # The hourly 0107 retention (wl_evidence_enforce_retention, the single
            # retention source) deletes expired pending/processing/ready stills;
            # the finalizer must not turn them into 'expired' rows it never deletes.
            cur.execute("savepoint retention_sp")
            cur.execute("select set_config('request.jwt.claims', %s, true)",
                        (json.dumps({"role": "service_role"}),))
            cur.execute("set local role service_role")
            retention, err = None, ""
            try:
                retention = cur.execute("select wl_evidence_enforce_retention()").fetchone()[0]
                cur.execute("reset role")
                cur.execute("release savepoint retention_sp")
            except psycopg.Error as exc:
                err = str(exc).splitlines()[0]
                cur.execute("rollback to savepoint retention_sp")
                cur.execute("reset role")
            cur.execute("select set_config('request.jwt.claims', '', true)")
            kept = {tag: still_row(i) for tag, i in still_ids.items()}
            step(
                retention is not None
                and kept["old-pending"] is None and kept["old-processing"] is None,
                "an expired in-flight still is gone after the finalizer plus retention",
                err or f"{kept['old-pending']} / {kept['old-processing']}",
            )
            step(
                all(kept[t] is not None
                    for t in ("spent", "lapsed", "fresh", "ready", "other-site")),
                "retention keeps the unexpired stills the finalizer handled",
                str({t: (r[0] if r else None) for t, r in kept.items()}),
            )
            again = as_anon("select wl_agent_claim_incident_stills(%s,%s,3)", agent, key)[0]
            step(
                str(still_ids["lapsed"]) in {str(s["request_id"]) for s in again},
                "a returned still is claimed by the next Agent poll",
                str([s["request_id"] for s in again]),
            )

            has_cron = cur.execute(
                "select exists(select 1 from pg_extension where extname='pg_cron')"
            ).fetchone()[0]
            if has_cron:
                job = cur.execute(
                    "select schedule,command from cron.job "
                    "where jobname='watchlog-finalize-stale-incident-stills'"
                ).fetchone()
                step(
                    job is not None and job[0] == "*/2 * * * *"
                    and "wl_finalize_stale_incident_stills" in job[1],
                    "the still finalizer is scheduled with pg_cron", str(job),
                )
            else:
                # The schedule source is a static contract
                # (test_incident_still_stale_recovery.py, backend job).
                print("  info  pg_cron is not installed here; schedule not checked")

            # ----------------------------------------------------------
            # ACL: the release RPC is Agent-facing only.
            # ----------------------------------------------------------
            rows = cur.execute(
                """select case when a.grantee=0 then 'PUBLIC'
                                else a.grantee::regrole::text end,
                          p.proowner::regrole::text
                     from pg_proc p,
                          aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                    where p.proname='wl_agent_release_inflight_evidence'
                      and a.privilege_type='EXECUTE'""",
            ).fetchall()
            grantees = {g for g, owner_role in rows if g != owner_role}
            step(grantees == {"anon"},
                 "release RPC EXECUTE ACL is exactly anon (+owner)", str(sorted(grantees)))

            rows = cur.execute(
                """select case when a.grantee=0 then 'PUBLIC'
                                else a.grantee::regrole::text end,
                          p.proowner::regrole::text
                     from pg_proc p,
                          aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                    where p.proname='wl_finalize_stale_incident_stills'
                      and a.privilege_type='EXECUTE'""",
            ).fetchall()
            grantees = {g for g, owner_role in rows if g != owner_role}
            step(grantees == {"service_role"},
                 "still finalizer EXECUTE ACL is exactly service_role (+owner)",
                 str(sorted(grantees)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

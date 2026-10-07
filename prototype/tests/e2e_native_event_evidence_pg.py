#!/usr/bin/env python3
"""Native-event evidence package (migration 0164): real Postgres.

Rolled back after execution. Proves:
- a qualifying native event (person, vehicle, line_crossing, intrusion, tamper, alarm_input by
  default) ingested through wl_ingest_events creates ONE clip request T-15 s .. T+30 s, source
  'native_event', bound to the event, its camera and its recorder, only when the site switch
  (sites.native_event_clips_enabled, default false) is on and the event has a camera;
- nothing is created for a non-qualifying type, a recorder-scoped event, a recovered archive
  event, a stale event, a type outside the site's list, or an event whose recorder is not the
  camera's recorder;
- per camera at most one automatic clip per 2 minutes: an event whose window still fits a
  pending request is merged into it (<= 60 s), others are skipped, another camera is separate;
- the claim guard: a request whose end_at (post-roll) is less than 10 s in the past is not
  claimable; the claimed row names the event's recorder and channel and carries its
  clock_source; a request stamped with another recorder than its camera's fails cleanly;
- the stale-clip lease is 420 s (default and floor) and the finalizer fails only older claims;
- wl_agent_complete_clip recomputes sha256 over the stored chunks in sequence order: a
  mismatch fails the clip and removes its bytes, a match makes it ready;
- event stills carry capture provenance: the Agent's real capture time becomes captured_at
  ('live_after_event'), otherwise the event time ('event_time'); periodic and archive stills
  are labelled as such;
- tenant isolation: the switch belongs to the site's own Owner/Admin, another tenant's site is
  unaffected, its Agent cannot claim or upload, its owner cannot read the clip status;
- the switch RPC is authenticated-only and the trigger functions are not callable by clients.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from datetime import timedelta
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
JPEG = b"\xff\xd8\xff\xe0native-event-still"


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

            def add_agent(tenant_id, site_id, key):
                return cur.execute(
                    """insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,
                          agent_version,last_seen_at
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          'native-agent','windows','5.1.2',now()
                        ) returning id""",
                    (tenant_id, site_id, key),
                ).fetchone()[0]

            def recorders(agent_id, key, keys):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps([
                        {"local_key": k, "display_name": k, "vendor": "Dahua",
                         "model": "NATIVE-TEST", "driver": "dahua",
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

            now = cur.execute("select now()").fetchone()[0]     # one now() per transaction
            counter = [0]

            def ingest(agent_id, key, recorder_id, channel, event_type, at, payload=None,
                       snapshot=None):
                counter[0] += 1
                device_event_id = f"native-e2e-{counter[0]}"
                row = {"recorder_id": str(recorder_id), "channel": channel,
                       "event_type": event_type, "device_event_id": device_event_id,
                       "device_ts": at.isoformat(), "agent_ts": now.isoformat(),
                       "payload": payload if payload is not None
                       else {"native_ai": True, "source": "recorder_native_ai"}}
                if snapshot is not None:
                    row["snapshot_b64"] = base64.b64encode(snapshot).decode("ascii")
                out = as_anon("select wl_ingest_events(%s,%s,%s::jsonb)",
                              agent_id, key, json.dumps([row]))[0]
                event_id = cur.execute(
                    "select id from events where device_event_id=%s", (device_event_id,)
                ).fetchone()
                return (event_id[0] if event_id else None), out

            def requests_for(event_id):
                return cur.execute(
                    """select id,status,start_at,end_at,source,camera_id,recorder_id,
                              requested_by,tenant_id
                         from incident_clip_requests where event_id=%s""",
                    (event_id,),
                ).fetchall()

            def native_count(camera_id):
                return cur.execute(
                    "select count(*) from incident_clip_requests "
                    "where camera_id=%s and source='native_event'",
                    (camera_id,),
                ).fetchone()[0]

            def clip_row(request_id):
                return cur.execute(
                    """select status,claimed_by_agent_id,started_at,error_message,
                              completed_at,
                              (select count(*) from incident_clip_chunks c
                                where c.request_id=r.id),
                              sha256
                         from incident_clip_requests r where id=%s""",
                    (request_id,),
                ).fetchone()

            def plain_event(tenant_id, site_id, agent_id, camera_id, recorder_id, tag,
                            event_type="motion", at="now()-interval '2 minutes'"):
                return cur.execute(
                    f"""insert into events(
                          tenant_id,site_id,camera_id,recorder_id,agent_id,event_type,
                          device_ts,agent_ts,dedupe_key
                        ) values (%s,%s,%s,%s,%s,%s,{at},now(),%s) returning id""",
                    (tenant_id, site_id, camera_id, recorder_id, agent_id, event_type,
                     f"native-plain-{tag}"),
                ).fetchone()[0]

            def direct_request(tenant_id, site_id, camera_id, event_id, end_offset,
                               recorder_id=None):
                return cur.execute(
                    f"""insert into incident_clip_requests(
                          tenant_id,site_id,camera_id,event_id,recorder_id,start_at,end_at
                        ) values (
                          %s,%s,%s,%s,%s,
                          now()-interval '{end_offset + 30} seconds',
                          now()-interval '{end_offset} seconds'
                        ) returning id""",
                    (tenant_id, site_id, camera_id, event_id, recorder_id),
                ).fetchone()[0]

            def claim(agent_id, key, limit=2):
                return as_anon("select wl_agent_claim_clip_requests(%s,%s,%s)",
                               agent_id, key, limit)[0]

            def upload(agent_id, key, request_id, seq, data):
                return as_anon(
                    "select wl_agent_upload_clip_chunk(%s,%s,%s,%s,%s)",
                    agent_id, key, request_id, seq, base64.b64encode(data).decode("ascii"),
                )[0]

            def complete(agent_id, key, request_id, sha, total):
                return as_anon(
                    "select wl_agent_complete_clip(%s,%s,%s,'video/mp4','mp4',%s,%s)",
                    agent_id, key, request_id, sha, total,
                )[0]

            def park_pending(site_id):
                cur.execute(
                    "update incident_clip_requests set status='expired' "
                    "where site_id=%s and status='pending'", (site_id,))

            owner, tenant, site = bootstrap("native@watchlog.test", "Native Tenant", "Native Site")
            key = "native-agent-key"
            agent = add_agent(tenant, site, key)
            recs = recorders(agent, key, ["rec-a", "rec-b"])
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]
            cam_a = camera(agent, key, rec_a)         # recorder A, channel 1
            cam_b = camera(agent, key, rec_b)         # recorder B, channel 1

            owner_x, tenant_x, site_x = bootstrap("native-x@watchlog.test", "Native X",
                                                  "Native X Site")
            key_x = "native-agent-x"
            agent_x = add_agent(tenant_x, site_x, key_x)
            rec_x = recorders(agent_x, key_x, ["rec-x"])["rec-x"]
            cam_x = camera(agent_x, key_x, rec_x)

            viewer = cur.execute(
                "insert into auth.users(id,email) values (gen_random_uuid(),"
                "'native-viewer@watchlog.test') returning id").fetchone()[0]
            cur.execute("insert into memberships(user_id,tenant_id,role) values (%s,%s,'viewer')",
                        (viewer, tenant))

            # ----------------------------------------------------------
            # A. Default OFF: no automatic clip, production unchanged.
            # ----------------------------------------------------------
            enabled, types = cur.execute(
                "select native_event_clips_enabled,native_event_clip_types from sites where id=%s",
                (site,)).fetchone()
            step(enabled is False and types == ["person", "vehicle", "line_crossing",
                                                 "intrusion", "tamper", "alarm_input"],
                 "the switch defaults OFF with the documented event types", f"{enabled} {types}")
            ev_off, _ = ingest(agent, key, rec_b, "1", "person", now - timedelta(seconds=300))
            step(ev_off is not None and requests_for(ev_off) == [],
                 "switch OFF: a native person event creates no clip request")

            # ----------------------------------------------------------
            # B. The switch is the site's own Owner/Admin's.
            # ----------------------------------------------------------
            _out, err = as_auth_try(viewer, "select wl_set_native_event_clips(%s,true)", site)
            step(_out is None and err != "", "a Viewer cannot turn native-event clips on", err)
            _out, err = as_auth_try(owner_x, "select wl_set_native_event_clips(%s,true)", site)
            step(_out is None and "not your site" in err,
                 "another tenant's owner cannot turn them on for this site", err)
            _out, err = as_auth_try(owner, "select wl_set_native_event_clips(%s,true,%s)",
                                    site, ["person", "fire"])
            step(_out is None and "event types" in err, "an unknown event type is refused", err)
            out = as_auth(owner, "select wl_set_native_event_clips(%s,true)", site)[0]
            step(out["native_event_clips_enabled"] is True, "the owner turns them on",
                 json.dumps(out))

            # ----------------------------------------------------------
            # C. A qualifying event: one request T-15 .. T+30, camera + recorder bound.
            # ----------------------------------------------------------
            t1 = now - timedelta(seconds=150)
            ev1, _ = ingest(agent, key, rec_b, "1", "person", t1,
                            {"native_ai": True, "source": "recorder_native_ai",
                             "clock_source": "agent_receive"})
            rows = requests_for(ev1)
            r1 = rows[0][0] if rows else None
            step(
                len(rows) == 1 and rows[0][1] == "pending" and rows[0][4] == "native_event"
                and rows[0][2] == t1 - timedelta(seconds=15)
                and rows[0][3] == t1 + timedelta(seconds=30)
                and str(rows[0][5]) == str(cam_b) and str(rows[0][6]) == str(rec_b)
                and rows[0][7] is None and str(rows[0][8]) == str(tenant),
                "native person event -> one pending request T-15 s .. T+30 s on its camera and recorder",
                str(rows),
            )

            # ----------------------------------------------------------
            # D. Non-qualifying events create nothing.
            # ----------------------------------------------------------
            ev_motion, _ = ingest(agent, key, rec_a, "1", "motion", now - timedelta(seconds=400),
                                  {"source": "recorder_event"})
            ev_alarm, _ = ingest(agent, key, rec_a, None, "alarm_input",
                                 now - timedelta(seconds=400),
                                 {"recorder_scoped": True})
            ev_recovered, _ = ingest(agent, key, rec_a, "1", "person",
                                     now - timedelta(seconds=410),
                                     {"source": "recorder_archive", "recovered": True})
            ev_stale, _ = ingest(agent, key, rec_a, "1", "person", now - timedelta(hours=2))
            mismatched = plain_event(tenant, site, agent, cam_b, rec_a, "mismatch",
                                     event_type="person", at="now()-interval '7 minutes'")
            step(
                all(e is not None and requests_for(e) == []
                    for e in (ev_motion, ev_alarm, ev_recovered, ev_stale, mismatched)),
                "no request for motion, a recorder-scoped alarm, a recovered or stale event, "
                "or an event whose recorder is not the camera's",
                str([len(requests_for(e)) for e in (ev_motion, ev_alarm, ev_recovered,
                                                    ev_stale, mismatched) if e]),
            )

            # ----------------------------------------------------------
            # E. Rate limit and merge, per camera.
            # ----------------------------------------------------------
            ev2, _ = ingest(agent, key, rec_b, "1", "person", t1 + timedelta(seconds=5))
            r1_row = cur.execute("select start_at,end_at from incident_clip_requests where id=%s",
                                 (r1,)).fetchone()
            step(
                requests_for(ev2) == [] and native_count(cam_b) == 1
                and r1_row == (t1 - timedelta(seconds=15), t1 + timedelta(seconds=35)),
                "an event 5 s later is merged into the pending request (window widened, <= 60 s)",
                str(r1_row),
            )
            ev3, _ = ingest(agent, key, rec_b, "1", "intrusion", t1 + timedelta(seconds=60))
            step(
                requests_for(ev3) == [] and native_count(cam_b) == 1
                and cur.execute("select end_at from incident_clip_requests where id=%s",
                                (r1,)).fetchone()[0] == t1 + timedelta(seconds=35),
                "an event 60 s later on the same camera is rate-limited (no second clip)",
            )
            ev4, _ = ingest(agent, key, rec_b, "1", "vehicle", t1 + timedelta(seconds=130))
            rows4 = requests_for(ev4)
            r4 = rows4[0][0] if rows4 else None
            step(len(rows4) == 1 and native_count(cam_b) == 2,
                 "an event more than 2 minutes later gets its own clip", str(rows4))
            ev5, _ = ingest(agent, key, rec_a, "1", "person", t1 + timedelta(seconds=5))
            rows5 = requests_for(ev5)
            r5 = rows5[0][0] if rows5 else None
            step(len(rows5) == 1 and str(rows5[0][6]) == str(rec_a),
                 "the same moment on another recorder's channel 1 is a separate clip",
                 str(rows5))

            # ----------------------------------------------------------
            # F. Claim guard: only once the post-roll is recorded.
            # ----------------------------------------------------------
            got = claim(agent, key, 2)
            ids = {str(c["request_id"]) for c in got}
            by_id = {str(c["request_id"]): c for c in got}
            step(
                ids == {str(r1), str(r5)} and clip_row(r4)[0] == "pending",
                "requests whose post-roll is recorded are claimed; one ending in the future is not",
                f"claimed={sorted(ids)} r4={clip_row(r4)[0]}",
            )
            c1, c5 = by_id.get(str(r1), {}), by_id.get(str(r5), {})
            step(
                str(c1.get("recorder_id")) == str(rec_b) and c1.get("channel") == "1"
                and str(c5.get("recorder_id")) == str(rec_a) and c5.get("channel") == "1",
                "each claimed clip names its own recorder and channel (B ch1 vs A ch1)",
                json.dumps(got, default=str),
            )
            step(
                c1.get("clock_source") == "agent_receive"
                and "clock_source" in c5 and c5.get("clock_source") is None,
                "the claim carries the event's clock_source (null when the event has none)",
                json.dumps(got, default=str),
            )

            park_pending(site)
            ev_edge = plain_event(tenant, site, agent, cam_a, rec_a, "edge")
            ev_ok = plain_event(tenant, site, agent, cam_a, rec_a, "ok")
            edge = direct_request(tenant, site, cam_a, ev_edge, 5)
            ready_soon = direct_request(tenant, site, cam_a, ev_ok, 11)
            got = claim(agent, key, 2)
            step(
                [str(c["request_id"]) for c in got] == [str(ready_soon)]
                and clip_row(edge)[0] == "pending",
                "end_at 11 s ago is claimable, end_at 5 s ago is not (10 s post-roll guard)",
                str([c["request_id"] for c in got]),
            )

            ev_moved = plain_event(tenant, site, agent, cam_b, rec_b, "moved")
            moved = direct_request(tenant, site, cam_b, ev_moved, 60, recorder_id=rec_a)
            stamped = cur.execute("select recorder_id from incident_clip_requests where id=%s",
                                  (ready_soon,)).fetchone()[0]
            got = claim(agent, key, 2)
            row = clip_row(moved)
            step(
                str(moved) not in {str(c["request_id"]) for c in got}
                and row[0] == "failed" and row[3] and "no longer connected" in row[3],
                "a request stamped with another recorder than its camera's fails, never exported",
                str(row),
            )
            step(str(stamped) == str(rec_a),
                 "a request created without a recorder is stamped with its camera's recorder",
                 str(stamped))

            # ----------------------------------------------------------
            # G. Stale lease: 420 s default and floor.
            # ----------------------------------------------------------
            cur.execute("update incident_clip_requests set started_at=now()-interval '300 seconds' "
                        "where id=%s", (r1,))
            n180 = cur.execute("select wl_finalize_stale_incident_clips(180)").fetchone()[0]
            ndef = cur.execute("select wl_finalize_stale_incident_clips()").fetchone()[0]
            step(n180 == 0 and ndef == 0 and clip_row(r1)[0] == "processing",
                 "a clip claimed 300 s ago is still within the 420 s lease (even if 180 is asked)",
                 f"{n180} {ndef} {clip_row(r1)[0]}")
            cur.execute("update incident_clip_requests set started_at=now()-interval '430 seconds' "
                        "where id=%s", (r1,))
            n = cur.execute("select wl_finalize_stale_incident_clips()").fetchone()[0]
            row = clip_row(r1)
            step(n == 1 and row[0] == "failed" and row[3] == STALE_CLIP_TEXT and row[1] is None,
                 "a clip claimed 430 s ago is failed by the finalizer", f"{n} {row}")
            default = cur.execute(
                """select pg_get_function_arguments(p.oid) from pg_proc p
                    where p.proname='wl_finalize_stale_incident_clips'""").fetchone()[0]
            step("420" in default, "the finalizer's default lease is 420 s", default)

            # ----------------------------------------------------------
            # H. Server-side sha256 over the stored chunks.
            # ----------------------------------------------------------
            part0, part1 = b"native-clip-part-0-" * 40, b"native-clip-part-1-" * 40
            upload(agent, key, r5, 1, part1)          # out of order on purpose
            upload(agent, key, r5, 0, part0)
            bad = complete(agent, key, r5, "0" * 64, len(part0) + len(part1))
            row = clip_row(r5)
            step(
                bad.get("ok") is False and bad.get("reason") == "checksum_mismatch"
                and row[0] == "failed" and row[5] == 0 and row[6] is None
                and "could not be verified" in (row[3] or ""),
                "a checksum that does not match the stored bytes fails the clip and removes them",
                f"{bad} / {row}",
            )
            upload(agent, key, ready_soon, 1, part1)
            upload(agent, key, ready_soon, 0, part0)
            good_sha = hashlib.sha256(part0 + part1).hexdigest()
            good = complete(agent, key, ready_soon, good_sha, len(part0) + len(part1))
            row = clip_row(ready_soon)
            step(good.get("ok") is True and row[0] == "ready" and row[6] == good_sha,
                 "the checksum of the chunks in sequence order is accepted", f"{good} / {row}")

            # ----------------------------------------------------------
            # I. Snapshot capture provenance.
            # ----------------------------------------------------------
            park_pending(site)
            as_auth(owner, "select wl_set_native_event_clips(%s,false)", site)
            at = now - timedelta(seconds=40)
            shot = at + timedelta(seconds=2)
            ev_live, _ = ingest(agent, key, rec_a, "1", "motion", at,
                                {"source": "recorder_event",
                                 "snapshot_captured_at": shot.isoformat(),
                                 "snapshot_source": "live_after_event"}, snapshot=JPEG)
            ev_old, _ = ingest(agent, key, rec_a, "1", "motion", at + timedelta(seconds=1),
                               {"source": "recorder_event"}, snapshot=JPEG)
            ev_bad, _ = ingest(agent, key, rec_a, "1", "motion", at + timedelta(seconds=2),
                               {"source": "recorder_event", "snapshot_captured_at": "yesterday-ish"},
                               snapshot=JPEG)
            ev_periodic, _ = ingest(agent, key, rec_a, "1", "visual_sample",
                                    at + timedelta(seconds=3),
                                    {"sample": True, "source": "periodic_snapshot"},
                                    snapshot=JPEG)
            ev_archive, _ = ingest(agent, key, rec_a, "1", "motion", at - timedelta(minutes=30),
                                   {"source": "recorder_archive", "recovered": True},
                                   snapshot=JPEG)

            def snap(event_id):
                return cur.execute(
                    "select captured_at,capture_source from snapshots where event_id=%s",
                    (event_id,)).fetchone()

            dev = cur.execute("select device_ts from events where id=%s", (ev_live,)).fetchone()[0]
            step(snap(ev_live) == (shot, "live_after_event") and dev == at,
                 "a reported capture time becomes captured_at; device_ts stays the event time",
                 f"{snap(ev_live)} device_ts={dev}")
            step(snap(ev_old) == (at + timedelta(seconds=1), "event_time")
                 and snap(ev_bad) == (at + timedelta(seconds=2), "event_time"),
                 "without a (readable) capture time the still keeps the event time, labelled so",
                 f"{snap(ev_old)} / {snap(ev_bad)}")
            step(snap(ev_periodic) == (at + timedelta(seconds=3), "periodic")
                 and snap(ev_archive) == (at - timedelta(minutes=30), "recorder_archive"),
                 "periodic and archive stills are labelled by their source",
                 f"{snap(ev_periodic)} / {snap(ev_archive)}")

            # ----------------------------------------------------------
            # J. Tenant isolation.
            # ----------------------------------------------------------
            as_auth(owner, "select wl_set_native_event_clips(%s,true)", site)
            ev_x, _ = ingest(agent_x, key_x, rec_x, "1", "person", now - timedelta(seconds=60))
            enabled_x = cur.execute("select native_event_clips_enabled from sites where id=%s",
                                    (site_x,)).fetchone()[0]
            step(enabled_x is False and requests_for(ev_x) == [],
                 "another tenant's site stays OFF and gets no automatic clip")
            # Far enough from camera A's earlier automatic clip to pass its 2 minute limit.
            ev_a2, _ = ingest(agent, key, rec_a, "1", "tamper", now - timedelta(seconds=400))
            ra2 = requests_for(ev_a2)
            got_x = claim(agent_x, key_x, 2)
            step(len(ra2) == 1 and str(ra2[0][8]) == str(tenant) and got_x == []
                 and clip_row(ra2[0][0])[0] == "pending",
                 "another tenant's Agent claims none of this tenant's native clips",
                 json.dumps(got_x, default=str))
            out, err = as_anon_try("select wl_agent_upload_clip_chunk(%s,%s,%s,0,%s)",
                                   agent_x, key_x, ra2[0][0],
                                   base64.b64encode(b"x").decode("ascii"))
            step(out is None and "not claimed" in err,
                 "another tenant's Agent cannot upload to this tenant's native clip", err)
            mine = as_auth(owner, "select wl_incident_clip_status(%s)", ev1)[0]
            theirs = as_auth(owner_x, "select wl_incident_clip_status(%s)", ev1)[0]
            step(mine is not None and str(mine["request_id"]) == str(r1) and theirs is None,
                 "the owner reads the native clip status; another tenant reads nothing",
                 f"{mine and mine.get('status')} / {theirs}")
            again = as_auth(owner, "select wl_request_incident_clip(%s)", ev_a2)[0]
            step(again["existing"] is True and str(again["request_id"]) == str(ra2[0][0]),
                 "an owner request for the same event returns the native clip, no duplicate",
                 json.dumps(again, default=str))

            # ----------------------------------------------------------
            # K. ACL and schedule.
            # ----------------------------------------------------------
            def grantees(fn):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC'
                                    else a.grantee::regrole::text end,
                              p.proowner::regrole::text
                         from pg_proc p,
                              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                        where p.proname=%s and a.privilege_type='EXECUTE'""",
                    (fn,),
                ).fetchall()
                return {g for g, owner_role in rows if g != owner_role}

            setter = grantees("wl_set_native_event_clips")
            step("authenticated" in setter and "anon" not in setter and "PUBLIC" not in setter,
                 "the switch RPC is executable by signed-in users only", str(sorted(setter)))
            for fn in ("wl_native_event_clip_request", "wl_snapshot_capture_provenance",
                       "wl_incident_clip_stamp_recorder"):
                g = grantees(fn)
                step(not ({"anon", "authenticated", "PUBLIC"} & g),
                     f"{fn} is not callable by clients", str(sorted(g)))

            has_cron = cur.execute(
                "select exists(select 1 from pg_extension where extname='pg_cron')"
            ).fetchone()[0]
            if has_cron:
                job = cur.execute(
                    "select schedule,command from cron.job "
                    "where jobname='watchlog-finalize-stale-incident-clips'"
                ).fetchone()
                step(job is not None and "wl_finalize_stale_incident_clips(420)" in job[1],
                     "the stale-clip finalizer runs with the 420 s lease", str(job))
            else:
                print("  info  pg_cron is not installed here; schedule not checked")

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

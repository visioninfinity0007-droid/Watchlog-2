#!/usr/bin/env python3
"""Full-incident evidence window (migrations 0092/0093/0097, with 0164's claim): real Postgres.

0092-0097 had no test. Rolled back after execution. Proves:
- wl_touch_operations_incident widens the physical episode both ways (earliest start, latest
  activity) and only while it is active;
- wl_end_operations_incident sets the evidence window to incident start - 15 s .. last
  activity + 30 s, records the 'ended' lifecycle event, and does not touch review status;
- wl_advance_operations_incident_evidence walks that window one bounded clip request at a
  time: contiguous segments of at most 55 s, each a 'rule' request on the incident's camera,
  each recorded as an incident_window action; it waits while a segment is in flight, ends
  report_ready (footage ready) once the window is covered, and ends report_ready (footage
  incomplete) when a segment failed;
- no request_footage action -> not_requested; no camera -> not_available; an active incident
  is not advanced;
- wl_advance_all_operations_incident_evidence only advances sites with Operations enabled;
- the segments the orchestrator queues are claimable by the site Agent under 0164's post-roll
  guard, routed to the camera's recorder, and carry no event clock_source (rule clips use the
  agent clock);
- the lifecycle/evidence functions are service_role only.
"""
from __future__ import annotations

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
SEGMENT = timedelta(seconds=55)


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
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            def one(sql, *params):
                return cur.execute(sql, params or None).fetchone()

            def bootstrap(email, company, site_name):
                uid = one("insert into auth.users(id,email) values (gen_random_uuid(),%s) "
                          "returning id", email)[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = one("select id from sites where tenant_id=%s order by created_at limit 1",
                           boot["tenant_id"])[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key):
                return one(
                    """insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,
                          agent_version,last_seen_at,capabilities
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          'orchestrator-agent','windows','5.1.2',now(),
                          '["operations_runtime","operations_evidence_clip"]'::jsonb
                        ) returning id""",
                    tenant_id, site_id, key)[0]

            def recorder_camera(agent_id, key, local_key):
                rec = as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)", agent_id, key,
                    json.dumps([{"local_key": local_key, "display_name": local_key,
                                 "vendor": "Hikvision", "model": "ORCH-TEST",
                                 "driver": "hikvision", "is_primary": True,
                                 "is_configured": True}]))[0][local_key]
                cam = as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)", agent_id, key, rec,
                    json.dumps([{"channel": "1", "name": "Gate", "is_configured": True}]))[0]["1"]
                return rec, cam

            def rule(tenant_id, site_id, camera_id, actions):
                return one(
                    "insert into monitoring_rules (tenant_id, site_id, camera_id, name, rule_type, "
                    "analytic_key, object_classes, severity, enabled, sensitive, review_required, "
                    "cooldown_seconds, actions) values (%s,%s,%s,'Orchestrator rule','zone_entry',"
                    "'zone_entry',%s,'attention',true,false,false,300,%s::jsonb) returning id",
                    tenant_id, site_id, camera_id, ["person"], json.dumps(actions))[0]

            def incident(tenant_id, site_id, agent_id, camera_id, rule_id, started, tag):
                return one(
                    """insert into operations_incidents(
                         tenant_id,site_id,camera_id,agent_id,rule_id,incident_type,occurred_at,
                         dedupe_key,base_dedupe_key,lifecycle_state,incident_started_at,
                         last_activity_at
                       ) values (%s,%s,%s,%s,%s,'zone_entry',%s,%s,%s,'active',%s,%s)
                       returning id""",
                    tenant_id, site_id, camera_id, agent_id, rule_id, started,
                    f"orch-{tag}", f"orch-base-{tag}", started, started)[0]

            def state(incident_id):
                return one(
                    """select lifecycle_state,status,evidence_window_start,evidence_window_end,
                              incident_started_at,last_activity_at,summary_json
                         from operations_incidents where id=%s""",
                    incident_id)

            def advance(incident_id):
                return one("select wl_advance_operations_incident_evidence(%s)", incident_id)[0]

            def segments(incident_id):
                return cur.execute(
                    """select r.id,r.start_at,r.end_at,r.status,r.source,r.camera_id
                         from incident_clip_requests r
                        where r.operations_incident_id=%s
                        order by r.start_at""",
                    (incident_id,)).fetchall()

            def finish(request_id, status="ready"):
                cur.execute("update incident_clip_requests set status=%s,completed_at=now() "
                            "where id=%s", (status, request_id))

            now = one("select now()")[0]
            owner, tenant, site = bootstrap("orch@watchlog.test", "Orch Tenant", "Orch Site")
            key = "orch-agent-key"
            agent = add_agent(tenant, site, key)
            rec, cam = recorder_camera(agent, key, "orch-rec")
            cur.execute("update sites set operations_runtime_enabled=true where id=%s", (site,))
            footage_rule = rule(tenant, site, cam, [{"type": "request_footage"}])

            # ----------------------------------------------------------
            # A. Episode timing: touch, then end.
            # ----------------------------------------------------------
            s = now - timedelta(minutes=10)
            inc = incident(tenant, site, agent, cam, footage_rule, s, "main")
            idle = advance(inc)
            step(idle.get("queued") is False and state(inc)[0] == "active",
                 "an active incident is not advanced", json.dumps(idle))
            one("select wl_touch_operations_incident(%s,%s)", inc, s + timedelta(seconds=40))
            one("select wl_touch_operations_incident(%s,%s)", inc, s - timedelta(seconds=5))
            st = state(inc)
            step(st[4] == s - timedelta(seconds=5) and st[5] == s + timedelta(seconds=40),
                 "touching widens the episode to the earliest start and latest activity",
                 f"{st[4]} .. {st[5]}")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')",
                inc, s + timedelta(seconds=40))
            st = state(inc)
            window_start, window_end = s - timedelta(seconds=20), s + timedelta(seconds=70)
            step(st[0] == "ended" and st[2] == window_start and st[3] == window_end,
                 "ending sets the window to incident start - 15 s .. last activity + 30 s",
                 f"{st[0]} {st[2]} .. {st[3]}")
            step(st[1] == "open", "ending the episode leaves human review untouched", st[1])
            ended = one("select count(*) from operations_incident_lifecycle_events "
                        "where incident_id=%s and event_type='ended'", inc)[0]
            step(ended == 1, "the 'ended' lifecycle event is recorded once", str(ended))
            late = one("select wl_touch_operations_incident(%s,%s)", inc,
                       s + timedelta(minutes=5))[0]
            step(late is None and state(inc)[5] == s + timedelta(seconds=40),
                 "an ended episode is not touched again", str(late))

            # ----------------------------------------------------------
            # B. Bounded segments over the whole window.
            # ----------------------------------------------------------
            first = advance(inc)
            segs = segments(inc)
            step(
                first.get("queued") is True and len(segs) == 1
                and segs[0][1] == window_start and segs[0][2] == window_start + SEGMENT
                and segs[0][4] == "rule" and str(segs[0][5]) == str(cam)
                and state(inc)[0] == "evidence_processing",
                "the first segment is window start .. +55 s on the incident's camera",
                f"{first} / {segs}",
            )
            waiting = advance(inc)
            step(waiting.get("queued") is False
                 and str(waiting.get("active_request_id")) == str(segs[0][0])
                 and len(segments(inc)) == 1,
                 "while a segment is in flight nothing else is queued", json.dumps(waiting))

            # The site Agent claims the segment (0164 post-roll guard: it ended minutes ago).
            got = as_anon("select wl_agent_claim_clip_requests(%s,%s,2)", agent, key)[0]
            mine = [g for g in got if str(g["request_id"]) == str(segs[0][0])]
            step(
                len(mine) == 1 and str(mine[0]["recorder_id"]) == str(rec)
                and mine[0]["channel"] == "1" and mine[0]["event_id"] is None
                and "clock_source" not in mine[0],
                "the Agent claims the segment on the camera's recorder; no event clock_source",
                json.dumps(got, default=str),
            )
            finish(segs[0][0])
            second = advance(inc)
            segs = segments(inc)
            step(
                second.get("queued") is True and len(segs) == 2
                and segs[1][1] == segs[0][2] and segs[1][2] == window_end,
                "the next segment starts where the last ended and stops at the window end",
                f"{second} / {[(x[1], x[2]) for x in segs]}",
            )
            finish(segs[1][0])
            done = advance(inc)
            st = state(inc)
            step(done.get("state") == "report_ready" and st[0] == "report_ready"
                 and (st[6] or {}).get("footage_status") == "ready",
                 "once the window is covered the incident is report_ready with footage ready",
                 f"{done} / {st[6]}")
            segs = segments(inc)
            covered = (segs[0][1], segs[-1][2])
            gaps = [a[2] for a, b in zip(segs, segs[1:]) if a[2] != b[1]]
            longest = max(x[2] - x[1] for x in segs)
            step(covered == (window_start, window_end) and gaps == [] and longest <= SEGMENT,
                 "segments cover start-15 s .. last activity+30 s exactly, each <= 55 s",
                 f"{covered} gaps={gaps} longest={longest}")
            actions = one(
                "select count(*) from operations_incident_actions where incident_id=%s "
                "and action_type='request_footage' and detail->>'window_kind'='incident_window'",
                inc)[0]
            ready_events = one(
                "select count(*) from operations_incident_lifecycle_events "
                "where incident_id=%s and event_type='report_ready'", inc)[0]
            step(actions == 2 and ready_events == 1,
                 "each segment is an auditable action; report_ready is recorded once",
                 f"{actions} {ready_events}")
            again = advance(inc)
            step(again.get("queued") is False and len(segments(inc)) == 2,
                 "advancing a finished incident queues nothing", json.dumps(again))

            # ----------------------------------------------------------
            # C. A failed segment ends the incident with incomplete footage.
            # ----------------------------------------------------------
            inc_fail = incident(tenant, site, agent, cam, footage_rule, s, "fail")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')",
                inc_fail, s + timedelta(seconds=90))
            advance(inc_fail)
            finish(segments(inc_fail)[0][0], "failed")
            out = advance(inc_fail)
            st = state(inc_fail)
            step(st[0] == "report_ready" and (st[6] or {}).get("footage_status") == "incomplete"
                 and len(segments(inc_fail)) == 1,
                 "a failed segment ends report_ready with footage incomplete, no further segment",
                 f"{out} / {st[6]}")

            # ----------------------------------------------------------
            # D. No footage action / no camera.
            # ----------------------------------------------------------
            still_rule = rule(tenant, site, cam, [{"type": "capture_still"}])
            inc_none = incident(tenant, site, agent, cam, still_rule, s, "none")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')", inc_none, s)
            advance(inc_none)
            st = state(inc_none)
            step(st[0] == "report_ready"
                 and (st[6] or {}).get("footage_status") == "not_requested"
                 and segments(inc_none) == [],
                 "a rule without request_footage ends report_ready, not_requested", str(st[6]))
            inc_nocam = incident(tenant, site, agent, None, footage_rule, s, "nocam")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')", inc_nocam, s)
            advance(inc_nocam)
            st = state(inc_nocam)
            step(st[0] == "report_ready"
                 and (st[6] or {}).get("footage_status") == "not_available"
                 and segments(inc_nocam) == [],
                 "an incident without a camera ends report_ready, not_available", str(st[6]))

            # ----------------------------------------------------------
            # E. The cron entry point only advances Operations-enabled sites.
            # ----------------------------------------------------------
            _ox, tenant_x, site_x = bootstrap("orch-x@watchlog.test", "Orch X", "Orch X Site")
            key_x = "orch-agent-x"
            agent_x = add_agent(tenant_x, site_x, key_x)
            _rec_x, cam_x = recorder_camera(agent_x, key_x, "orch-rec-x")
            rule_x = rule(tenant_x, site_x, cam_x, [{"type": "request_footage"}])
            inc_x = incident(tenant_x, site_x, agent_x, cam_x, rule_x, s, "x")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')", inc_x, s)
            inc_on = incident(tenant, site, agent, cam, footage_rule, s, "on")
            one("select wl_end_operations_incident(%s,%s,30,'activity_ended')", inc_on, s)
            n = one("select wl_advance_all_operations_incident_evidence()")[0]
            step(n >= 1 and state(inc_x)[0] == "ended" and segments(inc_x) == []
                 and state(inc_on)[0] == "evidence_processing" and len(segments(inc_on)) == 1,
                 "the scheduled pass advances only sites with Operations enabled",
                 f"n={n} x={state(inc_x)[0]} on={state(inc_on)[0]}")
            seg_on = segments(inc_on)[0]
            step(seg_on[1] == s - timedelta(seconds=15) and seg_on[2] == s + timedelta(seconds=30),
                 "a single-moment incident yields one T-15 s .. T+30 s segment",
                 f"{seg_on[1]} .. {seg_on[2]}")

            # ----------------------------------------------------------
            # F. ACL.
            # ----------------------------------------------------------
            for fn in ("wl_advance_operations_incident_evidence",
                       "wl_advance_all_operations_incident_evidence",
                       "wl_end_operations_incident", "wl_touch_operations_incident"):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC'
                                    else a.grantee::regrole::text end,
                              p.proowner::regrole::text
                         from pg_proc p,
                              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                        where p.proname=%s and a.privilege_type='EXECUTE'""",
                    (fn,)).fetchall()
                grantees = {g for g, owner_role in rows if g != owner_role}
                step(grantees == {"service_role"}, f"{fn} is service_role only",
                     str(sorted(grantees)))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

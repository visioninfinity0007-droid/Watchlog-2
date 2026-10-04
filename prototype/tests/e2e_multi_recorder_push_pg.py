#!/usr/bin/env python3
"""Recorder push on multi-recorder sites (0146 interim fail-closed): real Postgres.

Runs after the normal full-chain apply and rolls back. Recorder push still has
one site-level token and no recorder identity (0013/0108/0110), so until push
is recorder-scoped it must never attach an alarm to an arbitrary recorder's
camera. Proves:
- single-recorder push keeps working: event and snapshot land on that
  recorder's camera, with recorder_id set and the legacy site:channel key;
- a site with one configured recorder and a disabled secondary sharing the
  channel still resolves to the configured recorder's camera;
- on a site with more than one configured recorder, wl_ingest_push,
  wl_push_liveness and wl_agent_issue_push_token fail closed (42501) with a
  clear error, and no push event row is written;
- no event row anywhere has camera_id set with recorder_id NULL, or a camera
  that belongs to a different recorder than the event; snapshots follow
  their event's camera;
- the latest wl_ingest_push resolves cameras by recorder, not site+channel.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
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

            def as_anon_try(sql, *params):
                """(row, sqlstate, message): never aborts the outer transaction."""
                cur.execute("savepoint anon_try")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_try")
                    return None, exc.sqlstate, str(exc).splitlines()[0]
                cur.execute("reset role")
                cur.execute("release savepoint anon_try")
                return row, None, ""

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
                return boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.1.0',now()
                       ) returning id""",
                    (tenant_id, site_id, key, f"push-{suffix}"),
                ).fetchone()[0]

            def sync_recorders(agent_id, key, rows):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps(rows),
                )[0]

            def sync_cameras(agent_id, key, recorder_id, channels):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{"channel": ch, "name": f"Camera {ch}", "is_configured": True}
                                for ch in channels]),
                )[0]

            def push_events(channels, typ, ts):
                return json.dumps([
                    {"channel": ch, "event_type": typ, "device_ts": ts.isoformat(),
                     "agent_ts": ts.isoformat(), "snapshot_b64": "YWJj"}
                    for ch in channels
                ])

            def push_rows(site_id, typ):
                return cur.execute(
                    """select e.id,e.recorder_id,e.camera_id,e.dedupe_key,s.camera_id
                         from events e
                         left join snapshots s on s.event_id=e.id
                        where e.site_id=%s and e.event_type=%s""",
                    (site_id, typ),
                ).fetchall()

            rec_a_row = {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True}
            rec_b_row = {"local_key": "rec-b", "display_name": "Recorder B",
                         "is_primary": False, "is_configured": True}
            ts = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)
            sites = []

            # ----------------------------------------------------------------
            # Single-recorder control: a 5.0.x-style legacy site provisions push.
            # ----------------------------------------------------------------
            t1, s1 = bootstrap("push-single@watchlog.test", "Push Single", "Single Shop")
            sites.append(s1)
            key1 = "push-single-agent"
            agent1 = add_agent(t1, s1, key1, "single")
            cams1 = as_anon(
                "select wl_sync_cameras(%s,%s,%s::jsonb)", agent1, key1,
                json.dumps([{"channel": "1", "name": "Camera 1", "is_configured": True}]),
            )[0]
            rec1 = cur.execute(
                "select id from recorders where site_id=%s and is_configured", (s1,)
            ).fetchone()[0]
            issued, state, msg = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s)", agent1, key1,
            )
            token1 = (issued or [{}])[0].get("token")
            step(state is None and bool(token1),
                 "single-recorder site can still provision recorder push", msg)
            ingested, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token1, push_events(["1"], "push_single", ts),
            )
            rows = push_rows(s1, "push_single")
            legacy_key = cur.execute(
                "select wl_dedupe_key(%s,'1',null,%s,'push_single')", (s1, ts),
            ).fetchone()[0]
            step(state is None and ingested[0]["inserted"] == 1 and len(rows) == 1
                 and str(rows[0][1]) == str(rec1)
                 and str(rows[0][2]) == str(cams1["1"])
                 and str(rows[0][4]) == str(cams1["1"])
                 and rows[0][3] == legacy_key,
                 "single-recorder push lands on that recorder's camera with recorder_id "
                 "and the legacy site:channel key",
                 msg or str(rows))
            live, state, msg = as_anon_try("select wl_push_liveness(%s)", token1)
            step(state is None and live[0]["ok"] is True,
                 "single-recorder push liveness still records reachability", msg)

            # ----------------------------------------------------------------
            # One configured recorder plus a disabled secondary on channel 1.
            # ----------------------------------------------------------------
            t2, s2 = bootstrap("push-disabled@watchlog.test", "Push Disabled", "Rollback Shop")
            sites.append(s2)
            key2 = "push-disabled-agent"
            agent2 = add_agent(t2, s2, key2, "disabled")
            recs2 = sync_recorders(agent2, key2, [rec_a_row, rec_b_row])
            cam2_a = sync_cameras(agent2, key2, recs2["rec-a"], ["1"])["1"]
            sync_cameras(agent2, key2, recs2["rec-b"], ["1"])
            sync_recorders(agent2, key2, [rec_a_row, {**rec_b_row, "is_configured": False}])
            issued2, state, msg = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s)", agent2, key2,
            )
            token2 = (issued2 or [{}])[0].get("token")
            ingested2, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token2, push_events(["1"], "push_disabled", ts),
            )
            rows2 = push_rows(s2, "push_disabled")
            step(state is None and len(rows2) == 1
                 and str(rows2[0][1]) == str(recs2["rec-a"])
                 and str(rows2[0][2]) == str(cam2_a)
                 and str(rows2[0][4]) == str(cam2_a),
                 "a disabled secondary's channel 1 never captures the configured recorder's push",
                 msg or str(rows2))

            # ----------------------------------------------------------------
            # Multi-recorder site. The token was provisioned while the site had
            # one recorder; Recorder B (sharing channel 1, owning channel 2) is
            # added afterwards.
            # ----------------------------------------------------------------
            t3, s3 = bootstrap("push-multi@watchlog.test", "Push Multi", "Two Recorder Plaza")
            sites.append(s3)
            key3 = "push-multi-agent"
            agent3 = add_agent(t3, s3, key3, "multi")
            recs3 = sync_recorders(agent3, key3, [rec_a_row])
            sync_cameras(agent3, key3, recs3["rec-a"], ["1"])
            issued3, state, msg = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s)", agent3, key3,
            )
            token3 = (issued3 or [{}])[0].get("token")
            step(state is None and bool(token3),
                 "push token provisioned while the site had one recorder", msg)
            recs3 = sync_recorders(agent3, key3, [rec_a_row, rec_b_row])
            sync_cameras(agent3, key3, recs3["rec-b"], ["1", "2"])

            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token3,
                push_events(["1", "2"], "push_multi", ts),
            )
            step(state == "42501" and "more than one recorder" in msg.lower()
                 and push_rows(s3, "push_multi") == [],
                 "multi-recorder push ingest fails closed and writes no event", msg)
            _, state, msg = as_anon_try("select wl_push_liveness(%s)", token3)
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "multi-recorder push liveness fails closed", msg)
            _, state, msg = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s)", agent3, key3,
            )
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "multi-recorder site cannot obtain (or re-obtain) a push token", msg)

            # Disabling B restores the unambiguous singleton push path.
            sync_recorders(agent3, key3, [rec_a_row, {**rec_b_row, "is_configured": False}])
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token3,
                push_events(["1"], "push_multi_rollback", ts),
            )
            rows3 = push_rows(s3, "push_multi_rollback")
            step(state is None and len(rows3) == 1
                 and str(rows3[0][1]) == str(recs3["rec-a"]),
                 "push resumes on the configured recorder once the secondary is disabled",
                 msg or str(rows3))

            # ----------------------------------------------------------------
            # Lineage invariants over every site in this test.
            # ----------------------------------------------------------------
            bad = cur.execute(
                """select e.id, e.recorder_id, e.camera_id, c.recorder_id, s.camera_id
                     from events e
                     left join cameras c on c.id=e.camera_id
                     left join snapshots s on s.event_id=e.id
                    where e.site_id = any(%s::uuid[])
                      and (
                        (e.camera_id is not null and e.recorder_id is null)
                        or (e.camera_id is not null and c.recorder_id is distinct from e.recorder_id)
                        or (s.event_id is not null and s.camera_id is distinct from e.camera_id)
                      )""",
                ([str(x) for x in sites],),
            ).fetchall()
            step(bad == [],
                 "no event has a camera without recorder_id, a wrong-recorder camera, "
                 "or a snapshot on another camera", str(bad))

            body = cur.execute(
                "select pg_get_functiondef('public.wl_ingest_push(text,jsonb)'::regprocedure)"
            ).fetchone()[0]
            joins = re.findall(r"left join (?:public\.)?cameras c\s+on ([^\n]+(?:\n\s+and [^\n]+)*)",
                               body)
            step(len(joins) == 2 and all("c.recorder_id" in j for j in joins),
                 "latest wl_ingest_push resolves event and snapshot cameras by recorder",
                 str(joins))

            acl = cur.execute(
                """select has_function_privilege('anon','public.wl_ingest_push(text,jsonb)','execute'),
                          has_function_privilege('anon','public.wl_push_liveness(text)','execute'),
                          has_function_privilege('anon','public.wl_agent_issue_push_token(uuid,text)','execute'),
                          has_function_privilege('anon','public.wl_push_recorder_for_site(uuid,uuid)','execute'),
                          has_function_privilege('authenticated','public.wl_push_recorder_for_site(uuid,uuid)','execute')"""
            ).fetchone()
            step(acl == (True, True, True, False, False),
                 "push RPCs keep their token/agent-key ACL; the recorder guard is owner-only",
                 str(acl))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

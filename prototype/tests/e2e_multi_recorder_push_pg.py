#!/usr/bin/env python3
"""Recorder-scoped recorder push (MNVR-011 / MNVR-026): real Postgres.

Runs after the normal full-chain apply and rolls back. A push token names one
recorder (push_sources.recorder_id), so an alarm establishes its recorder
before any channel-to-camera resolution. Proves:
- single-recorder push keeps working: event and snapshot land on that
  recorder's camera, with recorder_id set and the legacy site:channel key, and
  the 5.0.x token RPC now scopes its token to that recorder;
- a site with one configured recorder and a disabled secondary sharing the
  channel still resolves to the configured recorder's camera;
- a legacy site-wide token (no recorder) keeps working only while exactly one
  recorder is configured; once a second recorder exists, ingest, liveness and
  the 5.0.x token RPC fail closed (42501) and no push event row is written;
- wl_agent_issue_push_token(agent, key, recorder) issues one idempotent token
  per recorder, only for the current site Agent and a configured recorder of
  its own site; on a single-recorder site it adopts the site's legacy token;
- two recorders sharing channel 1 each get their push events, stills and
  liveness attributed to the right recorder, with the recorder dedupe
  namespace; a disabled recorder's token fails closed;
- the owner-portal wl_issue_push_token is guarded the same way: the site-level
  form refuses a multi-recorder site, the recorder form rotates only that
  recorder's token; both forms authorise by the role held in the site's own
  account, never a role from another account;
- one alarm seen by both the Agent and push (same recorder, channel, type,
  time) is one event row, in either order (MNVR-026 shared dedupe identity);
- no event row has camera_id set with recorder_id NULL, or a camera from a
  different recorder; snapshots follow their event's camera;
- the latest wl_ingest_push resolves cameras by recorder and keys events with
  wl_recorder_event_dedupe_key; ACLs stay token/agent-key/owner shaped.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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

            def as_auth_try(uid, sql, *params):
                """(row, sqlstate, message): never aborts the outer transaction."""
                cur.execute("savepoint auth_try")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint auth_try")
                    return None, exc.sqlstate, str(exc).splitlines()[0]
                cur.execute("reset role")
                cur.execute("release savepoint auth_try")
                return row, None, ""

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
                return boot["tenant_id"], site, uid

            def add_agent(tenant_id, site_id, key, suffix, enrolled_ago="0 seconds",
                          seen_ago="0 seconds"):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at,enrolled_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.1.0',now()-%s::interval,now()-%s::interval
                       ) returning id""",
                    (tenant_id, site_id, key, f"push-{suffix}", seen_ago, enrolled_ago),
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

            def push_events(channels, typ, ts, snapshot=True):
                return json.dumps([
                    {"channel": ch, "event_type": typ, "device_ts": ts.isoformat(),
                     "agent_ts": ts.isoformat(), **({"snapshot_b64": "YWJj"} if snapshot else {})}
                    for ch in channels
                ])

            def push_rows(site_id, typ):
                return cur.execute(
                    """select e.id,e.recorder_id,e.camera_id,e.dedupe_key,s.camera_id
                         from events e
                         left join snapshots s on s.event_id=e.id
                        where e.site_id=%s and e.event_type=%s
                        order by e.id""",
                    (site_id, typ),
                ).fetchall()

            def issue(agent_id, key, recorder_id=None):
                if recorder_id is None:
                    return as_anon_try("select wl_agent_issue_push_token(%s,%s)", agent_id, key)
                return as_anon_try("select wl_agent_issue_push_token(%s,%s,%s)",
                                   agent_id, key, recorder_id)

            def token_of(result):
                row, _state, _msg = result
                return (row or [{}])[0].get("token") if row else None

            def source(token):
                """(recorder_id, enabled, last_push_at); all None for an unknown token."""
                if not token:
                    return (None, None, None)
                row = cur.execute(
                    """select to_jsonb(p)->>'recorder_id', enabled, last_push_at
                         from push_sources p where token=%s""",
                    (token,),
                ).fetchone()
                return (row[0], row[1], row[2]) if row else (None, None, None)

            def recorder_key(site_id, recorder_id, channel, typ, ts, event_id=None):
                return cur.execute(
                    "select wl_recorder_event_dedupe_key(%s,%s,%s,%s,%s,%s)",
                    (site_id, recorder_id, channel, event_id, ts, typ),
                ).fetchone()[0]

            rec_a_row = {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True}
            rec_b_row = {"local_key": "rec-b", "display_name": "Recorder B",
                         "is_primary": False, "is_configured": True}
            ts = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)
            sites = []

            # ----------------------------------------------------------------
            # Single-recorder control: a 5.0.x-style legacy site provisions push.
            # ----------------------------------------------------------------
            t1, s1, owner1 = bootstrap("push-single@watchlog.test", "Push Single", "Single Shop")
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
            issued = issue(agent1, key1)
            token1 = token_of(issued)
            step(issued[1] is None and bool(token1),
                 "single-recorder site can still provision recorder push", issued[2])
            step(token1 is not None and source(token1)[0] == str(rec1),
                 "the 5.0.x token RPC scopes a new token to the site's single recorder",
                 str(source(token1) if token1 else None))
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

            # Owner portal, single-recorder site: the site-level form still rotates.
            rotated, state, msg = as_auth_try(owner1, "select wl_issue_push_token(%s)", s1)
            token1b = (rotated or [{}])[0].get("token") if rotated else None
            step(state is None and token1b and token1b != token1
                 and source(token1)[1] is False and source(token1b)[0] == str(rec1),
                 "owner re-issue on a single-recorder site rotates to a token for that recorder",
                 msg or str((source(token1), source(token1b) if token1b else None)))

            # ----------------------------------------------------------------
            # One configured recorder plus a disabled secondary on channel 1.
            # ----------------------------------------------------------------
            t2, s2, _ = bootstrap("push-disabled@watchlog.test", "Push Disabled", "Rollback Shop")
            sites.append(s2)
            key2 = "push-disabled-agent"
            agent2 = add_agent(t2, s2, key2, "disabled")
            recs2 = sync_recorders(agent2, key2, [rec_a_row, rec_b_row])
            cam2_a = sync_cameras(agent2, key2, recs2["rec-a"], ["1"])["1"]
            sync_cameras(agent2, key2, recs2["rec-b"], ["1"])
            sync_recorders(agent2, key2, [rec_a_row, {**rec_b_row, "is_configured": False}])
            token2 = token_of(issue(agent2, key2))
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
            # Legacy site-wide token. It was issued before the site had any
            # recorder, so it names none; it works while exactly one recorder
            # is configured and fails closed once Recorder B is added.
            # ----------------------------------------------------------------
            t3, s3, _ = bootstrap("push-legacy@watchlog.test", "Push Legacy", "Legacy Token Plaza")
            sites.append(s3)
            key3 = "push-legacy-agent"
            agent3 = add_agent(t3, s3, key3, "legacy")
            token3 = token_of(issue(agent3, key3))
            step(bool(token3) and source(token3)[0] is None,
                 "a token issued before the site has a recorder names no recorder",
                 str(source(token3) if token3 else None))
            recs3 = sync_recorders(agent3, key3, [rec_a_row])
            cam3_a = sync_cameras(agent3, key3, recs3["rec-a"], ["1"])["1"]
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token3,
                push_events(["1"], "push_legacy_single", ts),
            )
            rows3 = push_rows(s3, "push_legacy_single")
            step(state is None and len(rows3) == 1
                 and str(rows3[0][1]) == str(recs3["rec-a"]) and str(rows3[0][2]) == str(cam3_a),
                 "a legacy site-wide token works while exactly one recorder is configured",
                 msg or str(rows3))
            recs3 = sync_recorders(agent3, key3, [rec_a_row, rec_b_row])
            sync_cameras(agent3, key3, recs3["rec-b"], ["1", "2"])
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token3,
                push_events(["1", "2"], "push_legacy_multi", ts),
            )
            step(state == "42501" and "more than one recorder" in msg.lower()
                 and push_rows(s3, "push_legacy_multi") == [],
                 "a legacy site-wide token fails closed on a multi-recorder site, no event", msg)
            _, state, msg = as_anon_try("select wl_push_liveness(%s)", token3)
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "legacy site-wide token liveness fails closed on a multi-recorder site", msg)
            _, state, msg = issue(agent3, key3)
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "the 5.0.x site-level token RPC refuses a multi-recorder site", msg)

            # ----------------------------------------------------------------
            # Single-recorder site upgraded to 5.1: the recorder form adopts
            # the site's legacy token, so the recorder's configuration stays valid.
            # ----------------------------------------------------------------
            t5, s5, _ = bootstrap("push-adopt@watchlog.test", "Push Adopt", "Adoption Arcade")
            sites.append(s5)
            key5 = "push-adopt-agent"
            agent5 = add_agent(t5, s5, key5, "adopt")
            token5 = token_of(issue(agent5, key5))
            recs5 = sync_recorders(agent5, key5, [rec_a_row])
            adopted = issue(agent5, key5, recs5["rec-a"])
            step(adopted[1] is None and token_of(adopted) == token5
                 and str(source(token5)[0]) == str(recs5["rec-a"]),
                 "on a single-recorder site the recorder form adopts the legacy token",
                 adopted[2] or str(source(token5)))
            sync_recorders(agent5, key5, [rec_a_row, rec_b_row])
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", token5,
                push_events(["1"], "push_adopted", ts, snapshot=False),
            )
            rows5 = push_rows(s5, "push_adopted")
            step(state is None and len(rows5) == 1 and str(rows5[0][1]) == str(recs5["rec-a"]),
                 "an adopted token keeps working for its recorder after a second recorder",
                 msg or str(rows5))

            # ----------------------------------------------------------------
            # Recorder-scoped push on a two-recorder site sharing channel 1.
            # ----------------------------------------------------------------
            t4, s4, owner4 = bootstrap("push-multi@watchlog.test", "Push Multi",
                                       "Two Recorder Plaza")
            sites.append(s4)
            key4 = "push-multi-agent"
            agent4 = add_agent(t4, s4, key4, "multi", enrolled_ago="1 day")
            recs4 = sync_recorders(agent4, key4, [rec_a_row, rec_b_row])
            rec4_a, rec4_b = recs4["rec-a"], recs4["rec-b"]
            cams4_a = sync_cameras(agent4, key4, rec4_a, ["1"])
            cams4_b = sync_cameras(agent4, key4, rec4_b, ["1", "2"])

            issued_a = issue(agent4, key4, rec4_a)
            issued_b = issue(agent4, key4, rec4_b)
            tok_a, tok_b = token_of(issued_a), token_of(issued_b)
            step(issued_a[1] is None and issued_b[1] is None and tok_a and tok_b
                 and tok_a != tok_b
                 and str(source(tok_a)[0]) == str(rec4_a)
                 and str(source(tok_b)[0]) == str(rec4_b),
                 "the current Agent gets one token per recorder on a multi-recorder site",
                 issued_a[2] or issued_b[2])
            again = issue(agent4, key4, rec4_a)
            step(token_of(again) == tok_a
                 and cur.execute(
                     "select count(*) from push_sources p where to_jsonb(p)->>'recorder_id'=%s",
                     (str(rec4_a),)).fetchone()[0] == 1,
                 "recorder token issuance is idempotent (no rotation, no extra push agent)",
                 again[2])

            ia, state_a, msg_a = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_a,
                push_events(["1"], "push_multi_a", ts),
            )
            ib, state_b, msg_b = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_b,
                push_events(["1", "2"], "push_multi_b", ts),
            )
            rows_a = push_rows(s4, "push_multi_a")
            rows_b = push_rows(s4, "push_multi_b")
            key_a1 = cur.execute("select wl_dedupe_key(%s,'1',null,%s,'push_multi_a')",
                                 (s4, ts)).fetchone()[0]
            keys_b = {recorder_key(s4, rec4_b, ch, "push_multi_b", ts) for ch in ("1", "2")}
            step(state_a is None and len(rows_a) == 1
                 and str(rows_a[0][1]) == str(rec4_a)
                 and str(rows_a[0][2]) == str(cams4_a["1"])
                 and str(rows_a[0][4]) == str(cams4_a["1"])
                 and rows_a[0][3] == key_a1,
                 "Recorder A's channel 1 push lands on A's camera with the continuity key",
                 msg_a or str(rows_a))
            step(state_b is None and len(rows_b) == 2
                 and all(str(r[1]) == str(rec4_b) for r in rows_b)
                 and {str(r[2]) for r in rows_b} == {str(cams4_b["1"]), str(cams4_b["2"])}
                 and all(str(r[4]) == str(r[2]) for r in rows_b)
                 and {r[3] for r in rows_b} == keys_b,
                 "Recorder B's channel 1 and 2 push land on B's cameras with B's dedupe namespace",
                 msg_b or str(rows_b))

            live_b, state, msg = as_anon_try("select wl_push_liveness(%s)", tok_b)
            step(state is None and live_b[0]["ok"] is True
                 and source(tok_b)[2] is not None
                 and source(tok_a)[2] is not None,
                 "recorder push liveness is recorded on that recorder's own source", msg)
            cur.execute("update push_sources set last_push_at=null where token=%s", (tok_a,))
            as_anon_try("select wl_push_liveness(%s)", tok_b)
            step(source(tok_a)[2] is None and source(tok_b)[2] is not None,
                 "liveness for Recorder B never marks Recorder A's push as live")
            status_b, state, msg = as_anon_try(
                "select wl_agent_push_status(%s,%s,%s)", agent4, key4, rec4_b)
            status_a, _, _ = as_anon_try(
                "select wl_agent_push_status(%s,%s,%s)", agent4, key4, rec4_a)
            step(state is None and status_b[0]["delivery_verified"] is True
                 and status_a[0]["delivery_verified"] is False,
                 "push delivery status is per recorder", msg or str((status_a, status_b)))
            _, state, msg = as_anon_try("select wl_agent_push_status(%s,%s)", agent4, key4)
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "site-level push status refuses a multi-recorder site instead of guessing", msg)

            # Authority and lineage of the recorder form.
            stale_key = "push-multi-stale"
            stale = add_agent(t4, s4, stale_key, "stale", enrolled_ago="2 days",
                              seen_ago="1 hour")
            _, state, msg = issue(stale, stale_key, rec4_a)
            step(state == "42501" and "current site authority" in msg.lower(),
                 "a superseded Agent cannot obtain a recorder push token", msg)
            _, state, msg = issue(agent4, key4, recs3["rec-a"])
            step(state == "42501",
                 "an Agent cannot obtain a push token for another site's recorder", msg)
            push_agent = (cur.execute(
                "select agent_id from push_sources where token=%s", (tok_a,)).fetchone()
                or [None])[0]
            # Give the virtual agent a known key so authentication passes and the
            # device_driver guard itself is what refuses (a random md5 hash would
            # fail authentication first, 28000, whether the guard exists or not).
            push_agent_key = "push-virtual-agent-known-key"
            cur.execute(
                """update agents set agent_key_hash=encode(sha256(convert_to(%s,'UTF8')),'hex')
                    where id=%s and device_driver='recorder-push'""",
                (push_agent_key, push_agent),
            )
            _, state, msg = issue(push_agent, push_agent_key, rec4_a)
            _, state2, msg2 = issue(push_agent, push_agent_key)
            step(push_agent is not None
                 and state == "42501"
                 and "recorder-push agent cannot issue push tokens" in msg.lower()
                 and state2 == "42501"
                 and "recorder-push agent cannot issue push tokens" in msg2.lower(),
                 "a recorder-push virtual agent cannot mint push tokens (recorder and site forms)",
                 f"{state} {msg} | {state2} {msg2}")

            # MNVR-026: one alarm seen by the Agent and by push is one event row.
            t_par = ts + timedelta(minutes=5)
            agent_first = as_anon(
                "select wl_ingest_events(%s,%s,%s::jsonb)", agent4, key4,
                json.dumps([{"recorder_id": str(rec4_b), "channel": "2",
                             "event_type": "alarm_parity", "device_ts": t_par.isoformat(),
                             "agent_ts": t_par.isoformat()}]),
            )[0]
            push_second, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_b,
                push_events(["2"], "alarm_parity", t_par),
            )
            push_first, state2, msg2 = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_a,
                push_events(["1"], "alarm_parity", t_par, snapshot=False),
            )
            agent_second = as_anon(
                "select wl_ingest_events(%s,%s,%s::jsonb)", agent4, key4,
                json.dumps([{"recorder_id": str(rec4_a), "channel": "1",
                             "event_type": "alarm_parity", "device_ts": t_par.isoformat(),
                             "agent_ts": t_par.isoformat()}]),
            )[0]
            parity = push_rows(s4, "alarm_parity")
            step(state is None and state2 is None
                 and agent_first["inserted"] == 1 and push_second[0]["inserted"] == 0
                 and push_first[0]["inserted"] == 1 and agent_second["inserted"] == 0
                 and len(parity) == 2
                 and sorted(str(r[1]) for r in parity) == sorted([str(rec4_a), str(rec4_b)]),
                 "the Agent and push share one dedupe identity per recorder alarm, either order",
                 msg or msg2 or str(parity))

            # Disabling Recorder B makes its token fail closed; A is unaffected.
            sync_recorders(agent4, key4, [rec_a_row, {**rec_b_row, "is_configured": False}])
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_b,
                push_events(["1"], "push_multi_disabled", ts),
            )
            step(state == "42501" and push_rows(s4, "push_multi_disabled") == [],
                 "a disabled recorder's push token fails closed and writes no event", msg)
            _, state, msg = as_anon_try("select wl_push_liveness(%s)", tok_b)
            step(state == "42501", "a disabled recorder's push liveness fails closed", msg)
            _, state, msg = issue(agent4, key4, rec4_b)
            step(state == "42501",
                 "a disabled recorder cannot obtain a push token", msg)
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_a,
                push_events(["1"], "push_multi_a_still", ts),
            )
            step(state is None and len(push_rows(s4, "push_multi_a_still")) == 1,
                 "Recorder A's push keeps working while B is disabled", msg)
            sync_recorders(agent4, key4, [rec_a_row, rec_b_row])

            # Owner portal on the multi-recorder site.
            _, state, msg = as_auth_try(owner4, "select wl_issue_push_token(%s)", s4)
            step(state == "42501" and "more than one recorder" in msg.lower(),
                 "the owner's site-level token form refuses a multi-recorder site", msg)
            rotated_b, state, msg = as_auth_try(
                owner4, "select wl_issue_push_token(%s,%s)", s4, rec4_b)
            tok_b2 = (rotated_b or [{}])[0].get("token") if rotated_b else None
            step(state is None and tok_b2 and tok_b2 != tok_b
                 and source(tok_b)[1] is False and source(tok_a)[1] is True
                 and str(source(tok_b2)[0]) == str(rec4_b),
                 "the owner's recorder form rotates only that recorder's token",
                 msg or str((source(tok_a), source(tok_b))))
            _, state, msg = as_anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", tok_b,
                push_events(["1"], "push_rotated_old", ts),
            )
            step(bool(tok_b) and state == "28000", "a rotated recorder token is refused", msg)
            _, state, msg = as_auth_try(owner1, "select wl_issue_push_token(%s,%s)", s4, rec4_a)
            step(state not in (None, "42883"),
                 "another account cannot issue a token for this site", msg)
            _, state, msg = as_auth_try(owner4, "select wl_issue_push_token(%s,%s)",
                                        s4, recs3["rec-a"])
            step(state not in (None, "42883"),
                 "the owner form refuses a recorder of another site", msg)
            listed = as_auth(owner4, "select wl_push_sources()")[0]
            by_recorder = {str(x.get("recorder_id")) for x in listed if x.get("enabled")}
            step(by_recorder == {str(rec4_a), str(rec4_b)},
                 "the owner's push source list names each source's recorder", str(listed)[:300])

            # A push token is a live ingest credential: only the role held in
            # the site's own account counts. wl_my_role() is account-blind, so a
            # viewer here who owns another account must still be refused. The
            # other account gets the lowest id and its membership is written
            # first, so an account-blind lookup meets it first whether it scans
            # the table or the (user_id, tenant_id) key; the site's membership
            # is the oldest, so it is the account the user acts in.
            owned_elsewhere = cur.execute(
                "insert into tenants(id,name,account_status) values "
                "('00000000-0000-0000-0000-0000000000d1','Push Owned Elsewhere','active') "
                "returning id").fetchone()[0]

            def viewer_here_owner_elsewhere(email, tenant_here):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (email,),
                ).fetchone()[0]
                cur.execute(
                    "insert into memberships(user_id,tenant_id,role) values (%s,%s,'owner')",
                    (uid, owned_elsewhere),
                )
                cur.execute(
                    "insert into memberships(user_id,tenant_id,role,created_at) "
                    "values (%s,%s,'viewer',now()-interval '30 days')",
                    (uid, tenant_here),
                )
                return uid

            def enabled_sources(site_id):
                return sorted(str(r[0]) for r in cur.execute(
                    "select id from push_sources where site_id=%s and enabled", (site_id,)
                ).fetchall())

            viewer4 = viewer_here_owner_elsewhere("push-multi-viewer@watchlog.test", t4)
            viewer1 = viewer_here_owner_elsewhere("push-single-viewer@watchlog.test", t1)
            acting = [as_auth(u, "select wl_my_tenant()::text, wl_my_role()")
                      for u in (viewer4, viewer1)]
            print(f"  info  wl_my_tenant/wl_my_role: {acting}")
            before4, before1 = enabled_sources(s4), enabled_sources(s1)
            row, state, msg = as_auth_try(
                viewer4, "select wl_issue_push_token(%s,%s)", s4, rec4_a)
            step(row is None and "role" in msg.lower() and enabled_sources(s4) == before4,
                 "a viewer here who owns another account cannot mint a recorder push token",
                 msg or str(row)[:120])
            row, state, msg = as_auth_try(viewer1, "select wl_issue_push_token(%s)", s1)
            step(row is None and "role" in msg.lower() and enabled_sources(s1) == before1,
                 "a viewer here who owns another account cannot mint a site push token",
                 msg or str(row)[:120])

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
            bad_src = cur.execute(
                """select ps.id from push_sources ps
                     join recorders r on r.id::text=to_jsonb(ps)->>'recorder_id'
                    where r.site_id is distinct from ps.site_id
                       or r.tenant_id is distinct from ps.tenant_id""").fetchall()
            dup = cur.execute(
                """select to_jsonb(ps)->>'recorder_id' from push_sources ps
                    where enabled and to_jsonb(ps)->>'recorder_id' is not null
                    group by 1 having count(*) > 1""").fetchall()
            unique_idx = cur.execute(
                """select count(*) from pg_indexes
                    where schemaname='public' and tablename='push_sources'
                      and indexdef ilike '%%unique%%(recorder_id)%%where%%enabled%%'""").fetchone()[0]
            step(bad_src == [] and dup == [] and unique_idx == 1,
                 "every push source keeps its recorder's lineage; one live token per recorder",
                 str((bad_src, dup, unique_idx)))

            body = cur.execute(
                "select pg_get_functiondef('public.wl_ingest_push(text,jsonb)'::regprocedure)"
            ).fetchone()[0]
            joins = re.findall(r"left join (?:public\.)?cameras c\s+on ([^\n]+(?:\n\s+and [^\n]+)*)",
                               body)
            step(len(joins) == 2 and all("c.recorder_id" in j for j in joins),
                 "latest wl_ingest_push resolves event and snapshot cameras by recorder",
                 str(joins))
            step("wl_recorder_event_dedupe_key" in body
                 and "c.site_id = v_agent.site_id and c.channel = d.channel" not in body,
                 "latest wl_ingest_push keys events in the recorder dedupe namespace")

            def can(role, signature):
                # NULL (missing function) reads as "no".
                return cur.execute(
                    "select coalesce(has_function_privilege(%s, to_regprocedure(%s), 'execute'), false)",
                    (role, signature),
                ).fetchone()[0]

            acl = tuple(can(role, sig) for role, sig in (
                ("anon", "public.wl_ingest_push(text,jsonb)"),
                ("anon", "public.wl_push_liveness(text)"),
                ("anon", "public.wl_agent_issue_push_token(uuid,text)"),
                ("anon", "public.wl_agent_issue_push_token(uuid,text,uuid)"),
                ("anon", "public.wl_agent_push_status(uuid,text,uuid)"),
                ("anon", "public.wl_issue_push_token(uuid)"),
                ("anon", "public.wl_issue_push_token(uuid,uuid)"),
                ("authenticated", "public.wl_issue_push_token(uuid,uuid)"),
                ("anon", "public.wl_push_recorder_for_site(uuid,uuid)"),
                ("authenticated", "public.wl_push_recorder_for_site(uuid,uuid)"),
                ("anon", "public.wl_push_source_recorder(uuid)"),
                ("authenticated", "public.wl_push_source_recorder(uuid)"),
            ))
            step(acl == (True, True, True, True, True, False, False, True,
                         False, False, False, False),
                 "push RPCs keep token/agent-key/owner ACLs; the recorder guards are owner-only",
                 str(acl))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

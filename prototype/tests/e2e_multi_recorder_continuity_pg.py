#!/usr/bin/env python3
"""Multi-recorder lifecycle continuity (0152): real Postgres execution.

Proves:
- one immutable continuity owner is created/backfilled;
- preferred primary can move after identities exist;
- continuity ownership never moves with preferred primary;
- old recorder keeps the legacy event dedupe namespace after promotion;
- promoted recorder remains recorder-namespaced;
- continuity owner cannot be disabled in contract v4 / WatchLog 5.1;
- ordinary secondary recorder disable/re-enable remains supported;
- Agent contract is v4 and advertises recorder_continuity.
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
for key in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
            "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(key):
        ENV[key] = os.environ[key]

import psycopg  # noqa: E402

STEPS = []


def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def run():
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
                cur.execute("select set_config('request.jwt.claims',%s,true)", (claims(uid),))
                cur.execute("set local role authenticated")
                try:
                    return cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    return cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")

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
                "insert into auth.users(id,email) values(gen_random_uuid(),%s) returning id",
                ("continuity@watchlog.test",),
            ).fetchone()[0]
            boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                           "Continuity Test", "Warehouse Continuity")[0]
            tenant_id = boot["tenant_id"]
            site_id = cur.execute(
                "select id from sites where tenant_id=%s order by created_at limit 1",
                (tenant_id,),
            ).fetchone()[0]
            key = "continuity-agent-key"
            agent_id = cur.execute(
                """insert into agents(
                     tenant_id,site_id,agent_key_hash,hostname,platform,
                     agent_version,last_seen_at
                   ) values(
                     %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                     'continuity-agent','windows','5.1.0',now()
                   ) returning id""",
                (tenant_id, site_id, key),
            ).fetchone()[0]

            first = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([{
                    "local_key": "rec-a",
                    "display_name": "Recorder A",
                    "is_primary": True,
                    "is_configured": True,
                }]),
            )[0]
            rec_a = first["rec-a"]
            row_a = cur.execute(
                "select is_primary,continuity_owner from recorders where id=%s",
                (rec_a,),
            ).fetchone()
            step(row_a == (True, True),
                 "first recorder is preferred primary and immutable continuity owner")

            cams_a = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_id, key, rec_a,
                json.dumps([{"channel": "1", "name": "A1", "is_configured": True}]),
            )[0]
            cam_a = cams_a["1"]

            both = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([
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
            rec_b = both["rec-b"]
            cam_b = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_id, key, rec_b,
                json.dumps([{"channel": "1", "name": "B1", "is_configured": True}]),
            )[0]["1"]

            # Move preferred primary to B.
            moved = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([
                    {
                        "local_key": "rec-a",
                        "display_name": "Recorder A",
                        "is_primary": False,
                        "is_configured": True,
                    },
                    {
                        "local_key": "rec-b",
                        "display_name": "Recorder B",
                        "is_primary": True,
                        "is_configured": True,
                    },
                ]),
            )[0]
            states = {
                str(r[0]): (r[1], r[2])
                for r in cur.execute(
                    "select id,is_primary,continuity_owner from recorders where site_id=%s",
                    (site_id,),
                ).fetchall()
            }
            step(states[str(rec_a)] == (False, True)
                 and states[str(rec_b)] == (True, False),
                 "preferred primary moves but continuity ownership stays on Recorder A",
                 str(states))

            ts = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
            typ = "continuity_probe"
            old_key = cur.execute(
                "select wl_dedupe_key(%s,'1',null,%s,%s)",
                (site_id, ts, typ),
            ).fetchone()[0]
            key_a = cur.execute(
                "select wl_recorder_event_dedupe_key(%s,%s,'1',null,%s,%s)",
                (site_id, rec_a, ts, typ),
            ).fetchone()[0]
            key_b = cur.execute(
                "select wl_recorder_event_dedupe_key(%s,%s,'1',null,%s,%s)",
                (site_id, rec_b, ts, typ),
            ).fetchone()[0]
            step(key_a == old_key,
                 "continuity owner keeps exact legacy event dedupe namespace")
            step(key_b != old_key and key_b != key_a,
                 "promoted preferred primary remains recorder-namespaced")

            ing = as_anon(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent_id, key,
                json.dumps([
                    {
                        "recorder_id": str(rec_a),
                        "channel": "1",
                        "event_type": typ,
                        "device_ts": ts.isoformat(),
                        "agent_ts": ts.isoformat(),
                    },
                    {
                        "recorder_id": str(rec_b),
                        "channel": "1",
                        "event_type": typ,
                        "device_ts": ts.isoformat(),
                        "agent_ts": ts.isoformat(),
                    },
                ]),
            )[0]
            event_rows = cur.execute(
                """select recorder_id,camera_id,dedupe_key
                     from events
                    where site_id=%s and event_type=%s""",
                (site_id, typ),
            ).fetchall()
            by_rec = {str(r[0]): (str(r[1]), r[2]) for r in event_rows}
            step(ing["inserted"] == 2
                 and by_rec[str(rec_a)][0] == str(cam_a)
                 and by_rec[str(rec_b)][0] == str(cam_b),
                 "events remain distinct after preferred-primary promotion")

            # Contract v4 / 5.1: continuity owner cannot be disabled. The failed
            # desired-state mutation must roll back atomically, including the temporary
            # preferred-primary clearing inside wl_sync_recorders.
            raised, msg = as_anon_raises(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([
                    {
                        "local_key": "rec-a",
                        "display_name": "Recorder A",
                        "is_primary": False,
                        "is_configured": False,
                    },
                    {
                        "local_key": "rec-b",
                        "display_name": "Recorder B",
                        "is_primary": True,
                        "is_configured": True,
                    },
                ]),
            )
            step(raised and "continuity recorder cannot be disabled" in msg.lower(),
                 "contract v4 rejects disabling the immutable continuity recorder", msg)

            after_reject = {
                str(r[0]): (r[1], r[2], r[3])
                for r in cur.execute(
                    "select id,is_primary,is_configured,continuity_owner "
                    "from recorders where site_id=%s",
                    (site_id,),
                ).fetchall()
            }
            step(after_reject[str(rec_a)] == (False, True, True)
                 and after_reject[str(rec_b)] == (True, True, False),
                 "failed continuity-disable payload is atomic; preferred primary remains B",
                 str(after_reject))

            cur.execute("savepoint continuity_constraint")
            direct_blocked = False
            try:
                cur.execute(
                    "update recorders set is_configured=false where id=%s",
                    (rec_a,),
                )
            except psycopg.Error:
                direct_blocked = True
            cur.execute("rollback to savepoint continuity_constraint")
            step(direct_blocked,
                 "database constraint blocks direct continuity-owner disable")

            preserved = cur.execute(
                "select id,recorder_id from cameras where id=%s",
                (cam_a,),
            ).fetchone()
            step(str(preserved[0]) == str(cam_a)
                 and str(preserved[1]) == str(rec_a),
                 "rejected lifecycle change preserves historical camera lineage")

            # Ordinary secondary lifecycle remains supported. Move preferred primary
            # back to A, disable B, then re-enable B without changing continuity owner.
            as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([
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
                        "is_configured": False,
                    },
                ]),
            )
            disabled_b = cur.execute(
                "select is_primary,is_configured,continuity_owner "
                "from recorders where id=%s",
                (rec_b,),
            ).fetchone()
            step(disabled_b == (False, False, False),
                 "ordinary secondary recorder can be disabled")

            as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_id, key, json.dumps([
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
            )
            enabled_b = cur.execute(
                "select is_primary,is_configured,continuity_owner "
                "from recorders where id=%s",
                (rec_b,),
            ).fetchone()
            step(enabled_b == (False, True, False),
                 "ordinary secondary recorder can be re-enabled")

                        contract = as_anon(
                "select wl_multi_recorder_agent_contract(%s,%s)",
                agent_id, key,
            )[0]
            step(contract["version"] == 4
                 and "recorder_continuity" in contract["features"],
                 "multi-recorder Agent contract is v4 with continuity feature")

            continuity_count = cur.execute(
                "select count(*) from recorders where site_id=%s and continuity_owner",
                (site_id,),
            ).fetchone()[0]
            step(continuity_count == 1,
                 "site has exactly one immutable continuity owner")

        finally:
            conn.rollback()

    passed = sum(1 for x in STEPS if x)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

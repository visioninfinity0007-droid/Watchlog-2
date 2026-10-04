#!/usr/bin/env python3
"""Multi-recorder foundation (0146): real Postgres execution and compatibility.

Runs only against disposable/test Postgres. The whole test is rolled back.

Proves:
- a simulated pre-0146 camera row is backfilled in place (same camera UUID),
- legacy single-recorder camera/capability sync still works,
- the upgraded Agent can adopt the legacy default recorder and add another,
- Recorder A ch1 and Recorder B ch1 remain distinct,
- legacy ambiguous site+channel sync fails closed once multiple recorders exist,
- explicit recorder sync is tenant/site scoped and requires current Agent authority,
- authenticated portal reads are tenant-isolated by RLS,
- camera/recorder composite lineage cannot cross tenants/sites,
- new Agent RPC/helper ACLs are narrow.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "supabase" / "migrations" / "0146_multi_recorder_foundation.sql"

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

            def as_authenticated(uid, sql, *params):
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
                boot = as_authenticated(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix, seen="now()"):
                return cur.execute(
                    f"""insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
                          device_vendor,device_model,device_driver,last_seen_at
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          %s,'windows','5.0.27','Hikvision','TEST-NVR','onvif',{seen}
                        ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}"),
                ).fetchone()[0]

            # ------------------------------------------------------------------
            # Simulate a legacy site/camera before 0146, then re-run 0146.
            # CI has already applied 0146 globally, so temporarily relax only
            # recorder_id NOT NULL inside this transaction. Full rollback restores
            # the already-migrated disposable DB after the test.
            # ------------------------------------------------------------------
            ua, ta, sa = bootstrap(
                "multi-recorder-a@watchlog.test", "Multi Recorder A", "Warehouse A"
            )
            key_a = "agent-key-a"
            agent_a = add_agent(ta, sa, key_a, "a")

            # 0146 is already present in the disposable DB. Disable only its
            # compatibility trigger while creating one simulated pre-0146 row;
            # re-running the migration below drops/recreates the trigger.
            cur.execute("alter table public.cameras disable trigger trg_camera_assign_default_recorder")
            cur.execute("alter table public.cameras alter column recorder_id drop not null")
            legacy_camera_id = cur.execute(
                """insert into public.cameras(
                     tenant_id,site_id,recorder_id,channel,physical_channel,name,
                     is_configured,is_canonical
                   ) values (%s,%s,null,'1','1','Legacy Camera 1',true,true)
                   returning id""",
                (ta, sa),
            ).fetchone()[0]

            step(cur.execute(
                "select count(*) from public.recorders where site_id=%s", (sa,)
            ).fetchone()[0] == 0,
                 "simulated pre-0146 site starts with camera but no recorder row")

            cur.execute(MIG.read_text(encoding="utf-8"))

            backfilled = cur.execute(
                "select id,recorder_id from cameras where id=%s", (legacy_camera_id,)
            ).fetchone()
            rec_a = cur.execute(
                "select id,local_key,is_primary,is_configured from recorders where site_id=%s",
                (sa,),
            ).fetchall()

            step(backfilled is not None and backfilled[0] == legacy_camera_id and backfilled[1] is not None,
                 "legacy camera UUID preserved while recorder_id is backfilled")
            step(len(rec_a) == 1 and rec_a[0][1] == "legacy-default" and rec_a[0][2] is True,
                 "exactly one primary default recorder created for legacy site")

            # Legacy single-recorder camera sync must reuse the existing camera ID.
            legacy_map = as_anon(
                "select wl_sync_cameras(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{"channel": "1", "name": "Camera 1", "is_configured": True}]),
            )[0]
            step(str(legacy_map["1"]) == str(legacy_camera_id),
                 "legacy wl_sync_cameras reuses existing camera UUID")

            cap = as_anon(
                "select wl_sync_capabilities(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps({"channels": [{"channel": "1", "snapshot": True}]}),
            )[0]
            mirrored = cur.execute(
                "select capabilities from recorders where id=%s", (backfilled[1],)
            ).fetchone()[0]
            step(cap.get("ok") is True and str(cap.get("recorder_id")) == str(backfilled[1])
                 and mirrored is not None,
                 "legacy capability sync mirrors effective capabilities to the single recorder")

            # Upgrade the legacy singleton identity and add a second recorder.
            mapping = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([
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
            rec_a_id = mapping["rec-a"]
            rec_b_id = mapping["rec-b"]
            step(str(rec_a_id) == str(backfilled[1]) and str(rec_a_id) != str(rec_b_id),
                 "upgraded Agent adopts legacy primary recorder and adds second recorder")

            # Same channel number on two recorders: first keeps historical UUID,
            # second gets a distinct camera UUID.
            map_a = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a_id,
                json.dumps([{"channel": "1", "name": "A Camera 1", "is_configured": True}]),
            )[0]
            map_b = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b_id,
                json.dumps([{"channel": "1", "name": "B Camera 1", "is_configured": True}]),
            )[0]
            step(str(map_a["1"]) == str(legacy_camera_id) and str(map_b["1"]) != str(map_a["1"]),
                 "Recorder A ch1 and Recorder B ch1 are distinct without re-keying A")

            rows = cur.execute(
                """select recorder_id,channel,id from cameras
                    where site_id=%s and channel='1' order by recorder_id""", (sa,)
            ).fetchall()
            step(len(rows) == 2 and len({str(r[0]) for r in rows}) == 2,
                 "database permits overlapping channel numbers across recorders")

            # Event ingestion must carry the recorder namespace too. These two
            # events are intentionally identical except recorder_id; both are valid.
            event_ts = "2026-10-02T12:34:56Z"
            event_type = "multi_recorder_probe"
            ingested = as_anon(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([
                    {
                        "recorder_id": str(rec_a_id),
                        "channel": "1",
                        "event_type": event_type,
                        "device_ts": event_ts,
                        "agent_ts": event_ts,
                        "snapshot_b64": "YWJj",
                    },
                    {
                        "recorder_id": str(rec_b_id),
                        "channel": "1",
                        "event_type": event_type,
                        "device_ts": event_ts,
                        "agent_ts": event_ts,
                        "snapshot_b64": "ZGVm",
                    },
                ]),
            )[0]
            step(ingested["received"] == 2 and ingested["inserted"] == 2
                 and ingested["snapshots"] == 2,
                 "same channel/time/type from two recorders inserts two events + snapshots",
                 json.dumps(ingested, default=str))

            event_rows = cur.execute(
                """select recorder_id,camera_id,dedupe_key
                     from public.events
                    where agent_id=%s and event_type=%s
                    order by recorder_id""",
                (agent_a, event_type),
            ).fetchall()
            event_by_recorder = {str(r[0]): (str(r[1]), r[2]) for r in event_rows}
            step(len(event_rows) == 2
                 and event_by_recorder[str(rec_a_id)][0] == str(map_a["1"])
                 and event_by_recorder[str(rec_b_id)][0] == str(map_b["1"])
                 and len({r[2] for r in event_rows}) == 2,
                 "events resolve camera by recorder+channel and dedupe independently")

            old_primary_key = cur.execute(
                "select wl_dedupe_key(%s,'1',null,%s::timestamptz,%s)",
                (sa, event_ts, event_type),
            ).fetchone()[0]
            step(event_by_recorder[str(rec_a_id)][1] == old_primary_key
                 and event_by_recorder[str(rec_b_id)][1] != old_primary_key,
                 "primary recorder preserves legacy dedupe namespace; secondary is namespaced")

            snapshot_cameras = {
                str(r[0]) for r in cur.execute(
                    """select s.camera_id
                         from public.snapshots s
                         join public.events e on e.id=s.event_id
                        where e.agent_id=%s and e.event_type=%s""",
                    (agent_a, event_type),
                ).fetchall()
            }
            step(snapshot_cameras == {str(map_a["1"]), str(map_b["1"])},
                 "snapshots resolve through recorder+channel, not site+channel")

            raised, msg = as_anon_raises(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{
                    "channel": "1",
                    "event_type": "legacy_ambiguous_event",
                    "device_ts": "2026-10-02T12:35:00Z",
                    "agent_ts": "2026-10-02T12:35:00Z",
                }]),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy event ingest fails closed on multi-recorder site", msg)

            raised, msg = as_anon_raises(
                "select wl_sync_cameras(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{"channel": "2", "name": "Ambiguous", "is_configured": True}]),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy camera sync fails closed on multi-recorder site", msg)

            raised, msg = as_anon_raises(
                "select wl_sync_capabilities(%s,%s,%s::jsonb)",
                agent_a, key_a, json.dumps({"channels": []}),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "legacy capability sync fails closed on multi-recorder site", msg)

            # ------------------------------------------------------------------
            # Tenant/site isolation.
            # ------------------------------------------------------------------
            ub, tb, sb = bootstrap(
                "multi-recorder-b@watchlog.test", "Multi Recorder B", "Factory B"
            )
            key_b = "agent-key-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            map_bsite = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_b, key_b,
                json.dumps([{
                    "local_key": "factory-rec",
                    "display_name": "Factory Recorder",
                    "is_primary": True,
                    "is_configured": True,
                }]),
            )[0]
            foreign_recorder = map_bsite["factory-rec"]

            raised, msg = as_anon_raises(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, foreign_recorder,
                json.dumps([{"channel": "9", "name": "Wrong tenant", "is_configured": True}]),
            )
            step(raised, "Agent A cannot sync cameras into tenant B recorder", msg)

            raised, msg = as_anon_raises(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([{
                    "recorder_id": str(foreign_recorder),
                    "channel": "1",
                    "event_type": "wrong_tenant_event",
                    "device_ts": "2026-10-02T12:36:00Z",
                    "agent_ts": "2026-10-02T12:36:00Z",
                }]),
            )
            step(raised and "not configured for this agent site" in msg.lower(),
                 "Agent A cannot ingest events into tenant B recorder", msg)

            # Authenticated table reads are RLS tenant-scoped.
            a_seen = as_authenticated(
                ua, "select count(*) from public.recorders"
            )[0]
            b_seen = as_authenticated(
                ub, "select count(*) from public.recorders"
            )[0]
            a_actual = cur.execute(
                "select count(*) from recorders where tenant_id=%s", (ta,)
            ).fetchone()[0]
            b_actual = cur.execute(
                "select count(*) from recorders where tenant_id=%s", (tb,)
            ).fetchone()[0]
            step(a_seen == a_actual and b_seen == b_actual,
                 "authenticated recorder reads are tenant-isolated by RLS")

            # Composite lineage FK prevents a camera from naming a recorder from
            # a different tenant/site even when all UUIDs are syntactically valid.
            cur.execute("savepoint lineage_sp")
            lineage_blocked = False
            try:
                cur.execute(
                    """insert into cameras(
                         tenant_id,site_id,recorder_id,channel,name,is_configured,is_canonical
                       ) values (%s,%s,%s,'99','Invalid lineage',true,true)""",
                    (ta, sa, foreign_recorder),
                )
            except psycopg.Error:
                lineage_blocked = True
            cur.execute("rollback to savepoint lineage_sp")
            step(lineage_blocked, "composite FK blocks cross-tenant/site camera-recorder lineage")

            # A stale Agent cannot mutate the explicit multi-recorder registry.
            stale_key = "stale-agent-key"
            stale_agent = add_agent(ta, sa, stale_key, "stale", seen="'2000-01-01'::timestamptz")
            raised, msg = as_anon_raises(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                stale_agent, stale_key,
                json.dumps([{"local_key": "should-not-write", "display_name": "No"}]),
            )
            step(raised and "current site authority" in msg.lower(),
                 "stale Agent cannot mutate explicit recorder registry", msg)

            # ------------------------------------------------------------------
            # Controlled rollback: disabling a bound secondary preserves history
            # but restores an unambiguous active singleton for legacy Agents.
            # ------------------------------------------------------------------
            uc, tc, sc = bootstrap(
                "multi-recorder-c@watchlog.test", "Multi Recorder C", "Rollback C"
            )
            key_c = "agent-key-c"
            agent_c = add_agent(tc, sc, key_c, "c")
            rollback_map = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_c, key_c,
                json.dumps([
                    {
                        "local_key": "primary-c",
                        "display_name": "Primary C",
                        "is_primary": True,
                        "is_configured": True,
                    },
                    {
                        "local_key": "secondary-c",
                        "display_name": "Secondary C",
                        "is_primary": False,
                        "is_configured": True,
                    },
                ]),
            )[0]
            primary_c = rollback_map["primary-c"]
            secondary_c = rollback_map["secondary-c"]
            primary_cam = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_c, key_c, primary_c,
                json.dumps([{"channel":"1","name":"Primary C1","is_configured":True}]),
            )[0]["1"]
            secondary_cam = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_c, key_c, secondary_c,
                json.dumps([{"channel":"1","name":"Secondary C1","is_configured":True}]),
            )[0]["1"]

            raised, msg = as_anon_raises(
                "select wl_sync_cameras(%s,%s,%s::jsonb)",
                agent_c, key_c,
                json.dumps([{"channel":"1","name":"Legacy blocked","is_configured":True}]),
            )
            step(raised and "ambiguous" in msg.lower(),
                 "two configured recorders block legacy singleton sync before rollback", msg)

            as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_c, key_c,
                json.dumps([
                    {
                        "local_key": "primary-c",
                        "display_name": "Primary C",
                        "is_primary": True,
                        "is_configured": True,
                    },
                    {
                        "local_key": "secondary-c",
                        "display_name": "Secondary C",
                        "is_primary": False,
                        "is_configured": False,
                    },
                ]),
            )
            legacy_after_disable = as_anon(
                "select wl_sync_cameras(%s,%s,%s::jsonb)",
                agent_c, key_c,
                json.dumps([{"channel":"1","name":"Legacy restored","is_configured":True}]),
            )[0]
            secondary_truth = cur.execute(
                """select r.is_configured,c.id
                     from recorders r
                     join cameras c on c.recorder_id=r.id
                    where r.id=%s and c.id=%s""",
                (secondary_c, secondary_cam),
            ).fetchone()
            step(
                str(legacy_after_disable["1"]) == str(primary_cam)
                and secondary_truth is not None
                and secondary_truth[0] is False
                and str(secondary_truth[1]) == str(secondary_cam),
                "disabled secondary preserves recorder/camera history and restores legacy singleton path",
            )

            # Exact EXECUTE ACLs.
            def execute_grantees(sig):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC' else a.grantee::regrole::text end,
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
                "public.wl_sync_recorders(uuid,text,jsonb)",
                "public.wl_sync_recorder_cameras(uuid,text,uuid,jsonb)",
            ):
                got = execute_grantees(sig)
                step(got == {"anon"}, f"{sig} EXECUTE ACL is exactly anon (+owner)", str(sorted(got)))

            for sig in (
                "public.wl_legacy_recorder_for_agent(uuid)",
                "public.wl_sync_recorder_cameras_core(uuid,uuid,uuid,jsonb)",
                "public.wl_recorder_event_dedupe_key(uuid,uuid,text,text,timestamptz,text)",
            ):
                got = execute_grantees(sig)
                step(got == set(), f"{sig} EXECUTE ACL is owner only", str(sorted(got)))

            # The new key replaces site/channel uniqueness.
            constraints = {
                r[0]: r[1]
                for r in cur.execute(
                    """select conname,pg_get_constraintdef(oid)
                         from pg_constraint
                        where conrelid='public.cameras'::regclass"""
                ).fetchall()
            }
            step("cameras_site_channel_uniq" not in constraints
                 and "cameras_recorder_channel_uniq" in constraints,
                 "recorder/channel uniqueness replaces site/channel uniqueness")

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

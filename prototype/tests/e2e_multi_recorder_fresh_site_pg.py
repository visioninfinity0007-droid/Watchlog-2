#!/usr/bin/env python3
"""Multi-recorder fresh-site continuity ownership (0146 + 0154): real Postgres.

Runs after the normal full-chain apply (apply_migrations.py) and never
re-executes a migration file. Everything is rolled back, except the rows case C
must commit for a second session to see, which it deletes again.

Sites created AFTER 0154 have no backfilled continuity owner, so the first
recorder row a site ever gets must become its owner. Proves:

case A - no recorders yet, then two recorders on day one:
- wl_sync_recorders succeeds on the first call (no 23514 invariant failure);
- the payload's preferred primary becomes the only continuity owner, whatever
  its position in the payload;
- the owner keeps the legacy site:channel dedupe namespace, the other recorder
  stays recorder-namespaced.

case B - a legacy row is created lazily first, then multi-recorder adoption:
- a 5.0.x legacy sync/ingest (and a trusted camera insert without recorder_id)
  lazily creates legacy-default as the continuity owner, under the per-site
  recorder-registry lock;
- legacy events on that row keep the site:channel dedupe namespace;
- the upgraded Agent adopts that row (camera UUID preserved) even when the
  payload lists the secondary first, and an event re-sent with explicit
  recorder identity dedupes against the pre-upgrade legacy event;
- every site still has exactly one continuity owner.

case C - concurrent first contacts (separate sessions, committed rows that are
deleted afterwards): while one session holds a lazily created legacy-default
uncommitted, a legacy ingest, a recorder sync or a trusted camera insert in a
second session waits on the per-site recorder-registry lock and then reuses or
adopts that row, leaving one recorder and one continuity owner.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
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


class CaseAborted(Exception):
    pass


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

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur, \
            psycopg.connect(**{**dsn, "autocommit": True}) as observer:
        try:
            backend_pid = cur.execute("select pg_backend_pid()").fetchone()[0]

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

            def as_anon(name, sql, *params):
                """Run an Agent RPC as anon. A database error is a FAIL step and
                aborts the current case instead of the whole script."""
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_sp")
                    cur.execute("reset role")
                    step(False, name, str(exc).splitlines()[0])
                    raise CaseAborted(name) from exc
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
                return boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix, version):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
                         device_vendor,device_model,device_driver,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows',%s,'Hikvision','TEST-NVR','onvif',now()
                       ) returning id""",
                    (tenant_id, site_id, key, f"fresh-{suffix}", version),
                ).fetchone()[0]

            def recorders(site_id):
                return {
                    str(r[0]): {
                        "local_key": r[1], "is_primary": r[2],
                        "is_configured": r[3], "continuity_owner": r[4],
                    }
                    for r in cur.execute(
                        """select id,local_key,is_primary,is_configured,continuity_owner
                             from recorders where site_id=%s""",
                        (site_id,),
                    ).fetchall()
                }

            def owners(site_id):
                return [rid for rid, r in recorders(site_id).items() if r["continuity_owner"]]

            def registry_lock_held(site_id, pid=None, granted=True):
                # Two-key advisory locks show key1/key2 as unsigned classid/objid.
                return observer.execute(
                    """select exists(
                         select 1 from pg_locks l
                          where l.pid=%s
                            and l.locktype='advisory'
                            and l.objsubid=2
                            and l.granted=%s
                            and l.classid=((hashtext('wl_site_recorder_registry')::bigint
                                            + 4294967296) %% 4294967296)::oid
                            and l.objid=((hashtext(%s::text)::bigint
                                          + 4294967296) %% 4294967296)::oid)""",
                    (pid or backend_pid, granted, str(site_id)),
                ).fetchone()[0]

            completed = []

            def run_case(label, fn):
                cur.execute("savepoint case_sp")
                try:
                    fn()
                    cur.execute("release savepoint case_sp")
                    completed.append(label)
                except CaseAborted as exc:
                    cur.execute("rollback to savepoint case_sp")
                    print(f"  (case {label} aborted at: {exc})")

            ts = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
            sites_seen = []

            # ----------------------------------------------------------------
            # Case A: no recorders yet, then two recorders on day one. The
            # payload deliberately lists the secondary first.
            # ----------------------------------------------------------------
            def case_a():
                tenant_id, site_id = bootstrap(
                    "fresh-a@watchlog.test", "Fresh Site A", "Day One Warehouse"
                )
                sites_seen.append(site_id)
                key = "fresh-a-agent-key"
                agent = add_agent(tenant_id, site_id, key, "a", "5.1.0")
                step(recorders(site_id) == {},
                     "A: freshly enrolled site starts with no recorder rows")

                contract = as_anon(
                    "A: multi-recorder contract on fresh site",
                    "select wl_multi_recorder_agent_contract(%s,%s)", agent, key,
                )[0]
                step(contract["version"] == 4 and contract["configured_recorders"] == 0,
                     "A: multi-recorder contract answers before any recorder exists")

                payload = [
                    {"local_key": "rec-b", "display_name": "Recorder B",
                     "is_primary": False, "is_configured": True},
                    {"local_key": "rec-a", "display_name": "Recorder A",
                     "is_primary": True, "is_configured": True},
                ]
                mapping = as_anon(
                    "A: first wl_sync_recorders with two recorders succeeds",
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent, key, json.dumps(payload),
                )[0]
                step(True, "A: first wl_sync_recorders with two recorders succeeds")
                step(registry_lock_held(site_id),
                     "A: recorder sync holds the per-site recorder-registry lock")
                rec_a, rec_b = str(mapping["rec-a"]), str(mapping["rec-b"])
                rows = recorders(site_id)
                step(owners(site_id) == [rec_a]
                     and rows[rec_a]["is_primary"] and rows[rec_a]["is_configured"]
                     and not rows[rec_b]["continuity_owner"],
                     "A: payload primary is the only continuity owner, regardless of payload order",
                     json.dumps(rows))

                again = as_anon(
                    "A: repeated recorder sync",
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent, key, json.dumps(payload),
                )[0]
                step(str(again["rec-a"]) == rec_a and str(again["rec-b"]) == rec_b
                     and owners(site_id) == [rec_a],
                     "A: repeated sync is idempotent and never moves continuity")

                cam_a = as_anon(
                    "A: camera sync on Recorder A",
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent, key, rec_a,
                    json.dumps([{"channel": "1", "name": "A1", "is_configured": True}]),
                )[0]["1"]
                cam_b = as_anon(
                    "A: camera sync on Recorder B",
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent, key, rec_b,
                    json.dumps([{"channel": "1", "name": "B1", "is_configured": True}]),
                )[0]["1"]
                step(str(cam_a) != str(cam_b),
                     "A: channel 1 on each recorder is a distinct camera")

                typ = "fresh_day_one_probe"
                ing = as_anon(
                    "A: explicit recorder event ingest",
                    "select wl_ingest_events(%s,%s,%s::jsonb)",
                    agent, key, json.dumps([
                        {"recorder_id": rec_a, "channel": "1", "event_type": typ,
                         "device_ts": ts.isoformat(), "agent_ts": ts.isoformat()},
                        {"recorder_id": rec_b, "channel": "1", "event_type": typ,
                         "device_ts": ts.isoformat(), "agent_ts": ts.isoformat()},
                    ]),
                )[0]
                keys = {
                    str(r[0]): (str(r[1]), r[2])
                    for r in cur.execute(
                        """select recorder_id,camera_id,dedupe_key from events
                            where site_id=%s and event_type=%s""",
                        (site_id, typ),
                    ).fetchall()
                }
                legacy_key = cur.execute(
                    "select wl_dedupe_key(%s,'1',null,%s,%s)", (site_id, ts, typ),
                ).fetchone()[0]
                step(ing["inserted"] == 2
                     and keys[rec_a] == (str(cam_a), legacy_key)
                     and keys[rec_b][0] == str(cam_b)
                     and keys[rec_b][1] != legacy_key,
                     "A: owner keeps the site:channel namespace; secondary is recorder-namespaced",
                     json.dumps(keys))

            run_case("A", case_a)

            # ----------------------------------------------------------------
            # Case B: a 5.0.x legacy Agent lazily creates legacy-default first,
            # then the same site is upgraded to two recorders.
            # ----------------------------------------------------------------
            def case_b():
                tenant_id, site_id = bootstrap(
                    "fresh-b@watchlog.test", "Fresh Site B", "Legacy First Shop"
                )
                sites_seen.append(site_id)
                key = "fresh-b-agent-key"
                agent = add_agent(tenant_id, site_id, key, "b", "5.0.27")

                legacy_map = as_anon(
                    "B: legacy wl_sync_cameras on a site with no recorder",
                    "select wl_sync_cameras(%s,%s,%s::jsonb)",
                    agent, key, json.dumps([
                        {"channel": "1", "name": "Camera 1", "is_configured": True},
                        {"channel": "2", "name": "Camera 2", "is_configured": True},
                    ]),
                )[0]
                step(registry_lock_held(site_id),
                     "B: lazy legacy creation holds the per-site recorder-registry lock")
                rows = recorders(site_id)
                legacy_id = next(iter(rows), None)
                step(len(rows) == 1
                     and rows[legacy_id]["local_key"] == "legacy-default"
                     and rows[legacy_id]["is_primary"]
                     and rows[legacy_id]["continuity_owner"],
                     "B: lazily created legacy-default is primary and continuity owner",
                     json.dumps(rows))

                typ = "fresh_legacy_probe"
                legacy_ingest = as_anon(
                    "B: legacy event ingest without recorder_id",
                    "select wl_ingest_events(%s,%s,%s::jsonb)",
                    agent, key, json.dumps([
                        {"channel": "1", "event_type": typ,
                         "device_ts": ts.isoformat(), "agent_ts": ts.isoformat()},
                    ]),
                )[0]
                legacy_event = cur.execute(
                    """select recorder_id,camera_id,dedupe_key from events
                        where site_id=%s and event_type=%s""",
                    (site_id, typ),
                ).fetchone()
                legacy_key = cur.execute(
                    "select wl_dedupe_key(%s,'1',null,%s,%s)", (site_id, ts, typ),
                ).fetchone()[0]
                step(legacy_ingest["inserted"] == 1
                     and str(legacy_event[0]) == legacy_id
                     and str(legacy_event[1]) == str(legacy_map["1"])
                     and legacy_event[2] == legacy_key,
                     "B: legacy events on the lazy row keep the site:channel dedupe namespace")

                # 5.1 upgrade: bind the continuity recorder alone first (what the
                # orchestrator does), then the full desired registry.
                agent_upgraded = cur.execute(
                    "update agents set agent_version='5.1.0' where id=%s returning id", (agent,),
                ).fetchone()[0]
                first = as_anon(
                    "B: upgraded Agent binds its continuity recorder",
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_upgraded, key, json.dumps([
                        {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True},
                    ]),
                )[0]
                full = as_anon(
                    "B: upgraded Agent adds a second recorder",
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_upgraded, key, json.dumps([
                        {"local_key": "rec-b", "display_name": "Recorder B",
                         "is_primary": False, "is_configured": True},
                        {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True},
                    ]),
                )[0]
                rec_a, rec_b = str(full["rec-a"]), str(full["rec-b"])
                step(str(first["rec-a"]) == legacy_id and rec_a == legacy_id
                     and owners(site_id) == [legacy_id]
                     and not recorders(site_id)[rec_b]["continuity_owner"],
                     "B: upgraded Agent adopts the lazy legacy row; new secondary is not an owner")

                cam_a = as_anon(
                    "B: recorder camera sync after adoption",
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_upgraded, key, rec_a,
                    json.dumps([{"channel": "1", "name": "Camera 1", "is_configured": True}]),
                )[0]["1"]
                step(str(cam_a) == str(legacy_map["1"]),
                     "B: adoption preserves the lazily created camera UUID")

                resend = as_anon(
                    "B: pre-upgrade event re-sent with explicit recorder identity",
                    "select wl_ingest_events(%s,%s,%s::jsonb)",
                    agent_upgraded, key, json.dumps([
                        {"recorder_id": rec_a, "channel": "1", "event_type": typ,
                         "device_ts": ts.isoformat(), "agent_ts": ts.isoformat()},
                    ]),
                )[0]
                step(resend["inserted"] == 0,
                     "B: owner's explicit event dedupes against the pre-upgrade legacy event")

            run_case("B", case_b)

            # Same as B, but a trusted camera insert without recorder_id creates
            # the lazy row and the upgraded payload lists the secondary first.
            def case_b_trigger():
                tenant_id, site_id = bootstrap(
                    "fresh-c@watchlog.test", "Fresh Site C", "Trigger First Office"
                )
                sites_seen.append(site_id)
                key = "fresh-c-agent-key"
                agent = add_agent(tenant_id, site_id, key, "c", "5.1.0")
                legacy_camera = cur.execute(
                    """insert into cameras(tenant_id,site_id,channel,physical_channel,name,
                                           is_configured,is_canonical)
                       values (%s,%s,'1','1','Camera 1',true,true) returning id,recorder_id""",
                    (tenant_id, site_id),
                ).fetchone()
                rows = recorders(site_id)
                step(list(rows) == [str(legacy_camera[1])]
                     and rows[str(legacy_camera[1])]["continuity_owner"],
                     "B': camera-insert trigger creates legacy-default as continuity owner",
                     json.dumps(rows))

                mapping = as_anon(
                    "B': one-call upgrade listing the secondary first",
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent, key, json.dumps([
                        {"local_key": "rec-b", "display_name": "Recorder B",
                         "is_primary": False, "is_configured": True},
                        {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True},
                    ]),
                )[0]
                step(str(mapping["rec-a"]) == str(legacy_camera[1])
                     and owners(site_id) == [str(legacy_camera[1])]
                     and str(cur.execute("select recorder_id from cameras where id=%s",
                                         (legacy_camera[0],)).fetchone()[0])
                     == str(mapping["rec-a"]),
                     "B': the payload primary, not the first listed row, adopts the legacy row")

            run_case("B'", case_b_trigger)

            bad = cur.execute(
                """select r.site_id, count(*) filter (where r.continuity_owner)
                     from recorders r
                    group by r.site_id
                   having count(*) filter (where r.continuity_owner) <> 1"""
            ).fetchall()
            step(completed == ["A", "B", "B'"] and len(sites_seen) == 3 and bad == [],
                 "every case completed and every site with recorders has exactly one "
                 "continuity owner", str((completed, bad)))

            # ----------------------------------------------------------------
            # Case C: concurrent first contacts on a fresh site. A second
            # session cannot see this script's uncommitted cases, so each race
            # commits its own tenant/site/Agent and deletes them afterwards.
            # Session 1 lazily creates legacy-default and keeps its transaction
            # open; session 2 must wait on the recorder-registry lock taken
            # BEFORE it counts recorders, then reuse or adopt that row. Without
            # that lock session 2 counts no recorder, waits only inside its own
            # recorders insert, then fails on a recorders unique index (local
            # key, or one configured primary per site).
            # ----------------------------------------------------------------
            race_rows = []

            def race(label, second, check):
                with psycopg.connect(**dsn) as s1, psycopg.connect(**dsn) as s2:
                    c1, c2 = s1.cursor(), s2.cursor()
                    tenant_id = c1.execute(
                        "insert into tenants(name) values (%s) returning id",
                        (f"Fresh Race {label}",),
                    ).fetchone()[0]
                    site_id = c1.execute(
                        "insert into sites(tenant_id,name) values (%s,%s) returning id",
                        (tenant_id, f"Race {label} Site"),
                    ).fetchone()[0]
                    key = f"fresh-race-{label}-{uuid.uuid4().hex}"
                    agent = c1.execute(
                        """insert into agents(
                             tenant_id,site_id,agent_key_hash,hostname,platform,
                             agent_version,device_driver,last_seen_at
                           ) values (
                             %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                             %s,'windows','5.1.0','onvif',now()
                           ) returning id""",
                        (tenant_id, site_id, key, f"fresh-race-{label}"),
                    ).fetchone()[0]
                    s1.commit()
                    race_rows.append((tenant_id, site_id))

                    c1.execute("set local role anon")
                    cams = c1.execute(
                        "select wl_sync_cameras(%s,%s,%s::jsonb)",
                        (agent, key, json.dumps([
                            {"channel": "1", "name": "Camera 1", "is_configured": True},
                        ])),
                    ).fetchone()[0]
                    c1.execute("reset role")
                    legacy_id = str(c1.execute(
                        "select id from recorders where site_id=%s", (site_id,),
                    ).fetchone()[0])

                    pid2 = c2.execute("select pg_backend_pid()").fetchone()[0]
                    c2.execute("set local lock_timeout = '20s'")
                    outcome = {}

                    def worker():
                        try:
                            outcome["value"] = second(c2, agent, key, tenant_id, site_id)
                        except psycopg.Error as exc:
                            outcome["error"] = str(exc).splitlines()[0]

                    t = threading.Thread(target=worker, daemon=True)
                    t.start()
                    waited = False
                    deadline = time.monotonic() + 10
                    while t.is_alive() and time.monotonic() < deadline:
                        if registry_lock_held(site_id, pid2, granted=False):
                            waited = True
                            break
                        time.sleep(0.05)
                    s1.commit()
                    t.join(30)
                    if t.is_alive():
                        s2.cancel()
                        t.join(10)
                    step(waited,
                         f"C {label}: second session waits on the recorder-registry lock")
                    if "value" not in outcome:
                        s2.rollback()
                        step(False, f"C {label}: second session completes after the first commits",
                             outcome.get("error", "did not finish"))
                        return
                    s2.commit()
                    rows = {
                        str(r[0]): (r[1], r[2])
                        for r in observer.execute(
                            """select id,local_key,continuity_owner
                                 from recorders where site_id=%s""",
                            (site_id,),
                        ).fetchall()
                    }
                    check(outcome["value"], legacy_id, cams, site_id, rows)

            def ingest_second(c, agent, key, tenant_id, site_id):
                c.execute("set local role anon")
                res = c.execute(
                    "select wl_ingest_events(%s,%s,%s::jsonb)",
                    (agent, key, json.dumps([
                        {"channel": "1", "event_type": "fresh_race_probe",
                         "device_ts": ts.isoformat(), "agent_ts": ts.isoformat()},
                    ])),
                ).fetchone()[0]
                c.execute("reset role")
                return res

            def ingest_check(res, legacy_id, cams, site_id, rows):
                ev = observer.execute(
                    """select recorder_id,camera_id from events
                        where site_id=%s and event_type='fresh_race_probe'""",
                    (site_id,),
                ).fetchall()
                step(res["inserted"] == 1
                     and [(str(e[0]), str(e[1])) for e in ev] == [(legacy_id, str(cams["1"]))]
                     and rows == {legacy_id: ("legacy-default", True)},
                     "C ingest: legacy ingest reuses the waiting legacy-default and its camera",
                     json.dumps(rows))

            def sync_second(c, agent, key, tenant_id, site_id):
                c.execute("set local role anon")
                res = c.execute(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    (agent, key, json.dumps([
                        {"local_key": "rec-a", "display_name": "Recorder A",
                         "is_primary": True, "is_configured": True},
                    ])),
                ).fetchone()[0]
                c.execute("reset role")
                return res

            def sync_check(res, legacy_id, cams, site_id, rows):
                step(str(res["rec-a"]) == legacy_id
                     and rows == {legacy_id: ("rec-a", True)},
                     "C sync: recorder sync adopts the waiting legacy-default as the only owner",
                     json.dumps(rows))

            def camera_second(c, agent, key, tenant_id, site_id):
                return c.execute(
                    """insert into cameras(tenant_id,site_id,channel,physical_channel,name,
                                           is_configured,is_canonical)
                       values (%s,%s,'9','9','Camera 9',true,true) returning recorder_id""",
                    (tenant_id, site_id),
                ).fetchone()[0]

            def camera_check(res, legacy_id, cams, site_id, rows):
                step(str(res) == legacy_id
                     and rows == {legacy_id: ("legacy-default", True)},
                     "C camera: trusted camera insert reuses the waiting legacy-default",
                     json.dumps(rows))

            try:
                race("ingest", ingest_second, ingest_check)
                race("sync", sync_second, sync_check)
                race("camera", camera_second, camera_check)
            finally:
                # Recorder-scoped rows (events, coverage, cameras, ...) reference
                # recorders ON DELETE RESTRICT, so they go before the tenant
                # cascade; cameras last, as events reference them too.
                scoped = [r[0] for r in observer.execute(
                    """select distinct conrelid::regclass::text from pg_constraint
                        where contype='f'
                          and confrelid='public.recorders'::regclass
                          and conrelid<>'public.cameras'::regclass"""
                ).fetchall()] + ["public.cameras", "public.recorders"]
                race_sites = [site_id for _, site_id in race_rows]
                with observer.transaction():
                    for table in scoped:
                        observer.execute(
                            f"delete from {table} where site_id = any(%s)", (race_sites,)
                        )
                    observer.execute(
                        "delete from tenants where id = any(%s)",
                        ([tenant_id for tenant_id, _ in race_rows],),
                    )
            left = observer.execute(
                "select count(*) from sites where id = any(%s)",
                ([site_id for _, site_id in race_rows],),
            ).fetchone()[0]
            step(len(race_rows) == 3 and left == 0,
                 "C: committed race rows are removed", str((len(race_rows), left)))

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

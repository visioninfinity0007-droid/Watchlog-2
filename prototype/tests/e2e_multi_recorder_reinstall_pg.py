#!/usr/bin/env python3
"""Same-site reinstall re-adopts the site's recorders (0154): real Postgres.

Uninstall removes the Agent identity and, since the installer fix for MNVR-017,
the recorder registry too. A reinstall enrolls a NEW Agent and Setup stages
the recorder under a FRESH local id. wl_sync_recorders used to look recorders
up by local id only, so the new Agent's first sync forked the site: a third
recorder with no history, the continuity recorder's cameras orphaned, and the
old secondary silently offline. Runs after the normal full-chain apply and
rolls back. Proves:
- the reinstalled Agent's fresh primary re-adopts the continuity recorder: same
  recorder UUID, continuity owner kept, no new recorder, and its camera sync
  reuses every existing camera UUID;
- the old secondary stays configured and is flagged as needing re-add
  (visible to the Agent through the contract read), never silently dropped;
  the same holds when Setup reused the continuity recorder's old local id;
- re-adding that recorder with the same serial re-adopts its row and cameras;
- the existing recorder push token is reused after the reinstall;
- a fresh registry whose primary is provably another recorder of the site, or
  whose primary's known serial differs from the continuity recorder's own known
  serial (a replaced NVR), never takes the continuity recorder's identity, yet
  still binds: the primary is that other recorder or a new one, and the
  continuity recorder keeps its UUID, serial and cameras and is listed as
  needing re-add; a superseded earlier installation fails closed (42501) and
  changes nothing; a re-run is idempotent;
- a recorder's known serial is never overwritten by a different one: a later
  sync proving the presumed re-adoption wrong is refused (42501), while
  disabling such a recorder still works;
- the current Agent re-adds or retires a flagged recorder (the continuity
  recorder only re-adds) by its cloud id: same UUID and cameras, flag cleared,
  no duplicate; ids of other sites, unflagged rows and proven-different
  recorders are refused;
- the same Agent losing its registry re-adopts deterministically too;
- a single-recorder site reinstalled the same way keeps one recorder and its
  legacy 5.0.x camera sync keeps working.
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

            def as_anon(sql, *params):
                row, state, msg = as_anon_try(sql, *params)
                if state is not None:
                    raise RuntimeError(f"{sql}: {state} {msg}")
                return row

            def bootstrap(email, company, site_name):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (email,),
                ).fetchone()[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                return boot["tenant_id"], boot["site_id"]

            def add_agent(tenant_id, site_id, key, host, enrolled_ago):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at,enrolled_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.1.0',now(),now()-%s::interval
                       ) returning id""",
                    (tenant_id, site_id, key, host, enrolled_ago),
                ).fetchone()[0]

            def seen(agent_id, ago):
                cur.execute("update agents set last_seen_at=now()-%s::interval where id=%s",
                            (ago, agent_id))

            def sync(agent_id, key, rows):
                return as_anon_try("select wl_sync_recorders(%s,%s,%s::jsonb)",
                                   agent_id, key, json.dumps(rows))

            def cameras(agent_id, key, recorder_id, channels):
                return as_anon("select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                               agent_id, key, recorder_id,
                               json.dumps([{"channel": ch, "name": f"Camera {ch}",
                                            "is_configured": True} for ch in channels]))[0]

            def recorders(site_id):
                rows = cur.execute(
                    """select id::text,local_key,is_primary,is_configured,continuity_owner,
                              to_jsonb(r)->>'readd_required_at'
                         from recorders r where site_id=%s order by created_at,id""",
                    (site_id,),
                ).fetchall()
                return {r[0]: {"key": r[1], "primary": r[2], "configured": r[3],
                               "continuity": r[4], "readd": r[5]} for r in rows}

            def site_cameras(site_id):
                return {str(r[0]): (str(r[1]), r[2]) for r in cur.execute(
                    "select id,recorder_id,channel from cameras where site_id=%s",
                    (site_id,)).fetchall()}

            def row(local_key, primary, serial=None, configured=True, name=None, readd=None):
                out = {"local_key": local_key, "display_name": name or local_key,
                       "driver": "hikvision", "is_primary": primary,
                       "is_configured": configured}
                if serial:
                    out["identity_fingerprint"] = f"serial:{serial}"
                if readd:
                    out["readd_recorder_id"] = readd
                return out

            def needing_readd(agent_id, key):
                got, state, msg = as_anon_try(
                    "select wl_multi_recorder_agent_contract(%s,%s)", agent_id, key)
                if state is not None:
                    return None
                return {str(r.get("id")) for r in (got[0].get("recorders_needing_readd") or [])}

            # ----------------------------------------------------------------
            # The earlier installation: Agent X binds A (continuity) and B.
            # ----------------------------------------------------------------
            tenant, site = bootstrap("reinstall-db@watchlog.test", "Reinstall DB",
                                     "Reinstall Warehouse")
            key_x = "reinstall-old-agent"
            agent_x = add_agent(tenant, site, key_x, "old-install", "30 days")
            first, state, msg = sync(agent_x, key_x, [
                row("rec-a-old", True, "AAA111", name="Recorder A"),
                row("rec-b-old", False, "BBB222", name="Recorder B"),
            ])
            mapping = (first or [{}])[0]
            rec_a, rec_b = str(mapping.get("rec-a-old")), str(mapping.get("rec-b-old"))
            cams_a = cameras(agent_x, key_x, rec_a, ["1", "2"])
            cams_b = cameras(agent_x, key_x, rec_b, ["1"])
            ts = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc).isoformat()
            as_anon("select wl_ingest_events(%s,%s,%s::jsonb)", agent_x, key_x, json.dumps([
                {"recorder_id": rec_a, "channel": "1", "event_type": "motion",
                 "device_ts": ts, "agent_ts": ts},
                {"recorder_id": rec_b, "channel": "1", "event_type": "motion",
                 "device_ts": ts, "agent_ts": ts},
            ]))
            tok_issued, tok_state, _ = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s,%s)", agent_x, key_x, rec_a)
            tok_a = (tok_issued or [{}])[0].get("token") if tok_issued else None
            before_cams = site_cameras(site)
            before_events = cur.execute(
                "select count(*) from events where site_id=%s", (site,)).fetchone()[0]
            step(state is None and len(recorders(site)) == 2
                 and recorders(site)[rec_a]["continuity"] and len(before_cams) == 3,
                 "the earlier installation bound A (continuity) and B with cameras", msg)

            # ----------------------------------------------------------------
            # Uninstall + reinstall: a new Agent Y; X is gone.
            # ----------------------------------------------------------------
            seen(agent_x, "1 hour")
            key_y = "reinstall-new-agent"
            agent_y = add_agent(tenant, site, key_y, "new-install", "0 seconds")
            again, state, msg = sync(agent_y, key_y, [
                row("rec-a-new", True, "AAA111", name="Recorder A"),
            ])
            mapped = (again or [{}])[0]
            recs = recorders(site)
            step(state is None and str(mapped.get("rec-a-new")) == rec_a,
                 "the reinstalled Agent's fresh primary re-adopts the continuity recorder",
                 msg or json.dumps(mapped, default=str))
            step(len(recs) == 2 and recs.get(rec_a, {}).get("continuity") is True
                 and recs[rec_a]["primary"] is True and recs[rec_a]["key"] == "rec-a-new",
                 "no new recorder: A keeps continuity, becomes primary under the new local id",
                 json.dumps(recs))
            step(recs.get(rec_b, {}).get("configured") is True
                 and recs[rec_b]["primary"] is False and recs[rec_b]["readd"] is not None,
                 "the old secondary B stays configured and is flagged as needing re-add",
                 json.dumps(recs.get(rec_b)))

            resynced = cameras(agent_y, key_y, rec_a, ["1", "2"])
            step({k: str(v) for k, v in resynced.items()} == {k: str(v) for k, v in cams_a.items()}
                 and site_cameras(site) == before_cams,
                 "A's camera sync after the reinstall reuses every camera UUID, none added",
                 json.dumps(resynced, default=str))

            contract, state, msg = as_anon_try(
                "select wl_multi_recorder_agent_contract(%s,%s)", agent_y, key_y)
            needing = (contract or [{}])[0].get("recorders_needing_readd") if contract else None
            step(state is None and isinstance(needing, list) and len(needing) == 1
                 and str(needing[0].get("id")) == rec_b
                 and set(needing[0]) <= {"id", "display_name", "since"},
                 "the Agent contract lists B as needing re-add (id and name only)",
                 msg or json.dumps(needing, default=str))

            reissued, state, msg = as_anon_try(
                "select wl_agent_issue_push_token(%s,%s,%s)", agent_y, key_y, rec_a)
            step(tok_state is None and state is None and tok_a
                 and (reissued or [{}])[0].get("token") == tok_a,
                 "the reinstalled Agent gets A's existing push token back (recorder config valid)",
                 msg)

            rerun, state, msg = sync(agent_y, key_y, [
                row("rec-a-new", True, "AAA111", name="Recorder A"),
            ])
            step(state is None and str((rerun or [{}])[0].get("rec-a-new")) == rec_a
                 and len(recorders(site)) == 2 and recorders(site)[rec_b]["readd"] is not None,
                 "a re-run of the reinstalled sync is idempotent", msg)

            # A fresh registry whose primary is provably B never takes A's
            # identity: B is re-adopted as the primary and A, untouched, is
            # flagged as needing re-add (the continuity recorder is gone from
            # this registry, so the site must not stay unbindable).
            def fingerprints(site_id):
                return dict(cur.execute(
                    "select id::text,identity_fingerprint from recorders where site_id=%s",
                    (site_id,)).fetchall())
            fp_before = fingerprints(site)
            cur.execute("savepoint proven_b_sp")
            got, state, msg = sync(agent_y, key_y, [
                row("rec-n", True, "BBB222", name="Recorder N"),
            ])
            recs = recorders(site)
            step(state is None and str((got or [{}])[0].get("rec-n")) == rec_b
                 and len(recs) == 2 and recs[rec_b]["primary"] is True
                 and recs[rec_b]["readd"] is None
                 and recs[rec_a]["key"] == "rec-a-new" and recs[rec_a]["continuity"] is True
                 and recs[rec_a]["configured"] is True and recs[rec_a]["primary"] is False
                 and recs[rec_a]["readd"] is not None
                 and fingerprints(site) == fp_before and site_cameras(site) == before_cams,
                 "a fresh primary proven to be B re-adopts B as primary; A is kept untouched "
                 "and flagged for re-add, no recorder created",
                 msg or json.dumps(recs))
            cur.execute("rollback to savepoint proven_b_sp")
            # A fresh primary beside a new row carrying A's serial: that row is
            # A (re-adopted by proof, continuity kept); the primary is new.
            cur.execute("savepoint beside_a_sp")
            got, state, msg = sync(agent_y, key_y, [
                row("rec-n", True, "NNN999", name="Recorder N"),
                row("rec-a-x", False, "AAA111", name="Recorder A"),
            ])
            mapped = (got or [{}])[0]
            recs = recorders(site)
            rec_n = str(mapped.get("rec-n"))
            step(state is None and str(mapped.get("rec-a-x")) == rec_a
                 and rec_n not in (rec_a, rec_b) and len(recs) == 3
                 and recs[rec_a]["key"] == "rec-a-x" and recs[rec_a]["continuity"] is True
                 and recs[rec_a]["primary"] is False and recs[rec_a]["readd"] is None
                 and recs[rec_n]["primary"] is True and recs[rec_n]["continuity"] is False
                 and recs[rec_b]["readd"] is not None
                 and fingerprints(site).get(rec_a) == "serial:AAA111"
                 and site_cameras(site) == before_cams,
                 "a fresh primary beside a row proven to be A: A re-adopted by its serial as a "
                 "secondary, the primary is a new recorder",
                 msg or json.dumps(recs))
            cur.execute("rollback to savepoint beside_a_sp")

            # ----------------------------------------------------------------
            # Re-adding B under a fresh local id with the same serial.
            # ----------------------------------------------------------------
            readd, state, msg = sync(agent_y, key_y, [
                row("rec-a-new", True, "AAA111", name="Recorder A"),
                row("rec-b-new", False, "bbb222", name="Recorder B"),
            ])
            mapped = (readd or [{}])[0]
            recs = recorders(site)
            step(state is None and str(mapped.get("rec-b-new")) == rec_b
                 and len(recs) == 2 and recs[rec_b]["key"] == "rec-b-new"
                 and recs[rec_b]["readd"] is None and recs[rec_b]["configured"] is True,
                 "re-adding B with the same serial re-adopts B's row and clears the flag",
                 msg or json.dumps(recs))
            resynced_b = cameras(agent_y, key_y, rec_b, ["1"])
            step({k: str(v) for k, v in resynced_b.items()}
                 == {k: str(v) for k, v in cams_b.items()}
                 and site_cameras(site) == before_cams,
                 "B's camera sync after re-adding reuses its camera UUID", json.dumps(resynced_b,
                                                                                     default=str))

            # ----------------------------------------------------------------
            # The superseded earlier installation regains authority (failover)
            # and replays its old registry: fail closed.
            # ----------------------------------------------------------------
            seen(agent_y, "10 minutes")
            seen(agent_x, "0 seconds")
            snapshot = recorders(site)
            _, state, msg = sync(agent_x, key_x, [
                row("rec-a-old", True, "AAA111", name="Recorder A"),
                row("rec-b-old", False, "BBB222", name="Recorder B"),
            ])
            step(state == "42501" and recorders(site) == snapshot,
                 "a superseded installation's old registry fails closed and changes nothing", msg)
            seen(agent_x, "1 hour")
            seen(agent_y, "0 seconds")

            # ----------------------------------------------------------------
            # The same Agent loses its registry (no serial known this time).
            # ----------------------------------------------------------------
            lost, state, msg = sync(agent_y, key_y, [row("rec-a-again", True, name="Primary")])
            recs = recorders(site)
            step(state is None and str((lost or [{}])[0].get("rec-a-again")) == rec_a
                 and len(recs) == 2 and recs[rec_a]["continuity"] is True
                 and recs[rec_b]["readd"] is not None,
                 "the same Agent re-adopts A after losing its registry; B needs re-add again",
                 msg or json.dumps(recs))

            # Without identity proof a new row is a new recorder; B stays visible.
            cur.execute("savepoint unknown_sp")
            fresh, state, msg = sync(agent_y, key_y, [
                row("rec-a-again", True, name="Primary"),
                row("rec-c", False, name="Recorder C"),
            ])
            recs = recorders(site)
            step(state is None and len(recs) == 3 and recs[rec_b]["readd"] is not None
                 and str((fresh or [{}])[0].get("rec-c")) not in (rec_a, rec_b),
                 "a recorder added without identity proof is new; B stays flagged, not merged",
                 msg or json.dumps(recs))
            cur.execute("rollback to savepoint unknown_sp")

            cur.execute("update agents set last_seen_at=now() where id=%s", (agent_y,))
            inv = cur.execute(
                """select count(*) filter (where continuity_owner),
                          count(*) filter (where is_primary and is_configured)
                     from recorders where site_id=%s""", (site,)).fetchone()
            after_events = cur.execute(
                "select count(*) from events where site_id=%s", (site,)).fetchone()[0]
            step(inv == (1, 1) and site_cameras(site) == before_cams
                 and after_events == before_events,
                 "one continuity owner, one configured primary; cameras and events preserved",
                 str((inv, after_events, before_events)))

            # ----------------------------------------------------------------
            # Reinstall where Setup could reuse the continuity recorder's old
            # local id (stale registry still readable): A is named, B is not.
            # ----------------------------------------------------------------
            tenant3, site3 = bootstrap("reinstall-reuse@watchlog.test", "Reinstall Reuse",
                                       "Reuse Depot")
            key_p = "reinstall-reuse-old-agent"
            agent_p = add_agent(tenant3, site3, key_p, "reuse-old-install", "30 days")
            old3, _, _ = sync(agent_p, key_p, [
                row("rec-a3", True, "AAA333", name="Recorder A"),
                row("rec-b3", False, "BBB333", name="Recorder B"),
            ])
            rec_a3, rec_b3 = (str((old3 or [{}])[0].get(k)) for k in ("rec-a3", "rec-b3"))
            normal, state, msg = sync(agent_p, key_p, [
                row("rec-a3", True, "AAA333", name="Recorder A"),
            ])
            step(state is None and recorders(site3)[rec_b3]["readd"] is None,
                 "the same installation omitting B is additive: B is not flagged", msg)
            seen(agent_p, "1 hour")
            key_q = "reinstall-reuse-new-agent"
            agent_q = add_agent(tenant3, site3, key_q, "reuse-new-install", "0 seconds")
            # Naming only B's reused local id: A (bound by the earlier
            # installation) is not silently left offline either.
            cur.execute("savepoint reuse_b_sp")
            got, state, msg = sync(agent_q, key_q, [
                row("rec-b3", True, "BBB333", name="Recorder B"),
            ])
            recs3 = recorders(site3)
            step(state is None and str((got or [{}])[0].get("rec-b3")) == rec_b3
                 and len(recs3) == 2 and recs3[rec_a3]["continuity"] is True
                 and recs3[rec_a3]["configured"] is True and recs3[rec_a3]["readd"] is not None
                 and needing_readd(agent_q, key_q) == {rec_a3},
                 "a reinstall naming only B's old local id flags the earlier installation's "
                 "continuity recorder for re-add", msg or json.dumps(recs3))
            cur.execute("rollback to savepoint reuse_b_sp")
            reused, state, msg = sync(agent_q, key_q, [
                row("rec-a3", True, "AAA333", name="Recorder A"),
            ])
            recs3 = recorders(site3)
            step(state is None and str((reused or [{}])[0].get("rec-a3")) == rec_a3
                 and len(recs3) == 2 and recs3[rec_b3]["configured"] is True
                 and recs3[rec_b3]["readd"] is not None,
                 "a reinstall reusing A's local id flags the earlier installation's B for re-add",
                 msg or json.dumps(recs3))

            # ----------------------------------------------------------------
            # Single-recorder site: 5.0.x lazily created, upgraded, reinstalled.
            # ----------------------------------------------------------------
            tenant2, site2 = bootstrap("reinstall-single@watchlog.test", "Reinstall Single",
                                       "Single Kiosk")
            key_l = "reinstall-legacy-agent"
            agent_l = add_agent(tenant2, site2, key_l, "legacy-install", "60 days")
            legacy = as_anon("select wl_sync_cameras(%s,%s,%s::jsonb)", agent_l, key_l,
                             json.dumps([{"channel": "1", "name": "Camera 1",
                                          "is_configured": True}]))[0]
            up, state, _ = sync(agent_l, key_l, [row("rec-1", True, name="Recorder")])
            only = str((up or [{}])[0].get("rec-1"))
            seen(agent_l, "1 hour")
            key_n = "reinstall-single-new-agent"
            agent_n = add_agent(tenant2, site2, key_n, "single-new-install", "0 seconds")
            back, state, msg = sync(agent_n, key_n, [row("rec-2", True, name="Recorder")])
            recs2 = recorders(site2)
            step(state is None and str((back or [{}])[0].get("rec-2")) == only
                 and len(recs2) == 1 and recs2[only]["continuity"] is True,
                 "a single-recorder site reinstalled the same way keeps its one recorder",
                 msg or json.dumps(recs2))
            again_legacy, state, msg = as_anon_try(
                "select wl_sync_cameras(%s,%s,%s::jsonb)", agent_n, key_n,
                json.dumps([{"channel": "1", "name": "Camera 1", "is_configured": True}]))
            step(state is None
                 and str((again_legacy or [{}])[0].get("1")) == str(legacy["1"]),
                 "its legacy camera sync still returns the same camera UUID", msg)

            # ----------------------------------------------------------------
            # Reinstall after the continuity NVR was replaced (Setup recorded a
            # known, different serial), or after it is gone and only a known
            # secondary is staged. The new installation must be able to bind:
            # A's UUID, serial and cameras stay unchanged, A is never grafted
            # onto another recorder, and A is listed as needing re-add.
            # ----------------------------------------------------------------
            tenant4, site4 = bootstrap("reinstall-replaced@watchlog.test",
                                       "Reinstall Replaced", "Replaced Yard")
            key_r = "reinstall-replaced-old-agent"
            agent_r = add_agent(tenant4, site4, key_r, "replaced-old-install", "30 days")
            old4, _, _ = sync(agent_r, key_r, [
                row("a-old", True, "AAA111", name="Recorder A"),
                row("b-old", False, "BBB222", name="Recorder B"),
            ])
            rec_a4, rec_b4 = (str((old4 or [{}])[0].get(k)) for k in ("a-old", "b-old"))
            cams_a4 = cameras(agent_r, key_r, rec_a4, ["1", "2"])
            cameras(agent_r, key_r, rec_b4, ["1"])
            cams4, fp4 = site_cameras(site4), fingerprints(site4)
            seen(agent_r, "1 hour")
            key_s = "reinstall-replaced-new-agent"
            agent_s = add_agent(tenant4, site4, key_s, "replaced-new-install", "0 seconds")

            cur.execute("savepoint only_b_sp")
            got, state, msg = sync(agent_s, key_s, [row("b-new", True, "BBB222", name="Recorder B")])
            recs4 = recorders(site4)
            step(state is None and str((got or [{}])[0].get("b-new")) == rec_b4
                 and len(recs4) == 2 and recs4[rec_b4]["primary"] is True
                 and recs4[rec_a4]["continuity"] is True and recs4[rec_a4]["key"] == "a-old"
                 and recs4[rec_a4]["readd"] is not None
                 and fingerprints(site4) == fp4 and site_cameras(site4) == cams4
                 and needing_readd(agent_s, key_s) == {rec_a4},
                 "continuity recorder gone, only a known secondary staged: it binds as the "
                 "primary; A untouched and listed as needing re-add", msg or json.dumps(recs4))
            cur.execute("rollback to savepoint only_b_sp")

            got, state, msg = sync(agent_s, key_s, [row("c-new", True, "CCC333", name="Recorder C")])
            rec_c4 = str((got or [{}])[0].get("c-new"))
            recs4 = recorders(site4)
            step(state is None and rec_c4 not in ("None", rec_a4, rec_b4) and len(recs4) == 3
                 and recs4[rec_c4]["primary"] is True and recs4[rec_c4]["continuity"] is False
                 and recs4[rec_a4]["continuity"] is True and recs4[rec_a4]["configured"] is True
                 and recs4[rec_a4]["key"] == "a-old" and recs4[rec_a4]["readd"] is not None
                 and recs4[rec_b4]["readd"] is not None
                 and fingerprints(site4) == {**fp4, rec_c4: "serial:CCC333"}
                 and site_cameras(site4) == cams4,
                 "replaced continuity NVR with a known serial: the new installation binds it as "
                 "a new recorder; A's UUID, serial and cameras unchanged",
                 msg or json.dumps(recs4))
            cams_c4 = cameras(agent_s, key_s, rec_c4, ["1", "2"]) if state is None else {}
            after4 = site_cameras(site4)
            step({str(v) for v in cams_c4.values()}.isdisjoint({str(v) for v in cams_a4.values()})
                 and all(after4.get(k) == v for k, v in cams4.items())
                 and len(after4) == len(cams4) + 2,
                 "the replacement's cameras are its own; A's camera UUIDs are untouched",
                 json.dumps(cams_c4, default=str))
            inv4 = cur.execute(
                """select count(*) filter (where continuity_owner),
                          count(*) filter (where is_primary and is_configured)
                     from recorders where site_id=%s""", (site4,)).fetchone()
            step(inv4 == (1, 1) and needing_readd(agent_s, key_s) == {rec_a4, rec_b4},
                 "one continuity owner and one primary; the Agent contract lists A and B as "
                 "needing re-add", str(inv4))

            # The flagged continuity recorder is re-added by its cloud id, never
            # onto a proven-different recorder and never disabled.
            snapshot4 = recorders(site4)
            _, state, msg = sync(agent_s, key_s, [
                row("c-new", True, "CCC333", name="Recorder C"),
                row("a-back", False, "DDD444", name="Recorder A", readd=rec_a4),
            ])
            step(state == "42501" and recorders(site4) == snapshot4
                 and fingerprints(site4).get(rec_a4) == "serial:AAA111",
                 "re-adding A by id with a different known serial fails closed, nothing changes",
                 msg)
            _, state, msg = sync(agent_s, key_s, [
                row("c-new", True, "CCC333", name="Recorder C"),
                row("a-back", False, configured=False, name="Recorder A", readd=rec_a4),
            ])
            step(state == "42501" and recorders(site4) == snapshot4,
                 "the continuity recorder cannot be retired by id in contract v4", msg)
            cur.execute("savepoint a_serial_sp")
            got, state, msg = sync(agent_s, key_s, [
                row("c-new", True, "CCC333", name="Recorder C"),
                row("a-serial", False, "aaa111", name="Recorder A"),
            ])
            step(state is None and str((got or [{}])[0].get("a-serial")) == rec_a4
                 and recorders(site4)[rec_a4]["readd"] is None,
                 "the old NVR A returning with its serial re-adopts A's row", msg)
            cur.execute("rollback to savepoint a_serial_sp")
            got, state, msg = sync(agent_s, key_s, [
                row("c-new", True, "CCC333", name="Recorder C"),
                row("a-back", False, name="Recorder A", readd=rec_a4),
            ])
            recs4 = recorders(site4)
            step(state is None and str((got or [{}])[0].get("a-back")) == rec_a4
                 and len(recs4) == 3 and recs4[rec_a4]["key"] == "a-back"
                 and recs4[rec_a4]["readd"] is None and recs4[rec_a4]["continuity"] is True
                 and fingerprints(site4).get(rec_a4) == "serial:AAA111"
                 and needing_readd(agent_s, key_s) == {rec_b4},
                 "re-adding A by its id without a serial re-adopts A's row and clears its flag",
                 msg or json.dumps(recs4))
            back_a4 = cameras(agent_s, key_s, rec_a4, ["1", "2"])
            step({k: str(v) for k, v in back_a4.items()} == {k: str(v) for k, v in cams_a4.items()},
                 "A's camera sync after re-adding by id reuses its camera UUIDs",
                 json.dumps(back_a4, default=str))
            again4, state, msg = sync(agent_s, key_s, [
                row("c-new", True, "CCC333", name="Recorder C"),
                row("a-back", False, name="Recorder A", readd=rec_a4),
            ])
            step(state is None and str((again4 or [{}])[0].get("a-back")) == rec_a4
                 and len(recorders(site4)) == 3,
                 "a re-run naming the same id is idempotent", msg)

            # ----------------------------------------------------------------
            # The presumption is later disproven: a re-adopted continuity
            # recorder's known serial is never overwritten by a different one.
            # ----------------------------------------------------------------
            tenant5, site5 = bootstrap("reinstall-serial@watchlog.test", "Reinstall Serial",
                                       "Serial Store")
            key_t = "reinstall-serial-old-agent"
            agent_t = add_agent(tenant5, site5, key_t, "serial-old-install", "30 days")
            old5, _, _ = sync(agent_t, key_t, [
                row("a-old", True, "AAA111", name="Recorder A"),
                row("b-old", False, "BBB222", name="Recorder B"),
            ])
            rec_a5, rec_b5 = (str((old5 or [{}])[0].get(k)) for k in ("a-old", "b-old"))
            seen(agent_t, "1 hour")
            key_u = "reinstall-serial-new-agent"
            agent_u = add_agent(tenant5, site5, key_u, "serial-new-install", "0 seconds")
            got, state, msg = sync(agent_u, key_u, [row("c-new", True, name="Recorder")])
            step(state is None and str((got or [{}])[0].get("c-new")) == rec_a5,
                 "a fresh primary that reported no serial is presumed to be A (documented "
                 "presumption)", msg)
            snapshot5, fp5 = recorders(site5), fingerprints(site5)
            _, state, msg = sync(agent_u, key_u, [row("c-new", True, "CCC333", name="Recorder")])
            step(state == "42501" and recorders(site5) == snapshot5 and fingerprints(site5) == fp5
                 and fp5.get(rec_a5) == "serial:AAA111",
                 "the next sync reporting a different serial is refused: A's recorded serial is "
                 "not overwritten, nothing changes", msg)
            got, state, msg = sync(agent_u, key_u, [row("c-new2", True, "CCC333", name="Recorder C")])
            rec_c5 = str((got or [{}])[0].get("c-new2"))
            recs5 = recorders(site5)
            step(state is None and rec_c5 not in ("None", rec_a5, rec_b5) and len(recs5) == 3
                 and recs5[rec_a5]["continuity"] is True and recs5[rec_a5]["readd"] is not None
                 and fingerprints(site5).get(rec_a5) == "serial:AAA111",
                 "re-staging the replacement binds it as a new recorder; A kept and flagged",
                 msg or json.dumps(recs5))
            cur.execute("savepoint secondary_serial_sp")
            got, state, msg = sync(agent_u, key_u, [
                row("c-new2", True, "CCC333", name="Recorder C"),
                row("b-new", False, "BBB222", name="Recorder B"),
            ])
            snapshot5 = recorders(site5)
            _, state2, msg2 = sync(agent_u, key_u, [
                row("c-new2", True, "CCC333", name="Recorder C"),
                row("b-new", False, "ZZZ999", name="Recorder B"),
            ])
            step(state is None and str((got or [{}])[0].get("b-new")) == rec_b5
                 and state2 == "42501" and recorders(site5) == snapshot5
                 and fingerprints(site5).get(rec_b5) == "serial:BBB222",
                 "a configured recorder whose known serial changes is refused for any recorder",
                 msg or msg2)
            _, state, msg = sync(agent_u, key_u, [
                row("c-new2", True, "CCC333", name="Recorder C"),
                row("b-new", False, "ZZZ999", configured=False, name="Recorder B"),
            ])
            recs5 = recorders(site5)
            step(state is None and recs5[rec_b5]["configured"] is False
                 and fingerprints(site5).get(rec_b5) == "serial:BBB222",
                 "disabling that recorder still works and keeps its recorded serial", msg)
            cur.execute("rollback to savepoint secondary_serial_sp")

            # ----------------------------------------------------------------
            # A flagged secondary that reports no serial: re-added or retired
            # by its cloud id, with no duplicate recorder or cameras.
            # ----------------------------------------------------------------
            tenant6, site6 = bootstrap("reinstall-byid@watchlog.test", "Reinstall By Id",
                                       "By Id Mart")
            key_v = "reinstall-byid-old-agent"
            agent_v = add_agent(tenant6, site6, key_v, "byid-old-install", "30 days")
            old6, _, _ = sync(agent_v, key_v, [
                row("a-old", True, name="Recorder A"),
                row("b-old", False, name="Recorder B"),
            ])
            rec_a6, rec_b6 = (str((old6 or [{}])[0].get(k)) for k in ("a-old", "b-old"))
            cams_b6 = cameras(agent_v, key_v, rec_b6, ["1", "2"])
            cams6 = site_cameras(site6)
            seen(agent_v, "1 hour")
            key_w = "reinstall-byid-new-agent"
            agent_w = add_agent(tenant6, site6, key_w, "byid-new-install", "0 seconds")
            got, state, msg = sync(agent_w, key_w, [row("a-new", True, name="Recorder A")])
            step(state is None and str((got or [{}])[0].get("a-new")) == rec_a6
                 and needing_readd(agent_w, key_w) == {rec_b6},
                 "after the reinstall B (no serial) is listed as needing re-add", msg)
            snapshot6 = recorders(site6)
            refusals = [
                ("42501", [row("a-new", True), row("b-new", False, readd=rec_a6)],
                 "a recorder that is not waiting for re-add"),
                ("42501", [row("a-new", True), row("b-new", False, readd=rec_b4)],
                 "another site's recorder"),
                ("22023", [row("a-new", True), row("b-new", False, readd=rec_b6),
                           row("b-two", False, readd=rec_b6)],
                 "the same id twice"),
                ("22023", [row("a-new", True), row("b-new", False, readd="not-a-recorder")],
                 "a malformed id"),
                ("42501", [row("a-new", True, readd=rec_b6)],
                 "a local id that already belongs to another recorder"),
            ]
            for want, payload, why in refusals:
                _, state, msg = sync(agent_w, key_w, payload)
                step(state == want and recorders(site6) == snapshot6,
                     f"re-add by id refuses {why} ({want}), nothing changes",
                     f"{state} {msg}")
            cur.execute("savepoint unlisted_sp")
            cur.execute("update recorders set is_configured=false where id=%s", (rec_b6,))
            _, state, msg = sync(agent_w, key_w, [row("a-new", True),
                                                  row("b-new", False, readd=rec_b6)])
            step(state == "42501",
                 "re-add by id refuses a flagged recorder the contract does not list (disabled)",
                 f"{state} {msg}")
            cur.execute("rollback to savepoint unlisted_sp")
            cur.execute("savepoint retire_sp")
            got, state, msg = sync(agent_w, key_w, [
                row("a-new", True, name="Recorder A"),
                row("b-gone", False, configured=False, name="Recorder B", readd=rec_b6),
            ])
            recs6 = recorders(site6)
            step(state is None and str((got or [{}])[0].get("b-gone")) == rec_b6
                 and len(recs6) == 2 and recs6[rec_b6]["configured"] is False
                 and recs6[rec_b6]["readd"] is None and needing_readd(agent_w, key_w) == set(),
                 "the current Agent retires flagged B by its id: disabled, flag cleared",
                 msg or json.dumps(recs6))
            cur.execute("rollback to savepoint retire_sp")
            got, state, msg = sync(agent_w, key_w, [
                row("a-new", True, name="Recorder A"),
                row("b-new", False, name="Recorder B", readd=rec_b6),
            ])
            recs6 = recorders(site6)
            step(state is None and str((got or [{}])[0].get("b-new")) == rec_b6
                 and len(recs6) == 2 and recs6[rec_b6]["key"] == "b-new"
                 and recs6[rec_b6]["readd"] is None and recs6[rec_b6]["configured"] is True
                 and needing_readd(agent_w, key_w) == set(),
                 "re-adding B by its id without a serial re-adopts B's row: flag cleared, no "
                 "second recorder", msg or json.dumps(recs6))
            back_b6 = cameras(agent_w, key_w, rec_b6, ["1", "2"])
            step({k: str(v) for k, v in back_b6.items()} == {k: str(v) for k, v in cams_b6.items()}
                 and site_cameras(site6) == cams6,
                 "B's camera sync after re-adding by id reuses its camera UUIDs, none added",
                 json.dumps(back_b6, default=str))

            acl = cur.execute(
                """select coalesce(has_function_privilege('anon',
                            to_regprocedure('public.wl_recorder_fingerprint_norm(text)'),'execute'),
                          false),
                          coalesce(has_function_privilege('authenticated',
                            to_regprocedure('public.wl_recorder_fingerprint_norm(text)'),'execute'),
                          false)""").fetchone()
            step(acl == (False, False), "the identity helper is not callable by app roles",
                 str(acl))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

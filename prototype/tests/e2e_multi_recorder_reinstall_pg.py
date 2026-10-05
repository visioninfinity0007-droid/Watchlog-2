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
- a fresh registry whose primary is provably another recorder of the site,
  or a superseded earlier installation, fails closed (42501) and changes
  nothing; a re-run is idempotent;
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

            def row(local_key, primary, serial=None, configured=True, name=None):
                out = {"local_key": local_key, "display_name": name or local_key,
                       "driver": "hikvision", "is_primary": primary,
                       "is_configured": configured}
                if serial:
                    out["identity_fingerprint"] = f"serial:{serial}"
                return out

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

            # A fresh registry whose primary is provably B cannot take A's identity.
            snapshot = recorders(site)
            _, state, msg = sync(agent_y, key_y, [
                row("rec-n", True, "BBB222", name="Recorder N"),
            ])
            step(state == "42501" and recorders(site) == snapshot,
                 "a fresh primary that is provably another recorder fails closed, nothing changes",
                 msg)
            # ...nor can a fresh primary sit beside an unknown row carrying A's serial.
            _, state, msg = sync(agent_y, key_y, [
                row("rec-n", True, "NNN999", name="Recorder N"),
                row("rec-a-x", False, "AAA111", name="Recorder A"),
            ])
            step(state == "42501" and recorders(site) == snapshot,
                 "a fresh primary beside a row proven to be A fails closed, nothing changes", msg)

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

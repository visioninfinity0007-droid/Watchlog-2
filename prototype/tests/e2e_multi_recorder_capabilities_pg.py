#!/usr/bin/env python3
"""Recorder-scoped capability sync (0149): real Postgres execution.

Rolled back after execution. Proves:
- same channel number on two recorders overlays against the correct camera;
- recorder capability documents remain independent;
- multi-recorder capability sync does not mutate site-wide capabilities;
- unknown channels remain explicitly unknown, not inferred;
- cross-tenant recorder writes are denied;
- stale Agents cannot mutate recorder capabilities;
- legacy capability sync fails closed for multi-recorder sites;
- legacy capability sync remains compatible on a singleton site;
- new helper/RPC ACLs are narrow.
"""
from __future__ import annotations

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
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix, seen="now()"):
                return cur.execute(
                    f"""insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.0.27',{seen}
                       ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}"),
                ).fetchone()[0]

            def sync_recorders(agent_id, key, rows):
                return as_anon(
                    "select wl_sync_recorders(%s,%s,%s::jsonb)",
                    agent_id, key, json.dumps(rows),
                )[0]

            def sync_camera(agent_id, key, recorder_id, channel, name):
                return as_anon(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                    agent_id, key, recorder_id,
                    json.dumps([{
                        "channel": channel,
                        "name": name,
                        "is_configured": True,
                    }]),
                )[0][channel]

            # --------------------------------------------------------------
            # Multi-recorder tenant A.
            # --------------------------------------------------------------
            ua, ta, sa = bootstrap(
                "caps-a@watchlog.test", "Capabilities A", "Office A"
            )
            key_a = "caps-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")

            contract = as_anon(
                "select wl_multi_recorder_agent_contract(%s,%s)",
                agent_a, key_a,
            )[0]
            # Final chain: 0154 supersedes the 0149 handshake with contract v4.
            step(
                contract["ok"] is True
                and contract["version"] == 4
                and contract["configured_recorders"] == 0
                and set(contract["features"]) == {
                    "recorders",
                    "recorder_cameras",
                    "recorder_events",
                    "recorder_health",
                    "recorder_recovery",
                    "recorder_reconciliation",
                    "recorder_capabilities",
                    "recorder_job_routing",
                    "recorder_analytics",
                    "recorder_continuity",
                },
                "multi-recorder Agent contract v4 advertises recorder capabilities",
                json.dumps(contract, default=str),
            )

            recs = sync_recorders(agent_a, key_a, [
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
            ])
            rec_a, rec_b = recs["rec-a"], recs["rec-b"]

            contract_after = as_anon(
                "select wl_multi_recorder_agent_contract(%s,%s)",
                agent_a, key_a,
            )[0]
            step(
                contract_after["configured_recorders"] == 2,
                "contract count reflects the configured recorder registry after sync",
                json.dumps(contract_after, default=str),
            )

            cam_a = sync_camera(agent_a, key_a, rec_a, "1", "A Camera 1")
            cam_b = sync_camera(agent_a, key_a, rec_b, "1", "B Camera 1")

            sentinel = {"legacy": "keep-site-level-unchanged"}
            cur.execute(
                "update sites set capabilities=%s::jsonb, capabilities_at=now() where id=%s",
                (json.dumps(sentinel), sa),
            )

            caps_a = {
                "source": "test-a",
                "channels": [
                    {"channel": "1", "motion": "supported"},
                    {"channel": "9", "motion": "unknown"},
                ],
            }
            caps_b = {
                "source": "test-b",
                "channels": [
                    {"channel": "1", "motion": "unsupported"},
                ],
            }

            out_a = as_anon(
                "select wl_sync_recorder_capabilities(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(caps_a),
            )[0]
            out_b = as_anon(
                "select wl_sync_recorder_capabilities(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, json.dumps(caps_b),
            )[0]
            step(
                out_a["channels"] == 2 and out_b["channels"] == 1,
                "each recorder capability payload is accepted independently",
            )

            rows = cur.execute(
                "select id,capabilities from recorders where id in (%s,%s)",
                (rec_a, rec_b),
            ).fetchall()
            by_rec = {str(r[0]): r[1] for r in rows}
            a_channels = {x["channel"]: x for x in by_rec[str(rec_a)]["channels"]}
            b_channels = {x["channel"]: x for x in by_rec[str(rec_b)]["channels"]}

            step(
                str(a_channels["1"]["camera_id"]) == str(cam_a)
                and a_channels["1"]["name"] == "A Camera 1"
                and a_channels["1"]["configured"] is True,
                "Recorder A Channel 1 overlays only Recorder A camera truth",
                json.dumps(a_channels["1"], default=str),
            )
            step(
                str(b_channels["1"]["camera_id"]) == str(cam_b)
                and b_channels["1"]["name"] == "B Camera 1"
                and b_channels["1"]["configured"] is True,
                "Recorder B Channel 1 overlays only Recorder B camera truth",
                json.dumps(b_channels["1"], default=str),
            )
            step(
                a_channels["9"]["configuration_state"] == "unknown"
                and a_channels["9"]["configured"] is None
                and "camera_id" not in a_channels["9"],
                "unknown recorder channel remains Unknown rather than inferred",
                json.dumps(a_channels["9"], default=str),
            )

            site_caps = cur.execute(
                "select capabilities from sites where id=%s", (sa,)
            ).fetchone()[0]
            step(
                site_caps == sentinel,
                "multi-recorder capability sync does not overwrite site-wide capabilities",
                json.dumps(site_caps, default=str),
            )

            raised, msg = as_anon_raises(
                "select wl_sync_capabilities(%s,%s,%s::jsonb)",
                agent_a, key_a, json.dumps(caps_a),
            )
            step(
                raised and "ambiguous" in msg.lower(),
                "legacy capability sync fails closed on multi-recorder site",
                msg,
            )

            # --------------------------------------------------------------
            # Tenant B: isolation + singleton compatibility.
            # --------------------------------------------------------------
            ub, tb, sb = bootstrap(
                "caps-b@watchlog.test", "Capabilities B", "Retail B"
            )
            key_b = "caps-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_c = sync_recorders(agent_b, key_b, [{
                "local_key": "rec-c",
                "display_name": "Recorder C",
                "is_primary": True,
                "is_configured": True,
            }])["rec-c"]
            cam_c = sync_camera(agent_b, key_b, rec_c, "1", "C Camera 1")

            raised, msg = as_anon_raises(
                "select wl_sync_recorder_capabilities(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_c, json.dumps(caps_a),
            )
            step(
                raised and "not configured for this agent site" in msg.lower(),
                "Agent A cannot write capability truth onto tenant B recorder",
                msg,
            )

            legacy_caps = {
                "source": "legacy-singleton",
                "channels": [{"channel": "1", "motion": "supported"}],
            }
            legacy_out = as_anon(
                "select wl_sync_capabilities(%s,%s,%s::jsonb)",
                agent_b, key_b, json.dumps(legacy_caps),
            )[0]
            site_b_caps = cur.execute(
                "select capabilities from sites where id=%s", (sb,)
            ).fetchone()[0]
            rec_b_caps = cur.execute(
                "select capabilities from recorders where id=%s", (rec_c,)
            ).fetchone()[0]
            # The legacy overlay (wl_overlay_camera_truth) carries the canonical
            # camera name/configuration of the site's configured recorder.
            legacy_channels = site_b_caps["channels"]
            cam_c_name = cur.execute(
                "select name from cameras where id=%s", (cam_c,)
            ).fetchone()[0]
            step(
                legacy_out["ok"] is True
                and str(legacy_out["recorder_id"]) == str(rec_c)
                and site_b_caps == rec_b_caps
                and len(legacy_channels) == 1
                and legacy_channels[0]["name"] == cam_c_name
                and legacy_channels[0]["configured"] is True,
                "legacy singleton capability sync still mirrors site + recorder truth",
                json.dumps(site_b_caps, default=str),
            )

            # --------------------------------------------------------------
            # Tenant C: one configured recorder plus a DISABLED secondary that
            # shares Channel 1. The legacy overlay must use only the configured
            # recorder's camera, never the disabled secondary's.
            # --------------------------------------------------------------
            uc, tc, sc = bootstrap(
                "caps-c@watchlog.test", "Capabilities C", "Retail C"
            )
            key_c = "caps-agent-c"
            agent_c = add_agent(tc, sc, key_c, "c")
            both = [
                {"local_key": "rec-p", "display_name": "Recorder P",
                 "is_primary": True, "is_configured": True},
                {"local_key": "rec-s", "display_name": "Recorder S",
                 "is_primary": False, "is_configured": True},
            ]
            recs_c = sync_recorders(agent_c, key_c, both)
            rec_p, rec_s = recs_c["rec-p"], recs_c["rec-s"]
            cam_p = sync_camera(agent_c, key_c, rec_p, "1", "P Camera 1")
            sync_camera(agent_c, key_c, rec_s, "1", "S Camera 1")
            both[1]["is_configured"] = False
            sync_recorders(agent_c, key_c, both)
            rec_s_caps_before = cur.execute(
                "select capabilities from recorders where id=%s", (rec_s,)
            ).fetchone()[0]

            legacy_c = as_anon(
                "select wl_sync_capabilities(%s,%s,%s::jsonb)",
                agent_c, key_c, json.dumps({
                    "source": "legacy-with-disabled-secondary",
                    "channels": [{"channel": "1", "motion": "supported"}],
                }),
            )[0]
            site_c_caps = cur.execute(
                "select capabilities from sites where id=%s", (sc,)
            ).fetchone()[0]
            rec_s_caps_after = cur.execute(
                "select capabilities from recorders where id=%s", (rec_s,)
            ).fetchone()[0]
            channels_c = site_c_caps["channels"]
            step(
                str(legacy_c["recorder_id"]) == str(rec_p)
                and len(channels_c) == 1
                and channels_c[0]["name"] == "P Camera 1"
                and channels_c[0]["configured"] is True
                and str(channels_c[0].get("camera_id", cam_p)) == str(cam_p)
                and rec_s_caps_after == rec_s_caps_before,
                "legacy capability overlay ignores a disabled secondary sharing the channel",
                json.dumps(site_c_caps, default=str),
            )

            read_c = as_auth(uc, "select wl_capabilities()")[0]
            read_channels = read_c[0]["capabilities"]["channels"] if read_c else []
            step(
                len(read_channels) == 1 and read_channels[0]["name"] == "P Camera 1",
                "portal capability read overlays only the configured recorder's camera",
                json.dumps(read_c, default=str),
            )

            stale_key = "caps-stale"
            stale_agent = add_agent(
                ta, sa, stale_key, "stale",
                seen="'2000-01-01'::timestamptz",
            )
            raised, msg = as_anon_raises(
                "select wl_sync_recorder_capabilities(%s,%s,%s,%s::jsonb)",
                stale_agent, stale_key, rec_a, json.dumps(caps_a),
            )
            step(
                raised and "current site authority" in msg.lower(),
                "stale Agent cannot mutate recorder capabilities",
                msg,
            )

            def execute_grantees(sig):
                rows = cur.execute(
                    """select case when a.grantee=0 then 'PUBLIC'
                                    else a.grantee::regrole::text end,
                              p.proowner::regrole::text
                         from pg_proc p,
                              aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                        where p.oid=%s::regprocedure
                          and a.privilege_type='EXECUTE'""",
                    (sig,),
                ).fetchall()
                owner = rows[0][1] if rows else None
                return {g for g, _ in rows if g != owner}

            got = execute_grantees(
                "public.wl_multi_recorder_agent_contract(uuid,text)"
            )
            step(
                got == {"anon"},
                "multi-recorder contract handshake EXECUTE ACL is exactly anon (+owner)",
                str(sorted(got)),
            )

            got = execute_grantees(
                "public.wl_sync_recorder_capabilities(uuid,text,uuid,jsonb)"
            )
            step(
                got == {"anon"},
                "recorder capability sync EXECUTE ACL is exactly anon (+owner)",
                str(sorted(got)),
            )

            got = execute_grantees(
                "public.wl_overlay_recorder_camera_truth(uuid,uuid,jsonb)"
            )
            step(
                got == set(),
                "recorder capability overlay helper is owner-only",
                str(sorted(got)),
            )

            got = execute_grantees("public.wl_overlay_camera_truth(uuid,jsonb)")
            step(
                got == {"service_role"},
                "legacy capability overlay keeps its service-role-only ACL",
                str(sorted(got)),
            )

        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

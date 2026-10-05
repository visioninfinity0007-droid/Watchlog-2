#!/usr/bin/env python3
"""Customer-safe multi-recorder owner read model (0152): real Postgres gate.

Runs in a disposable/test database and rolls back.

Proves:
- tenant A sees only its own configured recorders;
- recorder state is independent for overlapping Channel 1 recorders;
- camera IDs group under the correct recorder;
- the older site health snapshot and operations report list each recorder
  from its own health, with stale health unknown;
- tenant B cannot read tenant A's site;
- the payload does not expose vendor/model/driver/local address/credential fields;
- exact EXECUTE ACL is authenticated only.
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

            def as_auth_raises(uid, sql, *params):
                cur.execute("savepoint auth_err")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                raised, message = False, ""
                try:
                    cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    raised, message = True, str(exc).splitlines()[0]
                cur.execute("rollback to savepoint auth_err")
                return raised, message

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

            def add_agent(tenant_id, site_id, key, suffix):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.0.27',now()
                       ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}"),
                ).fetchone()[0]

            def anon_call(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            ua, ta, sa = bootstrap(
                "portal-rec-a@watchlog.test", "Portal Recorder A", "Warehouse A"
            )
            key_a = "portal-recorder-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")

            mapping = anon_call(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([
                    {
                        "local_key": "rec-a",
                        "display_name": "Loading area recorder",
                        "vendor": "VendorMustNotLeak",
                        "model": "ModelMustNotLeak",
                        "driver": "driver-must-not-leak",
                        "is_primary": True,
                        "is_configured": True,
                    },
                    {
                        "local_key": "rec-b",
                        "display_name": "Main building recorder",
                        "vendor": "OtherVendorMustNotLeak",
                        "model": "OtherModelMustNotLeak",
                        "driver": "other-driver-must-not-leak",
                        "is_primary": False,
                        "is_configured": True,
                    },
                ]),
            )[0]
            rec_a, rec_b = mapping["rec-a"], mapping["rec-b"]

            cam_a = anon_call(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a,
                json.dumps([{
                    "channel": "1", "name": "Loading bay", "is_configured": True
                }]),
            )[0]["1"]
            cam_b = anon_call(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b,
                json.dumps([{
                    "channel": "1", "name": "Main gate", "is_configured": True
                }]),
            )[0]["1"]

            healthy_report = {
                "nvr": {
                    "reachable": True, "auth_ok": True,
                    "reason": "ok", "state": "operational",
                },
                "channels": {
                    "enumerated": True,
                    "reported": [{"channel": "1", "enabled": True}],
                },
            }
            offline_report = {
                "nvr": {
                    "reachable": False, "auth_ok": None,
                    "reason": "nvr_unreachable", "state": "offline",
                },
                "channels": {"enumerated": False, "reported": []},
            }
            anon_call(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report),
            )
            anon_call(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_b, json.dumps(offline_report),
            )

            # Same Channel 1 on both recorders must remain distinguishable in
            # the owner context used by Cameras & Evidence.
            event_ts = "2026-10-03T10:00:00Z"
            ingested = anon_call(
                "select wl_ingest_events(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([
                    {
                        "recorder_id": str(rec_a),
                        "channel": "1",
                        "event_type": "portal_probe_a",
                        "device_ts": event_ts,
                        "agent_ts": event_ts,
                        "payload": {},
                    },
                    {
                        "recorder_id": str(rec_b),
                        "channel": "1",
                        "event_type": "portal_probe_b",
                        "device_ts": event_ts,
                        "agent_ts": event_ts,
                        "payload": {},
                    },
                ]),
            )[0]
            step(ingested["inserted"] == 2,
                 "same Channel 1 owner-context probe events both ingest")

            payload = as_auth(
                ua, "select wl_my_site_recorders(%s)", sa
            )[0]
            rows = payload["recorders"]
            by_id = {str(row["id"]): row for row in rows}

            step(payload["enabled"] is True and len(rows) == 2,
                 "owner read model returns the two configured recorders")
            step(by_id[str(rec_a)]["state"] == "healthy"
                 and by_id[str(rec_b)]["state"] == "offline",
                 "recorder availability remains independent")

            # The older owner read models (0050 report, 0089 snapshot) must name
            # each recorder from its own health, not one per-Agent nvr_health
            # row (here a frozen pre-upgrade 'reachable' value).
            cur.execute(
                """insert into nvr_health(agent_id,tenant_id,site_id,
                                          nvr_reachable,nvr_auth_ok,reason_code)
                   values (%s,%s,%s,true,true,'ok')
                   on conflict (agent_id) do update
                      set nvr_reachable=true,nvr_auth_ok=true,reason_code='ok'""",
                (agent_a, ta, sa),
            )

            def legacy_read_models():
                snap = as_auth(ua, "select wl_site_health_snapshot(%s)", sa)[0]
                rep = as_auth(
                    ua, "select wl_operations_report(%s,now()-interval '1 day',now())", sa
                )[0]
                return (
                    {str(r.get("recorder_id")): r for r in snap["recorders"]},
                    {str(r.get("recorder_id")): r for r in rep["reliability"]["recorders"]},
                )

            snap_by, rep_by = legacy_read_models()
            step(set(snap_by) == {str(rec_a), str(rec_b)}
                 and snap_by[str(rec_a)]["nvr_reachable"] is True
                 and snap_by[str(rec_b)]["nvr_reachable"] is False,
                 "site health snapshot lists each recorder with its own reachability",
                 json.dumps(list(snap_by.values()), default=str)[:400])
            step(set(rep_by) == {str(rec_a), str(rec_b)}
                 and rep_by[str(rec_a)]["reachable"] is True
                 and rep_by[str(rec_b)]["reachable"] is False,
                 "operations report lists each recorder with its own reachability",
                 json.dumps(list(rep_by.values()), default=str)[:400])

            unknown_auth_report = {
                "nvr": {
                    "reachable": True, "auth_ok": None,
                    "reason": "unknown", "state": "unknown",
                },
                "channels": {"enumerated": False, "reported": []},
            }
            anon_call(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(unknown_auth_report),
            )
            unknown_payload = as_auth(
                ua, "select wl_my_site_recorders(%s)", sa
            )[0]
            unknown_by_id = {
                str(row["id"]): row for row in unknown_payload["recorders"]
            }
            step(
                unknown_by_id[str(rec_a)]["state"] == "unknown"
                and unknown_by_id[str(rec_a)]["issue"] is None,
                "reachable recorder with unverified authentication stays Not verified",
            )
            anon_call(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report),
            )

            # A stale recorder-health row must never remain customer-visible as
            # Available/Healthy after its governed freshness window expires.
            cur.execute(
                """update recorder_health
                      set updated_at=now()-interval '16 minutes'
                    where recorder_id=%s and agent_id=%s""",
                (rec_a, agent_a),
            )
            stale_payload = as_auth(
                ua, "select wl_my_site_recorders(%s)", sa
            )[0]
            stale_by_id = {str(row["id"]): row for row in stale_payload["recorders"]}
            step(
                stale_by_id[str(rec_a)]["state"] == "unknown"
                and stale_by_id[str(rec_a)]["issue"] is None,
                "stale recorder health becomes Not verified, never stale healthy",
            )
            snap_by, rep_by = legacy_read_models()
            step(snap_by[str(rec_a)]["nvr_reachable"] is None
                 and snap_by[str(rec_a)]["health_fresh"] is False
                 and rep_by[str(rec_a)]["reachable"] is None
                 and rep_by[str(rec_a)]["fresh"] is False,
                 "snapshot and operations report show stale recorder health as unknown",
                 json.dumps([snap_by[str(rec_a)], rep_by[str(rec_a)]], default=str)[:400])
            # Restore a fresh governed observation for the remaining assertions.
            anon_call(
                "select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                agent_a, key_a, rec_a, json.dumps(healthy_report),
            )
            payload = as_auth(
                ua, "select wl_my_site_recorders(%s)", sa
            )[0]
            rows = payload["recorders"]
            by_id = {str(row["id"]): row for row in rows}
            step([str(x) for x in by_id[str(rec_a)]["camera_ids"]] == [str(cam_a)]
                 and [str(x) for x in by_id[str(rec_b)]["camera_ids"]] == [str(cam_b)],
                 "overlapping Channel 1 cameras group under the correct recorder")
            step(by_id[str(rec_a)]["camera_count"] == 1
                 and by_id[str(rec_b)]["camera_count"] == 1,
                 "configured camera counts are recorder-scoped")

            raw = json.dumps(payload, default=str).lower()
            forbidden = [
                "vendormustnotleak", "modelmustnotleak",
                "drivermust-not-leak", "local_key",
                "identity_fingerprint", "capabilities", "192.0.2."
            ]
            step(not any(word in raw for word in forbidden),
                 "customer read model omits vendor/model/driver/local/internal fields")

            context = as_auth(ua, "select wl_ai_context(%s)", sa)[0]
            camera_rows = {str(row["id"]): row for row in context["cameras"]}
            step(
                str(camera_rows[str(cam_a)]["recorder_id"]) == str(rec_a)
                and str(camera_rows[str(cam_b)]["recorder_id"]) == str(rec_b),
                "owner context camera rows carry deterministic recorder identity",
            )
            recent = [
                row for row in context["recent_events"]
                if row["event_type"] in ("portal_probe_a", "portal_probe_b")
            ]
            recent_by_type = {row["event_type"]: row for row in recent}
            step(
                len(recent) == 2
                and str(recent_by_type["portal_probe_a"]["camera_id"]) == str(cam_a)
                and str(recent_by_type["portal_probe_a"]["recorder_id"]) == str(rec_a)
                and str(recent_by_type["portal_probe_b"]["camera_id"]) == str(cam_b)
                and str(recent_by_type["portal_probe_b"]["recorder_id"]) == str(rec_b),
                "recent owner events remain distinct despite overlapping Channel 1",
            )
            step(context["facts_version"] == "watchlog-ai-context-v7",
                 "owner context version advertises recorder-safe capability semantics")
            step(
                len(context["recorders"]) == 2
                and {str(x["id"]) for x in context["recorders"]} == {str(rec_a), str(rec_b)},
                "AI context carries the customer-safe recorder list",
            )
            step(
                context["recorder"] is None
                and context["capabilities"] == {}
                and context["capability_known"] is False,
                "multi-recorder AI context fails closed on legacy aggregate recorder/capability truth",
            )

            # Disable the secondary through the governed Agent sync path. Historical
            # recorder/camera/event rows remain, but live owner context must stop
            # presenting that recorder's cameras/faults as current monitoring truth.
            # 0154 recorder sync is a desired-state sync: the payload names the
            # whole registry, including its one configured primary.
            anon_call(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent_a, key_a,
                json.dumps([
                    {
                        "local_key": "rec-a",
                        "display_name": "Loading area recorder",
                        "is_primary": True,
                        "is_configured": True,
                    },
                    {
                        "local_key": "rec-b",
                        "display_name": "Main building recorder",
                        "is_primary": False,
                        "is_configured": False,
                    },
                ]),
            )
            after_disable = as_auth(
                ua, "select wl_my_site_recorders(%s)", sa
            )[0]
            step(
                len(after_disable["recorders"]) == 1
                and str(after_disable["recorders"][0]["id"]) == str(rec_a),
                "disabled secondary leaves owner recorder list but preserves history",
            )
            context_disabled = as_auth(ua, "select wl_ai_context(%s)", sa)[0]
            active_camera_ids = {str(row["id"]) for row in context_disabled["cameras"]}
            step(
                str(cam_a) in active_camera_ids and str(cam_b) not in active_camera_ids,
                "disabled recorder cameras are not presented as current monitored cameras",
            )
            disabled_faults = context_disabled.get("faults") or []
            step(
                not any(str(row.get("camera") or "") == "Main gate" for row in disabled_faults),
                "disabled recorder camera faults are excluded from live owner context",
            )

            ub, tb, sb = bootstrap(
                "portal-rec-b@watchlog.test", "Portal Recorder B", "Office B"
            )
            raised, msg = as_auth_raises(
                ub, "select wl_my_site_recorders(%s)", sa
            )
            step(raised, "tenant B cannot read tenant A recorder summary", msg)

            own = as_auth(ub, "select wl_my_site_recorders(%s)", sb)[0]
            step(own["recorders"] == [],
                 "site without configured recorder returns an empty governed list")

            raised, msg = as_anon_raises(
                "select wl_my_site_recorders(%s)", sa
            )
            step(raised and "permission denied" in msg.lower(),
                 "anon cannot execute owner recorder read model", msg)

            rows_acl = cur.execute(
                """select case when a.grantee=0 then 'PUBLIC'
                               else a.grantee::regrole::text end,
                          p.proowner::regrole::text
                     from pg_proc p,
                          aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                    where p.oid='public.wl_my_site_recorders(uuid)'::regprocedure
                      and a.privilege_type='EXECUTE'"""
            ).fetchall()
            owner = rows_acl[0][1] if rows_acl else None
            grantees = {g for g, _ in rows_acl if g != owner}
            step(grantees == {"authenticated"},
                 "owner recorder read model EXECUTE ACL is authenticated only",
                 str(sorted(grantees)))

        finally:
            conn.rollback()

    passed = sum(1 for value in STEPS if value)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

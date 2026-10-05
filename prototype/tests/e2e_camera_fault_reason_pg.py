#!/usr/bin/env python3
"""Offline-camera fault cause follows camera_health.reason_code (NEW-L4): real Postgres.

Self-contained and rolled back. At Al-Khalid an ONVIF probe timeout made
every offline camera a critical 'camera_offline / video_loss' fault with no
video-loss signal. Proves, on the final wl_reconcile_site_faults:
- only reason 'video_loss' opens a critical camera_offline / video_loss fault;
- a probe timeout opens a 'camera_not_verified' warning with reason
  probe_timeout (never video loss); an unrecognised cause is 'unknown';
- the fault key stays 'camera:<id>:offline' whatever the cause;
- an open row follows a changed cause under the same key (same row id),
  keeping its acknowledgement; an already-open legacy video_loss row whose
  camera only timed out is downgraded, not left critical;
- a camera that recovers resolves its fault as before.
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
            def one(sql, *params):
                return cur.execute(sql, params or None).fetchone()

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
                """(row, message): a database error never aborts the transaction."""
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_sp")
                    return None, str(exc).splitlines()[0]
                cur.execute("reset role")
                cur.execute("release savepoint anon_sp")
                return row, ""

            uid = one("insert into auth.users(id,email) values (gen_random_uuid(),%s) "
                      "returning id", "fault-reason@watchlog.test")[0]
            boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                           "Fault Reason Co", "Fault Reason Site")[0]
            tenant = boot["tenant_id"]
            site = one("select id from sites where tenant_id=%s order by created_at limit 1",
                       tenant)[0]
            key = "fault-reason-agent"
            agent = one(
                """insert into public.agents(
                     tenant_id,site_id,agent_key_hash,hostname,platform,
                     agent_version,device_driver,last_seen_at
                   ) values (
                     %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                     'agent-reason','windows','5.1.0','onvif',now()
                   ) returning id""",
                tenant, site, key)[0]
            recs, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent, key,
                                json.dumps([{"local_key": "rec-main",
                                             "display_name": "Main Recorder",
                                             "is_primary": True, "is_configured": True}]))
            assert recs, msg
            rec = recs[0]["rec-main"]
            mapping, msg = as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)", agent, key, rec,
                json.dumps([{"channel": ch, "name": f"Camera {ch}", "is_configured": True}
                            for ch in ("1", "2", "3", "4")]))
            assert mapping, msg
            cams = {ch: mapping[0][ch] for ch in ("1", "2", "3", "4")}

            body = {"nvr": {"reachable": True, "auth_ok": True, "reason": "ok"},
                    "channels": {"enumerated": True,
                                 "reported": [{"channel": ch, "enabled": True}
                                              for ch in cams]}}
            row, msg = as_anon("select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                               agent, key, rec, json.dumps(body))
            assert row, msg

            def camera_health(states):
                row, msg = as_anon(
                    "select wl_report_recorder_camera_health(%s,%s,%s,%s::jsonb)",
                    agent, key, rec,
                    json.dumps({"cameras": [{"channel": ch, "health": h, "reason": r}
                                            for ch, (h, r) in states.items()]}))
                assert row, msg

            def faults():
                return {r[0]: {"id": r[1], "type": r[2], "severity": r[3],
                               "reason": r[4], "state": r[5]}
                        for r in cur.execute(
                            """select dedupe_key,id,fault_type,severity,
                                      reason_code::text,state
                                 from operational_faults
                                where site_id=%s and fault_domain='camera'
                                  and state<>'resolved'""",
                            (site,)).fetchall()}

            def reconcile():
                return one("select wl_reconcile_site_faults(%s)", site)[0]

            def k(ch):
                return f"camera:{cams[ch]}:offline"

            # The production row this fix must repair: camera 4 already carries
            # an open critical video_loss fault from an earlier probe timeout.
            cur.execute(
                """insert into operational_faults
                     (tenant_id,site_id,camera_id,fault_domain,fault_type,severity,
                      state,reason_code,dedupe_key,opened_at)
                   values (%s,%s,%s,'camera','camera_offline','critical','open',
                           'video_loss',%s,now()-interval '3 days')""",
                (tenant, site, cams["4"], k("4")))
            legacy_id = one("select id from operational_faults where dedupe_key=%s",
                            k("4"))[0]

            camera_health({"1": ("offline", "probe_timeout"),
                           "2": ("offline", "video_loss"),
                           "3": ("offline", "unknown"),
                           "4": ("offline", "probe_timeout")})
            res = reconcile()
            f = faults()
            step(k("2") in f and f[k("2")]["type"] == "camera_offline"
                 and f[k("2")]["severity"] == "critical"
                 and f[k("2")]["reason"] == "video_loss",
                 "a video-loss signal opens a critical camera_offline / video_loss fault",
                 json.dumps(f.get(k("2")), default=str))
            step(k("1") in f and f[k("1")]["type"] == "camera_not_verified"
                 and f[k("1")]["severity"] == "warning"
                 and f[k("1")]["reason"] == "probe_timeout",
                 "a probe timeout is a camera_not_verified warning, never video loss",
                 json.dumps(f.get(k("1")), default=str))
            step(k("3") in f and f[k("3")]["reason"] == "unknown"
                 and f[k("3")]["severity"] == "warning"
                 and f[k("3")]["type"] == "camera_not_verified",
                 "an offline camera with no known cause is reported as unknown",
                 json.dumps(f.get(k("3")), default=str))
            step(all(v["reason"] != "video_loss" for kk, v in f.items() if kk != k("2")),
                 "no camera without a video-loss signal is labelled video_loss",
                 json.dumps(f, default=str))
            step(k("4") in f and f[k("4")]["id"] == legacy_id
                 and f[k("4")]["severity"] == "warning"
                 and f[k("4")]["reason"] == "probe_timeout"
                 and f[k("4")]["type"] == "camera_not_verified",
                 "an open legacy video_loss row whose camera only timed out is downgraded "
                 "in place",
                 json.dumps([f.get(k("4")), res], default=str))

            # Acknowledge camera 1, then its cause changes to a real video loss.
            cur.execute("update operational_faults set state='acknowledged', "
                        "acknowledged_at=now() where id=%s", (f[k("1")]["id"],))
            before_1, before_2 = f[k("1")]["id"], f[k("2")]["id"]
            camera_health({"1": ("offline", "video_loss"),
                           "2": ("offline", "probe_timeout"),
                           "3": ("offline", "unknown"),
                           "4": ("offline", "probe_timeout")})
            reconcile()
            f = faults()
            step(k("1") in f and f[k("1")]["id"] == before_1
                 and f[k("1")]["type"] == "camera_offline"
                 and f[k("1")]["severity"] == "critical"
                 and f[k("1")]["reason"] == "video_loss"
                 and f[k("1")]["state"] == "acknowledged",
                 "the same row follows probe timeout -> video loss and keeps its "
                 "acknowledgement",
                 json.dumps(f.get(k("1")), default=str))
            step(k("2") in f and f[k("2")]["id"] == before_2
                 and f[k("2")]["severity"] == "warning"
                 and f[k("2")]["reason"] == "probe_timeout",
                 "the same row follows video loss -> probe timeout",
                 json.dumps(f.get(k("2")), default=str))
            open_rows = one("""select count(*) from operational_faults
                                where site_id=%s and fault_domain='camera'
                                  and state<>'resolved'""", site)[0]
            step(open_rows == 4, "one open row per camera key, whatever the cause",
                 str(open_rows))

            camera_health({"1": ("operational", "ok"), "2": ("operational", "ok"),
                           "3": ("operational", "ok"), "4": ("operational", "ok")})
            reconcile()
            step(faults() == {}, "recovered cameras resolve their faults",
                 json.dumps(faults(), default=str))
        finally:
            conn.rollback()

    failed = STEPS.count(False)
    print(f"\n{len(STEPS) - failed}/{len(STEPS)} passed")
    return 0 if STEPS and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())

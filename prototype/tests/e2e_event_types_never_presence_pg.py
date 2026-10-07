#!/usr/bin/env python3
"""Fault, restore and raw recorder signals are never people activity (0165): real Postgres.

5.1.2 Agents report video_restore, tamper_end, camera_disconnect, camera_reconnect,
alarm_input_end, recorder_restart and unmapped raw vendor codes. Before 0165 every event
outside four fault types became an activity and every activity outside video_loss/tamper a
'presence' episode, so a 03:00 recorder restart read as after-hours presence. Proves:
- person / motion still become presence episodes (unchanged);
- video_loss, tamper and camera_disconnect are camera faults / fault episodes;
- restores, ends, reconnects, restarts, recorder-scoped alarms and unmapped codes are not
  activity at all;
- the derive functions stay service_role-only.
Rolled back.
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
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('presence') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'p','UTC') returning id",
                              (tid,)).fetchone()[0]
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) "
                              "values (%s,%s,'1','Gate','area') returning id", (tid, sid)).fetchone()[0]
            n = [0]

            def ev(etype, minute, camera=cam, payload=None):
                n[0] += 1
                ts = f"2026-06-01 03:{minute:02d}:00+00"
                return cur.execute(
                    """insert into events (tenant_id,site_id,camera_id,event_type,device_ts,agent_ts,
                                           received_at,dedupe_key,payload)
                       values (%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,now(),%s,%s::jsonb)
                       returning id""",
                    (tid, sid, camera, etype, ts, ts, f"p-{n[0]}", json.dumps(payload or {}))).fetchone()[0]

            kept = {"person": ev("person", 1), "motion": ev("motion", 2)}
            faults = {t: ev(t, 10 + i) for i, t in enumerate(("video_loss", "tamper", "camera_disconnect"))}
            never = {
                "video_restore": ev("video_restore", 20),
                "tamper_end": ev("tamper_end", 21),
                "camera_reconnect": ev("camera_reconnect", 22),
                "alarm_input_end": ev("alarm_input_end", 23, camera=None, payload={"recorder_scoped": True}),
                "recorder_restart": ev("recorder_restart", 24, camera=None, payload={"recorder_scoped": True}),
                "alarm_input (recorder-scoped)": ev("alarm_input", 25, camera=None, payload={"recorder_scoped": True}),
                "videounfocus (unmapped)": ev("videounfocus", 26, payload={"unmapped": True}),
            }
            lo, hi = "2026-06-01 00:00+00", "2026-06-02 00:00+00"
            cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.execute("select wl_derive_episodes(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            acts = {r[0]: (r[1], r[2]) for r in cur.execute(
                "select source_event_id, activity_type, object_class from activities where site_id=%s",
                (sid,)).fetchall()}

            step(all(eid in acts and acts[eid][0] == t for t, eid in kept.items()),
                 "person and motion are still activities")
            step(all(acts.get(eid, ("",))[0] == "camera_fault" for eid in faults.values()),
                 "video_loss, tamper and camera_disconnect are camera faults",
                 str({t: acts.get(e) for t, e in faults.items()}))
            leaked = {t: acts[e] for t, e in never.items() if e in acts}
            step(not leaked, "restores, ends, reconnects, restarts, recorder-scoped alarms and "
                             "unmapped codes are not activity", str(leaked))
            eps = cur.execute("select episode_type, object_class from episodes where site_id=%s",
                              (sid,)).fetchall()
            presence = sorted(o for t, o in eps if t == "presence")
            fault_eps = sorted(o for t, o in eps if t == "video_loss")
            step(set(presence) <= {"person", "motion"} and presence,
                 "presence episodes contain only people/motion activity", str(presence))
            step(len(fault_eps) >= 1, "fault episodes exist for the camera faults", str(fault_eps))
            for fn in ("wl_derive_activities(uuid,timestamptz,timestamptz)",
                       "wl_derive_episodes(uuid,timestamptz,timestamptz,integer)"):
                anon = cur.execute("select has_function_privilege('anon', %s, 'EXECUTE')",
                                   (f"public.{fn}",)).fetchone()[0]
                auth = cur.execute("select has_function_privilege('authenticated', %s, 'EXECUTE')",
                                   (f"public.{fn}",)).fetchone()[0]
                step(not anon and not auth, f"{fn.split('(')[0]} stays service_role-only")
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

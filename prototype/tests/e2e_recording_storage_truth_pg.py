#!/usr/bin/env python3
"""Recording and storage truth (0161): real Postgres, rolled back.

Field facts (Al-Khalid DH-XVR1B08-I, Agent 5.1.1): recorder_health.recording_state was never
written after 0147's one-time copy, no disk inventory or capacity reached the cloud, and
per-camera recording could only be 'recording' or 'unknown'. Proves:

- wl_report_recorder_storage_disks: a wrong key is refused (28000); a recorder of another
  site, an unknown recorder and a non-current Agent are refused (42501); a good report stores
  per-disk rows and recorder totals; malformed sizes/states are sanitised, never trusted; a
  disk missing from a later report is kept as not present and unknown; a report without a
  disk list changes nothing; another tenant cannot read the rows; EXECUTE is anon-only;
- the recorder recording rollup: every camera recording -> recording; a live camera not
  recording -> not_recording; a video-loss camera is excused, never counted as recording;
  any unknown or stale camera -> unknown; storage_fault; a recorder without cameras ->
  unknown; recorders stay isolated;
- the current-proof core: 5.1.2 reason codes are kept, a no_recent_recording verdict without
  archive evidence becomes unknown, latest_recording_at is kept only with recording proof,
  never in the future and never moves backwards;
- old Agents: the 0147 recorder RPC and the 0089 site RPC keep their signatures and a
  5.1.1-shape payload still works (the legacy site RPC also gets the rollup);
- the owner read model shows recording/storage as unknown until proven, and 'verified'
  only with storage ok AND recording confirmed.
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

DISK_RPC = "select wl_report_recorder_storage_disks(%s,%s,%s,%s::jsonb)"
CURRENT_RPC = "select wl_report_recorder_recording_storage_current(%s,%s,%s,%s::jsonb)"
LEGACY_RPC = "select wl_report_recording_storage_current(%s,%s,%s::jsonb)"
TB = 1000068870144
NEWEST = datetime(2026, 1, 2, 10, 4, tzinfo=timezone.utc)


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def proof(channels, *, evidence="archive_search", storage=("ok", "ok")):
    """A current-proof payload. channels: [(channel, state, reason[, latest])]."""
    rows = []
    for c in channels:
        row = {"channel": c[0], "state": c[1], "reason": c[2]}
        if len(c) > 3 and c[3]:
            row["latest_recording_at"] = c[3]
        rows.append(row)
    return json.dumps({"storage": {"state": storage[0], "reason": storage[1]},
                       "recording": {"supported": True, "channels": rows},
                       "recording_evidence": evidence})


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

            def anon_call(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            def as_anon_try(sql, *params):
                cur.execute("savepoint anon_try")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_try")
                    return row, ""
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_try")
                    cur.execute("reset role")
                    return None, f"{exc.sqlstate} {str(exc).splitlines()[0]}"

            def bootstrap(label):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (f"{label}@watchlog.test",),
                ).fetchone()[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                               f"{label} Tenant", f"{label} Site")[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, suffix, *, stale=False):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,device_vendor,device_model,device_driver,
                         last_seen_at,enrolled_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         %s,'windows','5.1.2','Dahua','DH-XVR1B08-I','dahua-cgi',
                         case when %s then now()-interval '1 hour' else now() end,
                         case when %s then now()-interval '1 day' else now() end
                       ) returning id""",
                    (tenant_id, site_id, key, f"agent-{suffix}", stale, stale),
                ).fetchone()[0]

            def recorders(agent, key, rows):
                return anon_call("select wl_sync_recorders(%s,%s,%s::jsonb)", agent, key,
                                 json.dumps(rows))[0]

            def recorder_row(local_key, name, primary):
                return {"local_key": local_key, "display_name": name, "vendor": "Dahua",
                        "model": "DH-XVR1B08-I", "driver": "dahua-cgi",
                        "is_primary": primary, "is_configured": True}

            def cameras(agent, key, recorder, channels):
                return anon_call(
                    "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)", agent, key, recorder,
                    json.dumps([{"channel": c, "name": f"Camera {c}", "is_configured": True}
                                for c in channels]))[0]

            def rollup(recorder, agent):
                return cur.execute(
                    """select recording_state, recording_reason_code
                         from recorder_health where recorder_id=%s and agent_id=%s""",
                    (recorder, agent)).fetchone()

            def cam(camera_id):
                return cur.execute(
                    """select rec_current_state, rec_current_reason_code, latest_recording_at
                         from camera_health where camera_id=%s""", (camera_id,)).fetchone()

            def set_video(camera_id, tenant, site, state, reason):
                cur.execute(
                    """insert into camera_health(camera_id,tenant_id,site_id,health_state,reason_code)
                       values (%s,%s,%s,%s,%s)
                       on conflict (camera_id) do update
                          set health_state=excluded.health_state,
                              reason_code=excluded.reason_code""",
                    (camera_id, tenant, site, state, reason))

            # ---- fixtures ------------------------------------------------------------------
            ua, ta, sa = bootstrap("rst-a")
            key_a = "rst-agent-a"
            agent_a = add_agent(ta, sa, key_a, "a")
            mapping = recorders(agent_a, key_a, [recorder_row("rec-1", "Front recorder", True),
                                                 recorder_row("rec-2", "Back recorder", False),
                                                 recorder_row("rec-3", "Spare recorder", False)])
            rec1, rec2, rec3 = mapping["rec-1"], mapping["rec-2"], mapping["rec-3"]
            cams1 = cameras(agent_a, key_a, rec1, ["1", "2", "3"])
            cams2 = cameras(agent_a, key_a, rec2, ["1"])

            ub, tb, sb = bootstrap("rst-b")
            key_b = "rst-agent-b"
            agent_b = add_agent(tb, sb, key_b, "b")
            rec_b = recorders(agent_b, key_b, [recorder_row("rec-b", "B recorder", True)])["rec-b"]

            healthy = {"nvr": {"reachable": True, "auth_ok": True, "reason": "ok",
                               "state": "operational"},
                       "channels": {"enumerated": True,
                                    "reported": [{"channel": c, "enabled": True}
                                                 for c in ("1", "2", "3")]}}
            anon_call("select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                      agent_a, key_a, rec1, json.dumps(healthy))

            disks = json.dumps({"state": "ok", "reason": "ok", "disks": [
                {"id": "/dev/sda", "path": "/dev/sda1", "type": "ReadWrite", "state": "ok",
                 "reason": "ok", "total_bytes": TB, "free_bytes": TB - 612408090624},
                {"id": "/dev/sdb", "path": "/dev/sdb1", "type": "ReadWrite", "state": "ok",
                 "reason": "ok", "total_bytes": TB, "free_bytes": TB // 2}]})

            # ---- disk RPC authorisation -----------------------------------------------------
            _, err = as_anon_try(DISK_RPC, agent_a, "wrong-key", rec1, disks)
            step(err.startswith("28000"), "disk report with a wrong agent key is refused", err)
            _, err = as_anon_try(DISK_RPC, agent_a, key_a, rec_b, disks)
            step(err.startswith("42501"), "disk report for another site's recorder is refused", err)
            unknown = cur.execute("select gen_random_uuid()").fetchone()[0]
            _, err = as_anon_try(DISK_RPC, agent_a, key_a, unknown, disks)
            step(err.startswith("42501"), "disk report for an unknown recorder is refused", err)
            _, err = as_anon_try(DISK_RPC, agent_a, key_a, None, disks)
            step(err.startswith("42501"), "disk report without a recorder is refused", err)
            key_old = "rst-agent-a-old"
            agent_old = add_agent(ta, sa, key_old, "a-old", stale=True)
            _, err = as_anon_try(DISK_RPC, agent_old, key_old, rec1, disks)
            step(err.startswith("42501"), "a non-current Agent of the same site is refused", err)
            written = cur.execute(
                "select count(*) from recorder_storage_disks where recorder_id in (%s,%s)",
                (rec1, rec_b)).fetchone()[0]
            step(written == 0, "refused reports wrote nothing", str(written))

            # ---- disk RPC success, sanitising, missing disks ---------------------------------
            out, err = as_anon_try(DISK_RPC, agent_a, key_a, rec1, disks)
            res = out[0] if out else {}
            step(err == "" and res.get("ok") is True and res.get("disks_reported") == 2,
                 "a good disk report is stored", err or json.dumps(res))
            rows = cur.execute(
                """select disk_id, disk_path, state, total_bytes, free_bytes, present, agent_id
                     from recorder_storage_disks where recorder_id=%s order by disk_id""",
                (rec1,)).fetchall()
            step([(r[0], r[2], r[3], r[5]) for r in rows]
                 == [("/dev/sda", "ok", TB, True), ("/dev/sdb", "ok", TB, True)]
                 and all(r[6] == agent_a for r in rows),
                 "per-disk rows carry state, capacity and the reporting Agent", str(rows))
            totals = cur.execute(
                """select storage_total_bytes, storage_free_bytes, storage_disk_count
                     from recorder_health where recorder_id=%s and agent_id=%s""",
                (rec1, agent_a)).fetchone()
            step(totals == (2 * TB, (TB - 612408090624) + TB // 2, 2),
                 "recorder capacity totals are the sum of its disks", str(totals))

            bad = json.dumps({"disks": [
                {"id": "/dev/sda", "state": "exploded", "reason": "weird",
                 "total_bytes": 100, "free_bytes": 200},
                {"id": "/dev/sdb", "state": "fault", "reason": "disk_error",
                 "total_bytes": -5, "free_bytes": 1},
                {"id": "", "state": "ok"}, "not-an-object"]})
            out, err = as_anon_try(DISK_RPC, agent_a, key_a, rec1, bad)
            rows = {r[0]: r[1:] for r in cur.execute(
                """select disk_id, state, reason_code, total_bytes, free_bytes
                     from recorder_storage_disks where recorder_id=%s""", (rec1,)).fetchall()}
            step(err == "" and rows.get("/dev/sda") == ("unknown", "unknown", None, None)
                 and rows.get("/dev/sdb") == ("fault", "disk_error", None, None)
                 and len(rows) == 2,
                 "unrecognised states and inconsistent sizes are stored as unknown, never trusted",
                 err or str(rows))

            one = json.dumps({"disks": [{"id": "/dev/sda", "state": "ok", "reason": "ok",
                                         "total_bytes": TB, "free_bytes": TB // 4}]})
            out, err = as_anon_try(DISK_RPC, agent_a, key_a, rec1, one)
            rows = {r[0]: r[1:] for r in cur.execute(
                """select disk_id, present, state, reason_code
                     from recorder_storage_disks where recorder_id=%s""", (rec1,)).fetchall()}
            step(err == "" and out[0]["disks_not_reported"] == 1
                 and rows["/dev/sda"] == (True, "ok", "ok")
                 and rows["/dev/sdb"] == (False, "unknown", "disk_not_reported"),
                 "a disk missing from a later report is kept as not present and unknown",
                 err or str(rows))

            out, err = as_anon_try(DISK_RPC, agent_a, key_a, rec1, json.dumps({"state": "ok"}))
            still = cur.execute("select count(*) from recorder_storage_disks where recorder_id=%s",
                                (rec1,)).fetchone()[0]
            step(err == "" and out[0]["ok"] is False and still == 2,
                 "a report without a disk list changes nothing", err or json.dumps(out[0]))

            seen_b = as_auth(ub, "select count(*) from recorder_storage_disks")[0]
            seen_a = as_auth(ua, "select count(*) from recorder_storage_disks")[0]
            step(seen_b == 0 and seen_a == 2,
                 "another tenant cannot read a recorder's disks", f"a={seen_a} b={seen_b}")

            acl = cur.execute(
                """select has_function_privilege('anon',
                            'public.wl_report_recorder_storage_disks(uuid,text,uuid,jsonb)','execute'),
                          has_function_privilege('authenticated',
                            'public.wl_report_recorder_storage_disks(uuid,text,uuid,jsonb)','execute'),
                          has_function_privilege('anon',
                            'public.wl_rollup_recorder_recording(uuid,uuid)','execute')""").fetchone()
            step(acl == (True, False, False),
                 "disk RPC EXECUTE is anon-only; the rollup is internal", str(acl))

            # ---- rollup transitions ---------------------------------------------------------
            c1, c2, c3 = (cams1[c] for c in ("1", "2", "3"))
            out, err = as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok", "2026-01-02T10:04:00Z"),
                ("2", "recording", "ok"), ("3", "recording", "ok")]))
            step(err == "" and rollup(rec1, agent_a) == ("recording", "ok")
                 and out[0].get("recording_state") == "recording",
                 "every camera recording -> recorder recording", err or str(rollup(rec1, agent_a)))
            step(cam(c1)[2] == NEWEST, "latest_recording_at is stored with archive proof",
                 str(cam(c1)))

            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "not_recording", "no_recent_recording"),
                ("3", "recording", "ok")]))
            step(rollup(rec1, agent_a) == ("not_recording", "camera_not_recording")
                 and cam(c2)[:2] == ("not_recording", "no_recent_recording"),
                 "a live camera not recording -> recorder not_recording, reason kept",
                 str((rollup(rec1, agent_a), cam(c2))))

            fault = cur.execute(
                """select count(*) from operational_faults
                    where camera_id=%s and fault_type='not_recording' and state<>'resolved'""",
                (c2,)).fetchone()[0]
            step(fault == 1, "the not-recording camera raises its warning fault", str(fault))

            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "not_recording", "no_recent_recording"),
                ("3", "recording", "ok")], evidence="vendor_status"))
            step(cam(c2)[:2] == ("unknown", "unknown") and cam(c1)[:2] == ("unknown", "unknown")
                 and rollup(rec1, agent_a) == ("unknown", "not_verified"),
                 "without archive evidence neither recording nor no_recent_recording is accepted",
                 str((cam(c1), cam(c2), rollup(rec1, agent_a))))

            set_video(c2, ta, sa, "offline", "video_loss")
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "unknown", "video_loss"),
                ("3", "recording", "ok")]))
            step(rollup(rec1, agent_a) == ("recording", "video_loss_excluded"),
                 "a video-loss camera is excused, the rest recording -> recording",
                 str(rollup(rec1, agent_a)))
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "not_recording", "recording_disabled"),
                ("3", "recording", "ok")]))
            step(rollup(rec1, agent_a)[0] == "recording",
                 "a not_recording camera in video loss does not make the recorder not_recording",
                 str(rollup(rec1, agent_a)))
            set_video(c2, ta, sa, "operational", "ok")
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "not_recording", "recording_disabled"),
                ("3", "recording", "ok")]))
            step(rollup(rec1, agent_a)[0] == "not_recording"
                 and cam(c2)[1] == "recording_disabled",
                 "recording disabled on a live camera -> recorder not_recording",
                 str((rollup(rec1, agent_a), cam(c2))))

            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "unknown", "archive_search_failed"), ("2", "recording", "ok"),
                ("3", "recording", "ok")]))
            step(rollup(rec1, agent_a) == ("unknown", "not_verified")
                 and cam(c1)[1] == "archive_search_failed",
                 "one unknown camera keeps the recorder unknown, never recording",
                 str((rollup(rec1, agent_a), cam(c1))))

            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof(
                [("1", "storage_fault", "storage_fault"), ("2", "storage_fault", "storage_fault"),
                 ("3", "storage_fault", "storage_fault")], storage=("fault", "disk_error")))
            step(rollup(rec1, agent_a) == ("storage_fault", "storage_fault"),
                 "storage fault on every camera -> recorder storage_fault",
                 str(rollup(rec1, agent_a)))

            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "recording", "ok"), ("3", "recording", "ok")]))
            cur.execute("""update camera_health set rec_current_at=now()-interval '20 minutes'
                            where camera_id in (%s,%s)""", (c2, c3))
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([("1", "recording", "ok")]))
            step(rollup(rec1, agent_a) == ("unknown", "not_verified"),
                 "stale camera proof never counts as recording", str(rollup(rec1, agent_a)))

            out, err = as_anon_try(CURRENT_RPC, agent_a, key_a, rec2, proof([
                ("1", "not_recording", "no_recent_recording")]))
            step(err == "" and rollup(rec2, agent_a) == ("not_recording", "camera_not_recording")
                 and rollup(rec1, agent_a) == ("unknown", "not_verified")
                 and cam(cams2["1"])[0] == "not_recording" and cam(c1)[0] == "recording",
                 "recorders stay isolated: recorder 2's Channel 1 never touches recorder 1",
                 err or str((rollup(rec1, agent_a), rollup(rec2, agent_a))))

            out, err = as_anon_try(CURRENT_RPC, agent_a, key_a, rec3, proof([]))
            step(err == "" and rollup(rec3, agent_a) == ("unknown", "no_cameras"),
                 "a recorder without configured cameras is unknown", err or str(rollup(rec3, agent_a)))

            # ---- latest_recording_at rules --------------------------------------------------
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok", "2026-01-01T00:00:00Z"),
                ("2", "recording", "ok", "2099-01-01T00:00:00Z"),
                ("3", "not_recording", "no_recent_recording", "2026-01-03T00:00:00Z")]))
            step(cam(c1)[2] == NEWEST, "latest_recording_at never moves backwards", str(cam(c1)))
            step(cam(c2)[2] is None, "a future latest_recording_at is ignored", str(cam(c2)))
            step(cam(c3)[2] is None, "latest_recording_at needs a recording verdict", str(cam(c3)))

            # ---- reason codes ----------------------------------------------------------------
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof(
                [("1", "unknown", "no_recent_archive"), ("2", "unknown", "made_up_reason"),
                 ("3", "unknown", "video_loss")], storage=("unknown", "no_disks_reported")))
            sto = cur.execute(
                """select sto_current_state, sto_current_reason_code from recorder_health
                    where recorder_id=%s and agent_id=%s""", (rec1, agent_a)).fetchone()
            step(cam(c1)[1] == "no_recent_archive" and cam(c2)[1] == "unknown"
                 and cam(c3)[1] == "video_loss" and sto == ("unknown", "no_disks_reported"),
                 "5.1.2 reason codes are kept; an unknown reason becomes 'unknown'",
                 str((cam(c1), cam(c2), cam(c3), sto)))

            # ---- owner read model -------------------------------------------------------------
            def owner(recorder):
                rows = as_auth(ua, "select wl_my_site_recorders(%s)", sa)[0]["recorders"]
                return {str(r["id"]): r for r in rows}[str(recorder)]

            o = owner(rec1)
            step(o["state"] == "healthy" and o["recording_state"] == "unknown"
                 and o["storage_state"] == "unknown" and o["verified"] is False,
                 "a reachable recorder with unproven recording/storage is never verified",
                 json.dumps(o, default=str)[:300])
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "recording", "ok"), ("3", "recording", "ok")]))
            out, err = as_anon_try(DISK_RPC, agent_a, key_a, rec1, one)
            o = owner(rec1)
            step(o["recording_state"] == "recording" and o["storage_state"] == "ok"
                 and o["verified"] is True and o["storage_total_bytes"] == TB,
                 "storage ok and every camera recording -> verified, with capacity",
                 json.dumps(o, default=str)[:300])
            as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, proof([
                ("1", "recording", "ok"), ("2", "recording", "ok"), ("3", "recording", "ok")],
                storage=("degraded", "disk_full")))
            o = owner(rec1)
            step(o["state"] == "attention" and o["issue"] == "storage" and o["verified"] is False,
                 "a fresh low-space storage proof needs attention",
                 json.dumps(o, default=str)[:300])
            o2 = owner(rec2)
            step(o2["state"] == "unknown" and o2["verified"] is False,
                 "a recorder with no connectivity report is not healthy",
                 json.dumps(o2, default=str)[:300])
            payload = json.dumps(as_auth(ua, "select wl_my_site_recorders(%s)", sa)[0])
            step("/dev/sd" not in payload and "dahua" not in payload.lower(),
                 "the owner read model exposes no disk paths or vendor names")

            # ---- old Agent compatibility ------------------------------------------------------
            sigs = cur.execute(
                """select to_regprocedure('public.wl_report_recorder_recording_storage_current(uuid,text,uuid,jsonb)') is not null,
                          to_regprocedure('public.wl_report_recording_storage_current(uuid,text,jsonb)') is not null,
                          has_function_privilege('anon',
                            'public.wl_report_recorder_recording_storage_current(uuid,text,uuid,jsonb)','execute'),
                          has_function_privilege('anon',
                            'public.wl_report_recording_storage_current(uuid,text,jsonb)','execute')""").fetchone()
            step(sigs == (True, True, True, True),
                 "5.0.x / 5.1.1 current-proof signatures and grants are unchanged", str(sigs))

            old_shape = json.dumps({"storage": {"state": "unknown", "reason": "unknown"},
                                    "recording": {"supported": True, "channels": [
                                        {"channel": "1", "state": "recording", "reason": "ok"},
                                        {"channel": "2", "state": "recording", "reason": "ok"},
                                        {"channel": "3", "state": "recording", "reason": "ok"}]},
                                    "recording_evidence": "archive_search",
                                    "archive_window_start": "2026-10-07T09:50:00Z",
                                    "archive_window_end": "2026-10-07T10:00:00Z"})
            out, err = as_anon_try(CURRENT_RPC, agent_a, key_a, rec1, old_shape)
            step(err == "" and out[0]["ok"] is True and rollup(rec1, agent_a)[0] == "recording",
                 "a 5.1.1-shape payload on the recorder RPC still works and rolls up",
                 err or json.dumps(out[0], default=str)[:200])

            uc, tc, sc = bootstrap("rst-c")
            key_c = "rst-agent-c"
            agent_c = add_agent(tc, sc, key_c, "c")
            rec_c = cur.execute("select wl_legacy_recorder_for_agent(%s)", (agent_c,)).fetchone()[0]
            anon_call("select wl_sync_cameras(%s,%s,%s::jsonb)", agent_c, key_c,
                      json.dumps([{"channel": "1", "name": "Gate", "is_configured": True}]))
            out, err = as_anon_try(LEGACY_RPC, agent_c, key_c, json.dumps({
                "storage": {"state": "ok", "reason": "ok"},
                "recording": {"supported": True, "channels": [
                    {"channel": "1", "state": "recording", "reason": "ok"}]},
                "recording_evidence": "archive_search"}))
            nh = cur.execute("select sto_current_state from nvr_health where agent_id=%s",
                             (agent_c,)).fetchone()
            step(err == "" and out[0]["ok"] is True and nh == ("ok",)
                 and rollup(rec_c, agent_c) == ("recording", "ok"),
                 "the 0089 site-scoped RPC still works on a single-recorder site and rolls up",
                 err or str((out, nh, rollup(rec_c, agent_c))))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

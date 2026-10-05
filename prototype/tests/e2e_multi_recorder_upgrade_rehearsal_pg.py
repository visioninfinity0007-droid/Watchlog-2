#!/usr/bin/env python3
"""Production-order upgrade rehearsal for the multi-recorder chain (0146-0155).

Production runs 0001-0143 today (0144/0145 are reserved for production-only
portal migrations) with 5.0.x Agents writing data. The `integration` job applies
the whole chain to an EMPTY database, so no backfill there ever meets a real row.
This script reproduces the real upgrade on its OWN fresh, disposable database:

  stage 1  apply 0001..0143 with apply_migrations.py (WATCHLOG_MIGRATIONS_DIR
           pointed at a staged copy), exactly the production baseline;
  seed     5.0.x-shaped legacy data through the 5.0.x RPCs: tenant, site,
           enrolled Agent, 8 configured cameras plus 8 hidden non-canonical
           ONVIF profile rows (the Al-Khalid shape), events with site:channel
           dedupe keys, snapshots, nvr_health, and recovery_intervals in
           pending, in_progress and recovered states; a 5.0.x recorder push
           token (plus an older live duplicate), which must end up scoped to
           the site's recorder and keep working; plus a pre-provisioned
           site with no Agent or camera yet;
  stage 2  apply the rest (0146..0155) with the same runner and the real
           migrations directory, as the production deploy will;
  assert   camera UUIDs, events and snapshots preserved; exactly one recorder
           and one continuity owner per site; legacy 5.0.x payloads
           (wl_sync_cameras, wl_ingest_events, wl_heartbeat,
           wl_open_recovery_interval with uuid[], ...) still succeed on the
           single-recorder site; pending/in-progress recovery still claimable
           by the legacy Agent; recovered history still RECOVERED.

Steps marked "[gated: MNVR-015]" exercise the 0147 recovery_intervals backfill
(MNVR-015, owned by a later work package). They are expected to FAIL until that
fix lands and must stay in place so they gate it.

Disposable plain Postgres only (WATCHLOG_CI_PLAIN_POSTGRES=1, local host): the
script creates and drops its own database, <SUPABASE_DB_NAME>_upgrade_rehearsal,
next to SUPABASE_DB_NAME and never touches SUPABASE_DB_NAME itself.

    python prototype/tests/e2e_multi_recorder_upgrade_rehearsal_pg.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
APPLY = ROOT / "supabase" / "apply_migrations.py"
PRELUDE = ROOT / "supabase" / "ci_prelude.sql"
BASELINE_LAST = 143          # production baseline: 0001..0143
MNVR_015 = "[gated: MNVR-015] "

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


def dsn(dbname: str, autocommit: bool = False) -> dict:
    return dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=dbname,
        connect_timeout=30,
        autocommit=autocommit,
    )


def migration_number(path: Path) -> int:
    return int(path.name[:4])


def apply_migrations(dbname: str, migrations_dir: Path | None) -> str:
    env = dict(os.environ)
    env["SUPABASE_DB_NAME"] = dbname
    if migrations_dir is None:
        env.pop("WATCHLOG_MIGRATIONS_DIR", None)
    else:
        env["WATCHLOG_MIGRATIONS_DIR"] = str(migrations_dir)
    proc = subprocess.run([sys.executable, str(APPLY)], capture_output=True,
                          text=True, env=env, timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(f"apply_migrations.py failed (exit {proc.returncode}):\n{out[-4000:]}")
    return out


class Session:
    """RPC helpers on one connection to the rehearsal database."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def sql(self, query, *params):
        return self.cur.execute(query, params or None)

    def one(self, query, *params):
        return self.sql(query, *params).fetchone()

    def anon(self, query, *params):
        self.sql("savepoint rpc_sp")
        self.sql("set local role anon")
        try:
            return self.one(query, *params)
        finally:
            self.sql("reset role")
            self.sql("release savepoint rpc_sp")

    def anon_try(self, query, *params):
        """(row, message): a database error never aborts the transaction."""
        self.sql("savepoint rpc_try")
        self.sql("set local role anon")
        try:
            row = self.one(query, *params)
        except psycopg.Error as exc:
            self.sql("rollback to savepoint rpc_try")
            return None, str(exc).splitlines()[0]
        self.sql("reset role")
        self.sql("release savepoint rpc_try")
        return row, ""

    def member(self, uid, query, *params):
        self.sql("savepoint member_sp")
        self.sql("select set_config('request.jwt.claims',%s,true)",
                 json.dumps({"sub": str(uid), "role": "authenticated"}))
        self.sql("set local role authenticated")
        try:
            return self.one(query, *params)
        finally:
            self.sql("reset role")
            self.sql("release savepoint member_sp")


# 5.0.x ONVIF Agents report one row per encoding profile: transport channels
# 1..16 for 8 physical sources. Server-side 0117 keeps the last profile of each
# source canonical ("Camera N") and hides the other as a non-canonical row.
PROFILES = [
    {"channel": str(2 * n - 1 + i), "name": f"MediaProfile_Channel{n}_{kind}",
     "is_configured": True}
    for n in range(1, 9)
    for i, kind in enumerate(("MainStream", "SubStream"))
]
HEALTH = {
    "nvr": {"reachable": True, "auth_ok": True, "reason": "ok", "state": "operational",
            "vendor": "Hikvision", "model": "REHEARSAL-NVR", "firmware": "V4"},
    "channels": {"enumerated": True,
                 "reported": [{"channel": str(2 * n), "enabled": True} for n in range(1, 9)]},
}
CAPABILITIES = {"channels": [{"channel": str(2 * n), "snapshot": True} for n in range(1, 9)]}
LEGACY_EVENTS = [
    # MNVR-001 shape: 5.0.x ONVIF events land on transport channel "1".
    {"channel": "1", "event_type": "motion", "device_ts": "2026-09-20T08:00:00Z",
     "agent_ts": "2026-09-20T08:00:01Z", "snapshot_b64": "YWJj"},
    {"channel": "2", "event_type": "motion", "device_ts": "2026-09-20T08:05:00Z",
     "agent_ts": "2026-09-20T08:05:01Z", "snapshot_b64": "ZGVm"},
    {"channel": "16", "event_type": "line_crossing", "device_ts": "2026-09-20T08:10:00Z",
     "agent_ts": "2026-09-20T08:10:01Z"},
]
W_RECOVERED = ("2026-09-20T17:00:00Z", "2026-09-20T19:00:00Z")
W_IN_PROGRESS = ("2026-09-21T17:00:00Z", "2026-09-21T18:00:00Z")
W_PENDING = ("2026-09-22T17:00:00Z", "2026-09-22T17:30:00Z")
W_NEW = ("2026-10-01T17:00:00Z", "2026-10-01T17:10:00Z")
COVERAGE = ("2026-09-20T00:00:00Z", "2026-09-23T00:00:00Z")


def seed_legacy(s: Session) -> dict:
    """5.0.x production shape, written through the 5.0.x RPC surface."""
    uid = s.one("insert into auth.users(id,email) values (gen_random_uuid(),"
                "'rehearsal-owner@watchlog.test') returning id")[0]
    boot = s.member(uid, "select wl_bootstrap_tenant(%s,%s)",
                    "Rehearsal Security", "Rehearsal Warehouse")[0]
    tenant_id, site_id, code = boot["tenant_id"], boot["site_id"], boot["enrollment_code"]
    enrolled = s.anon("select wl_enroll(%s,%s,%s,%s,%s,%s,%s)",
                      code, "REHEARSAL-PC", "windows", "5.0.27",
                      "Hikvision", "REHEARSAL-NVR", "onvif")[0]
    agent_id, agent_key = enrolled["agent_id"], enrolled["agent_key"]
    # The Agent was enrolled months before the outages below.
    s.sql("update agents set enrolled_at='2026-05-01T00:00:00Z' where id=%s", agent_id)

    s.anon("select wl_heartbeat(%s,%s,%s,%s,%s,%s)", agent_id, agent_key,
           "5.0.27", "Hikvision", "REHEARSAL-NVR", "onvif")
    s.anon("select wl_sync_cameras(%s,%s,%s::jsonb)", agent_id, agent_key, json.dumps(PROFILES))
    camera_map = s.anon("select wl_sync_cameras(%s,%s,%s::jsonb)",
                        agent_id, agent_key, json.dumps(PROFILES))[0]
    s.anon("select wl_sync_capabilities(%s,%s,%s::jsonb)",
           agent_id, agent_key, json.dumps(CAPABILITIES))
    s.anon("select wl_report_health(%s,%s,%s::jsonb)", agent_id, agent_key, json.dumps(HEALTH))
    s.anon("select wl_ingest_events(%s,%s,%s::jsonb)",
           agent_id, agent_key, json.dumps(LEGACY_EVENTS))
    # Setup provisioned recorder push for this site (5.0.x site-level token).
    push_token = s.anon("select wl_agent_issue_push_token(%s,%s)",
                        agent_id, agent_key)[0]["token"]
    # An older live duplicate (two Setup runs racing): it must not block the
    # one-live-token-per-recorder index, and keeps working as a legacy token.
    dup_agent = s.one("""insert into agents(tenant_id,site_id,agent_key_hash,hostname,
                                            device_driver,last_seen_at)
                         values (%s,%s,md5(gen_random_uuid()::text),'Recorder push',
                                 'recorder-push',now()) returning id""",
                      tenant_id, site_id)[0]
    dup_token = s.one("""insert into push_sources(tenant_id,site_id,agent_id,created_at)
                         values (%s,%s,%s,now()-interval '1 day') returning token""",
                      tenant_id, site_id, dup_agent)[0]

    canonical = str(camera_map["2"])
    for start, end in (W_RECOVERED, W_IN_PROGRESS, W_PENDING):
        s.sql("""insert into agent_coverage_gaps(
                   tenant_id,site_id,agent_id,started_at,ended_at,cause,source
                 ) values (%s,%s,%s,%s,%s,'agent_restart','agent')""",
              tenant_id, site_id, agent_id, start, end)

    def open_interval(window):
        return s.anon("select wl_open_recovery_interval(%s,%s,%s,%s,%s::uuid[])",
                      agent_id, agent_key, window[0], window[1], [canonical])[0]["id"]

    recovered_id = open_interval(W_RECOVERED)
    s.anon("select wl_agent_claim_recovery(%s,%s,1,900)", agent_id, agent_key)
    s.anon("select wl_complete_recovery(%s,%s,%s,'recovered',12,%s::jsonb)",
           agent_id, agent_key, recovered_id, json.dumps({"cursor": "done"}))
    in_progress_id = open_interval(W_IN_PROGRESS)
    s.anon("select wl_agent_claim_recovery(%s,%s,1,900)", agent_id, agent_key)
    # The worker died mid-recovery an hour before the deploy.
    s.sql("update recovery_intervals set updated_at=now()-interval '1 hour' where id=%s",
          in_progress_id)
    pending_id = open_interval(W_PENDING)

    # A pre-provisioned site that has no Agent and no camera at deploy time.
    uid2 = s.one("insert into auth.users(id,email) values (gen_random_uuid(),"
                 "'rehearsal-new@watchlog.test') returning id")[0]
    boot2 = s.member(uid2, "select wl_bootstrap_tenant(%s,%s)",
                     "Rehearsal Newcomer", "Not Yet Installed")[0]
    return {
        "uid": uid, "tenant_id": tenant_id, "site_id": site_id,
        "agent_id": agent_id, "agent_key": agent_key, "camera_map": camera_map,
        "recovered_id": recovered_id, "in_progress_id": in_progress_id,
        "pending_id": pending_id, "push_token": push_token, "dup_token": dup_token,
        "new_site_id": boot2["site_id"],
        "new_site_code": boot2["enrollment_code"],
    }


def snapshot_state(s: Session, seed: dict) -> dict:
    site = seed["site_id"]
    return {
        "cameras": {str(r[0]): (r[1], r[2], r[3], r[4], r[5]) for r in s.sql(
            """select id,channel,physical_channel,name,is_canonical,is_configured
                 from cameras where site_id=%s""", site).fetchall()},
        "events": {r[0]: (str(r[1]) if r[1] else None, r[2]) for r in s.sql(
            "select id,camera_id,dedupe_key from events where site_id=%s", site).fetchall()},
        "snapshots": {r[0]: (r[1], str(r[2]) if r[2] else None) for r in s.sql(
            "select id,event_id,camera_id from snapshots where site_id=%s", site).fetchall()},
        "intervals": {str(r[0]): r[1] for r in s.sql(
            "select id,status from recovery_intervals where site_id=%s", site).fetchall()},
        "nvr_health": s.one(
            "select nvr_reachable,nvr_auth_ok from nvr_health where agent_id=%s",
            seed["agent_id"]),
    }


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("FATAL: disposable plain-Postgres rehearsal only (set WATCHLOG_CI_PLAIN_POSTGRES=1).")
    if ENV.get("SUPABASE_DB_HOST") not in ("localhost", "127.0.0.1", "::1"):
        sys.exit("FATAL: the rehearsal creates and drops a database; local Postgres only.")

    admin_db = ENV.get("SUPABASE_DB_NAME", "postgres")
    rehearsal_db = f"{admin_db}_upgrade_rehearsal"
    admin = psycopg.connect(**dsn(admin_db, autocommit=True))
    admin.execute(f'drop database if exists "{rehearsal_db}" with (force)')
    admin.execute(f'create database "{rehearsal_db}"')
    stage = Path(tempfile.mkdtemp(prefix="wl-rehearsal-"))
    try:
        with psycopg.connect(**dsn(rehearsal_db, autocommit=True)) as c:
            c.execute(PRELUDE.read_text(encoding="utf-8"))

        # ---------------- stage 1: production baseline 0001..0143 ----------------
        baseline = [p for p in sorted(MIGRATIONS.glob("*.sql"))
                    if migration_number(p) <= BASELINE_LAST]
        pending = [p for p in sorted(MIGRATIONS.glob("*.sql"))
                   if migration_number(p) > BASELINE_LAST]
        for p in baseline:
            shutil.copy2(p, stage / p.name)
        out1 = apply_migrations(rehearsal_db, stage)
        step(f"{len(baseline)} migration(s) executed" in out1
             and baseline[-1].name == "0143_site_period_facts.sql"
             and pending and migration_number(pending[0]) == 146,
             "stage 1 applies exactly the 0001..0143 production baseline",
             f"{len(baseline)} baseline, {len(pending)} pending")

        with psycopg.connect(**dsn(rehearsal_db)) as conn:
            s = Session(conn)
            seed = seed_legacy(s)
            before = snapshot_state(s, seed)
            pre_cov = s.one("select wl_site_coverage_report_classes(%s,%s,%s)",
                            seed["site_id"], *COVERAGE)[0]["classes"]
            conn.commit()
        step(len(before["cameras"]) == 16
             and sum(1 for v in before["cameras"].values() if v[3]) == 8
             and len(before["events"]) == 3 and len(before["snapshots"]) == 2
             and sorted(before["intervals"].values())
             == ["in_progress", "pending", "recovered"]
             and before["nvr_health"] == (True, True)
             and pre_cov["recovered_seconds"] > 0,
             "5.0.x legacy data seeded through the 5.0.x RPCs",
             json.dumps({"cameras": len(before["cameras"]), "events": len(before["events"]),
                         "intervals": before["intervals"], "recovered": pre_cov})[:400])

        # ---------------- stage 2: the multi-recorder chain ----------------
        out2 = apply_migrations(rehearsal_db, None)
        ran = re.findall(r"apply\s+(\S+\.sql)", out2)
        step(ran == [p.name for p in pending]
             and f"{len(pending)} migration(s) executed" in out2,
             "stage 2 applies exactly the pending 0146..0155 chain with the same runner",
             ", ".join(ran))

        with psycopg.connect(**dsn(rehearsal_db)) as conn:
            s = Session(conn)
            site, agent, key = seed["site_id"], seed["agent_id"], seed["agent_key"]
            after = snapshot_state(s, seed)

            recorders = s.sql(
                """select id,local_key,is_primary,is_configured,continuity_owner,vendor,model
                     from recorders where site_id=%s""", site).fetchall()
            rec_id = recorders[0][0] if recorders else None
            step(len(recorders) == 1 and recorders[0][1:5] == ("legacy-default", True, True, True),
                 "upgraded site has exactly one recorder: configured primary continuity owner",
                 str(recorders))
            step(after["cameras"] == before["cameras"]
                 and s.one("""select count(*) from cameras
                               where site_id=%s and recorder_id is distinct from %s""",
                           site, rec_id)[0] == 0,
                 "all 16 camera UUIDs (8 canonical + 8 hidden profiles) preserved on that recorder")
            step(after["events"] == before["events"]
                 and s.one("""select count(*) from events
                               where site_id=%s and camera_id is not null
                                 and recorder_id is distinct from %s""", site, rec_id)[0] == 0,
                 "events keep IDs, cameras and site:channel dedupe keys; recorder backfilled")
            step(after["snapshots"] == before["snapshots"],
                 "snapshots preserved with their events and cameras")
            step(after["nvr_health"] == before["nvr_health"],
                 "nvr_health preserved for existing fault and read models")

            # Recovery history (before any post-upgrade write).
            post_cov = s.member(seed["uid"], "select wl_site_coverage_report_classes(%s,%s,%s)",
                                site, *COVERAGE)[0]
            post_classes = post_cov.get("classes") or {}
            step(post_classes.get("recovered_seconds") == pre_cov["recovered_seconds"],
                 MNVR_015 + "pre-upgrade recovered history is still RECOVERED in coverage",
                 f"before={pre_cov['recovered_seconds']} after={post_classes.get('recovered_seconds')}")
            claimed, msg = s.anon_try("select wl_agent_claim_recovery(%s,%s,5,900)", agent, key)
            claimed_ids = {str(x["id"]) for x in (claimed[0] if claimed else [])}
            step(claimed_ids == {str(seed["pending_id"]), str(seed["in_progress_id"])},
                 MNVR_015 + "legacy Agent can still claim pending and stale in-progress intervals",
                 msg or str(sorted(claimed_ids)))
            done, msg = s.anon_try(
                "select wl_complete_recovery(%s,%s,%s,'recovered',3,%s::jsonb)",
                agent, key, seed["in_progress_id"], json.dumps({"cursor": "done"}))
            step(bool(done) and done[0].get("ok") is True,
                 MNVR_015 + "in-flight legacy recovery can still complete after the upgrade",
                 msg or json.dumps(done[0] if done else None))
            reopen, msg = s.anon_try(
                "select wl_open_recovery_interval(%s,%s,%s,%s,%s::uuid[])",
                agent, key, W_PENDING[0], W_PENDING[1], [str(seed["camera_map"]["2"])])
            step(bool(reopen) and reopen[0].get("duplicate") is True
                 and str(reopen[0].get("id")) == str(seed["pending_id"]),
                 MNVR_015 + "re-reporting a pre-upgrade outage window stays idempotent",
                 msg or json.dumps(reopen[0] if reopen else None))

            # Legacy 5.0.x payloads on the single-recorder site.
            beat, msg = s.anon_try("select wl_heartbeat(%s,%s,%s,%s,%s,%s)", agent, key,
                                   "5.0.27", "Hikvision", "REHEARSAL-NVR", "onvif")
            step(bool(beat) and beat[0].get("ok") is True, "legacy wl_heartbeat succeeds", msg)
            cams, msg = s.anon_try("select wl_sync_cameras(%s,%s,%s::jsonb)",
                                   agent, key, json.dumps(PROFILES))
            step(bool(cams) and {k: str(v) for k, v in cams[0].items()}
                 == {k: str(v) for k, v in seed["camera_map"].items()},
                 "legacy wl_sync_cameras (16 ONVIF profiles) returns the same camera UUIDs", msg)
            caps, msg = s.anon_try("select wl_sync_capabilities(%s,%s,%s::jsonb)",
                                   agent, key, json.dumps(CAPABILITIES))
            step(bool(caps) and caps[0].get("ok") is True
                 and str(caps[0].get("recorder_id")) == str(rec_id),
                 "legacy wl_sync_capabilities succeeds and mirrors to the recorder", msg)
            health, msg = s.anon_try("select wl_report_health(%s,%s,%s::jsonb)",
                                     agent, key, json.dumps(HEALTH))
            step(bool(health) and health[0].get("ok") is True,
                 "legacy wl_report_health succeeds", msg)
            resent, msg = s.anon_try("select wl_ingest_events(%s,%s,%s::jsonb)",
                                     agent, key, json.dumps(LEGACY_EVENTS))
            step(bool(resent) and resent[0]["received"] == 3 and resent[0]["inserted"] == 0,
                 "legacy re-send of pre-upgrade events dedupes in the site:channel namespace",
                 msg or json.dumps(resent[0] if resent else None, default=str))
            fresh, msg = s.anon_try(
                "select wl_ingest_events(%s,%s,%s::jsonb)", agent, key,
                json.dumps([{"channel": "4", "event_type": "motion",
                             "device_ts": "2026-10-05T09:00:00Z",
                             "agent_ts": "2026-10-05T09:00:01Z", "snapshot_b64": "Z2hp"}]))
            fresh_row = s.one(
                """select e.recorder_id,e.camera_id,e.dedupe_key,s.camera_id
                     from events e left join snapshots s on s.event_id=e.id
                    where e.site_id=%s and e.device_ts='2026-10-05T09:00:00Z'""", site)
            legacy_key = s.one(
                "select wl_dedupe_key(%s,'4',null,'2026-10-05T09:00:00Z'::timestamptz,'motion')",
                site)[0]
            step(bool(fresh) and fresh[0]["inserted"] == 1 and fresh_row is not None
                 and str(fresh_row[0]) == str(rec_id)
                 and str(fresh_row[1]) == str(seed["camera_map"]["4"])
                 and fresh_row[2] == legacy_key and str(fresh_row[3]) == str(fresh_row[1]),
                 "legacy wl_ingest_events inserts on the recorder's camera with the legacy key",
                 msg or str(fresh_row))
            opened, msg = s.anon_try(
                "select wl_open_recovery_interval(%s,%s,%s,%s,%s::uuid[])",
                agent, key, W_NEW[0], W_NEW[1], [str(seed["camera_map"]["2"])])
            step(bool(opened) and opened[0].get("ok") is True
                 and opened[0].get("status") == "pending",
                 "legacy wl_open_recovery_interval with uuid[] opens a new window", msg)

            # Recorder push provisioned by 5.0.x is scoped to the site's recorder.
            src = s.one("select to_jsonb(p)->>'recorder_id', enabled from push_sources p "
                        "where token=%s", seed["push_token"])
            dup = s.one("select to_jsonb(p)->>'recorder_id', enabled from push_sources p "
                        "where token=%s", seed["dup_token"])
            step(src is not None and src[0] == str(rec_id) and src[1] is True
                 and dup == (None, True),
                 "the newest pre-upgrade push token is backfilled to the site's single "
                 "recorder; an older live duplicate stays a legacy token",
                 str((src, dup)))
            dup_push, msg = s.anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", seed["dup_token"],
                json.dumps([{"channel": "6", "event_type": "motion",
                             "device_ts": "2026-10-05T10:30:00Z"}]))
            step(bool(dup_push) and dup_push[0]["inserted"] == 1,
                 "the legacy duplicate still ingests while the site has one recorder", msg)
            pushed, msg = s.anon_try(
                "select wl_ingest_push(%s,%s::jsonb)", seed["push_token"],
                json.dumps([{"channel": "4", "event_type": "motion",
                             "device_ts": "2026-10-05T10:00:00Z", "snapshot_b64": "anps"}]))
            push_row = s.one(
                """select e.recorder_id,e.camera_id,e.dedupe_key,s.camera_id
                     from events e left join snapshots s on s.event_id=e.id
                    where e.site_id=%s and e.device_ts='2026-10-05T10:00:00Z'""", site)
            push_key = s.one(
                "select wl_dedupe_key(%s,'4',null,'2026-10-05T10:00:00Z'::timestamptz,'motion')",
                site)[0]
            step(bool(pushed) and pushed[0]["inserted"] == 1 and push_row is not None
                 and str(push_row[0]) == str(rec_id)
                 and str(push_row[1]) == str(seed["camera_map"]["4"])
                 and push_row[2] == push_key and str(push_row[3]) == str(push_row[1]),
                 "the pre-upgrade push token still ingests on the recorder's camera, legacy key",
                 msg or str(push_row))
            reissued, msg = s.anon_try("select wl_agent_issue_push_token(%s,%s)", agent, key)
            step(bool(reissued) and reissued[0].get("token") == seed["push_token"],
                 "the 5.0.x token RPC returns the same token after the upgrade", msg)

            # A site first contacted after the upgrade gets its owner lazily.
            enrolled, msg = s.anon_try("select wl_enroll(%s,%s,%s,%s,%s,%s,%s)",
                                       seed["new_site_code"], "NEW-PC", "windows", "5.0.27",
                                       "Dahua", "NEW-NVR", "onvif")
            new_cams, msg2 = (None, "")
            if enrolled:
                new_cams, msg2 = s.anon_try(
                    "select wl_sync_cameras(%s,%s,%s::jsonb)",
                    enrolled[0]["agent_id"], enrolled[0]["agent_key"],
                    json.dumps([{"channel": "1", "name": "Camera 1", "is_configured": True}]))
            new_rec = s.sql("""select local_key,continuity_owner from recorders
                                where site_id=%s""", seed["new_site_id"]).fetchall()
            step(bool(new_cams) and new_rec == [("legacy-default", True)],
                 "a site first seen after the upgrade gets a lazily created continuity owner",
                 msg or msg2 or str(new_rec))

            bad = s.sql("""select site_id, count(*) filter (where continuity_owner)
                             from recorders group by site_id
                           having count(*) filter (where continuity_owner) <> 1""").fetchall()
            step(bad == [], "every site with recorders has exactly one continuity owner", str(bad))
            conn.rollback()
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        admin.execute(f'drop database if exists "{rehearsal_db}" with (force)')
        admin.close()

    passed = sum(1 for s_ in STEPS if s_)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

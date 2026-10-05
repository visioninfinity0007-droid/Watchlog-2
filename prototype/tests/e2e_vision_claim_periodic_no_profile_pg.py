#!/usr/bin/env python3
"""0156 section 4: periodic stills of cameras without a restaurant profile are claimed.

Before 0156 the wl_vision_claim_snapshots_v2 filter `not (rp.sampling_mode='event' and
source='periodic_snapshot')` was NULL for a camera with no enabled restaurant profile, so
`not NULL` dropped the row and office periodic stills stayed pending forever (all of
HASCO Head Office's in production on 2026-10-05). Event-mode restaurant cameras must
still skip their periodic stills.

Those stranded rows kept their insert-time next_attempt_at, so the claim (oldest
next_attempt_at first, at most 4 per worker run) would serve the whole backlog before
any fresh still from any site. Applying 0156 must retire that backlog, so a fresh
still is claimed in the first worker run after the apply.

Runs only against disposable/test Postgres; everything is rolled back.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0156 = ROOT / "supabase" / "migrations" / "0156_production_truth_hotfix.sql"
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
# Smallest well-formed JPEG: SOI, a comment segment, EOI.
JPEG = bytes.fromhex("ffd8fffe0004e2e2ffd9")


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def still(channel: str, n: int, source: str = "periodic_snapshot") -> dict:
    return {"channel": channel, "event_type": "visual_sample",
            "device_event_id": f"hikvision-sample-{channel}-{59700000 + n}",
            "device_ts": f"2026-10-05T0{n}:00:00Z", "agent_ts": f"2026-10-05T0{n}:00:00Z",
            "payload": {"sample": True, "source": source, "vendor": "hikvision"},
            "snapshot_b64": base64.b64encode(JPEG).decode()}


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        print("refusing: set WATCHLOG_CI_PLAIN_POSTGRES=1 (disposable Postgres only)")
        return 1
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    key = "vision-claim-no-profile-e2e-key"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            fn = cur.execute("select pg_get_functiondef('public.wl_vision_claim_snapshots_v2(integer,text,boolean)'::regprocedure)").fetchone()[0]
            step("coalesce(rp.sampling_mode,'')='event'" in fn,
                 "the latest claim function compares the profile sampling mode null-safely")

            tid = cur.execute("insert into tenants (name) values ('vision-claim-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'office','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            office = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Reception') returning id", (tid, sid)).fetchone()[0]
            event_cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'2','Back Entrance') returning id", (tid, sid)).fetchone()[0]
            cur.execute("""insert into restaurant_camera_profiles
                             (camera_id,tenant_id,site_id,analytics_role,sampling_mode,interval_seconds)
                           values (%s,%s,%s,'service_access','event',null)""", (event_cam, tid, sid))
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, key)).fetchone()[0]
            rows = [still("1", 1), still("1", 2), still("2", 3), still("2", 4, "manual_snapshot")]
            r = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent, key, json.dumps(rows))).fetchone()[0]
            step(r.get("inserted") == 4 and r.get("snapshots") == 4, "four stills ingested with images", str(r))
            ids = {did: eid for eid, did in cur.execute(
                "select id, device_event_id from events where site_id=%s", (sid,)).fetchall()}

            cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"role": "service_role"}),))
            claimed = set()
            for _ in range(10):
                batch = cur.execute("select wl_vision_claim_snapshots_v2(4, 'no-profile-e2e', false)").fetchone()[0] or []
                if not batch:
                    break
                claimed |= {int(b["event_id"]) for b in batch if str(b.get("site_id")) == str(sid)}
            office_ids = {ids[rows[0]["device_event_id"]], ids[rows[1]["device_event_id"]]}
            step(office_ids <= claimed,
                 "periodic stills of a camera with no restaurant profile are claimed for review",
                 f"{len(office_ids & claimed)}/2")
            step(ids[rows[2]["device_event_id"]] not in claimed,
                 "periodic stills of an event-mode restaurant camera are still skipped")
            step(ids[rows[3]["device_event_id"]] in claimed,
                 "a non-periodic still on the event-mode camera is still claimed")

            # Backlog: periodic stills the pre-0156 filter stranded, queued days ago.
            backlog_rows = [still("1", n) for n in range(3, 10)]
            r = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent, key, json.dumps(backlog_rows))).fetchone()[0]
            step(r.get("snapshots") == len(backlog_rows), "seven backlog stills ingested", str(r))
            ids = {did: eid for eid, did in cur.execute(
                "select id, device_event_id from events where site_id=%s", (sid,)).fetchall()}
            backlog = [ids[b["device_event_id"]] for b in backlog_rows]
            cur.execute("""update snapshot_visual_reviews
                              set captured_at=now() - interval '10 days', next_attempt_at=now() - interval '10 days'
                            where event_id = any(%s)""", (backlog,))
            # A fresh restaurant still at another tenant (the Chai Wala shape).
            tid_b = cur.execute("insert into tenants (name) values ('vision-claim-e2e-restaurant') returning id").fetchone()[0]
            sid_b = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'restaurant','Asia/Karachi') returning id", (tid_b,)).fetchone()[0]
            dining = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Dining') returning id", (tid_b, sid_b)).fetchone()[0]
            cur.execute("""insert into restaurant_camera_profiles
                             (camera_id,tenant_id,site_id,analytics_role,sampling_mode,interval_seconds)
                           values (%s,%s,%s,'dining_floor','interval',60)""", (dining, tid_b, sid_b))
            key_b = key + "-restaurant"
            agent_b = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                     values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                  (tid_b, sid_b, key_b)).fetchone()[0]
            fresh_row = still("1", 5, "restaurant_requested_snapshot")
            cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent_b, key_b, json.dumps([fresh_row])))
            fresh = cur.execute("select id from events where site_id=%s and device_event_id=%s",
                                (sid_b, fresh_row["device_event_id"])).fetchone()[0]
            # Work left by other tests on this database must not decide the first batch.
            cur.execute("""update snapshot_visual_reviews set next_attempt_at=now() + interval '1 day'
                            where site_id <> all(%s) and status in ('pending','failed','processing')""",
                        ([sid, sid_b],))

            # Apply 0156 (idempotent) over the stranded backlog, then one worker run.
            cur.execute(MIGRATION_0156.read_text(encoding="utf-8"))
            cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"role": "service_role"}),))
            first = cur.execute("select wl_vision_claim_snapshots_v2(4, 'no-profile-e2e', false)").fetchone()[0] or []
            first_ids = [int(b["event_id"]) for b in first]
            step(fresh in first_ids,
                 "a fresh restaurant still is claimed in the first worker run after 0156, ahead of the backlog",
                 f"first batch {first_ids}")
            states = cur.execute("""select status, attempts, analysis is null, coalesce(last_error,'')
                                      from snapshot_visual_reviews where event_id = any(%s)""", (backlog,)).fetchall()
            step(len(states) == len(backlog) and all(
                     st == "failed" and att >= 5 and no_analysis and "0156" in err
                     for st, att, no_analysis, err in states),
                 "the stranded backlog is retired as not reviewed (failed, attempts>=5, no analysis, reason names 0156)",
                 str(sorted(set((st, att, err[:60]) for st, att, _, err in states))))
            kept = dict(cur.execute("select event_id, status from snapshot_visual_reviews where event_id = any(%s)",
                                    ([ids[rows[0]["device_event_id"]], ids[rows[2]["device_event_id"]]],)).fetchall())
            step(kept.get(ids[rows[0]["device_event_id"]]) == "processing",
                 "a still already under review (live lease) is not retired")
            step(kept.get(ids[rows[2]["device_event_id"]]) == "pending",
                 "periodic stills of an event-mode restaurant camera are not touched")
            later = still("1", 0)
            cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent, key, json.dumps([later])))
            later_id = cur.execute("select id from events where site_id=%s and device_event_id=%s",
                                   (sid, later["device_event_id"])).fetchone()[0]
            claimed_later = set()
            for _ in range(10):
                batch = cur.execute("select wl_vision_claim_snapshots_v2(4, 'no-profile-e2e', false)").fetchone()[0] or []
                if not batch:
                    break
                claimed_later |= {int(b["event_id"]) for b in batch}
            step(later_id in claimed_later,
                 "a periodic still of a camera with no profile queued after 0156 is still claimed")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

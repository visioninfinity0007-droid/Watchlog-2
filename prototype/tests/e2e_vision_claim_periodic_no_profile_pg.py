#!/usr/bin/env python3
"""0156 section 4: periodic stills of cameras without a restaurant profile are claimed.

Before 0156 the wl_vision_claim_snapshots_v2 filter `not (rp.sampling_mode='event' and
source='periodic_snapshot')` was NULL for a camera with no enabled restaurant profile, so
`not NULL` dropped the row and office periodic stills stayed pending forever (all of
HASCO Head Office's in production on 2026-10-05). Event-mode restaurant cameras must
still skip their periodic stills.

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
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

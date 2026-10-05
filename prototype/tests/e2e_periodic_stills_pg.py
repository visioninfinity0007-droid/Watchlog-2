#!/usr/bin/env python3
"""Periodic stills from the canonical Agent reach the canonical consumers (NEW-L2; rolled back).

The rows are produced by the real periodic_stills worker (fake recorder, real SQLite spool),
then sent to the latest wl_ingest_events on real Postgres, exactly as upload_once sends them:

  * every row is accepted, lands on its camera (by channel) as event_type 'visual_sample'
    with payload source 'periodic_snapshot', and the dedupe key is site:channel:device_event_id
    (the production format);
  * the inline still becomes the event's snapshot row (captured_at = device_ts) and the
    snapshot trigger queues it for visual review;
  * re-sending the batch (an upload retried after a lost reply) inserts nothing;
  * wl_vision_claim_snapshots_v2 (0125/0126) selects the periodic stills of an interval-mode
    camera and skips them for an event-mode camera, while a non-periodic still on that
    event-mode camera is still selected: the consumer keys on payload source 'periodic_snapshot'.

KNOWN DB GAP (reported, not fixed here: this branch carries no migrations): for a camera with
NO enabled restaurant profile (every office camera, e.g. HASCO Head Office) the 0125/0126
filter `not (rp.sampling_mode='event' and ...)` evaluates to NULL, so its periodic stills are
never claimed. Production shows the same: all 252 HASCO periodic stills are still 'pending'.
This test prints that behaviour as INFO and proves the cause is the filter, not the payload: a
non-periodic still on the same camera is selected.

    python prototype/tests/e2e_periodic_stills_pg.py
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import periodic_stills  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import Channel  # noqa: E402
from spool import Spool  # noqa: E402

ENV = {}
for line in ((ROOT.parent / ".env").read_text(errors="ignore").splitlines()
             if (ROOT.parent / ".env").exists() else []):
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD",
          "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]
import psycopg  # noqa: E402

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


JPEG = b"\xff\xd8" + bytes(range(256)) * 120 + b"\xff\xd9"
BASE = datetime(2026, 10, 4, 22, 0, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def wall(self):
        return BASE + timedelta(seconds=self.t)


class _Stop:
    def __init__(self, clock, seconds):
        self.clock, self.until = clock, seconds

    def is_set(self):
        return self.clock.t >= self.until

    def wait(self, seconds=None):
        self.clock.t += max(float(seconds or 0.0), 0.01)
        return self.is_set()


class _Recorder:
    name = "hikvision-isapi"

    def __init__(self, clock):
        self.clock = clock

    def list_channels(self):
        return [Channel("1", "Floor"), Channel("2", "Back Entrance"), Channel("3", "Kitchen")]

    def get_snapshot(self, channel):
        self.clock.t += 0.4
        return JPEG

    def close(self):
        pass


def _produce_rows(tmp: Path) -> list[dict]:
    """One cadence of the real worker into a real spool; return the rows upload_once sends."""
    core.log = lambda _m: None
    clock = _Clock()
    recorder = _Recorder(clock)
    spool = Spool(tmp / "spool.sqlite")
    cfg = SimpleNamespace(nvr_url="http://recorder.test", snapshots=True,
                          snapshot_min_interval=60, camera_profiles=[])
    try:
        periodic_stills.periodic_still_worker(
            cfg, spool, _Stop(clock, 400), None, open_driver=lambda _c: (recorder, None),
            clock=clock, wall=clock.wall, rng=random.Random(5))
        _ids, rows = spool.take(core.UPLOAD_BATCH)
    finally:
        spool.close()
    return rows


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    key = "periodic-stills-e2e-key"
    with tempfile.TemporaryDirectory() as tmp:
        rows = _produce_rows(Path(tmp))
    channels = sorted({r["channel"] for r in rows})
    step(channels == ["1", "2", "3"] and len(rows) >= 3,
         "the worker spooled periodic stills for the three configured cameras",
         f"{len(rows)} rows, channels {channels}")

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            fn = cur.execute("""select pg_get_functiondef('public.wl_vision_claim_snapshots_v2(integer,text,boolean)'::regprocedure)""").fetchone()[0]
            step("periodic_snapshot" in fn,
                 "the deployed claim function is the 0125/0126 consumer that filters periodic stills")

            tid = cur.execute("insert into tenants (name) values ('periodic-stills-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'periodic','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            cams = {}
            for ch, name in (("1", "Floor"), ("2", "Back Entrance"), ("3", "Kitchen")):
                cams[ch] = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,%s,%s) returning id",
                                       (tid, sid, ch, name)).fetchone()[0]
            cur.execute("""insert into restaurant_camera_profiles
                             (camera_id,tenant_id,site_id,analytics_role,sampling_mode,interval_seconds)
                           values (%s,%s,%s,'service_access','event',null),
                                  (%s,%s,%s,'kitchen','interval',120)""",
                        (cams["2"], tid, sid, cams["3"], tid, sid))
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, key)).fetchone()[0]

            r1 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, key, json.dumps(rows))).fetchone()[0]
            step(r1.get("inserted") == len(rows) and r1.get("snapshots") == len(rows),
                 "wl_ingest_events accepts every periodic still with its image", str(r1))

            stored = cur.execute("""select e.id, e.camera_id, e.event_type, e.payload, e.dedupe_key,
                                           e.device_event_id, e.device_ts, s.bytes, s.captured_at,
                                           s.camera_id, s.content_type, r.status
                                      from events e
                                      left join snapshots s on s.event_id=e.id
                                      left join snapshot_visual_reviews r on r.event_id=e.id
                                     where e.site_id=%s order by e.id""", (sid,)).fetchall()
            step(len(stored) == len(rows), "one event per still", str(len(stored)))
            by_cam = {v: k for k, v in cams.items()}
            ok_rows = []
            for (eid, cam, etype, payload, dedupe, devid, dts, nbytes, cap, scam, ctype, rstatus) in stored:
                ch = by_cam.get(cam)
                ok_rows.append(
                    ch is not None and etype == "visual_sample"
                    and payload == {"sample": True, "source": "periodic_snapshot", "vendor": "hikvision"}
                    and re.fullmatch(rf"hikvision-sample-{ch}-\d+", devid or "") is not None
                    and dedupe == f"{sid}:{ch}:{devid}"
                    and nbytes == len(JPEG) and cap == dts and scam == cam
                    and ctype == "image/jpeg" and rstatus == "pending")
            step(ok_rows and all(ok_rows),
                 "each lands on its camera with the production payload, dedupe key "
                 "site:channel:device_event_id, a JPEG snapshot row at device_ts, and a pending review",
                 str(stored[0][2:7]) if stored else "none")

            r2 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, key, json.dumps(rows))).fetchone()[0]
            step(r2.get("inserted") == 0, "a retried upload of the same batch inserts nothing", str(r2))

            # A non-periodic still on the event-mode camera: the consumer must still select it.
            requested = {"channel": "2", "event_type": "visual_sample",
                         "device_event_id": "restaurant-request-e2e",
                         "device_ts": "2026-10-04T22:10:00Z", "agent_ts": "2026-10-04T22:10:00Z",
                         "payload": {"sample": True, "source": "restaurant_requested_snapshot"},
                         "snapshot_b64": rows[0]["snapshot_b64"]}
            cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent, key, json.dumps([requested])))
            requested_id = cur.execute("select id from events where site_id=%s and device_event_id='restaurant-request-e2e'",
                                       (sid,)).fetchone()[0]
            plain = dict(requested, channel="1", device_event_id="manual-still-e2e",
                         payload={"sample": True, "source": "manual_snapshot"})
            cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)", (agent, key, json.dumps([plain])))
            plain_id = cur.execute("select id from events where site_id=%s and device_event_id='manual-still-e2e'",
                                   (sid,)).fetchone()[0]

            cur.execute("select set_config('request.jwt.claims', %s, true)",
                        (json.dumps({"role": "service_role"}),))
            claimed = []
            for _ in range(10):
                batch = cur.execute("select wl_vision_claim_snapshots_v2(4, 'periodic-e2e', false)").fetchone()[0] or []
                if not batch:
                    break
                claimed.extend(batch)
            claimed_ids = {int(b["event_id"]) for b in claimed if str(b.get("site_id")) == str(sid)}
            periodic = {ch: {row[0] for row in stored if row[1] == cams[ch]} for ch in cams}
            no_profile = len(periodic["1"] & claimed_ids)
            print(f"  INFO  KNOWN DB GAP: periodic stills of a camera with no restaurant profile "
                  f"claimed {no_profile}/{len(periodic['1'])} (0125/0126 NULL-profile filter)")
            step(plain_id in claimed_ids,
                 "a non-periodic still on the no-profile camera is selected, so its periodic "
                 "stills are excluded by the consumer's filter, not by their payload")
            step(periodic["3"] and periodic["3"] <= claimed_ids,
                 "periodic stills of an interval-mode camera are selected for review")
            step(periodic["2"] and not (periodic["2"] & claimed_ids),
                 "periodic stills of an event-mode camera are skipped (source = periodic_snapshot)")
            step(requested_id in claimed_ids,
                 "a non-periodic still on that event-mode camera is still selected")
            images = [b for b in claimed if int(b["event_id"]) in periodic["3"]]
            step(images and all(b.get("bytes") == len(JPEG) and b.get("channel") == "3"
                                and b.get("image_b64") for b in images),
                 "the claimed periodic still carries its image and camera to the vision worker")
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

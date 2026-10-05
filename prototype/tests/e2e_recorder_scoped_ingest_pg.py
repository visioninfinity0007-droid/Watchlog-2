#!/usr/bin/env python3
"""Recorder-scoped live events ingest with NO camera (MNVR-028, server half; rolled back).

The driver tests prove a Dahua disk/alarm-input block and a Hikvision disk alert come out
with channel None plus recorder_scoped. This proves the row the packaged collector spools
for them (native_event_collector.spool_row) is accepted by wl_ingest_events on real
Postgres and lands correctly:

  * the recorder-scoped events are stored with camera_id NULL, never camera 1;
  * a camera event in the same batch still resolves to its camera;
  * the recorder_scoped flag survives in the stored payload;
  * re-ingesting the same batch is idempotent;
  * Event.to_json itself (the serialiser every spool path uses) writes the same JSON null, and
    an ONVIF storage fault serialised that way also lands with no camera.

    python prototype/tests/e2e_recorder_scoped_ingest_pg.py
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import native_event_collector  # noqa: E402
from drivers.native_recorder import NativeDahuaDriver, NativeHikvisionDriver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
import onvif_fake_recorder as onvif_fx  # noqa: E402

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


def _events():
    received = datetime(2026, 10, 4, 16, 0, 0, tzinfo=timezone.utc)
    dahua = NativeDahuaDriver("http://127.0.0.1", "admin", "x", timeout=1)
    hik = NativeHikvisionDriver("http://127.0.0.1", "admin", "x", timeout=1)
    try:
        dahua._received = (1000.0, received)
        disk = dahua._parse_line("Code=StorageLowSpace;action=Start;index=0")
        dahua._received = (1001.0, received)
        alarm = dahua._parse_line("Code=AlarmLocal;action=Start;index=3")
        dahua._received = (1002.0, received)
        motion = dahua._parse_line("Code=VideoMotion;action=Start;index=0")
        hik._received = (1003.0, received)
        hik_disk = hik._parse_alert(b"""<EventNotificationAlert><eventType>diskerror</eventType>
            <eventState>active</eventState><dateTime>2026-10-04T21:00:00+05:00</dateTime>
            <activePostCount>1</activePostCount></EventNotificationAlert>""")
    finally:
        dahua.close()
        hik.close()
    events = [disk, alarm, motion, hik_disk]
    assert all(e is not None for e in events)
    return events, received


def _onvif_storage_fault():
    """An ONVIF StorageFailure as the live ONVIF driver yields it (channel None)."""
    received = datetime(2026, 10, 4, 16, 0, 5, tzinfo=timezone.utc)
    onvif = OnvifDriver("http://127.0.0.1", "admin", "x", timeout=1)
    try:
        msg = onvif_fx.notification("tns1:Device/HardwareFailure/StorageFailure",
                                    "2026-10-04T16:00:05Z", {"Token": "HDD_1"},
                                    {"Failed": "true"})
        root = ET.fromstring(onvif_fx._ENV_OPEN + msg + onvif_fx._ENV_CLOSE)
        for elem in root.iter():
            elem.tag = elem.tag.split("}", 1)[-1]
        ev = onvif._parse_notification(root.find(".//NotificationMessage"), received)
    finally:
        onvif.close()
    assert ev is not None and ev.channel is None, ev
    return ev, received


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    key = "recorder-scope-e2e-key"
    events, received = _events()
    rows = [native_event_collector.spool_row(e, received) for e in events]
    step([r["channel"] for r in rows] == [None, None, "1", None],
         "spooled rows carry a JSON null channel for recorder-scoped events",
         str([r["channel"] for r in rows]))
    step([e.to_json(received) for e in events] == rows,
         "Event.to_json alone writes the same rows (JSON null, never the string 'None')",
         str([e.to_json(received)["channel"] for e in events]))
    onvif_event, onvif_received = _onvif_storage_fault()
    onvif_rows = [onvif_event.to_json(onvif_received)]
    step(onvif_rows[0]["channel"] is None,
         "an ONVIF storage fault serialises with a JSON null channel", str(onvif_rows[0]["channel"]))

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('rec-scope-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'scope','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            cam = cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Gate') returning id", (tid, sid)).fetchone()[0]
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, key)).fetchone()[0]

            r1 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, key, json.dumps(rows))).fetchone()[0]
            step(r1.get("inserted") == 4, "all four live events are accepted", str(r1))

            stored = cur.execute("""select event_type, camera_id, payload->>'recorder_scoped'
                                      from events where site_id=%s order by id""", (sid,)).fetchall()
            by_type = {}
            for etype, camera_id, scoped in stored:
                by_type.setdefault(etype, []).append((camera_id, scoped))
            step(by_type.get("disk_full") == [(None, "true")],
                 "Dahua disk event has no camera (not camera 1)", str(by_type.get("disk_full")))
            step(by_type.get("alarm_input") == [(None, "true")],
                 "Dahua alarm-input event has no camera (not camera 4)", str(by_type.get("alarm_input")))
            step(by_type.get("disk_error") == [(None, "true")],
                 "Hikvision disk alert has no camera", str(by_type.get("disk_error")))
            step(by_type.get("motion") == [(cam, None)],
                 "the camera event in the same batch still resolves to its camera",
                 str(by_type.get("motion")))

            r2 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, key, json.dumps(rows))).fetchone()[0]
            step(r2.get("inserted") == 0, "re-ingesting the same batch is idempotent", str(r2))

            r3 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, key, json.dumps(onvif_rows))).fetchone()[0]
            step(r3.get("inserted") == 1, "the ONVIF storage fault is accepted", str(r3))
            onvif_stored = cur.execute("""select camera_id, payload->>'recorder_scoped',
                                                  payload->>'clock_source' from events
                                           where site_id=%s and payload->>'vendor'='onvif'""",
                                       (sid,)).fetchall()
            step(onvif_stored == [(None, "true", "recorder")],
                 "the ONVIF storage fault has no camera (not camera 1) and keeps the same "
                 "recorder_scoped flag and clock provenance as the other drivers",
                 str(onvif_stored))
            none_rows = cur.execute("select count(*) from events where site_id=%s and "
                                    "dedupe_key like %s", (sid, "%:None:%")).fetchone()[0]
            step(none_rows == 0, "no stored dedupe key names a channel 'None'", str(none_rows))
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

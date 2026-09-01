#!/usr/bin/env python3
"""
Seed the DEMO tenant with clearly-tagged sample data for a live walkthrough.

Scope: touches ONLY the demo tenant (the one demo@watchlog.test belongs to).
Never AKSS or any real customer. Every seeded event carries payload
{"demo": true, "note": "SAMPLE - not real footage"} and the snapshots are
synthetic frames, so the data is unmistakably test data.

Idempotent: clears the demo tenant's seeded events/snapshots and re-inserts a
coherent recent set (a few days of accepted incidents across cameras, plus a
couple of equipment faults), sets the demo agent 'online' so the site reads
'ready', and leaves the tenant on trial so the demo can show the trial ->
checkout flow.

    python tools/seed_demo.py
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import re
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT / ".env").read_text(errors="ignore").splitlines():
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV[m.group(1)] = m.group(2).strip().strip('"').strip("'")
DSN = dict(host=ENV["SUPABASE_DB_HOST"], port=ENV["SUPABASE_DB_PORT"],
           user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
           dbname=ENV["SUPABASE_DB_NAME"], connect_timeout=30, autocommit=True)

DEMO_EMAIL = ENV.get("PORTAL_DEMO_EMAIL", "demo@watchlog.test")
CAMERAS = [("1", "Main Gate"), ("2", "Loading Bay"), ("3", "Rear Perimeter"), ("4", "Reception")]
# (hours_ago, channel, type)  — accepted incidents (person/vehicle/motorcycle) + faults
PLAN = [
    (2, "1", "person"), (3, "2", "vehicle"), (5, "1", "person"), (8, "4", "person"),
    (11, "2", "vehicle"), (14, "3", "person"), (20, "1", "motorcycle"), (26, "2", "vehicle"),
    (30, "3", "tamper"), (34, "4", "person"), (39, "1", "person"), (46, "2", "vehicle"),
    (52, "3", "person"), (55, "1", "motorcycle"), (61, "4", "person"), (68, "2", "vehicle"),
    (70, "3", "video_loss"), (73, "1", "person"),
]
FAULTS = {"tamper", "video_loss", "disk_error", "offline"}


def frame(rgb):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), rgb).save(buf, "JPEG")
    return buf.getvalue()


def main():
    conn = psycopg.connect(**DSN); cur = conn.cursor()
    # locate the demo tenant via the demo user's membership
    row = cur.execute("""select m.tenant_id from memberships m join auth.users u on u.id=m.user_id
                         where lower(u.email)=lower(%s) limit 1""", (DEMO_EMAIL,)).fetchone()
    if not row:
        raise SystemExit(f"no tenant for {DEMO_EMAIL}")
    tenant = row[0]
    cur.execute("update tenants set name='WatchLog Demo (sample data)' where id=%s and name<>'WatchLog Demo (sample data)'", (tenant,))
    site = cur.execute("select id from sites where tenant_id=%s order by created_at limit 1", (tenant,)).fetchone()[0]
    print("demo tenant:", tenant, "site:", site)

    # demo agent, shown online + ready
    ag = cur.execute("select id from agents where site_id=%s and hostname='DEMO-PC'", (site,)).fetchone()
    if ag:
        agent = ag[0]
        cur.execute("update agents set last_seen_at=now(), device_vendor='Dahua', device_model='DH-DEMO-8CH', device_driver='dahua-cgi' where id=%s", (agent,))
    else:
        agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash, hostname, platform,
                 agent_version, device_vendor, device_model, device_driver, enrolled_at, last_seen_at)
                 values (%s,%s, md5(gen_random_uuid()::text), 'DEMO-PC', 'Windows',
                 'demo', 'Dahua', 'DH-DEMO-8CH', 'dahua-cgi', now(), now()) returning id""", (tenant, site)).fetchone()[0]

    # cameras
    cam_id = {}
    for ch, name in CAMERAS:
        r = cur.execute("select id from cameras where site_id=%s and channel=%s", (site, ch)).fetchone()
        if r:
            cam_id[ch] = r[0]
            cur.execute("update cameras set name=%s where id=%s", (name, r[0]))
        else:
            cam_id[ch] = cur.execute("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,%s,%s) returning id", (tenant, site, ch, name)).fetchone()[0]

    # clear previously-seeded demo events (tagged) + their snapshots, reseed
    cur.execute("delete from snapshots where site_id=%s and event_id in (select id from events where site_id=%s and payload->>'demo'='true')", (site, site))
    cur.execute("delete from events where site_id=%s and payload->>'demo'='true'", (site,))

    colors = {"1": (60, 70, 95), "2": (70, 90, 70), "3": (95, 75, 60), "4": (80, 70, 95)}
    now = datetime.datetime.now(datetime.timezone.utc)
    n_ev = n_snap = 0
    for hrs, ch, etype in PLAN:
        ts = now - datetime.timedelta(hours=hrs)
        payload = json.dumps({"demo": True, "note": "SAMPLE - not real footage", "vendor": "demo"})
        dk = cur.execute("select wl_dedupe_key(%s,%s,%s,%s::timestamptz,%s)",
                         (site, ch, f"demo-{hrs}-{ch}", ts.isoformat(), etype)).fetchone()[0]
        eid = cur.execute("""insert into events (tenant_id, site_id, camera_id, agent_id, event_type,
                 device_event_id, device_ts, agent_ts, received_at, dedupe_key, payload)
                 values (%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz, now(), %s, %s::jsonb)
                 on conflict (tenant_id, dedupe_key) do nothing returning id""",
                 (tenant, site, cam_id[ch], agent, etype, f"demo-{hrs}-{ch}",
                  ts.isoformat(), ts.isoformat(), dk, payload)).fetchone()
        if not eid:
            continue
        eid = eid[0]; n_ev += 1
        if etype not in FAULTS:   # incidents carry a still; faults do not
            img = frame(colors.get(ch, (60, 60, 70)))
            cur.execute("""insert into snapshots (tenant_id, event_id, site_id, camera_id, image, bytes, content_type, captured_at)
                     values (%s,%s,%s,%s,%s,%s,'image/jpeg',%s::timestamptz)""",
                     (tenant, eid, site, cam_id[ch], img, len(img), ts.isoformat()))
            n_snap += 1

    # a report recipient (whatsapp test) if none
    if not cur.execute("select 1 from report_recipients where tenant_id=%s limit 1", (tenant,)).fetchone():
        cur.execute("insert into report_recipients (tenant_id, site_id, name, channel, destination) values (%s,null,'Demo control room','whatsapp','923000000000')", (tenant,))

    # keep the demo tenant on trial so the demo can show trial -> checkout
    cur.execute("update tenants set plan='trial', subscription_status='trialing', requested_plan=null, trial_started_at=now()-interval '2 days', trial_days=14 where id=%s", (tenant,))
    for tb in ("payment_transactions", "subscriptions", "billing_checkouts"):
        cur.execute(f"delete from {tb} where tenant_id=%s", (tenant,))

    print(f"seeded: {n_ev} events, {n_snap} snapshots across {len(CAMERAS)} cameras; agent online; tenant on trial")
    print("login: ", DEMO_EMAIL, "(password in .env PORTAL_DEMO_PASSWORD)")
    conn.close()


if __name__ == "__main__":
    main()

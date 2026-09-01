#!/usr/bin/env python3
"""Seed a deterministic, clearly labelled three-site WatchLog demo account.

This script is intentionally destructive ONLY inside the tenant belonging to
PORTAL_DEMO_EMAIL (default demo@watchlog.test). It rebuilds that tenant's sites,
agents, cameras, sample incidents, recipients and, when Analytics Studio is
migrated, operational analytics. It never touches AKSS or another customer.

Every synthetic event is tagged {"demo": true, "note": "SAMPLE - not real footage"}.
The resulting account is designed to read as a healthy product walkthrough:
three locations, one healthy Site Agent per location, varied camera activity,
one deliberately quiet camera and one recent recorder/camera fault.

    python tools/seed_demo.py
"""
from __future__ import annotations

import datetime as dt
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
DSN = dict(host=ENV["SUPABASE_DB_HOST"], port=ENV.get("SUPABASE_DB_PORT", 5432),
           user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
           dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30,
           autocommit=True)
DEMO_EMAIL = ENV.get("PORTAL_DEMO_EMAIL", "demo@watchlog.test")

SITES = [
    {"name": "Karachi Head Office", "type": "office_commercial", "host": "WL-DEMO-HQ",
     "vendor": "Dahua", "model": "NVR4216-4KS2", "driver": "dahua-cgi",
     "cams": [("1","Main Entrance","entrance_exit"),("2","Reception","reception"),("3","Office Floor","office_floor"),("4","Parking","parking")]},
    {"name": "Korangi Warehouse", "type": "warehouse_logistics", "host": "WL-DEMO-WH",
     "vendor": "Hikvision", "model": "DS-7616NI-K2", "driver": "hikvision-isapi",
     "cams": [("1","Main Gate","main_gate"),("2","Loading Bay","loading_bay"),("3","Warehouse Floor","warehouse_floor"),("4","Rear Perimeter","perimeter")]},
    {"name": "Landhi Factory Floor", "type": "manufacturing", "host": "WL-DEMO-MFG",
     "vendor": "Dahua", "model": "NVR4108HS", "driver": "dahua-cgi",
     "cams": [("1","Main Gate","main_gate"),("2","Production Floor","warehouse_floor"),("3","Packing Line","custom"),("4","Restricted Store","restricted_area")]},
]

# hours ago, site index, channel, incident/fault type.
# Korangi / Rear Perimeter intentionally has no event in the last 24h. The
# 38-hour event proves it used to report; Site Health can explain the silence.
PLAN = [
    (1,0,"1","person"),(2,1,"2","vehicle"),(3,2,"1","person"),(4,0,"4","vehicle"),
    (5,1,"1","motorcycle"),(6,2,"2","person"),(7,0,"2","person"),(8,1,"3","person"),
    (9,2,"3","person"),(10,0,"3","person"),(12,2,"4","person"),(13,1,"2","video_loss"),
    (16,1,"2","vehicle"),(20,2,"1","person"),(26,0,"1","person"),(31,1,"1","vehicle"),
    (38,1,"4","person"),(45,2,"2","person"),(54,0,"4","vehicle"),(67,1,"3","person"),
]
FAULTS = {"tamper","video_loss","disk_error","disk_full","offline"}
COLORS = [(47,66,93),(58,76,63),(77,58,89),(72,68,53)]


def frame(rgb):
    from PIL import Image, ImageDraw
    buf = io.BytesIO(); im = Image.new("RGB", (640, 360), rgb)
    draw = ImageDraw.Draw(im); draw.rectangle((18,18,622,342), outline=(120,135,160), width=2)
    draw.text((28,28), "WATCHLOG DEMO - SYNTHETIC STILL", fill=(205,215,230))
    im.save(buf, "JPEG", quality=82); return buf.getvalue()


def has_table(cur, name: str) -> bool:
    return bool(cur.execute("select to_regclass(%s)", (f"public.{name}",)).fetchone()[0])


def has_column(cur, table: str, column: str) -> bool:
    return bool(cur.execute("""select 1 from information_schema.columns
        where table_schema='public' and table_name=%s and column_name=%s""",
        (table, column)).fetchone())


def main():
    conn = psycopg.connect(**DSN); cur = conn.cursor()
    row = cur.execute("""select m.tenant_id from memberships m join auth.users u on u.id=m.user_id
        where lower(u.email)=lower(%s) limit 1""", (DEMO_EMAIL,)).fetchone()
    if not row: raise SystemExit(f"no demo tenant for {DEMO_EMAIL}")
    tenant = row[0]
    print("demo tenant:", tenant)

    # Curate the demo account as a whole. This is safer than leaving old test
    # agents/cameras behind and making the walkthrough look broken.
    for table in ("report_deliveries", "report_recipients"):
        if has_table(cur, table): cur.execute(f"delete from {table} where tenant_id=%s", (tenant,))
    cur.execute("delete from sites where tenant_id=%s", (tenant,))
    cur.execute("update tenants set name='WatchLog Demo', plan='trial', subscription_status='trialing', requested_plan=null, trial_started_at=now()-interval '2 days', trial_days=14 where id=%s", (tenant,))
    for table in ("payment_transactions","subscriptions","billing_checkouts"):
        if has_table(cur, table): cur.execute(f"delete from {table} where tenant_id=%s", (tenant,))

    analytics = has_table(cur, "monitoring_rules") and has_table(cur, "analytic_events") and has_column(cur, "monitoring_rules", "analytic_key")
    site_ids=[]; agents=[]; camera_ids=[]
    for idx, spec in enumerate(SITES):
        cols = "tenant_id,name,timezone"; vals = [tenant,spec["name"],"Asia/Karachi"]
        if has_column(cur,"sites","site_type"): cols += ",site_type"; vals.append(spec["type"])
        site_id = cur.execute(f"insert into sites ({cols}) values ({','.join(['%s']*len(vals))}) returning id", vals).fetchone()[0]
        site_ids.append(site_id)
        agent = cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
            device_vendor,device_model,device_driver,enrolled_at,last_seen_at)
            values (%s,%s,md5(gen_random_uuid()::text),%s,'Windows','0.3.0-demo',%s,%s,%s,now()-interval '30 days',now()-make_interval(secs=>%s)) returning id""",
            (tenant,site_id,spec["host"],spec["vendor"],spec["model"],spec["driver"],35+idx*20)).fetchone()[0]
        agents.append(agent); cmap={}
        for ch,name,purpose in spec["cams"]:
            if has_column(cur,"cameras","purpose"):
                cid=cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose,analytics_enabled) values (%s,%s,%s,%s,%s,true) returning id",(tenant,site_id,ch,name,purpose)).fetchone()[0]
            else:
                cid=cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,%s,%s) returning id",(tenant,site_id,ch,name)).fetchone()[0]
            cmap[ch]=cid
        camera_ids.append(cmap)

    now=dt.datetime.now(dt.timezone.utc); n_ev=n_snap=0
    for hrs,si,ch,etype in PLAN:
        site_id=site_ids[si]; cid=camera_ids[si][ch]; agent=agents[si]; ts=now-dt.timedelta(hours=hrs)
        payload=json.dumps({"demo":True,"note":"SAMPLE - not real footage","vendor":"demo","site":SITES[si]["name"]})
        dk=cur.execute("select wl_dedupe_key(%s,%s,%s,%s::timestamptz,%s)",(site_id,ch,f"demo-{si}-{hrs}-{ch}",ts.isoformat(),etype)).fetchone()[0]
        row=cur.execute("""insert into events (tenant_id,site_id,camera_id,agent_id,event_type,device_event_id,device_ts,agent_ts,received_at,dedupe_key,payload)
            values (%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s::timestamptz,%s,%s::jsonb)
            on conflict (tenant_id,dedupe_key) do nothing returning id""",
            (tenant,site_id,cid,agent,etype,f"demo-{si}-{hrs}-{ch}",ts.isoformat(),ts.isoformat(),ts.isoformat(),dk,payload)).fetchone()
        if not row: continue
        eid=row[0];n_ev+=1
        if etype not in FAULTS:
            img=frame(COLORS[(si+int(ch)-1)%len(COLORS)])
            cur.execute("insert into snapshots (tenant_id,event_id,site_id,camera_id,image,bytes,content_type,captured_at) values (%s,%s,%s,%s,%s,%s,'image/jpeg',%s::timestamptz)",(tenant,eid,site_id,cid,img,len(img),ts.isoformat()));n_snap+=1

    n_analytics=0
    if analytics:
        for si,spec in enumerate(SITES):
            sid=site_ids[si]
            schedule=cur.execute("""insert into monitoring_schedules (tenant_id,site_id,name,timezone,schedule_json,enabled)
                values (%s,%s,'Business hours','Asia/Karachi',%s::jsonb,true) returning id""",
                (tenant,sid,json.dumps({"days":{d:[["08:00","18:00"]] for d in ("mon","tue","wed","thu","fri")} | {"sat":[],"sun":[]}}))).fetchone()[0]
            rules=[]
            if spec["type"]=="warehouse_logistics":
                defs=[("1","Vehicle Flow","vehicle_flow","line_crossing",["car","motorcycle"]),("2","Loading Bay Activity","zone_activity","zone_entry",["person","car"])]
            elif spec["type"]=="manufacturing":
                defs=[("1","Visitor Flow","visitor_flow","line_crossing",["person"]),("2","Production Zone Activity","zone_activity","zone_entry",["person"]),("4","Restricted Area Dwell","dwell","zone_dwell",["person"])]
            else:
                defs=[("1","Visitor Flow","visitor_flow","line_crossing",["person"]),("4","Parking Vehicle Flow","vehicle_flow","line_crossing",["car","motorcycle"])]
            for ch,name,key,rtype,classes in defs:
                geom={"type":"line","points":[[.15,.55],[.85,.55]]} if rtype=="line_crossing" else {"type":"polygon","points":[[.2,.25],[.8,.25],[.8,.8],[.2,.8]]}
                rid=cur.execute("""insert into monitoring_rules (tenant_id,site_id,camera_id,name,analytic_key,rule_type,object_classes,geometry_json,direction_json,schedule_id,enabled,dwell_seconds)
                    values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,true,%s) returning id""",
                    (tenant,sid,camera_ids[si][ch],name,key,rtype,classes,json.dumps(geom),json.dumps({"negative_to_positive":"in","positive_to_negative":"out"}),schedule,60 if rtype=="zone_dwell" else None)).fetchone()[0]
                rules.append((rid,ch,key,rtype,classes[0]))
            # 14 days of measurements for charts, deterministic counts per site/day.
            for day in range(14):
                for rid,ch,key,rtype,obj in rules:
                    count=4+si*2+(day%5)
                    for n in range(count):
                        occurred=now-dt.timedelta(days=day,hours=1+n*.12+si*.2)
                        etype="occupancy" if rtype=="occupancy" else rtype
                        direction="in" if rtype=="line_crossing" else None
                        meta={"demo":True,"count":2+(n%4)} if rtype=="occupancy" else {"demo":True}
                        dedupe=f"demo-analytics-{si}-{day}-{rid}-{n}"
                        cur.execute("""insert into analytic_events (tenant_id,site_id,camera_id,agent_id,monitoring_rule_id,analytic_key,event_type,object_class,track_key,direction,occurred_at,dedupe_key,metadata_json)
                            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s,%s::jsonb) on conflict (tenant_id,dedupe_key) do nothing""",
                            (tenant,sid,camera_ids[si][ch],agents[si],rid,key,etype,obj,f"demo-{day}-{n}",direction,occurred.isoformat(),dedupe,json.dumps(meta)))
                        n_analytics+=1

    # Canonical recipient model after 0031 is one row per delivery endpoint.
    if has_column(cur,"report_recipients","whatsapp_destination"):
        cur.execute("""insert into report_recipients (tenant_id,site_id,name,channel,destination,whatsapp_destination,email_destination,enabled)
            values (%s,null,'Demo operations','whatsapp','923000000000','923000000000',null,true),
                   (%s,null,'Demo operations','email','demo+sample@watchlog.test',null,'demo+sample@watchlog.test',true)""",(tenant,tenant))
    else:
        cur.execute("insert into report_recipients (tenant_id,site_id,name,channel,destination) values (%s,null,'Demo operations','whatsapp','923000000000')",(tenant,))

    # A few clearly synthetic delivery rows make Reports useful in a visual
    # walkthrough without ever contacting a real provider.
    n_deliveries=0
    if has_table(cur,"report_deliveries"):
        for offset in range(1,6):
            for si,sid in enumerate(site_ids):
                report_day=(now-dt.timedelta(days=offset)).date()
                destination="demo+sample@watchlog.test"
                status="failed" if (offset==2 and si==1) else "sent"
                error="DEMO SAMPLE - simulated provider timeout" if status=="failed" else None
                cur.execute("""insert into report_deliveries
                    (tenant_id,site_id,report_date,channel,destination,status,provider_id,error,events,sent_at)
                    values (%s,%s,%s,'email',%s,%s,%s,%s,%s,%s::timestamptz)
                    on conflict do nothing""",
                    (tenant,sid,report_day,destination,status,f"demo-sample-{offset}-{si}",error,4+offset+si,(now-dt.timedelta(days=offset,hours=-7)).isoformat()))
                n_deliveries+=1

    print(f"seeded {len(SITES)} sites, {sum(len(s['cams']) for s in SITES)} cameras, {n_ev} incidents/events, {n_snap} synthetic stills")
    print("controlled health exceptions: Korangi Rear Perimeter quiet 24h+; Loading Bay video-loss sample within 24h")
    if analytics: print(f"seeded {n_analytics} semantic analytics measurements")
    else: print("analytics schema not present; skipped analytics seed safely")
    print(f"seeded {n_deliveries} synthetic report delivery history rows")
    print("login:",DEMO_EMAIL,"(password in .env PORTAL_DEMO_PASSWORD)")
    conn.close()

if __name__=="__main__": main()

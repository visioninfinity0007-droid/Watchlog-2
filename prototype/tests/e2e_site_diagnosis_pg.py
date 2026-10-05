#!/usr/bin/env python3
"""Site/recorder diagnosis data plane (0079, items 4/5) — the Site Control page's one call.

Rolled-back txn + bootstrap harness. Proves the diagnosis composes recorder identity,
connectivity, capability profile (evidence-graded), cameras with current VideoLoss, faults,
coverage, the caller's role and the Read/Recommend/Approve tiers — and is tenant-guarded.
Crucially it never returns a recorder credential.

Runs against the FINAL chain (the last wl_my_site_diagnosis definition, 0155); it never
re-executes an older migration file. MNVR-048: recorder identity comes from the configured
recorder (or the current site Agent), never max() across historical Agent rows, and only
canonical configured cameras are listed (no hidden ONVIF profile / legacy rows).
"""
from __future__ import annotations

import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m: ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
import os
for k in ("SUPABASE_DB_HOST","SUPABASE_DB_PORT","SUPABASE_DB_USER","SUPABASE_DB_PASSWORD","SUPABASE_DB_NAME"):
    if os.environ.get(k): ENV[k] = os.environ[k]
import psycopg  # noqa: E402

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            def claims(uid): return json.dumps({"sub": str(uid), "role": "authenticated"})
            def as_ok(uid, sql, *p):
                cur.execute("savepoint sp"); cur.execute("select set_config('request.jwt.claims',%s,true)",(claims(uid),))
                cur.execute("set local role authenticated"); r = cur.execute(sql, p or None).fetchone()
                cur.execute("reset role"); cur.execute("release savepoint sp"); return r
            def as_raises(uid, sql, *p):
                cur.execute("savepoint sp"); cur.execute("select set_config('request.jwt.claims',%s,true)",(claims(uid),))
                cur.execute("set local role authenticated"); bad=False
                try: cur.execute(sql, p or None).fetchone()
                except psycopg.Error: bad=True
                cur.execute("rollback to savepoint sp"); return bad
            def bootstrap(email, co, site):
                uid = cur.execute("insert into auth.users (id,email) values (gen_random_uuid(),%s) returning id",(email,)).fetchone()[0]
                b = as_ok(uid,"select wl_bootstrap_tenant(%s,%s)",co,site)[0]; t=b["tenant_id"]
                sid = cur.execute("select id from sites where tenant_id=%s order by created_at limit 1",(t,)).fetchone()[0]
                return uid, t, sid

            ua, ta, sa = bootstrap("diag-a@watchlog.test","Diag A","Site A")
            ub, tb, sb = bootstrap("diag-b@watchlog.test","Diag B","Site B")
            cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at,device_vendor,device_model,device_driver)
                           values (%s,%s, encode(sha256('k'::bytea),'hex'), now(),'Dahua','DH-XVR1B08-I','dahua')""",(ta,sa))
            # Monitored (is_configured) cameras: Site Control lists canonical configured cameras.
            c1 = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose,is_configured) values (%s,%s,'1','Reception','reception',true) returning id",(ta,sa)).fetchone()[0]
            c5 = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose,is_configured) values (%s,%s,'5','Armory','armory',true) returning id",(ta,sa)).fetchone()[0]
            cur.execute("insert into camera_health (tenant_id,site_id,camera_id,health_state) values (%s,%s,%s,'operational') on conflict (camera_id) do update set health_state=excluded.health_state",(ta,sa,c1))
            cur.execute("insert into camera_health (tenant_id,site_id,camera_id,health_state,reason_code) values (%s,%s,%s,'offline','video_loss') on conflict (camera_id) do update set health_state=excluded.health_state, reason_code=excluded.reason_code",(ta,sa,c5))

            d = as_ok(ua,"select wl_my_site_diagnosis(%s)",sa)[0]
            step(d["recorder"]["identified"] and d["recorder"]["model"]=="DH-XVR1B08-I", "recorder identity composed")
            step(d["capability_known"] and len(d["capabilities"])>0, "capability profile resolved (evidence-graded)")
            step(d["connectivity"]["agent_online"] is True, "connectivity: agent online")
            cams = {c["name"]: c for c in d["cameras"]}
            step(cams["Armory"]["video_loss"] is True and cams["Reception"]["video_loss"] is False, "current VideoLoss surfaced per camera")
            step(any(f["camera"]=="Armory" for f in d["faults"]), "faults list the offline camera")
            step(d["role"]=="owner" and d["tiers"]["approve"] is True and d["tiers"]["read"] is True, "role + Read/Recommend/Approve tiers", d["role"])
            step((d.get("coverage") or {}).get("coverage_ratio") is not None, "coverage composed")
            step("password" not in json.dumps(d).lower() and "credential" not in json.dumps(d).lower(), "no recorder credential ever returned")

            step(as_raises(ua,"select wl_my_site_diagnosis(%s)",sb), "cannot diagnose another tenant's site")

            # MNVR-048 (MAIN too): a site whose Hikvision DS-7608NI-Q1 was replaced by a Dahua
            # DH-XVR1B08-I keeps the old Agent row. max(vendor)/max(model) across Agent rows
            # would show the Hikvision profile; the diagnosis must use the configured
            # recorder / current site Agent. The ONVIF site also keeps 8 hidden profile rows.
            uc, tc, sc = bootstrap("diag-c@watchlog.test", "Diag C", "Site C")
            cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at,enrolled_at,
                                               device_vendor,device_model,device_driver)
                           values (%s,%s,encode(sha256('k-old'::bytea),'hex'),now()-interval '30 days',
                                   now()-interval '60 days','Hikvision','DS-7608NI-Q1','hikvision')""", (tc, sc))
            cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at,enrolled_at,
                                               device_vendor,device_model,device_driver)
                           values (%s,%s,encode(sha256('k-new'::bytea),'hex'),now(),
                                   now()-interval '1 day','Dahua','DH-XVR1B08-I','dahua')""", (tc, sc))
            for n in range(1, 9):
                cur.execute("""insert into cameras (tenant_id,site_id,channel,physical_channel,name,
                                                    is_canonical,is_configured)
                               values (%s,%s,%s,%s,%s,true,true)""", (tc, sc, str(n), str(n), f"Camera {n}"))
                cur.execute("""insert into cameras (tenant_id,site_id,channel,physical_channel,name,
                                                    is_canonical,is_configured)
                               values (%s,%s,%s,%s,%s,false,false)""",
                            (tc, sc, f"legacy-profile-{n}", str(n), f"MediaProfile_Channel{n}_SubStream"))
            dc = as_ok(uc,"select wl_my_site_diagnosis(%s)",sc)[0]
            step(dc["recorder"]["vendor"] == "Dahua" and dc["recorder"]["model"] == "DH-XVR1B08-I"
                 and dc["recorder"]["identified"] is True,
                 "MNVR-048: identity is the current recorder, not max() over historical Agent rows",
                 json.dumps(dc["recorder"]))
            expected_known = cur.execute(
                "select jsonb_array_length(wl_recorder_profile('Dahua','DH-XVR1B08-I'))>0").fetchone()[0]
            step(dc["capability_known"] is expected_known
                 and dc["capabilities"] == cur.execute(
                     "select wl_recorder_profile('Dahua','DH-XVR1B08-I')").fetchone()[0],
                 "MNVR-048: the capability profile belongs to that recorder's own model")
            names = [c["name"] for c in dc["cameras"]]
            raw = json.dumps(dc)
            step(len(dc["cameras"]) == 8 and names == [f"Camera {n}" for n in range(1, 9)]
                 and [c["channel"] for c in dc["cameras"]] == [str(n) for n in range(1, 9)]
                 and "MediaProfile" not in raw and "legacy-profile" not in raw,
                 "MNVR-048: only the 8 canonical configured cameras are listed, no hidden profile rows",
                 f"{len(dc['cameras'])} cameras: {names}")
            recs = dc.get("recorders") or [{}]
            step(dc.get("multi_recorder") is False and dc.get("recorder_count") == 1
                 and len(recs) == 1
                 and recs[0].get("model") == "DH-XVR1B08-I"
                 and [c["camera_id"] for c in recs[0].get("cameras") or []]
                     == [c.get("camera_id") for c in dc["cameras"]]
                 and all(c.get("recorder_id") == recs[0].get("recorder_id") for c in dc["cameras"]),
                 "MNVR-048: a single-recorder site lists its one recorder with its cameras",
                 json.dumps(dc.get("recorders"), default=str)[:300])
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

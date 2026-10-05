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
            model_profile = cur.execute(
                "select wl_recorder_profile('Dahua','DH-XVR1B08-I')").fetchone()[0]
            step(dc["capability_known"] is (len(model_profile) > 0)
                 and [c["capability"] for c in dc["capabilities"]]
                     == [c["capability"] for c in model_profile]
                 and [c["verdict"] for c in dc["capabilities"]]
                     == [c["verdict"] for c in model_profile],
                 "MNVR-048: the capability profile belongs to that recorder's own model")
            # MNVR-049: identity reported only by the Agent has no recorder-scoped field
            # evidence, so the model's FIELD_VERIFIED grade (one field test on another
            # unit) is capped exactly as wl_recorder_capability_for_recorder caps it.
            step(any(c["evidence_class"] == "FIELD_VERIFIED" for c in model_profile)
                 and not any(c["evidence_class"] == "FIELD_VERIFIED" for c in dc["capabilities"])
                 and all(c.get("evidence_scope") == "model" for c in dc["capabilities"]),
                 "MNVR-049: an Agent-reported identity never shows another unit's FIELD_VERIFIED",
                 json.dumps([[c["capability"], c["evidence_class"]] for c in dc["capabilities"]])[:300])

            # MNVR-049: a configured recorder of the field-tested model with no evidence
            # of its own. The diagnosis (and the AI context built from it) must show the
            # recorder-scoped grade, never the model's FIELD_VERIFIED.
            ud, td, sd = bootstrap("diag-d@watchlog.test", "Diag D", "Site D")
            kd = "diag-d-key"
            agent_d = cur.execute("""insert into agents (tenant_id,site_id,agent_key_hash,last_seen_at,enrolled_at)
                                     values (%s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),now(),now()-interval '1 day')
                                     returning id""", (td, sd, kd)).fetchone()[0]
            rec_d = cur.execute("select wl_sync_recorders(%s,%s,%s::jsonb)", (agent_d, kd, json.dumps([{
                "local_key": "rec-d", "display_name": "Recorder D", "vendor": "Dahua",
                "model": "DH-XVR1B08-I", "firmware": "X", "identity_fingerprint": "serial:OTHERUNIT123",
                "is_primary": True, "is_configured": True}]))).fetchone()[0]["rec-d"]
            dd = as_ok(ud, "select wl_my_site_diagnosis(%s)", sd)[0]
            scoped = {c: cur.execute("select wl_recorder_capability_for_recorder(%s,%s)->>'evidence_class'",
                                     (rec_d, c)).fetchone()[0]
                      for c in ("channel_title", "time_ntp_config")}
            caps_d = {c["capability"]: c for c in dd["capabilities"] or []}
            step(scoped["channel_title"] != "FIELD_VERIFIED"
                 and all(caps_d.get(c, {}).get("evidence_class") == scoped[c] for c in scoped)
                 and not any(c["evidence_class"] == "FIELD_VERIFIED" for c in dd["capabilities"]),
                 "MNVR-049: diagnosis shows the recorder-scoped grade, not another unit's FIELD_VERIFIED",
                 json.dumps({c: caps_d.get(c, {}).get("evidence_class") for c in scoped}) + " scoped=" + json.dumps(scoped))
            step(dd["capability_known"] is True and dd["recorders"][0]["capability_known"] is True,
                 "MNVR-049: the knowledge base still lists that model's capabilities (capability_known)")
            ai_d = as_ok(ud, "select wl_ai_context(%s)", sd)[0]
            step(not any(c.get("evidence_class") == "FIELD_VERIFIED" for c in ai_d.get("capabilities") or []),
                 "MNVR-049: the AI context never receives another unit's FIELD_VERIFIED")
            # The recorder's own read-back-verified evidence does make it FIELD_VERIFIED.
            cur.execute("""insert into recorder_field_evidence(
                             id,site_id,recorder_id,identity_fingerprint,vendor,model,
                             firmware,capability,operation,result,evidence_class,
                             test_date,read_back_verified
                           ) values ('TEST-DIAG-TIME',%s,%s,'serial:OTHERUNIT123','Dahua','DH-XVR1B08-I',
                             'X','time_ntp_config','read_write','write applied and read back',
                             'FIELD_VERIFIED',current_date,true)""", (sd, rec_d))
            dd2 = as_ok(ud, "select wl_my_site_diagnosis(%s)", sd)[0]
            caps_d2 = {c["capability"]: c["evidence_class"] for c in dd2["capabilities"]}
            step(caps_d2.get("time_ntp_config") == "FIELD_VERIFIED"
                 and caps_d2.get("channel_title") != "FIELD_VERIFIED",
                 "MNVR-049: only this recorder's own evidence shows as FIELD_VERIFIED", json.dumps(caps_d2)[:300])
            acl = cur.execute(
                """select has_function_privilege(r,'public.wl_recorder_profile_model_scoped(text,text)','execute')
                     from unnest(array['anon','authenticated','service_role']) r""").fetchall()
            step(not any(x[0] for x in acl), "MNVR-049: the capped model-profile helper is owner-only")
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

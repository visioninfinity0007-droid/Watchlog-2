#!/usr/bin/env python3
"""Site/recorder diagnosis data plane (0079, items 4/5) — the Site Control page's one call.

Rolled-back txn + bootstrap harness. Proves the diagnosis composes recorder identity,
connectivity, capability profile (evidence-graded), cameras with current VideoLoss, faults,
coverage, the caller's role and the Read/Recommend/Approve tiers — and is tenant-guarded.
Crucially it never returns a recorder credential.
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

MIG = (ROOT/"supabase"/"migrations"/"0079_site_diagnosis.sql").read_text(encoding="utf-8")
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG)
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
            c1 = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'1','Reception','reception') returning id",(ta,sa)).fetchone()[0]
            c5 = cur.execute("insert into cameras (tenant_id,site_id,channel,name,purpose) values (%s,%s,'5','Armory','armory') returning id",(ta,sa)).fetchone()[0]
            cur.execute("insert into camera_health (tenant_id,site_id,camera_id,health_state) values (%s,%s,%s,'operational')",(ta,sa,c1))
            cur.execute("insert into camera_health (tenant_id,site_id,camera_id,health_state,reason_code) values (%s,%s,%s,'offline','video_loss')",(ta,sa,c5))

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
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

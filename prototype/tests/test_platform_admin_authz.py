#!/usr/bin/env python3
"""Live non-mutating authorization gate for platform administration.

A normal tenant owner must not gain cross-tenant visibility simply because
`authenticated` can execute the platform RPC wrappers. Every wrapper performs
its own platform-role check before reading customer data.

Requires SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, PORTAL_DEMO_EMAIL and
PORTAL_DEMO_PASSWORD. It never writes production data.
"""

from __future__ import annotations
import json, os, sys, urllib.error, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
def envs():
    out=dict(os.environ); p=ROOT/".env"
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k,_,v=line.partition("=");out.setdefault(k.strip(),v.strip().strip('"').strip("'"))
    return out
E=envs(); URL=E.get("SUPABASE_URL","").rstrip("/"); KEY=E.get("SUPABASE_PUBLISHABLE_KEY","")

def rpc(name,body=None,bearer=None):
    req=urllib.request.Request(f"{URL}/rest/v1/rpc/{name}",data=json.dumps(body or {}).encode(),headers={"apikey":KEY,"Authorization":f"Bearer {bearer or KEY}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=25) as r:return r.status,json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:return e.code,e.read().decode()[:300]

def signin():
    req=urllib.request.Request(f"{URL}/auth/v1/token?grant_type=password",data=json.dumps({"email":E["PORTAL_DEMO_EMAIL"],"password":E["PORTAL_DEMO_PASSWORD"]}).encode(),headers={"apikey":KEY,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=25) as r:return json.loads(r.read().decode())["access_token"]

def run():
    if not all([URL,KEY,E.get("PORTAL_DEMO_EMAIL"),E.get("PORTAL_DEMO_PASSWORD")]):
        print("SKIP platform admin live gate: credentials unavailable");return 0
    jwt=signin(); failures=[]
    st,me=rpc("wl_platform_me",{},jwt)
    if st!=200 or me is not None: failures.append(f"normal tenant owner platform_me expected null, got {st} {me}")
    probes=[
        ("wl_platform_overview",{}),
        ("wl_platform_tenants",{"p_search":None,"p_status":None,"p_limit":1,"p_offset":0}),
        ("wl_platform_operations",{"p_limit":1}),
        ("wl_platform_billing",{}),
        ("wl_platform_audit",{"p_limit":1}),
        ("wl_platform_admins",{}),
    ]
    for name,body in probes:
        st,res=rpc(name,body,jwt)
        if st not in (401,403,400): failures.append(f"{name} leaked to tenant owner: {st} {res}")
        else: print(f"PASS {name}: tenant owner denied ({st})")
    st,_=rpc("wl_platform_overview",{})
    if st not in (401,403,400): failures.append(f"anon platform overview not denied: {st}")
    else: print(f"PASS anonymous platform overview denied ({st})")
    if failures:
        print("\n".join("FAIL "+x for x in failures));return 1
    print("Platform admin live authz gate: PASS")
    return 0

def test_platform_admin_authz(): assert run()==0
if __name__=="__main__":sys.exit(run())

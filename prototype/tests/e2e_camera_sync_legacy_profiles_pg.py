#!/usr/bin/env python3
"""Camera sync after an ONVIF-profile era (0159): real Postgres, rolled back.

Field defect (Al-Khalid, 2026-10-07): a site that first ran through the ONVIF fallback has
canonical cameras '1'..'8' (channel = physical channel) plus the renamed profile rows
'legacy-profile-1', '-3', ... sharing those physical channels. The 5.1.1 Agent then syncs
the same recorder over its native driver (8 numeric channels) and wl_sync_cameras failed
with 23505 on cameras_recorder_channel_uniq. Proves, with that exact row set:

- the native sync succeeds and returns the eight canonical camera ids, unchanged;
- canonical names and configured flags are kept; legacy profile rows stay non-canonical,
  keep their ids and are never deleted;
- the sync is idempotent (a second identical call changes nothing);
- the first ONVIF -> native transition (no legacy rows yet) still moves profile rows aside
  and gives each physical channel one canonical camera;
- a rename never collides with an existing legacy name.
"""
from __future__ import annotations

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

# Al-Khalid's rows before the 5.1.1 sync: (channel, physical_channel, name, configured, canonical)
ALKHALID = [
    ("1", "1", "Reception Main Entrance", True, True),
    ("2", "2", "Directors Office", True, True),
    ("3", "3", "Armory Gate", True, True),
    ("4", "4", "Admin Manager Dir Entrance", False, True),
    ("5", "5", "Armory", True, True),
    ("6", "6", "Channel6", True, True),
    ("7", "7", "Channel7", True, True),
    ("8", "8", "Channel8", True, True),
] + [(f"legacy-profile-{2 * n - 1}", str(n), f"MediaProfile_Channel{n}_MainStream", False, False)
     for n in range(1, 9)]


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=ENV.get("SUPABASE_DB_NAME", "postgres"),
        connect_timeout=30,
        autocommit=False,
    )

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            def claims(uid):
                return json.dumps({"sub": str(uid), "role": "authenticated"})

            def as_auth(uid, sql, *params):
                cur.execute("savepoint auth_sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims(uid),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")
                return row

            def as_anon_try(sql, *params):
                cur.execute("savepoint anon_try")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_try")
                    return row, ""
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_try")
                    cur.execute("reset role")
                    return None, f"{exc.sqlstate} {str(exc).splitlines()[0]}"

            def new_site(label):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (f"{label}@watchlog.test",),
                ).fetchone()[0]
                tenant = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                                 f"{label} Tenant", f"{label} Site")[0]["tenant_id"]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (tenant,)).fetchone()[0]
                key = f"{label}-agent-key"
                agent = cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,agent_version,
                         device_vendor,device_model,device_driver,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         'camsync','windows','5.1.1','Dahua','DH-XVR1B08-I','dahua-cgi',now()
                       ) returning id""",
                    (tenant, site, key)).fetchone()[0]
                recorder = cur.execute("select wl_legacy_recorder_for_agent(%s)",
                                       (agent,)).fetchone()[0]
                return tenant, site, agent, key, recorder

            def rows(recorder):
                return {r[0]: r[1:] for r in cur.execute(
                    """select channel, id, physical_channel, name, is_configured, is_canonical
                         from cameras where recorder_id=%s""", (recorder,)).fetchall()}

            native = json.dumps([{"channel": str(n), "name": f"CH{n}", "is_configured": True}
                                 for n in range(1, 9)])

            # ---- Al-Khalid's exact rows -----------------------------------------------
            tenant, site, agent, key, rec = new_site("alkhalid-camsync")
            for ch, phys, name, conf, canon in ALKHALID:
                cur.execute(
                    """insert into cameras(tenant_id,site_id,recorder_id,channel,physical_channel,
                                           name,is_configured,is_canonical)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant, site, rec, ch, phys, name, conf, canon))
            before = rows(rec)

            out, err = as_anon_try("select wl_sync_cameras(%s,%s,%s::jsonb)", agent, key, native)
            step(err == "", "native sync after an ONVIF era succeeds (was 23505)", err)
            mapping = out[0] if out else {}
            step(sorted(mapping, key=int) == [str(n) for n in range(1, 9)]
                 and all(mapping[str(n)] == str(before[str(n)][0]) for n in range(1, 9)),
                 "returns the eight canonical cameras with their existing ids",
                 json.dumps(mapping))
            after = rows(rec)
            step(set(after) == set(before) and all(after[c][0] == before[c][0] for c in before),
                 "no camera row is deleted or re-keyed")
            step(after["1"][2] == "Reception Main Entrance" and after["5"][2] == "Armory",
                 "configured canonical names are kept", f"{after['1'][2]!r}, {after['5'][2]!r}")
            step(all(not after[c][4] and not after[c][3] for c in after if c.startswith("legacy-")),
                 "legacy profile rows stay non-canonical and unconfigured")
            step(all(after[str(n)][4] for n in range(1, 9)), "channels 1..8 are canonical")

            out, err = as_anon_try("select wl_sync_cameras(%s,%s,%s::jsonb)", agent, key, native)
            step(err == "" and rows(rec) == after, "a second identical sync changes nothing", err)

            # ---- first ONVIF -> native transition (no legacy rows yet) ----------------
            tenant2, site2, agent2, key2, rec2 = new_site("onvif-first-camsync")
            # ONVIF era: transport channels 1..16, two profiles per physical channel.
            for t in range(1, 17):
                phys = (t + 1) // 2
                kind = "MainStream" if t % 2 else "SubStream"
                canon = t % 2 == 1
                cur.execute(
                    """insert into cameras(tenant_id,site_id,recorder_id,channel,physical_channel,
                                           name,is_configured,is_canonical)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant2, site2, rec2, str(t), str(phys),
                     f"Camera {phys}" if canon else f"MediaProfile_Channel{phys}_{kind}",
                     canon, canon))
            out, err = as_anon_try("select wl_sync_cameras(%s,%s,%s::jsonb)", agent2, key2, native)
            step(err == "", "first ONVIF -> native transition succeeds", err)
            r2 = rows(rec2)
            canonical = sorted((c for c in r2 if r2[c][4]), key=lambda c: (len(c), c))
            step(canonical == [str(n) for n in range(1, 9)],
                 "one canonical camera per physical channel, numbered natively", str(canonical))
            step(len(r2) == 16 and all(not r2[c][4] for c in r2 if c.startswith("legacy-profile-")),
                 "the profile rows are kept aside, none deleted", str(len(r2)))

            # ---- a rename never collides with an existing legacy name ----------------
            tenant3, site3, agent3, key3, rec3 = new_site("collision-camsync")
            for ch, phys, name, canon in (("3", "2", "MediaProfile_Channel2_MainStream", False),
                                          ("legacy-profile-3", "2", "old profile", False),
                                          ("2", "2", "Gate", True)):
                cur.execute(
                    """insert into cameras(tenant_id,site_id,recorder_id,channel,physical_channel,
                                           name,is_configured,is_canonical)
                       values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant3, site3, rec3, ch, phys, name, canon, canon))
            out, err = as_anon_try("select wl_sync_cameras(%s,%s,%s::jsonb)", agent3, key3,
                                   json.dumps([{"channel": "2", "name": "CH2"}]))
            r3 = rows(rec3)
            step(err == "" and "3" not in r3 and len(r3) == 3
                 and any(c.startswith("legacy-profile-3-") for c in r3) and r3["2"][2] == "Gate",
                 "a taken legacy name gets a unique suffix; nothing is lost", err or str(sorted(r3)))
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

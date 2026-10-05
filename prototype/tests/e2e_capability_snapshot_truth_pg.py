#!/usr/bin/env python3
"""A recorder capability snapshot is current only for the Agent and driver that took it (NEW-L5).

Self-contained and rolled back. At Al-Khalid sites.capabilities still held a
2026-09-24 dahua-cgi sync (native_ai=true, footage candidate=true) while the
site had run ONVIF since 2026-09-26 with zero native events. Proves:
- a snapshot synced by the current Agent under its current driver is shown;
- the same Agent switching driver in place makes the snapshot unknown, and a
  re-sync under the new driver makes it current again;
- the Agent's startup order (capability sync BEFORE the run's first heartbeat)
  cannot launder a snapshot: sync -> heartbeat on another driver -> heartbeat
  back on the original driver stays unknown (RV-L5-1);
- a snapshot recorded by a previous Agent is unknown (the Al-Khalid shape:
  dahua-cgi sync, then a new ONVIF Agent);
- a sync from a stale, non-current Agent is unknown, never stamped as the
  current Agent's work (RV-L5-2);
- a write no Agent made (direct/service write) is unknown;
- a snapshot older than the current Agent's enrollment is unknown, isolated
  from the Agent-id check (RV-L5-4);
- a snapshot with no recorded provenance (every pre-existing row) is unknown;
- a site with more than one configured recorder never shows the site-wide
  snapshot;
- unknown is fail-closed: no capability keys are returned at all;
- the AI context, Site Control diagnosis and owner recorder read model never
  project the snapshot's native_ai / incident_footage claims;
- the provenance table and helper are not exposed to anon/authenticated.
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

STALE_CLAIMS = {
    "channels": [{"channel": "1", "name": "Gate", "analytics": [
        {"key": "human_vehicle", "label": "People and vehicles", "supported": True,
         "active": True, "geometry": False, "native_ai": True}]}],
    "event_source": "recorder",
    "native_ai": True,
    "incident_footage": {"mode": "on_demand", "format": "dav",
                         "candidate": True, "validated": False},
}


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
            def one(sql, *params):
                return cur.execute(sql, params or None).fetchone()

            def as_auth(uid, sql, *params):
                cur.execute("savepoint auth_sp")
                cur.execute("select set_config('request.jwt.claims', %s, true)",
                            (json.dumps({"sub": str(uid), "role": "authenticated"}),))
                cur.execute("set local role authenticated")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")
                return row

            def as_anon(sql, *params):
                """(row, message): a database error never aborts the transaction."""
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint anon_sp")
                    return None, str(exc).splitlines()[0]
                cur.execute("reset role")
                cur.execute("release savepoint anon_sp")
                return row, ""

            def bootstrap(email, company, site_name):
                uid = one("insert into auth.users(id,email) values (gen_random_uuid(),%s) "
                          "returning id", email)[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = one("select id from sites where tenant_id=%s order by created_at limit 1",
                           boot["tenant_id"])[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant_id, site_id, key, driver, enrolled, seen):
                return one(
                    f"""insert into public.agents(
                          tenant_id,site_id,agent_key_hash,hostname,platform,
                          agent_version,device_vendor,device_model,device_driver,
                          enrolled_at,last_seen_at
                        ) values (
                          %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                          %s,'windows','5.0.26','Dahua','DH-XVR1B08-I',%s,
                          {enrolled},{seen}
                        ) returning id""",
                    tenant_id, site_id, key, f"pc-{key}", driver)[0]

            def sync_caps(agent, key):
                row, msg = as_anon("select wl_sync_capabilities(%s,%s,%s::jsonb)",
                                   agent, key, json.dumps(STALE_CLAIMS))
                assert row, msg
                return row[0]

            def heartbeat(agent, key, driver):
                row, msg = as_anon("select wl_heartbeat(%s,%s,'5.0.27','Dahua',"
                                   "'DH-XVR1B08-I',%s)", agent, key, driver)
                assert row, msg

            def site_caps(uid, site):
                rows = as_auth(uid, "select wl_capabilities()")[0] or []
                return next((r for r in rows if str(r.get("site_id")) == str(site)), None)

            def shown(entry):
                return bool(entry) and entry.get("snapshot_state") == "current" \
                    and isinstance(entry.get("capabilities"), dict) \
                    and entry["capabilities"].get("native_ai") is True

            def hidden(entry):
                return bool(entry) and entry.get("snapshot_state") == "unknown" \
                    and entry.get("capabilities") is None

            # ---------------- single-recorder site ----------------
            uid, tenant, site = bootstrap("caps-truth@watchlog.test", "Caps Truth", "Shop")
            key_a = "caps-agent-a"
            agent_a = add_agent(tenant, site, key_a, "dahua-cgi",
                                "now()-interval '12 days'", "now()")
            sync_caps(agent_a, key_a)
            entry = site_caps(uid, site)
            step(shown(entry),
                 "a snapshot from the current Agent under its current driver is shown",
                 json.dumps(entry, default=str))

            # The same Agent identity switches to another driver in place.
            cur.execute("update agents set device_driver='onvif' where id=%s", (agent_a,))
            entry = site_caps(uid, site)
            step(hidden(entry),
                 "a driver switch in place makes the old snapshot unknown",
                 json.dumps(entry, default=str))
            step(entry is not None and "native_ai" not in json.dumps(entry)
                 and "incident_footage" not in json.dumps(entry),
                 "unknown returns no capability claims at all",
                 json.dumps(entry, default=str))

            sync_caps(agent_a, key_a)
            entry = site_caps(uid, site)
            step(shown(entry), "a re-sync under the new driver is current again",
                 json.dumps(entry, default=str))

            # RV-L5-1: the Agent syncs capabilities at startup BEFORE the run's
            # first heartbeat records the run's driver. Row says 'onvif'; the
            # Agent restarts on dahua-cgi and syncs a dahua-cgi snapshot, then
            # heartbeats dahua-cgi; later it is switched back to ONVIF (which
            # reports no channels, so nothing re-syncs) and heartbeats onvif.
            cur.execute("update agents set device_driver='onvif' where id=%s", (agent_a,))
            sync_caps(agent_a, key_a)
            heartbeat(agent_a, key_a, "dahua-cgi")
            entry_mid = site_caps(uid, site)
            heartbeat(agent_a, key_a, "onvif")
            entry = site_caps(uid, site)
            step(hidden(entry_mid) and hidden(entry),
                 "sync before heartbeat: a driver change and change back stays unknown",
                 json.dumps([entry_mid, entry], default=str))

            # A plain restart on the same driver keeps a truthful snapshot current.
            sync_caps(agent_a, key_a)
            heartbeat(agent_a, key_a, "onvif")
            entry = site_caps(uid, site)
            step(shown(entry), "a restart on the same driver keeps the snapshot current",
                 json.dumps(entry, default=str))

            # A write that no Agent made carries no provenance.
            cur.execute("update sites set capabilities_at=now()-interval '1 minute' "
                        "where id=%s", (site,))
            entry = site_caps(uid, site)
            step(hidden(entry), "a write no Agent made is unknown",
                 json.dumps(entry, default=str))

            # Al-Khalid: a dahua-cgi snapshot, then a new ONVIF Agent enrolls.
            cur.execute("update agents set device_driver='dahua-cgi', "
                        "last_seen_at=now()-interval '9 days' where id=%s", (agent_a,))
            sync_caps(agent_a, key_a)
            key_b = "caps-agent-b"
            agent_b = add_agent(tenant, site, key_b, "onvif",
                                "now()-interval '9 days'", "now()")
            current = one("select wl_current_site_agent(%s)", site)[0]
            entry = site_caps(uid, site)
            step(str(current) == str(agent_b) and hidden(entry),
                 "a snapshot recorded by a previous Agent is unknown",
                 json.dumps([str(current), entry], default=str))

            # RV-L5-2: the old dahua-cgi Agent re-syncs while the ONVIF Agent is
            # the current authority. Its snapshot must never be stamped as B's.
            sync_caps(agent_a, key_a)
            current = one("select wl_current_site_agent(%s)", site)[0]
            prov = one("select agent_id, driver from site_capability_provenance "
                       "where site_id=%s", site)
            entry = site_caps(uid, site)
            step(str(current) == str(agent_b) and hidden(entry)
                 and (prov is None or prov[0] is None),
                 "a stale Agent's sync is unknown, never stamped as the current Agent's",
                 json.dumps([str(current), [str(x) for x in (prov or [])], entry],
                            default=str))

            # A pre-existing snapshot carries no provenance at all.
            sync_caps(agent_b, key_b)
            step(shown(site_caps(uid, site)), "the new Agent's own sync is current",
                 json.dumps(site_caps(uid, site), default=str))
            cur.execute("delete from site_capability_provenance where site_id=%s", (site,))
            entry = site_caps(uid, site)
            step(hidden(entry), "a snapshot with no recorded provenance is unknown",
                 json.dumps(entry, default=str))

            # RV-L5-4: isolate the enrollment clause. The snapshot is the current
            # Agent's own, under its own driver, but that Agent identity is
            # (re-)enrolled after the snapshot was taken.
            sync_caps(agent_b, key_b)
            pre = site_caps(uid, site)
            cur.execute("update agents set enrolled_at=now()+interval '1 second' "
                        "where id=%s", (agent_b,))
            current = one("select wl_current_site_agent(%s)", site)[0]
            entry = site_caps(uid, site)
            step(shown(pre) and str(current) == str(agent_b) and hidden(entry),
                 "a snapshot older than the current Agent's enrollment is unknown",
                 json.dumps([pre, str(current), entry], default=str))
            cur.execute("update agents set enrolled_at=now()-interval '9 days' "
                        "where id=%s", (agent_b,))

            # Read models that reach the AI and the portal never project the snapshot.
            sync_caps(agent_b, key_b)
            cur.execute("update agents set device_driver='dahua-cgi' where id=%s", (agent_b,))
            ai = as_auth(uid, "select wl_ai_context(%s)", site)[0]
            diag = as_auth(uid, "select wl_my_site_diagnosis(%s)", site)[0]
            recs = as_auth(uid, "select wl_my_site_recorders(%s)", site)[0]
            blob = json.dumps([ai, diag, recs], default=str)
            step("native_ai" not in blob and "incident_footage" not in blob
                 and '"event_source"' not in blob,
                 "AI context, diagnosis and owner recorders never carry snapshot claims",
                 blob[:300])

            # ---------------- multi-recorder site ----------------
            uid_m, tenant_m, site_m = bootstrap("caps-multi@watchlog.test", "Caps Multi", "Depot")
            key_m = "caps-agent-m"
            agent_m = add_agent(tenant_m, site_m, key_m, "onvif",
                                "now()-interval '2 days'", "now()")
            rec_a = {"local_key": "rec-a", "display_name": "Recorder A",
                     "is_primary": True, "is_configured": True}
            recs_m, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent_m, key_m,
                                  json.dumps([rec_a]))
            assert recs_m, msg
            sync_caps(agent_m, key_m)
            step(shown(site_caps(uid_m, site_m)),
                 "one-recorder site: current snapshot shown",
                 json.dumps(site_caps(uid_m, site_m), default=str))
            recs_m, msg = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent_m, key_m,
                                  json.dumps([
                                      rec_a,
                                      {"local_key": "rec-b", "display_name": "Recorder B",
                                       "is_primary": False, "is_configured": True}]))
            n_conf = one("select count(*) from recorders where site_id=%s and is_configured",
                         site_m)[0]
            entry = site_caps(uid_m, site_m)
            step(bool(recs_m) and n_conf == 2 and hidden(entry),
                 "a multi-recorder site never shows the site-wide snapshot",
                 msg or json.dumps([n_conf, entry], default=str))

            # ---------------- exposure ----------------
            acl = one("""select
                           has_table_privilege('anon','public.site_capability_provenance','select'),
                           has_table_privilege('authenticated',
                                               'public.site_capability_provenance','select'),
                           has_function_privilege('anon',
                             'public.wl_site_capability_snapshot_current(uuid)','execute'),
                           has_function_privilege('authenticated',
                             'public.wl_site_capability_snapshot_current(uuid)','execute'),
                           (select relrowsecurity from pg_class
                             where oid='public.site_capability_provenance'::regclass),
                           (select proconfig from pg_proc
                             where oid='public.wl_site_capability_snapshot_current(uuid)'
                                       ::regprocedure)""")
            step(acl[:4] == (False, False, False, False) and acl[4] is True
                 and acl[5] and any(c.startswith("search_path=") for c in acl[5]),
                 "provenance table and helper are internal, RLS on, search_path pinned",
                 str(acl))
            wc_anon, msg = as_anon("select wl_capabilities()")
            step(wc_anon is None, "anon cannot read capabilities", msg)
        finally:
            conn.rollback()

    failed = STEPS.count(False)
    print(f"\n{len(STEPS) - failed}/{len(STEPS)} passed")
    return 0 if STEPS and failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())

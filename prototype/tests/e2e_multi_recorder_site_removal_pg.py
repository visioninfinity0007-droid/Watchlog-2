#!/usr/bin/env python3
"""Site removal and tenant deletion after the multi-recorder chain (0146+): real Postgres.

0146-0155 attach recorder-scoped rows to public.recorders through composite
(recorder_id, tenant_id, site_id) foreign keys declared ON DELETE RESTRICT, while
recorders, cameras, events and the other rows also cascade from sites and tenants.
RESTRICT is checked immediately, so a cascade that reaches a recorder before the
rows that reference it would block the owner's "remove site" and an account
deletion. This test proves, on a site with two recorders, cameras, events,
snapshots, recorder_health (and transitions), coverage intervals, a recovery
interval and a recorder-scoped push source:

  * wl_remove_site (owner RPC) removes the site and every recorder-scoped row;
  * deleting the tenant removes the same;
  * another tenant's rows are untouched by both;
  * a direct recorder delete is still RESTRICTed while cameras reference it.

Each deletion runs inside a savepoint and the whole run is rolled back.
Disposable/test Postgres only.

    python prototype/tests/e2e_multi_recorder_site_removal_pg.py
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


def step(ok: bool, name: str, detail: str = "") -> None:
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


HEALTHY = {
    "nvr": {"reachable": True, "auth_ok": True, "reason": "ok", "state": "operational"},
    "channels": {"enumerated": True,
                 "reported": [{"channel": "1", "enabled": True}, {"channel": "2", "enabled": True}]},
}
DOWN = {
    "nvr": {"reachable": False, "auth_ok": None, "reason": "timeout", "state": "unreachable"},
    "channels": {"enumerated": False, "reported": []},
}


def run() -> int:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1":
        sys.exit("FATAL: disposable plain-Postgres test only (set WATCHLOG_CI_PLAIN_POSTGRES=1).")
    dsn = dict(
        host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False,
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
                    return one(sql, *params)
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint auth_sp")

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    return one(sql, *params)
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")

            def attempt(fn):
                """(ok, message); a failure rolls back to the attempt savepoint only."""
                cur.execute("savepoint attempt_sp")
                try:
                    fn()
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint attempt_sp")
                    return False, str(exc).splitlines()[0]
                cur.execute("release savepoint attempt_sp")
                return True, ""

            # Every foreign key that points at public.recorders, so the check
            # covers tables added by any later migration too.
            fks = cur.execute(
                """select c.conrelid::regclass::text, a.attname
                     from pg_constraint c
                     join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
                    where c.contype='f' and c.confrelid='public.recorders'::regclass
                    order by 1""").fetchall()
            restrict = one("""select count(*) from pg_constraint
                               where contype='f' and confrelid='public.recorders'::regclass
                                 and confdeltype='r'""")[0]
            step(restrict >= 5, "recorder-scoped rows use ON DELETE RESTRICT composite keys",
                 f"{restrict} RESTRICT of {len(fks)} foreign keys to recorders")

            def referencing(recorder_ids):
                out = {}
                for table, col in fks:
                    n = one(f"select count(*) from {table} where {col} = any(%s)", list(recorder_ids))[0]
                    if n:
                        out[f"{table}.{col}"] = n
                return out

            def seed(tag):
                uid = one("insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                          f"removal-{tag}@watchlog.test")[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)",
                               f"Removal {tag}", f"Removal Site {tag}")[0]
                tenant, site = boot["tenant_id"], boot["site_id"]
                key = f"removal-agent-{tag}"
                agent = one("""insert into agents(tenant_id,site_id,agent_key_hash,hostname,platform,
                                                  agent_version,last_seen_at,enrolled_at)
                               values (%s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),%s,
                                       'windows','5.0.27',now(),'2026-09-01T00:00:00Z')
                               returning id""", tenant, site, key, f"pc-{tag}")[0]
                recs = as_anon("select wl_sync_recorders(%s,%s,%s::jsonb)", agent, key, json.dumps([
                    {"local_key": "rec-a", "display_name": "Recorder A",
                     "is_primary": True, "is_configured": True},
                    {"local_key": "rec-b", "display_name": "Recorder B",
                     "is_primary": False, "is_configured": True}]))[0]
                rec_a, rec_b = recs["rec-a"], recs["rec-b"]
                for rec in (rec_a, rec_b):
                    as_anon("select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)", agent, key, rec,
                            json.dumps([{"channel": "1", "name": "Door", "is_configured": True},
                                        {"channel": "2", "name": "Yard", "is_configured": True}]))
                    as_anon("select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                            agent, key, rec, json.dumps(HEALTHY))
                as_anon("select wl_report_recorder_health(%s,%s,%s,%s::jsonb)",
                        agent, key, rec_b, json.dumps(DOWN))
                as_anon("select wl_ingest_events(%s,%s,%s::jsonb)", agent, key, json.dumps([
                    {"recorder_id": str(rec), "channel": "1", "event_type": "motion",
                     "device_ts": "2026-10-02T12:00:00Z", "agent_ts": "2026-10-02T12:00:01Z",
                     "snapshot_b64": "YWJj"} for rec in (rec_a, rec_b)]))
                as_anon("select wl_open_recorder_recovery_interval(%s,%s,%s,%s,%s,%s)",
                        agent, key, rec_b, "2026-10-02T10:00:00Z", "2026-10-02T11:00:00Z", ["1"])
                # Recorder push is single-recorder only (wl_push_recorder_for_site), so the
                # recorder-scoped push source is written directly, as 0146 backfills it.
                push_agent = one("""insert into agents(tenant_id,site_id,agent_key_hash,hostname,
                                                       device_driver,last_seen_at)
                                    values (%s,%s,md5(gen_random_uuid()::text),'Recorder push',
                                            'recorder-push',now()) returning id""", tenant, site)[0]
                one("""insert into push_sources(tenant_id,site_id,agent_id,recorder_id)
                       values (%s,%s,%s,%s) returning id""", tenant, site, push_agent, rec_a)
                return {"uid": uid, "tenant": tenant, "site": site, "agent": agent,
                        "recorders": [rec_a, rec_b], "name": f"Removal Site {tag}"}

            a = seed("a")
            other = seed("b")

            def site_counts(s):
                return {
                    "recorders": one("select count(*) from recorders where site_id=%s", s["site"])[0],
                    "cameras": one("select count(*) from cameras where site_id=%s", s["site"])[0],
                    "events": one("select count(*) from events where site_id=%s", s["site"])[0],
                    "snapshots": one("select count(*) from snapshots where site_id=%s", s["site"])[0],
                    "recorder_health": one("select count(*) from recorder_health where site_id=%s",
                                           s["site"])[0],
                    "coverage": one("select count(*) from recorder_coverage_intervals where site_id=%s",
                                    s["site"])[0],
                }

            before = site_counts(a)
            refs = referencing(a["recorders"])
            step(before["recorders"] == 2 and before["cameras"] == 4 and before["events"] == 2
                 and before["snapshots"] == 2 and before["recorder_health"] == 2
                 and before["coverage"] >= 1,
                 "seeded site: 2 recorders, 4 cameras, events, snapshots, recorder_health, coverage",
                 json.dumps(before))
            step(len(refs) >= 5, "recorder-scoped rows reference the recorders through several tables",
                 json.dumps(refs))
            other_before = (site_counts(other), referencing(other["recorders"]))

            # Direct recorder delete stays blocked while its cameras exist.
            ok, msg = attempt(lambda: cur.execute("delete from recorders where id=%s",
                                                  (a["recorders"][0],)))
            step(not ok and "foreign key" in msg,
                 "deleting a recorder that still has cameras is RESTRICTed", msg)

            # 1. Owner removes the site.
            cur.execute("savepoint remove_site")
            res = None

            def remove():
                nonlocal res
                res = as_auth(a["uid"], "select wl_remove_site(%s,%s)", a["site"], a["name"])[0]

            ok, msg = attempt(remove)
            step(ok and res and res.get("ok") is True,
                 "wl_remove_site succeeds on a multi-recorder site", msg or json.dumps(res))
            after = site_counts(a)
            step(ok and all(v == 0 for v in after.values()) and not referencing(a["recorders"]),
                 "site removal leaves no recorder, camera, event, snapshot, health or coverage row",
                 json.dumps({"after": after, "refs": referencing(a["recorders"])}))
            step((site_counts(other), referencing(other["recorders"])) == other_before,
                 "site removal leaves another tenant's recorders and rows untouched")
            cur.execute("rollback to savepoint remove_site")

            # 2. Tenant (account) deletion.
            cur.execute("savepoint delete_tenant")
            ok, msg = attempt(lambda: cur.execute("delete from tenants where id=%s", (a["tenant"],)))
            step(ok, "deleting the tenant succeeds with recorder-scoped rows present", msg)
            after = site_counts(a)
            step(ok and all(v == 0 for v in after.values()) and not referencing(a["recorders"])
                 and one("select count(*) from sites where tenant_id=%s", a["tenant"])[0] == 0,
                 "tenant deletion leaves no site, recorder or recorder-scoped row",
                 json.dumps({"after": after, "refs": referencing(a["recorders"])}))
            step((site_counts(other), referencing(other["recorders"])) == other_before,
                 "tenant deletion leaves another tenant's recorders and rows untouched")
            cur.execute("rollback to savepoint delete_tenant")
            step(site_counts(a) == before, "rolled back: the seeded site is intact again")
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

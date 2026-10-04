#!/usr/bin/env python3
"""Same-site reinstall re-attaches to the continuity recorder (MNVR-017).

Uninstall removes the Agent identity and the Secrets directory but keeps
recorders.json. Setup quarantines that registry and stages the newly proven
recorder; the fresh row keeps the old continuity recorder's local id. This runs
the real Setup staging (setup_backend._stage_recorder_registry) and the real
Agent startup binding (multi_recorder_orchestrator.bind_cloud_identities) with
the cloud calls executed as `anon` RPCs on Postgres.

Proves:
- the old Agent binds A (continuity) and B;
- after uninstall + reinstall on the same site, the new Agent's sync maps the
  staged row to the existing continuity recorder A: no third recorder;
- control: a newly minted local id (the old behaviour) would add a recorder.

Needs the multi-recorder migrations (0146-0155). Disposable local Postgres only;
everything runs in one transaction that is rolled back.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from e2e_setup_recorder_disable_pg import ENV, PgCloud, _plain_secrets, agent_bind, psycopg

import credential_store as cs
import recorder_registry as rr
import setup_backend as sb

STEPS = []


def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def run():
    dsn = dict(
        host=ENV["SUPABASE_DB_HOST"],
        port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
        user=ENV["SUPABASE_DB_USER"],
        password=ENV["SUPABASE_DB_PASSWORD"],
        dbname=ENV.get("SUPABASE_DB_NAME", "postgres"),
        connect_timeout=30,
        autocommit=False,
    )
    saved_programdata = os.environ.get("PROGRAMDATA")
    tmp = tempfile.TemporaryDirectory()
    os.environ["PROGRAMDATA"] = tmp.name
    _plain_secrets()
    try:
        with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
            try:
                _scenario(cur)
            finally:
                conn.rollback()
    finally:
        if saved_programdata is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = saved_programdata
        tmp.cleanup()


def _agent(cur, tenant_id, site_id, key, hostname):
    return cur.execute(
        """insert into agents(tenant_id,site_id,agent_key_hash,hostname,platform,
                              agent_version,last_seen_at,enrolled_at)
           values(%s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                  %s,'windows','5.1.0',now(),now())
           returning id""",
        (tenant_id, site_id, key, hostname),
    ).fetchone()[0]


def _site_recorders(cur, site_id):
    return {str(r[0]): r[1:] for r in cur.execute(
        "select id,continuity_owner,is_primary,is_configured from recorders where site_id=%s",
        (site_id,)).fetchall()}


def _scenario(cur):
    uid = cur.execute(
        "insert into auth.users(id,email) values(gen_random_uuid(),%s) returning id",
        ("setup-reinstall@watchlog.test",),
    ).fetchone()[0]
    cur.execute("savepoint boot_sp")
    cur.execute("select set_config('request.jwt.claims',%s,true)",
                (json.dumps({"sub": str(uid), "role": "authenticated"}),))
    cur.execute("set local role authenticated")
    boot = cur.execute("select wl_bootstrap_tenant(%s,%s)",
                       ("Setup Reinstall Test", "Warehouse Setup Reinstall")).fetchone()[0]
    cur.execute("reset role")
    cur.execute("release savepoint boot_sp")
    tenant_id = boot["tenant_id"]
    site_id = cur.execute(
        "select id from sites where tenant_id=%s order by created_at limit 1", (tenant_id,),
    ).fetchone()[0]
    cloud = PgCloud(cur)

    # --- the earlier installation: A (continuity) and B, bound by its Agent ---
    key1 = "reinstall-old-agent-key"
    agent1 = _agent(cur, tenant_id, site_id, key1, "old-install")
    state1 = {"agent_id": str(agent1), "agent_key": key1,
              "site_id": str(site_id), "tenant_id": str(tenant_id)}
    root = Path(os.environ["PROGRAMDATA"]) / "WatchLog"
    root.mkdir(parents=True, exist_ok=True)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    cs.save_recorder_credential(a, "old-a", "old-pw-a")
    cs.save_recorder_credential(b, "old-b", "old-pw-b")
    rr.save_registry({"schema": rr.REGISTRY_SCHEMA, "recorders": [
        {"local_id": a, "cloud_recorder_id": None, "display_name": "Recorder A",
         "url": "http://192.0.2.10", "driver": "hikvision", "is_primary": True,
         "continuity_owner": True, "is_configured": True},
        {"local_id": b, "cloud_recorder_id": None, "display_name": "Recorder B",
         "url": "http://192.0.2.20", "driver": "dahua-cgi", "is_primary": False,
         "continuity_owner": False, "is_configured": True},
    ]})
    first = agent_bind(cloud, state1, root)
    cloud_a = first.get(a)
    step(set(first) == {a, b} and len(_site_recorders(cur, site_id)) == 2,
         "the earlier installation bound A and B", json.dumps(first))

    # --- uninstall: Secrets and identity removed, recorders.json kept ---
    shutil.rmtree(root / "Secrets")

    # --- reinstall on the same site: a new Agent identity, the old one stale ---
    cur.execute("update agents set last_seen_at=now()-interval '1 hour', "
                "enrolled_at=now()-interval '2 hours' where id=%s", (agent1,))
    key2 = "reinstall-new-agent-key"
    agent2 = _agent(cur, tenant_id, site_id, key2, "new-install")
    state2 = {"agent_id": str(agent2), "agent_key": key2,
              "site_id": str(site_id), "tenant_id": str(tenant_id)}
    ini = root / "watchlog.ini"
    ini.write_text("[watchlog]\nnvr_url = http://192.0.2.50\nnvr_driver = hikvision\n",
                   encoding="utf-8")
    cs.save_nvr_credential("admin", "new-pw")          # as finalize_install does first
    proven = {"url": "http://192.0.2.50", "vendor": "Hikvision", "model": "DS-NEW",
              "driver": "hikvision", "serial": "SER-NEW"}
    sb._stage_recorder_registry(ini, proven, "admin", "new-pw", None, state2)
    (row,) = rr.recorders()
    step(row["local_id"] == a and row["cloud_recorder_id"] is None,
         "Setup staged the reinstall under the continuity local id, unbound",
         row["local_id"][:8])

    # Control: a newly minted local id would fork the site's recorders.
    cur.execute("savepoint control_sp")
    cloud.call("wl_sync_recorders", p_agent_id=state2["agent_id"], p_agent_key=key2,
               p_recorders=[{"local_key": str(uuid.uuid4()), "display_name": "Primary Recorder",
                             "is_primary": True, "is_configured": True}])
    forked = len(_site_recorders(cur, site_id))
    cur.execute("rollback to savepoint control_sp")
    step(forked == 3, "control: a new local id adds a third recorder", f"{forked} recorders")

    second = agent_bind(cloud, state2, root)
    rows = _site_recorders(cur, site_id)
    step(second == {a: cloud_a}, "the new Agent re-attached to continuity recorder A",
         json.dumps(second))
    step(len(rows) == 2 and rows.get(cloud_a) == (True, True, True),
         "no third recorder; A stays the configured continuity primary", str(rows))
    step((rr.recorder(a) or {}).get("cloud_recorder_id") == cloud_a,
         "the registry row is bound to A again")
    print(f"  INFO  B after reinstall (configured={rows.get(first.get(b), (None,) * 3)[2]}): "
          "re-adding or retiring it is a separate Manage Recorders step")


if __name__ == "__main__":
    run()
    sys.exit(0 if all(STEPS) else 1)

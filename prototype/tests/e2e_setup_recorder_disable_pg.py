#!/usr/bin/env python3
"""Manage Recorders > Disable against the real recorder registry contract (MNVR-003).

Runs the actual Setup code (setup_backend.disable_managed_recorder) and the
actual Agent registry sync (multi_recorder_orchestrator._sync_registry_state)
with their cloud calls executed as `anon` RPCs on Postgres, so the payload Setup
builds meets the real wl_sync_recorders rules (0154: a non-empty payload names
exactly one configured primary; the continuity recorder is never disabled).

Proves:
- the Agent binds a two-recorder registry (A primary/continuity, B secondary);
- Setup's disable of B is accepted, B is no longer configured in WatchLog,
  A stays the configured primary, and only then is B disabled on this PC;
- control: a payload naming only the disabled recorder (no primary) is
  rejected, which is why Setup sends every bound row.

Needs the multi-recorder migrations (0146-0155). Disposable local Postgres only;
everything runs in one transaction that is rolled back.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

ENV = {}
env_path = ROOT.parent / ".env"
for line in env_path.read_text(errors="ignore").splitlines() if env_path.exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for key in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
            "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(key):
        ENV[key] = os.environ[key]

import psycopg  # noqa: E402

import credential_store as cs  # noqa: E402
import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
import watchlog_agent as core  # noqa: E402

STEPS = []


def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


class PgCloud:
    """core.Cloud.call executed as an anon RPC inside the test transaction."""

    def __init__(self, cur):
        self.cur = cur
        self.calls = []

    def call(self, fn, **params):
        if not re.fullmatch(r"wl_[a-z0-9_]+", fn):
            raise AssertionError(fn)
        self.calls.append((fn, params))
        names = sorted(params)
        args = ", ".join(
            f"{n} => %s" + ("::jsonb" if isinstance(params[n], (dict, list)) else "")
            for n in names
        )
        values = [
            json.dumps(params[n]) if isinstance(params[n], (dict, list)) else params[n]
            for n in names
        ]
        self.cur.execute("savepoint rpc_sp")
        self.cur.execute("set local role anon")
        try:
            row = self.cur.execute(f"select public.{fn}({args})", values).fetchone()
        except psycopg.Error as exc:
            self.cur.execute("rollback to savepoint rpc_sp")
            raise core.CloudError(fn, 400, getattr(exc.diag, "sqlstate", None),
                                  str(exc).splitlines()[0]) from None
        self.cur.execute("reset role")
        self.cur.execute("release savepoint rpc_sp")
        return row[0]


def _plain_secrets():
    """DPAPI is Windows-only; the registry flow only needs a round-trip store."""
    def wjs(path, obj):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("JSON:" + json.dumps(obj), encoding="utf-8")

    def rjs(path):
        return json.loads(Path(path).read_text(encoding="utf-8")[5:])

    cs.write_json_secret, cs.read_json_secret = wjs, rjs


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


def _scenario(cur):
    uid = cur.execute(
        "insert into auth.users(id,email) values(gen_random_uuid(),%s) returning id",
        ("setup-disable@watchlog.test",),
    ).fetchone()[0]
    cur.execute("savepoint boot_sp")
    cur.execute("select set_config('request.jwt.claims',%s,true)",
                (json.dumps({"sub": str(uid), "role": "authenticated"}),))
    cur.execute("set local role authenticated")
    boot = cur.execute("select wl_bootstrap_tenant(%s,%s)",
                       ("Setup Disable Test", "Warehouse Setup Disable")).fetchone()[0]
    cur.execute("reset role")
    cur.execute("release savepoint boot_sp")
    tenant_id = boot["tenant_id"]
    site_id = cur.execute(
        "select id from sites where tenant_id=%s order by created_at limit 1", (tenant_id,),
    ).fetchone()[0]
    key = "setup-disable-agent-key"
    agent_id = cur.execute(
        """insert into agents(tenant_id,site_id,agent_key_hash,hostname,platform,
                              agent_version,last_seen_at)
           values(%s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                  'setup-disable-agent','windows','5.1.0',now())
           returning id""",
        (tenant_id, site_id, key),
    ).fetchone()[0]
    state = {"agent_id": str(agent_id), "agent_key": key,
             "site_id": str(site_id), "tenant_id": str(tenant_id)}
    cloud = PgCloud(cur)

    root = Path(os.environ["PROGRAMDATA"]) / "WatchLog"
    root.mkdir(parents=True, exist_ok=True)
    ini = root / "watchlog.ini"
    ini.write_text("[watchlog]\nsupabase_url = https://example.invalid\n"
                   "supabase_publishable_key = test\n", encoding="utf-8")
    a, b = "11111111-1111-4111-8111-11111111111a", "22222222-2222-4222-8222-22222222222b"
    cs.save_recorder_credential(a, "user-a", "pw-a")
    cs.save_recorder_credential(b, "user-b", "pw-b")
    rr.save_registry({
        "schema": rr.REGISTRY_SCHEMA,
        "recorders": [
            {"local_id": a, "cloud_recorder_id": None, "display_name": "Recorder A",
             "url": "http://192.0.2.10", "driver": "hikvision", "is_primary": True,
             "continuity_owner": True, "is_configured": True},
            {"local_id": b, "cloud_recorder_id": None, "display_name": "Recorder B",
             "url": "http://192.0.2.20", "driver": "dahua-cgi", "is_primary": False,
             "continuity_owner": False, "is_configured": True},
        ],
    })

    # The Agent's own startup registry sync binds both rows.
    mapping = mro._sync_registry_state(cloud, state)
    rows = {r["local_id"]: r for r in rr.recorders()}
    step(set(mapping) == {a, b} and all(rows[x]["cloud_recorder_id"] for x in (a, b)),
         "Agent registry sync bound A and B", json.dumps(mapping))
    cloud_a, cloud_b = rows[a]["cloud_recorder_id"], rows[b]["cloud_recorder_id"]

    # Control: the payload of a single non-primary row is not a valid desired state.
    try:
        cloud.call("wl_sync_recorders", p_agent_id=state["agent_id"],
                   p_agent_key=key, p_recorders=[
                       {"local_key": b, "display_name": "Recorder B",
                        "is_primary": False, "is_configured": False}])
        rejected, message = False, "accepted"
    except core.CloudError as exc:
        rejected, message = True, str(exc)
    step(rejected and "exactly one primary" in message,
         "control: a disable payload without the primary is rejected", message[:160])

    # Setup's real disable flow (activation of the background Agent stubbed).
    sb._lifecycle_cloud = lambda _config_path: (cloud, dict(state))
    sb.ensure_background_agent = lambda *a_, **k_: {"started": True, "detail": "e2e"}
    sb.confirm_background_agent = lambda *a_, **k_: {"confirmed": True, "detail": "e2e"}
    try:
        out = sb.disable_managed_recorder(ini, b)
        ok, detail = True, json.dumps({k: out.get(k) for k in ("is_configured", "retained_events")})
    except Exception as exc:  # noqa: BLE001
        out, ok, detail = None, False, f"{type(exc).__name__}: {exc}"
    step(ok and out and out.get("is_configured") is False,
         "Setup disable of B is confirmed by wl_sync_recorders", detail)

    sent = [kw["p_recorders"] for fn, kw in cloud.calls if fn == "wl_sync_recorders"][-1]
    step({r["local_key"] for r in sent} == {a, b}
         and [r["local_key"] for r in sent if r["is_primary"]] == [a],
         "Setup sent the whole bound registry with A as the one primary",
         json.dumps([(r["local_key"][:8], r["is_primary"], r["is_configured"]) for r in sent]))

    db = {str(r[0]): r[1:] for r in cur.execute(
        "select id,is_primary,is_configured,continuity_owner from recorders where site_id=%s",
        (site_id,)).fetchall()}
    step(db.get(cloud_b) == (False, False, False),
         "B is no longer configured in WatchLog", str(db.get(cloud_b)))
    step(db.get(cloud_a) == (True, True, True) and len(db) == 2,
         "A stays the configured primary and continuity owner", str(db))
    step(rr.recorder(b)["is_configured"] is False and rr.recorder(a)["is_configured"] is True,
         "B is disabled on this PC only after WatchLog confirmed")


if __name__ == "__main__":
    run()
    sys.exit(0 if all(STEPS) else 1)

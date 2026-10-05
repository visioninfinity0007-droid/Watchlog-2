#!/usr/bin/env python3
"""Recorder-scoped owner truth (0157): real Postgres.

Rolled back after execution. Runs against the FINAL chain.

Proves, for a single-recorder site whose recorder reports its own vendor/model:
- wl_my_site_diagnosis serves the recorder-scoped capability profile: every row
  carries evidence_scope, so Site Control can tell evidence proven on THIS
  recorder from evidence proven on another unit of the same model (MNVR-049);
- a model-level FIELD_VERIFIED row with no evidence for this recorder is not
  FIELD_VERIFIED here; with this recorder's own field evidence it is
  FIELD_VERIFIED with evidence_scope 'recorder';
- wl_ai_context (Watch AI) serves exactly the same capability rows;
- a site identified only through its Agent keeps the model profile (no
  evidence_scope), which no reader presents as verified on this recorder.
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

# The model with FIELD_VERIFIED rows in the capability KB (proven on one unit).
VENDOR, MODEL = "Dahua", "DH-XVR1B08-I"


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

            def as_anon(sql, *params):
                cur.execute("savepoint anon_sp")
                cur.execute("set local role anon")
                try:
                    row = cur.execute(sql, params or None).fetchone()
                finally:
                    cur.execute("reset role")
                    cur.execute("release savepoint anon_sp")
                return row

            def bootstrap(email, company, site_name):
                uid = cur.execute(
                    "insert into auth.users(id,email) values (gen_random_uuid(),%s) returning id",
                    (email,),
                ).fetchone()[0]
                boot = as_auth(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
                site = cur.execute(
                    "select id from sites where tenant_id=%s order by created_at limit 1",
                    (boot["tenant_id"],),
                ).fetchone()[0]
                return uid, boot["tenant_id"], site

            def add_agent(tenant, site, key, vendor=None, model=None):
                return cur.execute(
                    """insert into public.agents(
                         tenant_id,site_id,agent_key_hash,hostname,platform,
                         agent_version,device_vendor,device_model,device_driver,last_seen_at
                       ) values (
                         %s,%s,encode(sha256(convert_to(%s,'UTF8')),'hex'),
                         'owner-truth-agent','windows','5.1.0',%s,%s,'dahua-cgi',now()
                       ) returning id""",
                    (tenant, site, key, vendor, model),
                ).fetchone()[0]

            def by_cap(rows):
                return {r["capability"]: r for r in rows or []}

            model_rows = cur.execute(
                "select wl_recorder_profile(%s,%s)", (VENDOR, MODEL)
            ).fetchone()[0]
            model_verified = sorted(
                r["capability"] for r in model_rows if r["evidence_class"] == "FIELD_VERIFIED"
            )
            step(bool(model_verified),
                 "fixture: the model profile has FIELD_VERIFIED rows (proven on another unit)",
                 str(model_verified))
            target = "channel_title" if "channel_title" in model_verified else (model_verified or [""])[0]

            # ----------------------------------------------------------
            # Single recorder that reports its own identity.
            # ----------------------------------------------------------
            uid, tenant, site = bootstrap("owner-truth-a@watchlog.test", "Owner Truth A", "Site A")
            key = "owner-truth-agent-a"
            agent = add_agent(tenant, site, key)
            rec = as_anon(
                "select wl_sync_recorders(%s,%s,%s::jsonb)",
                agent, key, json.dumps([{
                    "local_key": "rec-a", "display_name": "Recorder A",
                    "vendor": VENDOR, "model": MODEL, "firmware": "fw-a",
                    "identity_fingerprint": "fp-a", "driver": "dahua-cgi",
                    "is_primary": True, "is_configured": True,
                }]),
            )[0]["rec-a"]
            as_anon(
                "select wl_sync_recorder_cameras(%s,%s,%s,%s::jsonb)",
                agent, key, rec,
                json.dumps([{"channel": "1", "name": "Gate", "is_configured": True}]),
            )

            diag = as_auth(uid, "select wl_my_site_diagnosis(%s)", site)[0]
            caps = diag.get("capabilities") or []
            step(diag.get("recorder_count") == 1 and len(caps) == len(model_rows)
                 and diag.get("capability_known") is True,
                 "single-recorder diagnosis lists one row per capability of the recorder's model",
                 f"{len(caps)} rows vs {len(model_rows)}")
            step(caps and all("evidence_scope" in r for r in caps),
                 "every diagnosis capability row carries evidence_scope (MNVR-049)",
                 json.dumps(caps[:1], default=str))
            here = [r["capability"] for r in caps if r.get("evidence_class") == "FIELD_VERIFIED"]
            step(here == [],
                 "model-level FIELD_VERIFIED from another unit is not FIELD_VERIFIED on this recorder",
                 str(here))
            step(all(r.get("evidence_scope") != "recorder" for r in caps),
                 "no row is recorder-scoped before this recorder has its own field evidence")

            ctx = as_auth(uid, "select wl_ai_context(%s)", site)[0]
            step(ctx.get("capabilities") == caps,
                 "Watch AI context serves the same recorder-scoped rows as Site Control")

            cur.execute(
                """insert into recorder_field_evidence(
                     id,site_id,recorder_id,identity_fingerprint,vendor,model,
                     firmware,capability,operation,result,evidence_class,
                     test_date,read_back_verified
                   ) values (
                     'TEST-OWNER-TRUTH-A',%s,%s,'fp-a',%s,%s,'fw-a',%s,'read_write',
                     'write applied and read back','FIELD_VERIFIED',current_date,true
                   )""",
                (site, rec, VENDOR, MODEL, target),
            )
            diag = as_auth(uid, "select wl_my_site_diagnosis(%s)", site)[0]
            row = by_cap(diag.get("capabilities")).get(target) or {}
            step(row.get("evidence_class") == "FIELD_VERIFIED"
                 and row.get("evidence_scope") == "recorder"
                 and row.get("verdict") == "supported",
                 "this recorder's own field evidence makes the row FIELD_VERIFIED with evidence_scope 'recorder'",
                 json.dumps(row, default=str))
            others = [r["capability"] for r in diag.get("capabilities") or []
                      if r["capability"] != target and r.get("evidence_scope") == "recorder"]
            step(others == [], "only the capability with evidence is recorder-scoped", str(others))
            ctx = as_auth(uid, "select wl_ai_context(%s)", site)[0]
            step(by_cap(ctx.get("capabilities")).get(target) == row,
                 "Watch AI context carries the same recorder-scoped row")

            # ----------------------------------------------------------
            # Identity known only from the current site Agent.
            # ----------------------------------------------------------
            ub, tb, sb = bootstrap("owner-truth-b@watchlog.test", "Owner Truth B", "Site B")
            add_agent(tb, sb, "owner-truth-agent-b", VENDOR, MODEL)
            cur.execute(
                """insert into cameras (tenant_id,site_id,channel,name,is_configured)
                   values (%s,%s,'1','Camera 1',true)""",
                (tb, sb),
            )
            diag_b = as_auth(ub, "select wl_my_site_diagnosis(%s)", sb)[0]
            step(diag_b["recorder"]["identified"] is True
                 and diag_b.get("capabilities") == model_rows,
                 "an Agent-only identity keeps the model profile",
                 json.dumps(diag_b.get("recorder"), default=str))
            step(all("evidence_scope" not in r for r in diag_b.get("capabilities") or []),
                 "the model profile never claims recorder scope")
        finally:
            conn.rollback()

    passed = sum(1 for s in STEPS if s)
    print(f"\n  {passed}/{len(STEPS)} steps passed")
    return 0 if passed == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())

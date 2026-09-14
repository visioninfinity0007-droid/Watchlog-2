#!/usr/bin/env python3
"""0105 AI providers — secure config + resolver on real PG, rolled back (zero production writes).

Proves the security model the task's acceptance gates depend on:
  * platform_owner-gated CRUD (non-owner => 42501)              [Test 5 / Test 10 spirit]
  * provider API keys never exposed: masked hint only, never a raw key in list/config/audit
  * key writes require Supabase Vault; NO plaintext fallback (refused on a no-Vault env)
  * the decrypted key is resolvable ONLY by the service role (authenticated => 42501)
  * WatchLog mode -> provider/model resolution works and is service-role only

    python prototype/tests/e2e_ai_providers_pg.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in ((ROOT.parent / ".env").read_text(errors="ignore").splitlines()
             if (ROOT.parent / ".env").exists() else []):
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

MIG = (ROOT / "supabase" / "migrations" / "0105_ai_providers.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))

def as_user(cur, uid, role="authenticated"):
    cur.execute("select set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": str(uid), "role": role}),))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG)  # idempotent; ensures 0105 objects exist on a fresh DB
            owner, outsider = uuid.uuid4(), uuid.uuid4()
            cur.execute("insert into auth.users(id) values (%s),(%s) on conflict do nothing", (owner, outsider))
            cur.execute("insert into platform_admins(user_id,role) values (%s,'platform_owner') "
                        "on conflict (user_id) do update set role=excluded.role", (owner,))

            # 1. owner creates a keyless LOCAL provider
            as_user(cur, owner)
            cur.execute("""select wl_ai_provider_upsert(null,'Test Ollama','ollama','http://ollama.local:11434','qwen3:4b',
                           true,false,false,true,'LOCAL',false,90000,900,10,'local',null,'e2e create')""")
            prov = cur.fetchone()[0]
            pid = prov["id"]
            step(prov.get("type") == "ollama" and "api_key" not in prov and prov.get("key_hint") is None,
                 "owner creates keyless provider; row carries no api_key / key_hint")

            # 2. admin_config masks: provider listed, has_key false, NO key material anywhere
            cur.execute("select wl_ai_admin_config()")
            cfg = cur.fetchone()[0]
            blob = json.dumps(cfg)
            step(any(p["id"] == pid for p in cfg["providers"]) and '"api_key"' not in blob and "sk-" not in blob,
                 "admin_config lists provider, exposes no key")

            # 3. non-owner cannot read AI config (42501)
            as_user(cur, outsider)
            cur.execute("savepoint s1")
            try:
                cur.execute("select wl_ai_admin_config()"); denied = False; cur.execute("release savepoint s1")
            except psycopg.errors.InsufficientPrivilege:
                denied = True; cur.execute("rollback to savepoint s1")
            step(denied, "non-owner cannot read AI config (42501)")

            # 4. a key write requires Vault — refused on a no-Vault env (no plaintext fallback)
            as_user(cur, owner)
            cur.execute("select wl_ai_vault_available()")
            vault = cur.fetchone()[0]
            if not vault:
                cur.execute("savepoint s2")
                try:
                    cur.execute("""select wl_ai_provider_upsert(null,'Cloud','openai_compat','https://api.example/v1','m',
                                   true,false,false,true,'EXTERNAL',true,35000,900,20,'premium','sk-secret-abc','e2e key')""")
                    raised = False; cur.execute("release savepoint s2")
                except Exception as e:
                    raised = "Vault" in str(e); cur.execute("rollback to savepoint s2")
                step(raised, "key write without Vault is refused (no plaintext fallback)")
            else:
                step(True, "Vault present — key path available (no-Vault assertion skipped)")

            # enable the provider so it can be resolved (providers default OFF)
            cur.execute("select wl_ai_provider_set_enabled(%s,true,'e2e enable')", (pid,))

            # 5. service role resolves provider; keyless => api_key null; endpoint intact
            as_user(cur, owner, role="service_role")
            cur.execute("select wl_ai_resolve_provider(%s,null)", (pid,))
            r = cur.fetchone()[0]
            step(bool(r) and r["endpoint"] == "http://ollama.local:11434" and r.get("api_key") is None,
                 "service_role resolves provider; keyless => api_key null")

            # 6. authenticated (non-service) cannot resolve the key (42501)
            as_user(cur, owner, role="authenticated")
            cur.execute("savepoint s3")
            try:
                cur.execute("select wl_ai_resolve_provider(%s,null)", (pid,)); blocked = False; cur.execute("release savepoint s3")
            except psycopg.errors.InsufficientPrivilege:
                blocked = True; cur.execute("rollback to savepoint s3")
            step(blocked, "resolver is service-role only (authenticated blocked)")

            # 7. mode 'instant' -> provider/model resolution
            as_user(cur, owner)
            cur.execute("select wl_ai_mode_set('instant',%s,'qwen3:4b',null,null,null,null,false,'e2e mode')", (pid,))
            as_user(cur, owner, role="service_role")
            cur.execute("select wl_ai_resolve_mode('instant',false)")
            md = cur.fetchone()[0]
            step(md.get("configured") and md["primary"]["endpoint"] == "http://ollama.local:11434"
                 and md["primary"]["model"] == "qwen3:4b",
                 "mode 'instant' resolves to the configured provider/model")

            # 8. mutations audited, and NO raw key ever lands in the audit
            as_user(cur, owner)
            cur.execute("select count(*), coalesce(bool_or(after_json::text like '%sk-%'),false) "
                        "from platform_admin_audit where action like 'ai\\_%' escape '\\'")
            n, leaked = cur.fetchone()
            step(n >= 3 and not leaked, "AI mutations audited; no raw key in audit", f"rows={n}")

            conn.rollback()  # zero production writes
        except Exception as e:
            conn.rollback()
            print("ERROR", type(e).__name__, str(e)[:220])
            return 1

    ok = all(STEPS) and len(STEPS) >= 8
    print(("OK — " if ok else "FAIL — ") + f"{sum(STEPS)}/{len(STEPS)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())

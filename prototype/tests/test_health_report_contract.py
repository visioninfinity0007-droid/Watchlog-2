#!/usr/bin/env python3
"""Static contract for Phase A increment 3 (migration 0044) — no database.

Pins wl_report_health to the increment's rules: agent-authenticated, tenant/site bound to
the AGENT ROW (never the payload), inventory derived like inventory_model, transitions only
on change, recording/HDD/snapshot/event work explicitly out of scope, and no secrets stored.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
import inventory_model as inv  # noqa: E402  (proves the oracle module is valid + shares vocab)

MIG = (ROOT / "prototype/supabase/migrations/0044_agent_health_report.sql").read_text(encoding="utf-8")


def func_body(name: str) -> str:
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def main():
    fn = func_body("wl_report_health")

    # --- agent auth + definer posture ----------------------------------------
    assert "security definer" in fn and "set search_path = public" in fn, "must be definer w/ search_path"
    require("wl_auth_agent(p_agent_id, p_agent_key)", "must authenticate the agent by key", fn)
    require("using errcode = '28000'", "unrecognised agent must raise 28000", fn)
    require("revoke all on function public.wl_report_health(uuid, text, jsonb) from public",
            "must revoke from public")
    require("grant execute on function public.wl_report_health(uuid, text, jsonb) to anon, authenticated",
            "agent RPC must be granted to anon+authenticated")

    # --- tenant/site come from the AGENT ROW, never the payload ---------------
    assert "v_agent.tenant_id" in fn and "v_agent.site_id" in fn, "must bind to the agent row"
    assert "cm.site_id = v_agent.site_id" in fn, "inventory must be scoped to the agent's site"
    # the payload must never be a source of tenant/site identity: no tenant/site KEY is
    # ever extracted from p_report / v_nvr.
    if re.search(r"(p_report|v_nvr)\s*(#>>?|->>?)\s*'?\{?[^;']*(tenant|site)", fn, re.I):
        raise AssertionError("tenant/site must not be extracted from the report payload")

    # --- inventory derivation mirrors inventory_model -------------------------
    for tok in (inv.PRESENT, inv.MISSING, inv.DISABLED, inv.UNKNOWN):
        require(f"'{tok}'", f"inventory state '{tok}' missing from derivation", fn)
    # determinable gate = reachable AND auth AND enumeration succeeded
    assert "v_reachable and coalesce(v_auth_ok, true) and v_enumerated" in fn, \
        "UNKNOWN unless recorder reachable+auth AND channels enumerated"
    require("else 'missing'", "a known channel not reported must become MISSING", fn)

    # --- transitions only on change (idempotency) -----------------------------
    assert fn.count("is distinct from") >= 2, "nvr and inventory transitions must guard on change"
    require("p.inventory_state is distinct from d.state", "inventory transition must fire only on change", fn)

    # --- explicitly OUT of scope for increment 3 ------------------------------
    for out_of_scope in ("recording_state", "storage_state", "snapshot", "get_clip", "incident"):
        if out_of_scope in fn:
            raise AssertionError(f"increment 3 must not touch {out_of_scope}")
    # must not write the security event/snapshot streams
    if re.search(r"insert into (public\.)?(events|snapshots)\b", fn, re.I):
        raise AssertionError("health report must not write events/snapshots")

    # --- no secrets stored ----------------------------------------------------
    code = "\n".join(l for l in fn.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential", "agent_key_hash"):
        if secret in code.lower():
            raise AssertionError(f"health report must not handle/store {secret}")

    print("Agent health report contract: PASS")


if __name__ == "__main__":
    main()

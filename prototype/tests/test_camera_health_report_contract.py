#!/usr/bin/env python3
"""Static contract for Phase A increment 4 (migration 0045) — no database.

Pins wl_report_camera_health: agent-authenticated, tenant/site bound to the agent row,
camera-bound by channel to this site only, states/reasons clamped to their domains,
transitions only on change, no recording/snapshot/event work, and no image bytes or
secrets accepted or stored.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = (ROOT / "prototype/supabase/migrations/0045_camera_health_report.sql").read_text(encoding="utf-8")


def func_body(name):
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def main():
    fn = func_body("wl_report_camera_health")

    # agent auth + definer posture + grants
    assert "security definer" in fn and "set search_path = public" in fn, "definer + search_path"
    require("wl_auth_agent(p_agent_id, p_agent_key)", "must authenticate the agent", fn)
    require("using errcode = '28000'", "unrecognised agent must raise 28000", fn)
    require("revoke all on function public.wl_report_camera_health(uuid, text, jsonb) from public",
            "must revoke from public")
    require("grant execute on function public.wl_report_camera_health(uuid, text, jsonb) to anon, authenticated",
            "agent RPC granted to anon+authenticated")

    # tenant/site from the agent row, camera bound to this site only
    assert "v_agent.tenant_id" in fn and "v_agent.site_id" in fn, "must bind to the agent row"
    require("cm.site_id = v_agent.site_id and cm.channel = r.channel",
            "cameras must bind by channel within the agent's site", fn)
    if re.search(r"(p_report|c)\s*(#>>?|->>?)\s*'?\{?[^;']*(tenant|site)", fn, re.I):
        raise AssertionError("tenant/site must not come from the payload")

    # health + reason clamped to their domains (buggy agent cannot poison the column)
    require("in ('operational','degraded','offline','unknown')", "health_state must be clamped", fn)
    require("else 'unknown' end as reason", "reason_code must be clamped to the domain", fn)

    # transitions only on change
    require("p.health_state is distinct from m.health", "transition only when health changes", fn)

    # OUT of scope for increment 4
    for oos in ("recording_state", "storage_state", "snapshot", "get_clip", "incident", "base64"):
        if oos in fn:
            raise AssertionError(f"increment 4 must not touch {oos}")
    if re.search(r"insert into (public\.)?(events|snapshots)\b", fn, re.I):
        raise AssertionError("camera health must not write events/snapshots")

    # no secrets / no image bytes accepted or stored
    code = "\n".join(l for l in fn.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential", "image"):
        if secret in code.lower():
            raise AssertionError(f"camera health report must not handle/store {secret}")

    print("Camera health report contract: PASS")


if __name__ == "__main__":
    main()

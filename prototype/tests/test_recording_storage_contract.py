#!/usr/bin/env python3
"""Static contract for Phase A increment 6 (migration 0047) — no database.

Pins wl_report_recording_storage: agent-authenticated with server-authoritative tenant/site,
states clamped to their domains, storage + recording transitions on change, per-channel
recording bound to the agent's site, and a clean separation from video health / connectivity.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
import recording_model as rm  # noqa: E402  (proves the classifier module is valid)

MIG = (ROOT / "prototype/supabase/migrations/0047_recording_storage_report.sql").read_text(encoding="utf-8")


def func_body(name: str) -> str:
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def main():
    fn = func_body("wl_report_recording_storage")

    # --- agent auth + server-authoritative binding + grants --------------------
    assert "security definer" in fn and "set search_path = public" in fn, "definer + search_path"
    require("wl_auth_agent(p_agent_id, p_agent_key)", "must authenticate the agent", fn)
    require("using errcode = '28000'", "unrecognised agent -> 28000", fn)
    assert "v_agent.tenant_id" in fn and "v_agent.site_id" in fn, "bind to the agent row"
    require("cm.site_id = v_agent.site_id and cm.channel = r.channel",
            "per-channel recording must bind to the agent's site", fn)
    if re.search(r"(p_report|c|r)\s*(#>>?|->>?)\s*'?\{?[^;']*(tenant|site_id)", fn, re.I):
        raise AssertionError("tenant/site must not come from the payload")
    require("revoke all on function public.wl_report_recording_storage(uuid, text, jsonb) from public",
            "must revoke from public")
    require("grant execute on function public.wl_report_recording_storage(uuid, text, jsonb) to anon, authenticated",
            "agent RPC granted to anon+authenticated")

    # --- states clamped to their domains (buggy agent cannot poison a column) ---
    require("in ('ok','degraded','fault','unknown')", "storage_state must be clamped to wl_storage_state", fn)
    require("in ('recording','not_recording','storage_fault','unknown')",
            "recording_state must be clamped to wl_recording_state", fn)

    # --- storage fault DOMINATES the recording rollup (design §26.3) ------------
    require("when v_storage = 'fault' then 'storage_fault'",
            "a storage fault must force the recording rollup to storage_fault", fn)
    require("when count(*) > 0 and count(*) filter (where s = 'recording') = count(*) then 'recording'",
            "rollup is 'recording' only when every channel is recording", fn)

    # --- transitions only on change, on the storage AND recording layers -------
    assert fn.count("is distinct from") >= 2, "storage + recording transitions must guard on change"
    require("'storage'", "must record a storage-layer transition", fn)
    require("'recording'", "must record a recording-layer transition", fn)

    # --- clean separation: this must NOT touch video health or connectivity ----
    if re.search(r"health_state\s*=", fn):
        raise AssertionError("recording/storage report must not write camera_health.health_state (video health)")
    for connectivity in ("nvr_reachable", "nvr_auth_ok"):
        if re.search(rf"{connectivity}\s*=", fn):
            raise AssertionError(f"must not overwrite NVR connectivity column {connectivity}")

    # --- honesty + no secrets --------------------------------------------------
    for oos in ("snapshot", "get_clip", "image", "base64", "incident"):
        if oos in fn.lower():
            raise AssertionError(f"increment 6 must not touch {oos} (recording is never inferred from a snapshot)")
    code = "\n".join(l for l in fn.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential"):
        if secret in code.lower():
            raise AssertionError(f"must not handle/store {secret}")

    print("Recording/storage report contract: PASS")


if __name__ == "__main__":
    main()

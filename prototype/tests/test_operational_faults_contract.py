#!/usr/bin/env python3
"""Static contract for Phase A increment 7 (migration 0048) — no database.

Pins the operational-fault lifecycle + customer read model to prototype/server/fault_model.py and to
the SaaS conventions: server-derived faults (cron/definer only), layered-observer suppression, dedupe
by dedupe_key, tenant-scoped customer reads, role-gated ack, fail-closed grants, no secrets.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
import fault_model as fm  # noqa: E402  (importing proves the oracle module is valid)

MIG = (ROOT / "prototype/supabase/migrations/0048_operational_faults.sql").read_text(encoding="utf-8")


def func_body(name: str) -> str:
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def require_key(builder, hay):
    """The SQL must build the SAME dedupe_key literal the oracle does (prefix + suffix around the id)."""
    pre, suf = builder("ID").split("ID")
    require(pre, f"dedupe-key prefix {pre!r} missing", hay)
    require(suf, f"dedupe-key suffix {suf!r} missing", hay)


def main():
    rec = func_body("wl_reconcile_site_faults")
    swp = func_body("wl_sweep_faults")
    ack = func_body("wl_ack_fault")
    snap = func_body("wl_site_health_snapshot")

    # === 1. reconcile derives from CONFIRMED current state, with layered suppression ==============
    assert "security definer" in rec and "set search_path = public" in rec, "definer + search_path"
    for tbl in ("nvr_health", "camera_health", "camera_inventory", "agent_unreachable_intervals"):
        require(tbl, f"reconcile must read {tbl}", rec)
    # tenant is derived from the site row, never trusted from a caller payload
    require("select tenant_id into v_tenant from sites where id = p_site_id", "tenant from the site row", rec)
    require("pg_advisory_xact_lock", "reconcile must serialize per-site vs the cron sweep", rec)

    # layered-observer suppression (mirrors fault_model.desired_faults):
    require("ended_at is null) as agent_down", "agent-down derived from an OPEN unreachable interval", rec)
    require("from rec where agent_down", "agent-unreachable is asserted when the agent is down", rec)
    require("not agent_down and nvr_reachable is false", "nvr-unreachable only when agent up", rec)
    require("not agent_down and nvr_reachable is true and nvr_auth_ok is false", "auth fault gated on reachable", rec)
    # cameras are evaluated ONLY when the site is observable (an up, reachable, authed recorder)
    require("not agent_down and nvr_reachable is true and nvr_auth_ok is true", "observable gate", rec)
    require("(select ok from observable)", "camera faults gated on observability", rec)
    # UNKNOWN never opens a fault: only positively-confirmed bad states trigger
    require("ch.health_state = 'offline'", "camera fault only on confirmed OFFLINE", rec)
    require("ch.recording_state in ('not_recording', 'storage_fault')", "recording fault only on confirmed bad", rec)
    require("storage_state in ('fault', 'degraded')", "storage fault only on confirmed fault/degraded", rec)
    # MISSING/DISABLED is inventory, not a fault
    require("not in ('missing', 'disabled')", "removed/disabled channels are inventory, not faults", rec)

    # dedupe-key literals must match the oracle EXACTLY (one live row per condition)
    for builder in (fm.agent_key, fm.nvr_reach_key, fm.nvr_auth_key, fm.storage_key,
                    fm.camera_offline_key, fm.camera_recording_key):
        require_key(builder, rec)
    # domains + severities align with the model vocabulary
    for dom in (fm.DOMAIN_AGENT, fm.DOMAIN_NVR_CONNECTIVITY, fm.DOMAIN_NVR_AUTH, fm.DOMAIN_STORAGE,
                fm.DOMAIN_CAMERA, fm.DOMAIN_RECORDING):
        require(f"'{dom}'", f"fault domain {dom} must appear", rec)
    for sev in (fm.SEV_CRITICAL, fm.SEV_WARNING):
        require(f"'{sev}'", f"severity {sev} must appear", rec)

    # reconcile = open new (deduped) + resolve cleared; idempotent by construction
    require("on conflict (dedupe_key) where state <> 'resolved' do nothing", "open dedupes on the partial index", rec)
    require("set state = 'resolved', resolved_at = v_now", "cleared conditions are resolved", rec)
    require("not exists (select 1 from jsonb_to_recordset(v_desired)", "resolve = open faults no longer desired", rec)
    for k in ("'opened'", "'resolved'", "'open_total'"):
        require(k, f"reconcile must report {k}", rec)

    # === 2. sweep is server-only and derives faults across sites ==================================
    assert "security definer" in swp, "sweep is definer"
    require("wl_reconcile_site_faults(r.site_id)", "sweep reconciles each site", swp)
    require("from nvr_health", "sweep covers sites with recorder health", swp)
    require("revoke all on function public.wl_sweep_faults() from public, anon, authenticated",
            "sweep is cron/superuser only — never the portal/anon")
    require("revoke all on function public.wl_reconcile_site_faults(uuid) from public, anon, authenticated",
            "per-site reconcile is internal — never the portal/anon")
    # scheduled, but guarded so it applies where pg_cron is absent (disposable Postgres)
    require("pg_available_extensions where name = 'pg_cron'", "cron scheduling must be guarded")
    require("cron.schedule('watchlog-fault-sweep'", "the fault sweep must be scheduled")

    # === 3. operator ack — role-gated + tenant-scoped ============================================
    require("wl_require_role(array['owner', 'admin'])", "ack requires owner/admin", ack)
    require("tenant_id = v_tenant and state = 'open'", "ack only your own OPEN fault", ack)
    require("state = 'acknowledged'", "ack sets acknowledged", ack)
    require("revoke all on function public.wl_ack_fault(bigint) from public, anon", "ack revoked from anon")
    require("grant execute on function public.wl_ack_fault(bigint) to authenticated", "ack granted to authenticated")

    # === 4. customer read model — tenant-scoped, read-only, inventory distinct from health ========
    assert "stable" in snap and "security definer" in snap, "snapshot is a stable definer read"
    require("v_tenant uuid := wl_my_tenant()", "snapshot scopes to the caller's tenant", snap)
    require("that site does not belong to your account", "snapshot verifies site ownership", snap)
    for tbl in ("nvr_health", "camera_health", "camera_inventory", "operational_faults"):
        require(tbl, f"snapshot must project {tbl}", snap)
    require("'inventory_state'", "snapshot keeps inventory distinct from health", snap)
    require("f.state <> 'resolved'", "snapshot shows only live faults", snap)
    require("revoke all on function public.wl_site_health_snapshot(uuid) from public, anon", "snapshot revoked from anon")
    require("grant execute on function public.wl_site_health_snapshot(uuid) to authenticated",
            "snapshot granted to authenticated")
    # a customer read must never leak another tenant: every projection is tenant-filtered
    if re.search(r"from (nvr_health|camera_health|operational_faults)[^;]*?where[^;]*?;", snap, re.S):
        pass  # presence check below is stricter
    assert snap.count("tenant_id = v_tenant") >= 4, "every snapshot projection must filter tenant_id = v_tenant"

    # === no secrets ==============================================================================
    code = "\n".join(l for l in MIG.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential",
                   "agent_key", "snapshot_url", "image", "jpeg"):
        if secret in code.lower():
            raise AssertionError(f"0048 must not handle/store {secret}")

    print("Operational faults + read-model contract: PASS")


if __name__ == "__main__":
    main()

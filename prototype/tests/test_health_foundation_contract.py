#!/usr/bin/env python3
"""Static DB/authz + state-contract for Phase A migration 0042.

No database: this parses the SQL and cross-checks it against the pure state
machine (prototype/agent/health_model.py), proving three things CI can enforce
without touching production:

  1. STATE VOCABULARY LOCKSTEP — the domains in 0042 (inventory/health/recording
     states, reason codes) carry exactly the strings the Python enums emit. A
     rename on either side fails here, not in the field.
  2. FAIL-CLOSED POSTURE — every Phase A table has RLS enabled, all direct grants
     revoked, and no client-facing policies (RPC-only, like control_room_layouts).
  3. TENANT SCOPING + NO SECRETS — every table carries tenant_id/site_id and the
     schema stores no credentials/stream URLs (health is not a video product).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import health_model as hm  # noqa: E402

MIG = (ROOT / "prototype/supabase/migrations/0042_health_foundation.sql").read_text(encoding="utf-8")

TABLES = [
    "camera_inventory", "camera_inventory_transitions",
    "camera_health", "camera_health_transitions",
    "nvr_health", "nvr_health_transitions",
    "agent_unreachable_intervals",
    "monitoring_coverage", "unverified_intervals",
    "operational_faults",
]


def require(needle, message):
    if needle not in MIG:
        raise AssertionError(message)


def domain_values(domain: str) -> set[str]:
    """Extract the allowed string literals from a `create domain <name> ... in (...)`."""
    m = re.search(rf"create domain public\.{domain} as text\s*\n?\s*check \(value in \(([^)]*)\)\)",
                  MIG, re.I)
    if not m:
        raise AssertionError(f"domain {domain} not found / not parseable")
    return set(re.findall(r"'([^']+)'", m.group(1)))


def enum_values(enum_cls) -> set[str]:
    return {m.value for m in enum_cls}


def main():
    # comment-stripped SQL for keyword checks (header prose mentions RPCs by name)
    code = "\n".join(l for l in MIG.splitlines() if not l.lstrip().startswith("--"))

    # 1. Vocabulary lockstep with the state machine ----------------------------
    assert domain_values("wl_inventory_state") == enum_values(hm.Inventory), \
        "inventory_state domain != Inventory enum"
    assert domain_values("wl_health_state") == enum_values(hm.Health), \
        "health_state domain != Health enum"
    assert domain_values("wl_recording_state") == enum_values(hm.RecordingState), \
        "recording_state domain != RecordingState enum"
    # reason_code is a SUPERSET: it also carries operational reasons (storage_fault,
    # not_recording, tamper, ...) the camera machine never emits. Every Reason the
    # machine CAN emit must be persistable.
    reasons = domain_values("wl_reason_code")
    missing = enum_values(hm.Reason) - reasons
    assert not missing, f"reason_code domain cannot persist machine reasons: {sorted(missing)}"

    # The VOCAB comment markers must match the domain bodies (doc/reality lockstep).
    for domain, marker in [
        ("wl_inventory_state", "inventory_state"),
        ("wl_health_state", "health_state"),
        ("wl_recording_state", "recording_state"),
        ("wl_reason_code", "reason_code"),
        ("wl_storage_state", "storage_state"),
    ]:
        cm = re.search(rf"-- VOCAB {marker}: (.+)", MIG)
        assert cm, f"VOCAB marker for {marker} missing"
        assert set(cm.group(1).split("|")) == domain_values(domain), \
            f"VOCAB marker for {marker} disagrees with domain {domain}"

    # 2. Fail-closed posture for EVERY table -----------------------------------
    for t in TABLES:
        require(f"create table if not exists public.{t} ", f"{t}: table missing")
        require(f"alter table public.{t}", f"{t}: RLS not toggled")
        assert re.search(rf"alter table public\.{t}\s+enable row level security", MIG), \
            f"{t}: RLS not enabled"
        assert re.search(rf"revoke all on table public\.{t}\s+from public, anon, authenticated", MIG), \
            f"{t}: direct grants not revoked"

    # RPC-only: no client policies in a schema-only contract migration.
    if "create policy" in code.lower():
        raise AssertionError("Phase A schema is RPC-only; no client policies belong in 0042")
    # No agent/definer RPCs yet either — those are later increments.
    if "security definer" in code.lower():
        raise AssertionError("0042 is schema only; RPCs (security definer) arrive in 0043+")

    # 3. Tenant scoping + no secrets -------------------------------------------
    # Each table (except the child-of-camera 1:1 tables keyed by camera_id/agent_id)
    # carries tenant_id + site_id for tenant isolation.
    for t in TABLES:
        block = re.search(rf"create table if not exists public\.{t} \((.*?)\n\);", MIG, re.S)
        assert block, f"{t}: table body not parseable"
        body = block.group(1)
        assert "tenant_id" in body and "references public.tenants(id)" in body, \
            f"{t}: not tenant-scoped"
        assert "site_id" in body, f"{t}: missing site_id"

    sql_no_comments = "\n".join(l for l in MIG.splitlines() if not l.lstrip().startswith("--"))
    for forbidden in ("password", "rtsp", "stream_url", "video_url", "agent_key", "credential"):
        if forbidden in sql_no_comments.lower():
            raise AssertionError(f"health schema must not store {forbidden}")

    # Design invariants that must survive refactors:
    #  * inventory is a SEPARATE column/table from health (not folded together)
    require("create table if not exists public.camera_inventory ", "inventory/health must be separate")
    require("create table if not exists public.camera_health ", "inventory/health must be separate")
    #  * server never guesses the CAUSE of an agent gap
    require("cause = 'agent_unreachable'", "agent-unreachable cause must not be guessed")
    #  * open faults dedupe (one row per sustained condition)
    require("op_faults_open_uidx", "operational faults must dedupe open conditions")
    #  * coverage separates monitored vs wall-clock vs unverified
    for col in ("wall_seconds", "monitored_seconds", "unverified_seconds"):
        require(col, f"monitoring_coverage missing {col}")

    print("Health Foundation contract: PASS")


if __name__ == "__main__":
    main()

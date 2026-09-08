#!/usr/bin/env python3
"""Phase A — increment 7: operational-fault lifecycle spec (derive / dedupe / reconcile).

Pure logic for turning the CONFIRMED current health state of a site into the set of operational
faults that should be OPEN, and for reconciling that against what is already open. wl_reconcile_site_faults
(0048) mirrors this; test_operational_faults_contract.py pins them together.

An operational fault is "your CCTV needs attention" (camera offline, recorder unreachable, storage
fault, recording stopped) — it is NOT a security incident (person/intrusion). It is reliability, not
alarm. Faults dedupe so a sustained condition is ONE row, not a storm (operational_faults has a
partial-unique index on dedupe_key where state <> 'resolved').

Two load-bearing honesty rules, the same ones the whole health model insists on:

  * NEVER fabricate a downstream fault when the OBSERVER is down. If the cloud cannot see the site
    (agent unreachable), the ONLY assertable fault is agent_unreachable — every camera/NVR/storage
    reading is now UNKNOWN or stale, so those faults are SUPPRESSED, never carried. Likewise a down
    NVR suppresses the camera/recording/storage faults beneath it, and an auth failure suppresses
    everything that needs an authenticated read. This is the layered model (design §5-6, §25.7).

  * UNKNOWN is never a fault. We open a fault only for a POSITIVELY CONFIRMED bad state (offline,
    unreachable, auth_failed, fault, not_recording). UNKNOWN / not-yet-observed opens nothing, and a
    camera the operator removed is MISSING (inventory) — not a fault.

Severity: info | warning | critical. Hysteresis (K fail / J recover) is applied UPSTREAM in the
agent/ingest layer before it reaches camera_health/nvr_health; this module only reads the settled
current state, so a single flap never opens a fault here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

# ---- fault domains (must match operational_faults.fault_domain CHECK in 0042) --------------------
DOMAIN_CAMERA = "camera"
DOMAIN_NVR_CONNECTIVITY = "nvr_connectivity"
DOMAIN_NVR_AUTH = "nvr_auth"
DOMAIN_RECORDING = "recording"
DOMAIN_STORAGE = "storage"
DOMAIN_AGENT = "agent"
DOMAIN_COVERAGE = "coverage"          # surfaced via the coverage read model in inc7, not auto-opened

SEV_INFO, SEV_WARNING, SEV_CRITICAL = "info", "warning", "critical"


@dataclass(frozen=True)
class Fault:
    domain: str                        # one of the DOMAIN_* above
    fault_type: str                    # stable machine type, e.g. 'camera_offline'
    severity: str                      # info|warning|critical
    reason_code: str                   # a wl_reason_code value (evidence)
    dedupe_key: str                    # ONE live row per key (partial-unique where state<>'resolved')
    camera_id: Optional[str] = None
    agent_id: Optional[str] = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CameraState:
    camera_id: str
    health_state: str                  # wl_health_state: operational|degraded|offline|unknown
    inventory_state: str = "present"   # wl_inventory_state: present|missing|disabled|unknown
    recording_state: str = "unknown"   # wl_recording_state: recording|not_recording|storage_fault|unknown


# dedupe-key builders — ONE stable key per (entity, condition). Kept here so the SQL can mirror the
# EXACT literal format (the contract test pins these substrings).
def agent_key(agent_id: str) -> str:              return f"agent:{agent_id}:unreachable"
def nvr_reach_key(agent_id: str) -> str:          return f"nvr:{agent_id}:unreachable"
def nvr_auth_key(agent_id: str) -> str:           return f"nvr:{agent_id}:auth"
def storage_key(agent_id: str) -> str:            return f"nvr:{agent_id}:storage"
def camera_offline_key(camera_id: str) -> str:    return f"camera:{camera_id}:offline"
def camera_recording_key(camera_id: str) -> str:  return f"camera:{camera_id}:recording"


def desired_faults(*, agent_id: str,
                   agent_reachable: bool,
                   nvr_reachable: Optional[bool],
                   nvr_auth_ok: Optional[bool],
                   storage_state: str,
                   cameras: Sequence[CameraState]) -> Dict[str, Fault]:
    """The set of faults that SHOULD be open for a site right now, keyed by dedupe_key.

    Evaluated top-down through the observability layers; the FIRST observer that is down short-circuits
    everything beneath it (its readings are UNKNOWN/stale), so we never fabricate a fault we cannot
    actually see. Only positively-confirmed bad states open a fault; UNKNOWN opens nothing.
    """
    # Layer 1 — AGENT. If the cloud cannot observe the site, the only thing we can assert is that we
    # cannot observe it. Everything below is unknowable now, so suppress it entirely.
    if not agent_reachable:
        f = Fault(DOMAIN_AGENT, "agent_unreachable", SEV_CRITICAL, "agent_unreachable",
                  agent_key(agent_id), agent_id=agent_id)
        return {f.dedupe_key: f}

    # Layer 2 — NVR connectivity / auth. A down or unauthenticated recorder makes every channel and
    # the storage state UNKNOWN, so it too suppresses the layers beneath it.
    if nvr_reachable is False:
        f = Fault(DOMAIN_NVR_CONNECTIVITY, "nvr_unreachable", SEV_CRITICAL, "nvr_unreachable",
                  nvr_reach_key(agent_id), agent_id=agent_id)
        return {f.dedupe_key: f}
    if nvr_auth_ok is False:
        f = Fault(DOMAIN_NVR_AUTH, "nvr_auth_failed", SEV_CRITICAL, "nvr_auth_failed",
                  nvr_auth_key(agent_id), agent_id=agent_id)
        return {f.dedupe_key: f}

    out: Dict[str, Fault] = {}

    # Layer 3 — STORAGE (recorder-level). fault is critical; degraded is a warning (low space etc.);
    # unknown/ok open nothing.
    if storage_state == "fault":
        f = Fault(DOMAIN_STORAGE, "storage_fault", SEV_CRITICAL, "storage_fault",
                  storage_key(agent_id), agent_id=agent_id)
        out[f.dedupe_key] = f
    elif storage_state == "degraded":
        f = Fault(DOMAIN_STORAGE, "storage_degraded", SEV_WARNING, "disk_full",
                  storage_key(agent_id), agent_id=agent_id)
        out[f.dedupe_key] = f

    # Layer 4 — per CAMERA. Only confirmed OFFLINE opens a camera fault (UNKNOWN/degraded do not, and
    # MISSING/DISABLED are inventory, not faults). Recording faults only on a confirmed bad channel.
    for c in cameras:
        if c.inventory_state in ("missing", "disabled"):
            continue                                   # not configured/expected -> not a fault
        if c.health_state == "offline":
            f = Fault(DOMAIN_CAMERA, "camera_offline", SEV_CRITICAL, "video_loss",
                      camera_offline_key(c.camera_id), camera_id=c.camera_id)
            out[f.dedupe_key] = f
        if c.recording_state == "not_recording":
            f = Fault(DOMAIN_RECORDING, "not_recording", SEV_WARNING, "not_recording",
                      camera_recording_key(c.camera_id), camera_id=c.camera_id)
            out[f.dedupe_key] = f
        elif c.recording_state == "storage_fault":
            f = Fault(DOMAIN_RECORDING, "recording_storage_fault", SEV_WARNING, "storage_fault",
                      camera_recording_key(c.camera_id), camera_id=c.camera_id)
            out[f.dedupe_key] = f
    return out


def reconcile_faults(open_keys: Set[str], desired: Dict[str, Fault]
                     ) -> Tuple[List[Fault], List[str]]:
    """Diff the desired open-set against what is already open (state <> 'resolved') for the site.

    Returns (to_open, to_resolve_keys):
      * to_open      — desired faults whose dedupe_key is not already open (a fresh row is inserted;
                       the partial-unique index guarantees only one live row per key).
      * to_resolve   — currently-open keys no longer desired (the condition cleared) -> mark resolved,
                       which frees the key for a future recurrence.
    A key that is both open and desired is LEFT UNTOUCHED — so an acknowledged fault stays acknowledged
    while its condition persists, and a sustained condition never restarts its lifecycle. Idempotent:
    when desired == open, both lists are empty.
    """
    to_open = [f for k, f in desired.items() if k not in open_keys]
    to_resolve = [k for k in open_keys if k not in desired]
    return to_open, to_resolve


__all__ = ["Fault", "CameraState", "desired_faults", "reconcile_faults",
           "DOMAIN_CAMERA", "DOMAIN_NVR_CONNECTIVITY", "DOMAIN_NVR_AUTH", "DOMAIN_RECORDING",
           "DOMAIN_STORAGE", "DOMAIN_AGENT", "DOMAIN_COVERAGE",
           "SEV_INFO", "SEV_WARNING", "SEV_CRITICAL",
           "agent_key", "nvr_reach_key", "nvr_auth_key", "storage_key",
           "camera_offline_key", "camera_recording_key"]

#!/usr/bin/env python3
"""Phase A — increment 6: recording + storage health classifier.

Pure logic. The agent reads the recorder's vendor storage/record API and this decides the
state; the cloud persists it. Shared spec for the agent assessor and the SQL clamp.

Hard rules (design §5/§6, §26.3-4, and the increment-6 review):
  * READ, don't infer. Recording/storage come from the vendor API; where the API is missing,
    unvalidated, or only proves CONFIGURATION (a schedule/mode, not that frames are being written)
    the state is explicitly UNKNOWN. A JPEG never proves recording.
  * upper layer down (NVR unreachable/auth-failed/unknown) -> recording+storage UNKNOWN (with cause).
  * a channel that is MISSING/DISABLED in inventory has no meaningful recording state -> UNKNOWN,
    never NOT_RECORDING.
  * storage: all usable storage healthy -> OK; a partial/low-space/redundancy issue while usable
    storage remains -> DEGRADED; no usable recording storage / recording blocked -> FAULT;
    unsupported/ambiguous -> UNKNOWN. Low space is DEGRADED, NOT a blanket FAULT.
  * a storage FAULT (no usable storage) dominates -> the channel is recording STORAGE_FAULT. A
    storage DEGRADED does NOT turn every camera into STORAGE_FAULT.
  * recording is INDEPENDENT of camera video health.
"""
from __future__ import annotations

# storage_state vocabulary (matches the SQL wl_storage_state domain: ok/degraded/fault/unknown)
STORAGE_OK = "ok"
STORAGE_DEGRADED = "degraded"
STORAGE_FAULT = "fault"
STORAGE_UNKNOWN = "unknown"

# recording_state vocabulary (matches health_model.RecordingState / wl_recording_state)
REC_RECORDING = "recording"
REC_NOT = "not_recording"
REC_STORAGE_FAULT = "storage_fault"
REC_UNKNOWN = "unknown"


def _nvr_reason(nvr_state: str) -> str:
    return {"unreachable": "nvr_unreachable",
            "auth_failed": "nvr_auth_failed"}.get(nvr_state, "agent_unreachable")


def classify_storage(*, nvr_state: str, supported: bool, raw_state,
                     native_fatal: bool = False, native_lowspace: bool = False):
    """(storage_state, reason). raw_state is the driver's read: 'ok'|'degraded'|'fault'|None.

    native_fatal  = a StorageFailure/StorageNotExist event (no usable storage) -> FAULT.
    native_lowspace = a StorageLowSpace event (usable, but low) -> DEGRADED, never FAULT.
    """
    if nvr_state != "ok":
        return STORAGE_UNKNOWN, _nvr_reason(nvr_state)
    if native_fatal:
        return STORAGE_FAULT, "disk_error"                    # no usable recording storage
    if raw_state == "fault":
        return STORAGE_FAULT, "storage_fault"
    if raw_state == "degraded" or native_lowspace:
        return STORAGE_DEGRADED, "disk_full"                  # low space / partial — still usable
    if not supported or raw_state is None:
        return STORAGE_UNKNOWN, "unknown"                     # unreadable/unsupported -> UNKNOWN, never 'ok'
    if raw_state == "ok":
        return STORAGE_OK, "ok"
    return STORAGE_UNKNOWN, "unknown"


def classify_recording(*, nvr_state: str, storage_state: str, inventory_state: str,
                       supported: bool, raw_channel_state):
    """(recording_state, reason). raw_channel_state: 'recording'|'not_recording'|None (unreadable /
    config-only). Camera VIDEO health is deliberately NOT an input — recording is a distinct layer.

    A driver returns 'recording' ONLY when it has evidence recording is actually active; a mere
    schedule/mode ('should record') is None -> UNKNOWN. 'not_recording' means the recorder explicitly
    says the channel is off.
    """
    if nvr_state != "ok":
        return REC_UNKNOWN, _nvr_reason(nvr_state)
    if inventory_state == "missing":
        return REC_UNKNOWN, "channel_missing"                 # no camera -> no meaningful recording state
    if inventory_state == "disabled":
        return REC_UNKNOWN, "channel_disabled"
    if storage_state == STORAGE_FAULT:
        return REC_STORAGE_FAULT, "storage_fault"             # no usable storage -> cannot be recording
    # storage DEGRADED (low space) does NOT force a fault — recording can continue; fall through.
    if not supported or raw_channel_state is None:
        return REC_UNKNOWN, "unknown"                         # config-only/unreadable -> never assume recording
    if raw_channel_state == "not_recording":
        return REC_NOT, "not_recording"
    if raw_channel_state == "recording":
        return REC_RECORDING, "ok"
    return REC_UNKNOWN, "unknown"


__all__ = ["classify_storage", "classify_recording",
           "STORAGE_OK", "STORAGE_DEGRADED", "STORAGE_FAULT", "STORAGE_UNKNOWN",
           "REC_RECORDING", "REC_NOT", "REC_STORAGE_FAULT", "REC_UNKNOWN"]

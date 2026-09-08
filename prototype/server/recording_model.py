#!/usr/bin/env python3
"""Phase A — increment 6: recording + storage health classifier.

Pure logic. The agent reads the recorder's vendor storage/record API and this decides the
state; the cloud (0047) persists it. Kept in prototype/server/ as the shared spec so the agent
assessor (prototype/agent/recording_health.py) and the SQL clamp speak one vocabulary.

Hard rules (design §5/§6, acceptance §26.3-4):
  * READ, don't infer. Recording/storage come from the vendor API; where the API is missing or
    unreadable the state is explicitly UNKNOWN. A JPEG proves an image, NOT that the NVR records it.
  * upper layer down (NVR unreachable/auth-failed/unknown) -> recording+storage UNKNOWN.
  * a storage FAULT dominates: a channel over faulted storage is recording STORAGE_FAULT even if
    its video is fine.
  * recording is INDEPENDENT of camera health: OPERATIONAL video + NOT_RECORDING is valid.
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
    """Map a non-ok NVR layer state to its reason code (we never guess a lower layer through it)."""
    return {"unreachable": "nvr_unreachable",
            "auth_failed": "nvr_auth_failed"}.get(nvr_state, "agent_unreachable")


def classify_storage(*, nvr_state: str, supported: bool, raw_state,
                     native_fault: bool = False):
    """(storage_state, reason). raw_state is the driver's read: 'ok'|'degraded'|'fault'|None."""
    if nvr_state != "ok":
        return STORAGE_UNKNOWN, _nvr_reason(nvr_state)          # cannot see storage through a down NVR
    if native_fault:
        return STORAGE_FAULT, "disk_error"                     # a native StorageFailure event is authoritative
    if not supported or raw_state is None:
        return STORAGE_UNKNOWN, "unknown"                      # unreadable/unsupported -> UNKNOWN, never 'ok'
    if raw_state == "fault":
        return STORAGE_FAULT, "storage_fault"
    if raw_state == "degraded":
        return STORAGE_DEGRADED, "disk_full"                   # low space
    if raw_state == "ok":
        return STORAGE_OK, "ok"
    return STORAGE_UNKNOWN, "unknown"


def classify_recording(*, nvr_state: str, storage_state: str, supported: bool,
                       raw_channel_state):
    """(recording_state, reason). raw_channel_state: 'recording'|'not_recording'|None (unreadable).

    Note: camera VIDEO health is deliberately NOT an input — recording is a distinct layer.
    """
    if nvr_state != "ok":
        return REC_UNKNOWN, _nvr_reason(nvr_state)
    if storage_state == STORAGE_FAULT:
        return REC_STORAGE_FAULT, "storage_fault"              # no working storage -> cannot be recording
    if not supported or raw_channel_state is None:
        return REC_UNKNOWN, "unknown"                          # never assume "recording" we cannot read
    if raw_channel_state == "not_recording":
        return REC_NOT, "not_recording"
    if raw_channel_state == "recording":
        return REC_RECORDING, "ok"
    return REC_UNKNOWN, "unknown"


__all__ = ["classify_storage", "classify_recording",
           "STORAGE_OK", "STORAGE_DEGRADED", "STORAGE_FAULT", "STORAGE_UNKNOWN",
           "REC_RECORDING", "REC_NOT", "REC_STORAGE_FAULT", "REC_UNKNOWN"]

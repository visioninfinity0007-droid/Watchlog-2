#!/usr/bin/env python3
"""WatchLog Site Status model — the data behind the customer status/control panel (0.4.4 P1).

This is the business logic the Qt "WatchLog Site Status" screen renders and the agent emits via
`--status-json`. It is deliberately Qt-free and pure: given already-gathered section inputs it
assembles one honest status document. Rendering (the panel) and probing (the agent) are separate,
so the model — camera classification, honest "not available" storage, summary counts, coverage/
recovery states — is fully unit-testable without Qt, a recorder, or a database.

Honesty rules:
  * an intentionally unused recorder channel is NEVER counted as an offline/failed camera;
  * a metric the recorder cannot expose renders as "Not available on this recorder", never a guess;
  * every state is one of a fixed, explicit vocabulary — nothing is fabricated.
"""
from __future__ import annotations

SCHEMA = "watchlog.site_status.v1"

NA = "Not available on this recorder"

# fixed vocabularies (the panel maps these to colour/label)
CAMERA_STATES = ("monitored", "offline", "degraded", "unknown", "unused")
RECORDING_STATES = ("verified", "warning", "unknown", "not_applicable")
ARCHIVE_STATES = ("verified", "available_frame_unverified", "empty", "unsupported", "failed", "unknown")
RECOVERY_STATES = ("available", "unsupported", "pending", "partial", "failed", "none")
STORAGE_HEALTH = ("healthy", "warning", "failed", "unknown", "unavailable")


def _bool_state(ok, *, good, bad, unknown="unknown"):
    if ok is None:
        return unknown
    return good if ok else bad


def camera_view(channels, *, configured=None, health=None) -> dict:
    """Assemble the per-camera view + summary.

    channels:   [{"channel","name"}, ...] as enumerated from the recorder
    configured: {channel: bool}  (is this channel a monitored WatchLog camera?) — missing => True
    health:     {channel: {"health_state","recording_state","last_seen"}}

    Unused channels are listed but classified 'unused' and excluded from offline/degraded counts.
    """
    configured = configured or {}
    health = health or {}
    cams, summary = [], {"monitored": 0, "offline": 0, "degraded": 0, "unknown": 0, "unused": 0}
    for row in channels or []:
        ch = str(row.get("channel"))
        name = row.get("name") or f"Camera {ch}"
        is_conf = configured.get(ch, True)
        h = health.get(ch) or {}
        if not is_conf:
            state = "unused"
        else:
            hs = (h.get("health_state") or "unknown").lower()
            state = {"operational": "monitored", "offline": "offline",
                     "degraded": "degraded"}.get(hs, "unknown")
        summary[state] = summary.get(state, 0) + 1
        cams.append({
            "channel": ch, "name": name, "configured": bool(is_conf), "status": state,
            "recording_state": (h.get("recording_state") if is_conf else None),
            "last_seen": h.get("last_seen"),
        })
    summary["configured_total"] = sum(1 for c in cams if c["configured"])
    summary["headline"] = f"{summary['monitored']}/{summary['configured_total']} monitored"
    return {"cameras": cams, "summary": summary}


def recording_view(cameras, *, recording=None) -> dict:
    """Per active-camera recording verdict. recording: {channel: 'verified'|'warning'|'unknown'}."""
    recording = recording or {}
    rows = []
    counts = {"verified": 0, "warning": 0, "unknown": 0}
    for cam in cameras or []:
        if not cam.get("configured"):
            continue
        st = recording.get(cam["channel"], "unknown")
        if st not in RECORDING_STATES:
            st = "unknown"
        if st in counts:
            counts[st] += 1
        rows.append({"channel": cam["channel"], "name": cam["name"], "state": st})
    return {"rows": rows, "summary": counts}


def archive_view(*, proof_status=None, last_proof_at=None, recovery_backlog=None,
                 frame_decoded=None) -> dict:
    """Fold the setup/recheck archive proof + recovery backlog into the panel's archive section.

    proof_status: from dahua_archive.prove_recorder_archive (verified/empty/unsupported/unknown).
    frame_decoded: True/False/None — whether a historical frame could actually be decoded.
    """
    if proof_status == "verified":
        archive = "verified" if frame_decoded is not False else "available_frame_unverified"
        recovery = "available"
    elif proof_status == "empty":
        archive, recovery = "empty", "none"
    elif proof_status == "unsupported":
        archive, recovery = "unsupported", "unsupported"
    elif proof_status in (None, "unknown"):
        archive, recovery = "unknown", "none"
    else:                                            # 'error'/anything else
        archive, recovery = "failed", "failed"
    backlog = int(recovery_backlog or 0)
    if backlog > 0 and recovery in ("available", "none"):
        recovery = "pending"
    return {"archive_access": archive, "recovery": recovery,
            "last_archive_proof": last_proof_at, "recovery_backlog": backlog}


def storage_view(storage=None) -> dict:
    """Honest storage view. `storage` may carry any of health/total_gb/free_gb/retention_days/
    oldest_recording; anything absent renders as the 'Not available on this recorder' sentinel."""
    s = storage or {}
    health = s.get("health")
    if health not in STORAGE_HEALTH:
        health = "unavailable" if health is None else "unknown"

    def field(key):
        v = s.get(key)
        return v if v is not None else NA
    return {
        "health": health,
        "total_gb": field("total_gb"),
        "free_gb": field("free_gb"),
        "retention_days": field("retention_days"),
        "oldest_recording": field("oldest_recording"),
    }


def agent_view(*, build_meta=None, channel=None, state=None, running=None, last_heartbeat=None,
               cloud_ok=None, spool_backlog=None, recovery_backlog=None) -> dict:
    meta = build_meta or {}
    st = state or {}
    return {
        "running": _bool_state(running, good="running", bad="stopped"),
        "version": meta.get("version"),
        "build_sha": meta.get("build_sha") or None,
        "channel": channel,
        "enrollment": "enrolled" if st.get("agent_id") else "not_enrolled",
        "last_heartbeat": last_heartbeat,
        "cloud": _bool_state(cloud_ok, good="connected", bad="disconnected"),
        "spool_backlog": int(spool_backlog or 0),
        "recovery_backlog": int(recovery_backlog or 0),
    }


def recorder_view(*, reachable=None, auth_ok=None, info=None, capability=None,
                  device_time=None, time_offset_seconds=None) -> dict:
    info = info or {}
    cap = capability or {}
    return {
        "connection": _bool_state(reachable, good="connected", bad="disconnected"),
        "authentication": _bool_state(auth_ok, good="ok", bad="failed"),
        "vendor": info.get("vendor") or NA,
        "model": info.get("model") or NA,
        "driver": info.get("driver") or NA,
        "device_time": device_time or NA,
        "time_offset_seconds": time_offset_seconds if time_offset_seconds is not None else NA,
        "archive_capability": (cap.get("segments") or "unknown"),
    }


def build_snapshot(*, agent, recorder, camera, recording, archive, storage, generated_at=None) -> dict:
    """Assemble the full, honest Site Status document from the section views above."""
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "agent": agent,
        "recorder": recorder,
        "cameras": camera,
        "recording": recording,
        "archive": archive,
        "storage": storage,
    }


__all__ = ["SCHEMA", "NA", "CAMERA_STATES", "RECORDING_STATES", "ARCHIVE_STATES", "RECOVERY_STATES",
           "STORAGE_HEALTH", "camera_view", "recording_view", "archive_view", "storage_view",
           "agent_view", "recorder_view", "build_snapshot"]

#!/usr/bin/env python3
"""Phase A — increment 6: agent-side recording + storage health assessment.

Reads the recorder's vendor storage/record API (ONLY when the NVR layer is up — we never probe a
recorder we cannot reach/authenticate) and produces the cloud report:

  * storage: {state: ok|degraded|fault|unknown, reason}
  * recording.channels: [{channel, state: recording|not_recording|storage_fault|unknown, reason}]

Honesty rules live in recording_model.py (agent-side classifier): unreadable/unsupported -> UNKNOWN
(never a fabricated 'recording'/'ok'); a storage FAULT dominates recording; recording is
independent of camera video health. Telemetry carries only states/reasons/channel numbers — no
image bytes, no URLs, no credentials.
"""
from __future__ import annotations

from recording_model import classify_recording, classify_storage


def assess_recording_storage(driver, channels, nvr_state: str, inventory=None) -> dict:
    """`inventory` maps channel -> inventory_state (present|missing|disabled|unknown); a
    MISSING/DISABLED channel has no meaningful recording state (UNKNOWN, never NOT_RECORDING)."""
    channels = [str(c) for c in channels]
    inventory = {str(k): v for k, v in (inventory or {}).items()}
    supported_s, raw_s, raw_s_reason = False, None, None
    native_fatal, native_lowspace = False, False   # storage CGI is the source; native storage events
    supported_r, rec_map, rec_reasons = False, {}, {}  # flow to the cloud via the event stream, not here
    rec_config: dict = {}
    disks, total_bytes, free_bytes = None, None, None

    if nvr_state == "ok":     # only touch the recorder when the layer above it is proven up
        try:
            s = driver.storage_status() or {}
            supported_s = bool(s.get("supported"))
            raw_s = s.get("state")
            raw_s_reason = s.get("reason")
            native_fatal = bool(s.get("native_fatal"))
            native_lowspace = bool(s.get("native_lowspace"))
            if supported_s and isinstance(s.get("disks"), list):
                disks = [_disk(d) for d in s["disks"] if isinstance(d, dict)]
                total_bytes, free_bytes = _bytes(s.get("total_bytes")), _bytes(s.get("free_bytes"))
        except Exception:                              # noqa: BLE001 — a read failure is UNKNOWN, not a crash
            supported_s, raw_s, raw_s_reason, disks = False, None, None, None
        try:
            r = driver.recording_status(channels) or {}
            supported_r = bool(r.get("supported"))
            rec_map = r.get("channels") or {}
            rec_reasons = r.get("reasons") or {}
            rec_config = {str(k): str(v) for k, v in (r.get("config") or {}).items()}
        except Exception:                              # noqa: BLE001
            supported_r, rec_map, rec_reasons, rec_config = False, {}, {}, {}

    st_state, st_reason = classify_storage(nvr_state=nvr_state, supported=supported_s, raw_state=raw_s,
                                           native_fatal=native_fatal, native_lowspace=native_lowspace,
                                           raw_reason=raw_s_reason)
    out = []
    for c in channels:
        rec_state, rec_reason = classify_recording(
            nvr_state=nvr_state, storage_state=st_state,
            inventory_state=inventory.get(c, "present"),
            supported=supported_r, raw_channel_state=rec_map.get(c),
            raw_reason=rec_reasons.get(c))
        out.append({"channel": c, "state": rec_state, "reason": rec_reason})

    storage = {"state": st_state, "reason": st_reason}
    if disks is not None:
        # Per-disk inventory and recorder totals (no paths beyond the recorder's own disk names,
        # never an address or credential).
        storage.update(disks=disks, total_bytes=total_bytes, free_bytes=free_bytes)
    report = {"storage": storage, "recording": {"supported": supported_r, "channels": out}}
    if rec_config:
        # Configuration only (disabled | continuous | scheduled | unknown): never proof of recording.
        report["recording_config"] = {c: rec_config[c] for c in channels if c in rec_config}
    return report


def _bytes(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _disk(d: dict) -> dict:
    state = d.get("state") if d.get("state") in ("ok", "fault", "unknown") else "unknown"
    return {"id": str(d.get("id") or "")[:64], "path": d.get("path"), "type": d.get("type"),
            "state": state, "reason": d.get("reason") or "unknown",
            "total_bytes": _bytes(d.get("total_bytes")), "free_bytes": _bytes(d.get("free_bytes"))}


__all__ = ["assess_recording_storage"]

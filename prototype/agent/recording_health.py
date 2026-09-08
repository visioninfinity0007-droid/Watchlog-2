#!/usr/bin/env python3
"""Phase A — increment 6: agent-side recording + storage health assessment.

Reads the recorder's vendor storage/record API (ONLY when the NVR layer is up — we never probe a
recorder we cannot reach/authenticate) and produces the cloud report:

  * storage: {state: ok|degraded|fault|unknown, reason}
  * recording.channels: [{channel, state: recording|not_recording|storage_fault|unknown, reason}]

Honesty rules live in prototype/server/recording_model.py: unreadable/unsupported -> UNKNOWN
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
    supported_s, raw_s = False, None
    native_fatal, native_lowspace = False, False   # storage CGI is the source; native storage events
    supported_r, rec_map = False, {}               # flow to the cloud via the event stream, not here

    if nvr_state == "ok":     # only touch the recorder when the layer above it is proven up
        try:
            s = driver.storage_status() or {}
            supported_s = bool(s.get("supported"))
            raw_s = s.get("state")
            native_fatal = bool(s.get("native_fatal"))
            native_lowspace = bool(s.get("native_lowspace"))
        except Exception:                              # noqa: BLE001 — a read failure is UNKNOWN, not a crash
            supported_s, raw_s = False, None
        try:
            r = driver.recording_status(channels) or {}
            supported_r = bool(r.get("supported"))
            rec_map = r.get("channels") or {}
        except Exception:                              # noqa: BLE001
            supported_r, rec_map = False, {}

    st_state, st_reason = classify_storage(nvr_state=nvr_state, supported=supported_s, raw_state=raw_s,
                                           native_fatal=native_fatal, native_lowspace=native_lowspace)
    out = []
    for c in channels:
        rec_state, rec_reason = classify_recording(
            nvr_state=nvr_state, storage_state=st_state,
            inventory_state=inventory.get(c, "present"),
            supported=supported_r, raw_channel_state=rec_map.get(c))
        out.append({"channel": c, "state": rec_state, "reason": rec_reason})

    return {"storage": {"state": st_state, "reason": st_reason},
            "recording": {"supported": supported_r, "channels": out}}


__all__ = ["assess_recording_storage"]

#!/usr/bin/env python3
"""Site Control — agent-side recorder READ executor (H6, P1).

The Site Agent is the ONLY thing that talks to the recorder; the recorder credential
never leaves it and no external caller (AI/portal) ever receives it. This module turns a
structured, capability-gated READ action from the WatchLog Site Control command plane into
a driver call and a structured result. It is strictly READ-ONLY in P1 — nothing here
mutates the recorder.

Every result carries the ACTION and either {'ok': True, 'data': ...} or
{'ok': False, 'error': <sanitised>} — never a credential, URL, or raw driver error string.
The LIVE observation returned here is combined with manufacturer knowledge (the recorder
capability model, migration 0061) and WatchLog's implementation state IN THE CLOUD, so the
three truths (documented / implemented / observed) are never collapsed into one.
"""
from __future__ import annotations

from drivers.base import DriverError

# READ actions available in P1. Writes are a separate, managed-tier plane (H6 safe-write).
READ_ACTIONS = (
    "get_recorder_identity", "get_channels", "get_clock_config",
    "get_video_loss_state", "get_analytics_config", "get_recording_status",
    "get_storage_status", "request_snapshot", "inspect_recorder",
)


def _identity(driver) -> dict:
    info = driver.probe()
    return {"vendor": getattr(info, "vendor", None), "model": getattr(info, "model", None),
            "firmware": getattr(info, "firmware", None), "serial": getattr(info, "serial", None),
            "channel_count": getattr(info, "channel_count", None),
            "driver": getattr(info, "driver", None)}


def _channels(driver) -> list:
    return [{"channel": str(c.channel), "name": getattr(c, "name", None),
             "enabled": bool(getattr(c, "enabled", True))} for c in driver.list_channels()]


def _sanitise(e) -> str:
    try:
        import nvr_health                     # strips URLs / creds / IPs from arbitrary text
        return nvr_health.redact(str(e))
    except Exception:                          # noqa: BLE001
        return type(e).__name__


def inspect(driver) -> dict:
    """Composite live snapshot — identity, channels, clock, video-loss, analytics, recording,
    storage. Each sub-read is independent so one failure never sinks the rest."""
    def safe(fn, default):
        try:
            return fn()
        except Exception:                      # noqa: BLE001
            return default
    return {
        "identity":   safe(lambda: _identity(driver), None),
        "channels":   safe(lambda: _channels(driver), []),
        "clock":      safe(lambda: driver.get_clock(), {"supported": False}),
        "video_loss": safe(lambda: driver.current_faults(), {"supported": False}),
        "analytics":  safe(lambda: driver.capabilities(), {"channels": []}),
        "recording":  safe(lambda: driver.recording_status(None), {"supported": False}),
        "storage":    safe(lambda: driver.storage_status(), {"supported": False}),
    }


def execute_read(driver, action: str, params: "dict | None" = None) -> dict:
    """Run one READ action against the recorder via the driver. Never raises; a driver fault
    becomes {'ok': False, 'error': <sanitised>}. `params` carries e.g. the snapshot channel."""
    params = params or {}
    if action not in READ_ACTIONS:
        return {"action": action, "ok": False, "error": "unsupported_read_action"}
    try:
        if action == "get_recorder_identity":
            data = _identity(driver)
        elif action == "get_channels":
            data = _channels(driver)
        elif action == "get_clock_config":
            data = driver.get_clock()
        elif action == "get_video_loss_state":
            data = driver.current_faults()
        elif action == "get_analytics_config":
            data = driver.capabilities()
        elif action == "get_recording_status":
            data = driver.recording_status(None)
        elif action == "get_storage_status":
            data = driver.storage_status()
        elif action == "request_snapshot":
            ch = str(params.get("channel") or "1")
            img = driver.get_snapshot(ch)
            data = {"channel": ch, "obtained": bool(img), "bytes": (len(img) if img else 0)}
        else:  # inspect_recorder
            data = inspect(driver)
        return {"action": action, "ok": True, "data": data}
    except DriverError as e:
        return {"action": action, "ok": False, "error": _sanitise(e)}
    except Exception as e:                      # noqa: BLE001 — never crash the executor
        return {"action": action, "ok": False, "error": type(e).__name__}


__all__ = ["READ_ACTIONS", "execute_read", "inspect"]

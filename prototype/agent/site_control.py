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


# ---------------------------------------------------------------------
# SAFE WRITE plane (managed tier): read -> backup -> minimal diff -> apply -> read-back ->
# verify; on a failed verify or a driver fault, roll back to the pre-write values and verify
# the rollback. Only field-proven writes; the recorder-capability gate is enforced cloud-side.
# ---------------------------------------------------------------------

WRITE_ACTIONS = ("rename_channel", "configure_smd", "configure_time")

# The recorder-capability key each write depends on (checked cloud-side against the model).
WRITE_CAPABILITY = {
    "rename_channel": "channel_title",
    "configure_smd": "human_vehicle_classification",
    "configure_time": "time_ntp_config",
}

_SMD_FIELDS = ("human", "vehicle", "sensitivity", "enable")
_TIME_FIELDS = ("dst_enabled", "ntp_enabled")

_WRITE_SPECS = {
    "rename_channel": {
        "read":    lambda d, p: {"name": d.get_channel_title(p["channel"])},
        "desired": lambda p: {"name": p["name"]},
        "apply":   lambda d, p, v: d.set_channel_title(p["channel"], v["name"]),
    },
    "configure_smd": {
        "read":    lambda d, p: {k: v for k, v in d.get_smd(p["channel"]).items() if k in _SMD_FIELDS},
        "desired": lambda p: {k: p[k] for k in _SMD_FIELDS if k in p},
        "apply":   lambda d, p, v: d.set_smd(p["channel"], **v),
    },
    "configure_time": {
        "read":    lambda d, p: {k: v for k, v in d.get_clock().items() if k in _TIME_FIELDS},
        "desired": lambda p: {k: p[k] for k in _TIME_FIELDS if k in p},
        "apply":   lambda d, p, v: d.set_time_config(**v),
    },
}


def _matches(state: dict, desired: dict) -> bool:
    return all(state.get(k) == val for k, val in desired.items())


def _rollback(read, apply, before: dict, desired: dict) -> bool:
    """Re-apply the pre-write values for the fields we changed, then verify the restore."""
    try:
        restore = {k: before.get(k) for k in desired}
        apply(restore)
        return _matches(read(), restore)
    except Exception:                          # noqa: BLE001
        return False


def execute_write(driver, action: str, params: "dict | None" = None) -> dict:
    """One transactional safe write. Returns before/after/verified/changed/rolled_back — an
    HTTP 200 is never treated as success; only a matching read-back is. The action must be in
    WRITE_ACTIONS; capability + authorization are gated cloud-side before this ever runs."""
    params = params or {}
    spec = _WRITE_SPECS.get(action)
    if spec is None:
        return {"action": action, "ok": False, "error": "unsupported_write_action"}
    read = lambda: spec["read"](driver, params)          # noqa: E731
    apply = lambda v: spec["apply"](driver, params, v)   # noqa: E731
    try:
        before = read()
    except DriverError as e:
        return {"action": action, "ok": False, "error": _sanitise(e)}
    desired = spec["desired"](params)
    if not desired:
        return {"action": action, "ok": False, "error": "no_desired_state", "before": before}
    if _matches(before, desired):              # minimal diff: nothing to change
        return {"action": action, "ok": True, "verified": True, "changed": False,
                "before": before, "after": before}
    try:
        apply(desired)
    except DriverError as e:                    # apply faulted -> restore
        return {"action": action, "ok": False, "error": _sanitise(e),
                "before": before, "rolled_back": _rollback(read, apply, before, desired)}
    try:
        after = read()
    except DriverError:
        after = None
    if after is not None and _matches(after, desired):
        return {"action": action, "ok": True, "verified": True, "changed": True,
                "before": before, "after": after, "rollback_available": True}
    # read-back did not match the desired state -> roll back and verify the rollback
    return {"action": action, "ok": False, "verified": False, "before": before,
            "after": after, "rolled_back": _rollback(read, apply, before, desired)}


__all__ = ["READ_ACTIONS", "WRITE_ACTIONS", "WRITE_CAPABILITY",
           "execute_read", "execute_write", "inspect"]

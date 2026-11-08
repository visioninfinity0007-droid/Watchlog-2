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

# Remote maintenance (5.1.2): read tier, outbound-only, executed by the Agent runtime
# (site_maintenance.execute) because they need more than one recorder driver: the full
# acceptance suite, diagnostics, inventory/capability refresh, collector reconnect and a
# graceful restart. Kept in step with site_maintenance.MAINTENANCE_ACTIONS.
MAINTENANCE_ACTIONS = (
    "run_full_acceptance_test", "run_recording_check", "run_archive_check",
    "collect_diagnostics", "refresh_inventory", "refresh_capabilities",
    "reconnect_recorder", "restart_agent",
)

# READ actions available in P1. Writes are a separate, managed-tier plane (H6 safe-write).
# Exactly the read catalog of wl_site_command_enqueue (test_site_control_catalog_parity).
READ_ACTIONS = (
    "get_recorder_identity", "get_channels", "get_clock_config",
    "get_video_loss_state", "get_analytics_config", "get_recording_status",
    "get_storage_status", "request_snapshot", "inspect_recorder",
    "probe_view_quality",
) + MAINTENANCE_ACTIONS


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


def _view_quality(driver, params: dict) -> dict:
    """Probe the still sizes the recorder can give (read-only). With ``apply: true`` the Agent
    switches the site's view quality: ``sharp`` only when the probe proved a larger still comes
    back, ``standard`` always allowed. This changes which still the Agent asks for, never the
    recorder (view_quality.py)."""
    import view_quality                                         # noqa: PLC0415
    prober = getattr(driver, "probe_view_quality", None)
    probe = prober(params.get("channels")) if callable(prober) else {"supported": False, "channels": []}
    raised = []
    if params.get("raise_snapshot") and callable(getattr(driver, "set_snapshot_format", None)):
        raised = _raise_snapshot_sizes(driver, probe)
        if any(r.get("changed") for r in raised):
            probe = prober(params.get("channels"))
    proven = view_quality.choose_mode(probe)
    mode = view_quality.current_mode()
    if params.get("apply"):
        wanted = params.get("mode") or proven
        if wanted == "sharp" and proven != "sharp":
            wanted = "standard"
        mode = view_quality.save_mode(wanted, {"proven": proven,
                                               "channels": len(probe.get("channels") or [])})
    out = {"probe": probe, "proven": proven, "mode": mode}
    if params.get("raise_snapshot"):
        out["snapshot_raised"] = raised
    return out


SNAPSHOT_MAX_WIDTH = 1920


def _raise_snapshot_sizes(driver, probe: dict) -> list:
    """5.1.13: for each camera whose still is smaller than its main stream, raise the recorder's
    snapshot size to the main-stream size (at most SNAPSHOT_MAX_WIDTH wide) with the transactional
    configure_snapshot write: read -> apply -> read-back, rolled back on any mismatch. A camera
    whose still already matches is left alone. Returns one result per camera."""
    results = []
    for row in (probe or {}).get("channels") or []:
        ch = str(row.get("channel") or "")
        today = (row.get("default") or {}).get("size")
        if not ch or not today:
            continue
        try:
            main = driver.get_main_size(ch)
        except Exception:                                   # noqa: BLE001
            main = None
        if not main or main[0] <= today[0] or main[0] > SNAPSHOT_MAX_WIDTH:
            results.append({"channel": ch, "changed": False, "skipped": "no larger main stream" if not main or main[0] <= today[0] else "main stream too large"})
            continue
        try:
            current = driver.get_snapshot_format(ch)
        except DriverError as e:
            results.append({"channel": ch, "changed": False, "error": _sanitise(e)})
            continue
        if not current:
            results.append({"channel": ch, "changed": False, "skipped": "no snapshot setting"})
            continue
        size = f"{main[0]}x{main[1]}"
        res = execute_write(driver, "configure_snapshot", {"channel": ch, "sizes": {k: size for k in current}})
        results.append({"channel": ch, **{k: res.get(k) for k in ("ok", "changed", "verified", "rolled_back", "before", "after", "error")
                                          if k in res}})
    return results


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
    if action in MAINTENANCE_ACTIONS:
        # Never run against a bare driver: the Agent routes these to site_maintenance before
        # opening one (watchlog_agent._run_claimed_command).
        return {"action": action, "ok": False, "error": "requires_agent_runtime"}
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
        elif action == "probe_view_quality":
            data = _view_quality(driver, params)
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
_MOTION_EVENT_FIELDS = ("motion", "notify_center")

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
    # 5.1.13: the recorder's snapshot size (Dahua SnapFormat), raised to the camera's main-stream
    # size so stills are sharp. LOCAL ONLY, like configure_motion_events: run by the view-quality
    # probe when the owner asks for it (raise_snapshot); not in WRITE_ACTIONS.
    "configure_snapshot": {
        "read":    lambda d, p: d.get_snapshot_format(p["channel"]),
        "desired": lambda p: dict(p.get("sizes") or {}),
        "apply":   lambda d, p, v: d.set_snapshot_format(p["channel"], **v),
    },
    # 5.1.11: detections reach WatchLog (motion on; on Hikvision, linked to the surveillance
    # center). Only the fields this driver supports are read, wanted and written. LOCAL ONLY:
    # run by the Agent's own recorder event setup (recorder_event_setup.py); not in
    # WRITE_ACTIONS, so the cloud catalog (wl_site_command_propose_write) cannot request it yet.
    "configure_motion_events": {
        "read":    lambda d, p: {k: v for k, v in d.get_motion_events(p["channel"]).items()
                                 if k in _MOTION_EVENT_FIELDS},
        "desired": lambda p: {k: p[k] for k in _MOTION_EVENT_FIELDS if k in p},
        "apply":   lambda d, p, v: d.set_motion_events(p["channel"], **v),
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


__all__ = ["READ_ACTIONS", "MAINTENANCE_ACTIONS", "WRITE_ACTIONS", "WRITE_CAPABILITY",
           "execute_read", "execute_write", "inspect"]

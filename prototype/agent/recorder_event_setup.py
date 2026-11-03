"""Recorder event setup (5.1.11): make each camera's detections reach WatchLog, from the Agent.

Field (HASCO DS-7608NI-Q1, 2026-10-30): motion detection on for all 8 channels, yet no motion
event ever arrived — Hikvision only puts an event on the alert stream when its linkage includes
"Notify Surveillance Center". Until now that was an on-site recorder setting nobody made.

Runs in the background, in the one-shot capability worker right after the census (never in
Setup: field Build 41 showed a recorder write on the installer path stranding Step 06), per recorder:
  * Hikvision: motion detection on, and its linkage "Notify Surveillance Center" on;
  * Dahua: motion detection on, and smart motion (people) on where the recorder has it.
Every change goes through site_control.execute_write: read, change only what differs, read back,
roll back on a mismatch. Channels switched off on the recorder are left alone. A recorder that is
fully set up is remembered (by its channel list) and not touched again until its channels change;
`recorder_event_setup = false` in watchlog.ini (or WATCHLOG_RECORDER_EVENT_SETUP=0) turns it off.
Afterwards the capability census is re-sent, so the portal shows the new state.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
from typing import Callable

import site_control

def enabled(cfg) -> bool:
    value = os.environ.get("WATCHLOG_RECORDER_EVENT_SETUP")
    ini_path = getattr(cfg, "_ini_path", None)
    if value is None and ini_path is not None:
        try:
            ini = configparser.ConfigParser()
            ini.read(ini_path, encoding="utf-8-sig")
            value = ini.get("watchlog", "recorder_event_setup", fallback=None)
        except (configparser.Error, OSError):
            value = None
    return str(value if value is not None else "true").strip().lower() not in ("0", "false", "no", "off")


def _marker_path(cfg) -> Path | None:
    state_path = getattr(cfg, "state_path", None)
    return Path(state_path).parent / "recorder_event_setup.json" if state_path else None


def _load(path: Path | None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path | None, data: dict) -> None:
    if path is None:
        return
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def configure(driver, channels: list[str]) -> dict:
    """Set up event delivery on these channels. Never raises. Returns a summary:
    {"supported", "channels", "changed", "already", "failed": [{"channel", "action", "error"}]}."""
    fields = tuple(getattr(driver, "MOTION_EVENT_FIELDS", ()) or ())
    out = {"supported": bool(fields), "channels": len(channels), "changed": 0, "already": 0,
           "failed": []}
    if not fields:
        return out
    for ch in channels:
        steps = [("configure_motion_events", {"channel": ch, **{f: True for f in fields}})]
        try:
            smd = driver.get_smd(ch) or {}
        except Exception:                                   # noqa: BLE001
            smd = {}
        if smd.get("enable") is False:   # Dahua smart motion present but off
            steps.append(("configure_smd", {"channel": ch, "enable": True, "human": True}))
        changed = False
        for action, params in steps:
            res = site_control.execute_write(driver, action, params)
            if not res.get("ok"):
                out["failed"].append({"channel": ch, "action": action,
                                      "error": str(res.get("error") or "not confirmed")[:120]})
            elif res.get("changed"):
                changed = True
        if changed:
            out["changed"] += 1
        elif not any(f["channel"] == ch for f in out["failed"]):
            out["already"] += 1
    return out


def setup_once(cfg, state: dict, cloud, open_fn: Callable, *, recorder_id: str | None = None,
               log: Callable[[str], None] = lambda _m: None, force: bool = False) -> dict | None:
    """One pass for one recorder. Never raises; None when skipped."""
    if not enabled(cfg):
        log("recorder event setup: off in watchlog.ini")
        return None
    driver = None
    marker = _marker_path(cfg)
    key = str(recorder_id or getattr(cfg, "recorder_local_id", None) or "primary")
    try:
        driver, _info = open_fn(cfg)
        channels = [c.channel for c in driver.list_channels() if getattr(c, "enabled", True)]
        done = _load(marker)
        if not force and done.get(key) == sorted(channels):
            return None
        summary = configure(driver, channels)
        if not summary["supported"]:
            log("recorder event setup: not available for this recorder")
            return summary
        log(f"recorder event setup: {summary['channels']} channel(s), {summary['changed']} changed, "
            f"{summary['already']} already set, {len(summary['failed'])} failed"
            + "".join(f"; ch {f['channel']} {f['action']}: {f['error']}" for f in summary["failed"][:4]))
        if not summary["failed"]:
            done[key] = sorted(channels)
            _save(marker, done)
        if summary["changed"]:
            try:
                driver.close()
            except Exception:                               # noqa: BLE001
                pass
            driver = None
            import capability_sync
            capability_sync.sync_once(cfg, state, cloud, open_fn, recorder_id=recorder_id, log=log)
        return summary
    except BaseException as error:  # noqa: BLE001 — SystemExit from a missing config included
        if isinstance(error, KeyboardInterrupt):
            raise
        log(f"recorder event setup skipped: {type(error).__name__}")
        return None
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:                               # noqa: BLE001
                pass


__all__ = ["configure", "enabled", "setup_once"]

"""Recorder capability enrichment, deliberately OFF the startup path (field Build 41/69).

``driver.capabilities()`` is optional enrichment for the portal ("your recorder supports
motion, line crossing..."). On Hikvision it fans out into several ISAPI calls per channel, and
field Build 41 spent minutes in it before the live collector and heartbeat loop started. The
field builds therefore stopped running it at startup. It now runs once, in the background,
after the Agent has been monitoring for a while: through the recorder's own HTTP lock on
Hikvision (so it takes turns with the live stream and never competes with it), read-only,
best-effort, and never able to disturb monitoring.
"""
from __future__ import annotations

import threading
from typing import Callable

# Monitoring first: the enrichment waits this long after the Agent starts.
CAPABILITY_SYNC_DELAY_SECONDS = 120.0


def sync_once(cfg, state: dict, cloud, open_fn: Callable, *, recorder_id: str | None = None,
              log: Callable[[str], None] = lambda _m: None) -> bool:
    """Read the recorder's capabilities once and send them to the cloud. Never raises."""
    driver = None
    try:
        driver, _info = open_fn(cfg)
        capabilities = driver.capabilities()
        if not (isinstance(capabilities, dict) and capabilities.get("channels")):
            return False
        params = {"p_agent_id": state["agent_id"], "p_agent_key": state["agent_key"],
                  "p_capabilities": capabilities}
        if recorder_id:
            cloud.call("wl_sync_recorder_capabilities", p_recorder_id=recorder_id, **params)
        else:
            cloud.call("wl_sync_capabilities", **params)
        log(f"analytics reported: {len(capabilities['channels'])} channel(s)")
        return True
    except BaseException as error:  # noqa: BLE001 — SystemExit from a missing config included
        if isinstance(error, KeyboardInterrupt):
            raise
        log(f"recorder capability sync skipped: {type(error).__name__}")
        return False
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001
                pass


def defer(cfg, state: dict, cloud, open_fn: Callable, *, recorder_id: str | None = None,
          log: Callable[[str], None] = lambda _m: None, stop: threading.Event | None = None,
          delay: float | None = None) -> threading.Thread:
    """Run ``sync_once`` on a daemon thread after the start-up delay (or not at all if
    ``stop`` is set first)."""
    wait = CAPABILITY_SYNC_DELAY_SECONDS if delay is None else float(delay)
    stop = stop or threading.Event()

    def run() -> None:
        if stop.wait(wait):
            return
        sync_once(cfg, state, cloud, open_fn, recorder_id=recorder_id, log=log)

    name = f"capability-sync-{str(recorder_id)[:8]}" if recorder_id else "capability-sync"
    thread = threading.Thread(target=run, daemon=True, name=name)
    thread.start()
    return thread

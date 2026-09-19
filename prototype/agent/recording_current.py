"""Fresh recording/storage proof for the packaged Agent.

The durable health store intentionally records *transitions* only. That is the
right historical ledger, but an unchanged state cannot double as fresh evidence
forever. This module adds a separate present-tense snapshot path without
changing transition ordering/watermarks.

For Dahua recorders, RecordMode is configuration, not proof that frames reached
disk. Positive recording truth therefore comes from a successful recent archive
search. One empty/ambiguous search never becomes ``not_recording``; it becomes
UNKNOWN / Not verified. This is deliberately conservative.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

import dahua_archive
import recording_health

_ORIGINAL_ASSESS = recording_health.assess_recording_storage
_LOCAL = threading.local()
_INSTALLED = False

ARCHIVE_LOOKBACK_MINUTES = 12
ARCHIVE_SETTLE_MINUTES = 2


def _is_dahua(driver) -> bool:
    return driver is not None and getattr(driver, "name", "") == "dahua-cgi"


def _archive_assess(driver, channels, nvr_state: str, inventory=None) -> dict:
    """Preserve storage assessment, replace Dahua RecordMode with archive proof."""
    base = _ORIGINAL_ASSESS(driver, channels, nvr_state, inventory=inventory)
    if not _is_dahua(driver) or nvr_state != "ok":
        base["recording_evidence"] = "vendor_status"
        return base

    inventory = {str(k): v for k, v in (inventory or {}).items()}
    storage = base.get("storage") or {"state": "unknown", "reason": "unknown"}
    now = datetime.now(timezone.utc)
    window_end = now - timedelta(minutes=ARCHIVE_SETTLE_MINUTES)
    window_start = now - timedelta(minutes=ARCHIVE_LOOKBACK_MINUTES)

    rows = []
    archive_api_proven = False
    for raw_channel in channels:
        channel = str(raw_channel)
        inv = inventory.get(channel, "present")
        if inv == "missing":
            rows.append({"channel": channel, "state": "unknown", "reason": "channel_missing"})
            continue
        if inv == "disabled":
            rows.append({"channel": channel, "state": "unknown", "reason": "channel_disabled"})
            continue
        if storage.get("state") == "fault":
            rows.append({"channel": channel, "state": "storage_fault", "reason": "storage_fault"})
            continue
        try:
            found = dahua_archive.has_recording(driver, channel, window_start, window_end)
            archive_api_proven = True
        except Exception:  # noqa: BLE001 — archive ambiguity is UNKNOWN, never a false alarm
            found = False
        rows.append({
            "channel": channel,
            "state": "recording" if found else "unknown",
            "reason": "ok" if found else "unknown",
        })

    return {
        "storage": storage,
        "recording": {"supported": archive_api_proven, "channels": rows},
        "recording_evidence": "archive_search",
        "archive_window_start": window_start.isoformat().replace("+00:00", "Z"),
        "archive_window_end": window_end.isoformat().replace("+00:00", "Z"),
    }


def _capture_assess(driver, channels, nvr_state: str, inventory=None) -> dict:
    report = _archive_assess(driver, channels, nvr_state, inventory=inventory)
    _LOCAL.latest = report
    return report


def install(core) -> None:
    """Patch the production health cycle with a separate current-state RPC.

    ``core.health_cycle`` still owns all existing durable transition behavior.
    We only capture the assessment it already performs and, after that cycle
    finishes, publish the same result to the current-proof RPC. If the new RPC
    is absent or temporarily unavailable, normal event/health collection is
    unaffected.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    recording_health.assess_recording_storage = _capture_assess
    original_cycle = core.health_cycle

    def wrapped_cycle(cloud, state, cfg, holder):
        _LOCAL.latest = None
        result = original_cycle(cloud, state, cfg, holder)
        report = getattr(_LOCAL, "latest", None)
        if report:
            try:
                cloud.call(
                    "wl_report_recording_storage_current",
                    p_agent_id=state["agent_id"],
                    p_agent_key=state["agent_key"],
                    p_report=report,
                )
            except Exception as error:  # noqa: BLE001 — never disturb the agent
                try:
                    core.log(
                        "recording current proof deferred: "
                        f"{type(error).__name__}: {str(error).splitlines()[0][:160]}"
                    )
                except Exception:  # noqa: BLE001
                    pass
        return result

    core.health_cycle = wrapped_cycle


__all__ = ["install", "_archive_assess"]

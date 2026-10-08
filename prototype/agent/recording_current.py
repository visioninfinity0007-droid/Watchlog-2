"""Fresh recording/storage proof for the packaged Agent.

The durable health store intentionally records *transitions* only. That is the
right historical ledger, but an unchanged state cannot double as fresh evidence
forever. This module adds a separate present-tense snapshot path without
changing transition ordering/watermarks.

Recording configuration (Dahua RecordMode / Record schedule, Hikvision record
tracks) is not proof that frames reached disk. Per camera, for a recent window:

  * archive footage found                       -> recording (ok), with
                                                   latest_recording_at = newest segment end
  * the recorder says the channel does not
    record (mode off / track disabled / no
    recording in the schedule)                  -> not_recording (recording_disabled)
  * the archive search failed or was ambiguous  -> unknown (archive_search_failed)
  * the search succeeded and found nothing:
      - the camera is in video loss             -> unknown (video_loss)
      - recording is configured CONTINUOUSLY
        all week AND the recorder positively
        says the camera is connected            -> not_recording (no_recent_recording)
      - otherwise (event/part-week schedule,
        liveness or schedule not readable)      -> unknown (no_recent_archive)

So ``not_recording`` is only ever reported on positive evidence; an empty or
ambiguous search on its own stays UNKNOWN / Not verified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

import dahua_archive
import hikvision_archive
import recorder_registry
import recording_health

_ORIGINAL_ASSESS = recording_health.assess_recording_storage
_LOCAL = threading.local()
_INSTALLED = False

RECORDER_CURRENT_RPC = "wl_report_recorder_recording_storage_current"
SITE_CURRENT_RPC = "wl_report_recording_storage_current"
DISKS_RPC = "wl_report_recorder_storage_disks"
# Set once the database answers that the recorder-aware RPC does not exist.
_RECORDER_RPC_ABSENT = False
# Set once the database answers that the disk-inventory RPC (0161) does not exist.
_DISKS_RPC_ABSENT = False

ARCHIVE_LOOKBACK_MINUTES = 12
ARCHIVE_SETTLE_MINUTES = 2
# Rows read per channel: enough to find the newest segment in a 10-minute window.
ARCHIVE_ROW_LIMIT = 8
# Dahua: rows kept from a search that already read every finder page (no extra recorder calls).
DAHUA_ROWS_FOR_NEWEST = 10_000


def _is_dahua(driver) -> bool:
    return driver is not None and getattr(driver, "name", "") == "dahua-cgi"

def _is_hikvision(driver) -> bool:
    return driver is not None and getattr(driver, "name", "") == "hikvision-isapi"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc(text) -> datetime | None:
    """A segment time that names its zone, as aware UTC; None for a zone-less or unreadable one
    (a recorder-local time with an unknown offset is never assumed to be UTC)."""
    try:
        value = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return value.astimezone(timezone.utc) if value.tzinfo is not None else None


def _liveness(driver, channels) -> tuple[set, set]:
    """(live, lost) channel sets from the recorder's own present-tense answer.

    Dahua: the VideoLoss event index lists every lost channel, so the rest are live (only when
    that index itself answered). Hikvision: the IP-channel status names each channel online or
    offline; a channel it does not name is neither. Any failure leaves liveness unknown."""
    wanted = {str(c) for c in channels}
    try:
        if _is_dahua(driver):
            faults = driver.current_faults() or {}
            if faults.get("supported") and faults.get("video_loss_supported", True):
                lost = {str(c) for c in faults.get("video_loss") or []} & wanted
                return wanted - lost, lost
        elif _is_hikvision(driver) and hasattr(driver, "channel_liveness"):
            status = driver.channel_liveness() or {}
            if status.get("supported"):
                return ({str(c) for c in status.get("online") or []} & wanted,
                        {str(c) for c in status.get("offline") or []} & wanted)
    except Exception:  # noqa: BLE001 — unknown liveness never becomes a fault
        pass
    return set(), set()


def _search(driver, channel: str, start: datetime, end: datetime) -> tuple[str, str | None]:
    """('found' | 'empty' | 'failed', latest segment end as ISO UTC or None).

    'empty' only when the recorder's archive search itself succeeded and returned no footage."""
    try:
        if _is_dahua(driver):
            # Every finder page is read anyway and the rows come back oldest-first, so a page
            # of ARCHIVE_ROW_LIMIT rows held the OLDEST segments: Al-Khalid (DH-XVR1B08-I,
            # 2026-10-08) showed a newest recording of 01:00-02:00 PKT all afternoon. The newest
            # end is taken over every row the search returned.
            result = dahua_archive.enumerate_historical_events(
                driver, channel, start, end, None, DAHUA_ROWS_FOR_NEWEST) or {}
            ends = [_utc((e.get("segment") or {}).get("end")) for e in result.get("events") or [] if e]
            if result.get("events"):
                latest = max((t for t in ends if t is not None), default=None)
                return "found", (_iso(latest) if latest else None)
            return ("empty", None) if result.get("status") == "supported" else ("failed", None)
        page = hikvision_archive.search_recordings(driver, channel, start, end,
                                                   limit=ARCHIVE_ROW_LIMIT)
        matches = page.get("matches") or []
        if matches or page.get("incomplete"):
            # A match without a time span is still footage in the window: found, newest unknown.
            ends = [_utc(m.get("end")) for m in matches]
            latest = max((t for t in ends if t is not None), default=None)
            return "found", (_iso(latest) if latest else None)
        return ("empty", None) if page.get("status") == "supported" else ("failed", None)
    except Exception:  # noqa: BLE001 — archive ambiguity is UNKNOWN, never a false alarm
        return "failed", None


def _archive_assess(driver, channels, nvr_state: str, inventory=None) -> dict:
    """Preserve the storage assessment; replace vendor recording status with archive proof,
    read together with the recorder's recording configuration and channel liveness."""
    base = _ORIGINAL_ASSESS(driver, channels, nvr_state, inventory=inventory)
    if nvr_state != "ok" or not (_is_dahua(driver) or _is_hikvision(driver)):
        base["recording_evidence"] = "vendor_status"
        return base

    inventory = {str(k): v for k, v in (inventory or {}).items()}
    storage = base.get("storage") or {"state": "unknown", "reason": "unknown"}
    config = base.get("recording_config") or {}
    now = datetime.now(timezone.utc)
    window_end = now - timedelta(minutes=ARCHIVE_SETTLE_MINUTES)
    window_start = now - timedelta(minutes=ARCHIVE_LOOKBACK_MINUTES)
    live, lost = _liveness(driver, channels)

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
        outcome, latest = _search(driver, channel, window_start, window_end)
        if outcome in ("found", "empty"):
            archive_api_proven = True
        mode = config.get(channel)
        if outcome == "found":
            # Footage on disk is the proof, whatever the configuration says.
            row = {"channel": channel, "state": "recording", "reason": "ok"}
            if latest:
                row["latest_recording_at"] = latest
        elif mode == "disabled":
            row = {"channel": channel, "state": "not_recording", "reason": "recording_disabled"}
        elif outcome == "failed":
            row = {"channel": channel, "state": "unknown", "reason": "archive_search_failed"}
        elif channel in lost:
            row = {"channel": channel, "state": "unknown", "reason": "video_loss"}
        elif mode == "continuous" and channel in live:
            row = {"channel": channel, "state": "not_recording", "reason": "no_recent_recording"}
        else:
            row = {"channel": channel, "state": "unknown", "reason": "no_recent_archive"}
        rows.append(row)

    report = {
        "storage": storage,
        "recording": {"supported": archive_api_proven, "channels": rows},
        "recording_evidence": "archive_search",
        "archive_window_start": _iso(window_start),
        "archive_window_end": _iso(window_end),
    }
    if config:
        report["recording_config"] = config
    return report


def _capture_assess(driver, channels, nvr_state: str, inventory=None) -> dict:
    report = _archive_assess(driver, channels, nvr_state, inventory=inventory)
    _LOCAL.latest = report
    return report


def _rpc_absent(error: Exception, fn: str) -> bool:
    """The database has no such function: PostgREST PGRST202 / 404, or Postgres 42883."""
    return (getattr(error, "fn", "") == fn
            and (getattr(error, "code", None) in ("PGRST202", "42883")
                 or getattr(error, "status", None) == 404))


def _single_recorder_site() -> bool:
    """True when this PC monitors at most one recorder (no registry counts as one)."""
    try:
        configured = [row for row in recorder_registry.recorders() if row.get("is_configured")]
    except Exception:  # noqa: BLE001 — an unreadable registry is never assumed single
        return False
    return len(configured) <= 1


def report_current(core, cloud, state, cfg, report) -> None:
    """Publish one recorder's present-tense recording/storage proof (MNVR-013).

    A bound recorder names itself: its proof covers only its own channels and storage. The
    site-scoped RPC applies a proof to every same-numbered channel of the site, so it is
    used only by a recorder-less runtime or, on a database that has no recorder-aware RPC,
    only while this site has a single recorder. A multi-recorder site on such a database
    defers the proof rather than apply one recorder's state to another's cameras."""
    global _RECORDER_RPC_ABSENT
    recorder_id = str(getattr(cfg, "recorder_cloud_id", "") or "").strip()
    args = dict(p_agent_id=state["agent_id"], p_agent_key=state["agent_key"])
    if recorder_id and not _RECORDER_RPC_ABSENT:
        try:
            cloud.call(RECORDER_CURRENT_RPC, p_recorder_id=recorder_id, p_report=report, **args)
            return
        except Exception as error:  # noqa: BLE001 — classified below
            if not _rpc_absent(error, RECORDER_CURRENT_RPC):
                raise
            _RECORDER_RPC_ABSENT = True
    if recorder_id and not _single_recorder_site():
        core.log("recording current proof deferred: this WatchLog database cannot yet take "
                 "a proof for one recorder of a multi-recorder site")
        return
    cloud.call(SITE_CURRENT_RPC, p_report=report, **args)


def disk_report(report: dict) -> dict | None:
    """The per-disk inventory and recorder totals of one assessment, or None when storage was
    not read this cycle (an unread recorder never clears what the cloud last saw)."""
    storage = (report or {}).get("storage") or {}
    disks = storage.get("disks")
    if not isinstance(disks, list):
        return None
    return {"state": storage.get("state", "unknown"), "reason": storage.get("reason", "unknown"),
            "disks": disks, "total_bytes": storage.get("total_bytes"),
            "free_bytes": storage.get("free_bytes")}


def report_disks(core, cloud, state, cfg, report) -> None:
    """Publish one recorder's disk inventory and capacity (0161). Recorder-scoped only: a
    recorder-less runtime cannot attribute disks. A database without the RPC (5.0.x / 5.1.1
    servers) is skipped silently and not asked again."""
    global _DISKS_RPC_ABSENT
    recorder_id = str(getattr(cfg, "recorder_cloud_id", "") or "").strip()
    payload = disk_report(report)
    if not recorder_id or payload is None or _DISKS_RPC_ABSENT:
        return
    try:
        cloud.call(DISKS_RPC, p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                   p_recorder_id=recorder_id, p_report=payload)
    except Exception as error:  # noqa: BLE001 — classified below
        if not _rpc_absent(error, DISKS_RPC):
            raise
        _DISKS_RPC_ABSENT = True


def install(core) -> None:
    """Patch the production health cycle with a separate current-state RPC.

    ``core.health_cycle`` still owns all existing durable transition behavior.
    We only capture the assessment it already performs and, after that cycle
    finishes, publish the same result to the current-proof RPC and the disk
    inventory to the disk RPC. If either RPC is absent or temporarily
    unavailable, normal event/health collection is unaffected.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    recording_health.assess_recording_storage = _capture_assess
    original_cycle = core.health_cycle

    def _log_deferred(what: str, error: Exception) -> None:
        try:
            core.log(f"{what} deferred: "
                     f"{type(error).__name__}: {str(error).splitlines()[0][:160]}")
        except Exception:  # noqa: BLE001
            pass

    def wrapped_cycle(cloud, state, cfg, holder):
        _LOCAL.latest = None
        result = original_cycle(cloud, state, cfg, holder)
        report = getattr(_LOCAL, "latest", None)
        if report:
            try:
                report_current(core, cloud, state, cfg, report)
            except Exception as error:  # noqa: BLE001 — never disturb the agent
                _log_deferred("recording current proof", error)
            try:
                report_disks(core, cloud, state, cfg, report)
            except Exception as error:  # noqa: BLE001 — never disturb the agent
                _log_deferred("recorder disk inventory", error)
        return result

    core.health_cycle = wrapped_cycle


__all__ = ["install", "report_current", "report_disks", "disk_report", "_archive_assess"]

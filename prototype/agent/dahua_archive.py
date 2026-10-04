"""Dahua recorder archive search + bounded clip download.

This module is deliberately separate from the live-event driver until the path is
field-validated on the Al-Khalid DH-XVR1B08-I. The packaged release imports it
explicitly and installs :func:`get_clip` on ``DahuaDriver``.

Safety / truth rules:
* read-only CGI calls only;
* WatchLog channel numbers are converted to Dahua's native zero-based indexes;
* the recorder's own clock is read once per call. Times the Agent stamped (UTC
  cloud windows, Dahua CGI event receive times) move onto recorder wall time by
  the measured offset, drift included; times the recorder's own clock stamped
  (archive segment times, ONVIF UtcTime) move by the recorder's zone only.
  Archive segment times go back to UTC the same way, so no naive recorder-local
  time leaves this module to be misread as UTC;
* mediaFileFind must prove a recording exists in the requested window before
  loadfile is allowed to transfer bytes;
* downloads are bounded to the same 32 MiB pilot limit as incident_evidence and
  to a total time budget;
* ambiguous clock/search/download responses fail closed with DriverError.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import time
from typing import Iterable

import requests
from requests.auth import HTTPBasicAuth

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable, explain
from drivers.dahua import DahuaDriver

MAX_CLIP_BYTES = 32 * 1024 * 1024
DOWNLOAD_TIMEOUT = (5, 30)           # (connect, read) seconds for the streamed loadfile request
CLIP_TOTAL_SECONDS = 90              # whole get_clip: clock read, search and download
FINDER_COUNT = 100
ZONE_STEP_SECONDS = 15 * 60          # every civil UTC offset is a whole number of quarter hours
MAX_ZONE_DRIFT_SECONDS = 5 * 60      # beyond this the recorder's zone cannot be told from drift
AGENT_CLOCK, RECORDER_CLOCK = "agent", "recorder"

_ITEM_RE = re.compile(r"items\[(\d+)\]\.([^=]+)=(.*)")
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S%z",
)


def _request(driver: DahuaDriver, path: str, *, params=None, stream=False, timeout=None):
    """Binary-safe counterpart to DahuaDriver._get with the same auth policy."""
    url = driver.base_url + path
    try:
        response = driver.s.get(
            url,
            params=params,
            timeout=timeout or driver.timeout,
            stream=stream,
        )
    except requests.RequestException as error:
        raise NvrUnreachable(f"{url}: {explain(error)}") from error

    if response.status_code == 401:
        driver.s.auth = HTTPBasicAuth(driver.username, driver.password)
        try:
            response = driver.s.get(
                url,
                params=params,
                timeout=timeout or driver.timeout,
                stream=stream,
            )
        except requests.RequestException as error:
            raise NvrUnreachable(f"{url}: {explain(error)}") from error

    if response.status_code in (401, 403):
        raise NvrAuthFailed(
            f"{url}: HTTP {response.status_code} — recorder rejected the username or password"
        )
    if response.status_code >= 400:
        try:
            detail = response.text[:160]
        except Exception:  # noqa: BLE001
            detail = ""
        raise DriverError(f"{url}: HTTP {response.status_code} {detail}".strip())
    return response


def _text(driver: DahuaDriver, path: str, *, params=None, timeout=None) -> str:
    return _request(driver, path, params=params, timeout=timeout).text


def _parse_time(value: str) -> datetime | None:
    normalized = str(value).strip().replace("Z", "+0000")
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _parse_device_clock(text: str) -> datetime:
    """Return recorder wall clock as a naive datetime.

    Dahua CGI commonly returns ``result=YYYY-MM-DD HH:MM:SS``. A few firmware
    families use a different key or the bare value, so all values are inspected.
    We intentionally do not guess if no supported timestamp is present.
    """
    raw = str(text or "").strip()
    candidates: list[str] = []
    for line in raw.splitlines():
        value = line.split("=", 1)[1].strip() if "=" in line else line.strip()
        if value:
            candidates.append(value)
    for value in candidates:
        parsed = _parse_time(value)
        if parsed is not None:
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
    raise DriverError("recorder current time was not parseable; refusing ambiguous archive request")


def _recorder_clock(driver: DahuaDriver) -> tuple[timedelta, timedelta | None]:
    """Read the recorder clock once and return ``(offset, zone)`` against the agent's UTC clock.

    ``offset`` is recorder wall time minus agent UTC, unrounded. It carries the recorder's drift,
    which is exactly what maps an instant the agent stamped onto the recorder's footage.
    ``zone`` is that offset snapped to the nearest quarter hour: the recorder's configured UTC
    offset without drift, for times the recorder's own clock stamped. It is None when the drift
    is too large to tell zone from drift. It is the CURRENT zone; footage recorded before a DST
    change is not re-zoned.
    """
    device_now = _parse_device_clock(_text(driver, "/cgi-bin/global.cgi", params={"action": "getCurrentTime"}))
    offset = device_now - datetime.now(timezone.utc).replace(tzinfo=None)
    seconds = offset.total_seconds()
    if abs(seconds) > 15 * 3600:
        raise DriverError("recorder clock offset is implausible; refusing archive request")
    zone = timedelta(seconds=round(seconds / ZONE_STEP_SECONDS) * ZONE_STEP_SECONDS)
    if abs((offset - zone).total_seconds()) > MAX_ZONE_DRIFT_SECONDS:
        zone = None
    return offset, zone


def _clock_shift(clock: str, offset: timedelta, zone: timedelta | None) -> timedelta:
    """Distance from UTC times stamped by ``clock`` to recorder wall time."""
    if clock == AGENT_CLOCK:
        return offset
    if clock == RECORDER_CLOCK:
        if zone is None:
            raise DriverError("recorder clock is too far off to tell its time zone; refusing archive request")
        return zone
    raise DriverError(f"unknown archive clock source: {clock}")


def _localize_window(driver: DahuaDriver, start: datetime, end: datetime, *,
                     clock: str = AGENT_CLOCK, recorder_clock=None) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise DriverError("archive request timestamps must be timezone-aware")
    if end <= start:
        raise DriverError("archive request end must be after start")

    # The offset is used unrounded. Rounding it to the minute dropped up to 30 s of real drift
    # for agent-stamped events and doubled it for recorder-stamped ones, either of which can push
    # a 30 s incident window off the event.
    shift = _clock_shift(clock, *(recorder_clock or _recorder_clock(driver)))
    local_start = start.astimezone(timezone.utc).replace(tzinfo=None) + shift
    local_end = end.astimezone(timezone.utc).replace(tzinfo=None) + shift
    # The recorder takes whole seconds: widen outward so the request is never narrower.
    if local_end.microsecond:
        local_end = local_end.replace(microsecond=0) + timedelta(seconds=1)
    return local_start.replace(microsecond=0), local_end


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _segment_utc(value, zone: timedelta) -> str | None:
    """Recorder-stamped archive time -> ISO-8601 UTC ("...Z"); None when the recorder gave none."""
    if not value:
        return None
    parsed = _parse_time(value)
    if parsed is None:
        raise DriverError("recorder returned an unparseable archive segment time")
    if parsed.tzinfo is None:
        parsed = (parsed - zone).replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _native_channel(channel) -> int:
    try:
        native_channel = int(str(channel)) - 1
    except ValueError as error:
        raise DriverError(f"invalid camera channel: {channel}") from error
    if native_channel < 0:
        raise DriverError(f"invalid camera channel: {channel}")
    return native_channel


def _finder_id(text: str) -> str:
    for line in str(text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() in ("result", "object") and value.strip():
                return value.strip()
    raise DriverError("recorder did not create a media-file finder")


def _parse_items(text: str) -> list[dict]:
    rows: dict[int, dict] = {}
    for line in str(text or "").splitlines():
        match = _ITEM_RE.match(line.strip())
        if not match:
            continue
        idx, key, value = int(match.group(1)), match.group(2), match.group(3)
        rows.setdefault(idx, {})[key] = value
    return [rows[idx] for idx in sorted(rows)]


def _row_fields(row: dict) -> tuple:
    return (row.get("StartTime") or row.get("BeginTime") or row.get("startTime"),
            row.get("EndTime") or row.get("endTime"),
            row.get("FilePath") or row.get("filepath"))


def _find_local(driver: DahuaDriver, native_channel: int, local_start: datetime,
                local_end: datetime, *, max_items: int) -> list[dict]:
    """One mediaFileFind session over an already recorder-local window."""
    finder = _finder_id(_text(
        driver,
        "/cgi-bin/mediaFileFind.cgi",
        params={"action": "factory.create"},
    ))
    try:
        started = _text(
            driver,
            "/cgi-bin/mediaFileFind.cgi",
            params={
                "action": "findFile",
                "object": finder,
                "condition.Channel": native_channel,
                "condition.StartTime": _fmt(local_start),
                "condition.EndTime": _fmt(local_end),
                "condition.Types[0]": "dav",
            },
        )
        if str(started).strip().upper() != "OK":
            raise DriverError("recorder rejected the archive-search window")

        result = _text(
            driver,
            "/cgi-bin/mediaFileFind.cgi",
            params={"action": "findNextFile", "object": finder,
                    "count": min(max(1, int(max_items)), FINDER_COUNT)},
            timeout=max(driver.timeout, 30),
        )
        return _parse_items(result)
    finally:
        # Best effort cleanup: finder handles are recorder resources. A cleanup
        # failure must not mask a successful/meaningful search result.
        for action in ("close", "destroy"):
            try:
                _text(driver, "/cgi-bin/mediaFileFind.cgi",
                      params={"action": action, "object": finder})
            except Exception:  # noqa: BLE001
                pass


def find_recordings(driver: DahuaDriver, channel: str, start: datetime, end: datetime,
                    *, max_items: int = FINDER_COUNT) -> list[dict]:
    """Search recorder archive for DAV recordings overlapping ``start..end``.

    Public WatchLog channels are 1-based; Dahua CGI channel indexes are 0-based.
    Returned metadata is recorder-native and used only to prove the requested
    channel/time has media before download.
    """
    native_channel = _native_channel(channel)
    local_start, local_end = _localize_window(driver, start, end)
    return _find_local(driver, native_channel, local_start, local_end, max_items=max_items)


def has_recording(driver: DahuaDriver, channel: str, start: datetime, end: datetime) -> bool:
    return bool(find_recordings(driver, channel, start, end, max_items=1))


def _read_bounded(response, max_bytes: int = MAX_CLIP_BYTES, *, deadline=None) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=256 * 1024):
        # A read timeout alone never ends a download that keeps trickling data.
        if deadline is not None and time.monotonic() >= deadline:
            raise DriverError("recorder did not export this footage window in time")
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise DriverError("incident footage exceeds the 32 MiB pilot limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise DriverError("recorder returned an empty footage download")

    # Common CGI error bodies are tiny text/html. DHAV itself is binary and may
    # contain ASCII fragments later, so inspect only the beginning.
    head = data[:256].lstrip().lower()
    if head.startswith((b"<html", b"<!doctype", b"error", b"bad request", b"result=error")):
        raise DriverError("recorder returned an error instead of footage")
    return data


def get_clip(driver: DahuaDriver, channel: str, start: datetime, end: datetime, *,
             clock: str = AGENT_CLOCK) -> bytes | None:
    """Retrieve a bounded recorder-native DAV clip for one requested window.

    ``clock`` names the clock that stamped ``start``/``end``: "agent" (the default) for times the
    Agent stamped, i.e. UTC cloud windows and Dahua CGI events, which carry the PC receive time;
    "recorder" for times the recorder's own clock stamped, i.e. archive segment times from
    :func:`enumerate_historical_events` or ONVIF UtcTime. The whole call, clock read and search
    included, runs within CLIP_TOTAL_SECONDS.
    """
    deadline = time.monotonic() + CLIP_TOTAL_SECONDS
    native_channel = _native_channel(channel)
    # One clock reading serves both the search and the download, so both use the same window.
    local_start, local_end = _localize_window(driver, start, end, clock=clock)
    # Search first. This both proves the archive has media in the requested
    # channel/window and prevents a download call for an empty period.
    if not _find_local(driver, native_channel, local_start, local_end, max_items=1):
        return None

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DriverError("recorder did not export this footage window in time")
    response = _request(
        driver,
        "/cgi-bin/loadfile.cgi",
        params={
            "action": "startLoad",
            "channel": native_channel,
            "startTime": _fmt(local_start),
            "endTime": _fmt(local_end),
            "subtype": 0,
        },
        stream=True,
        # (connect, read): no single read may outlive what is left of the total budget.
        timeout=(DOWNLOAD_TIMEOUT[0], max(1.0, min(DOWNLOAD_TIMEOUT[1], remaining))),
    )
    # A streamed response holds the underlying connection open until it is fully
    # consumed OR explicitly closed. _read_bounded may raise (empty / oversized /
    # error-body) or return early, so the response is ALWAYS closed here — a leaked
    # streamed connection would eventually exhaust the recorder's session pool.
    try:
        return _read_bounded(response, deadline=deadline)
    except requests.RequestException as error:
        # A stalled or reset download. requests' own text names the recorder's LAN host, which
        # must not reach cloud-visible failure text.
        raise DriverError("recorder stopped sending this footage window") from error
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001 — close must never mask the real outcome
            pass


def enumerate_historical_events(driver: DahuaDriver, channel, start, end, cursor=None, limit: int = 500) -> dict:
    """Recovery enumeration: the recorder's ARCHIVE segments overlapping [start, end) become
    recoverable intelligence (each recorded segment is a recovered evidence window). Honest
    status: an unreachable/ambiguous recorder returns 'unknown' (never a fabricated 'supported'
    with empty data, and never masquerading as live). Read-only; bounded by FINDER_COUNT.

    ``start``/``end`` are agent-clock UTC. Segment times come back as ISO-8601 UTC converted with
    the recorder's zone, because the recorder's own clock stamped them; replaying them through
    ``get_recorded_segment`` lands on the same recorder wall time.
    """
    try:
        native_channel = _native_channel(channel)
        recorder_clock = _recorder_clock(driver)
        # Refuse before searching if segment times could not be converted back to UTC.
        zone = _clock_shift(RECORDER_CLOCK, *recorder_clock)
        local_start, local_end = _localize_window(driver, start, end, recorder_clock=recorder_clock)
        rows = _find_local(driver, native_channel, local_start, local_end,
                           max_items=min(max(1, int(limit)), FINDER_COUNT))
        events = []
        for row in rows:
            st_raw, et_raw, path = _row_fields(row)
            st, et = _segment_utc(st_raw, zone), _segment_utc(et_raw, zone)
            events.append({
                "ts": st,
                "type": "recorded_segment",
                # Identity stays recorder-native, so a zone change never re-recovers a file.
                "device_event_id": path or f"{channel}:{st_raw}:{et_raw}",
                "channel": str(channel),
                "segment": {"start": st, "end": et, "path": path},
            })
    except (NvrUnreachable, NvrAuthFailed):
        return {"status": "unknown", "events": [], "next_cursor": None}
    except DriverError:
        # An ambiguous clock/search response failed closed upstream — unknown, not unsupported.
        return {"status": "unknown", "events": [], "next_cursor": None}
    return {"status": "supported", "events": events, "next_cursor": None}


def historical_capability(driver: DahuaDriver = None) -> dict:
    """Dahua archive: segment enumeration + bounded clip retrieval are supported (validated on the
    Cooper-I pilot path); snapshot-at-timestamp is not exposed on the validated path."""
    return {"events": "supported", "snapshots": "unsupported", "segments": "supported"}


ARCHIVE_PROOF_WINDOW = 1800          # default recent window (30 min) for a setup-time archive proof


def prove_recorder_archive(driver, channel, *, now: datetime = None,
                           window_seconds: int = ARCHIVE_PROOF_WINDOW, limit: int = 8) -> dict:
    """Prove — bounded and read-only — that this recorder actually RETAINS retrievable footage on
    one channel, so automatic outage recovery has something to recover. This is the setup-time
    analog of :func:`enumerate_historical_events`; it never downloads media (fast enough for the
    setup deadline) and NEVER raises — setup evidence must degrade to an honest status, not crash.

    Honest verdict (mirrors the three-coverage-classes honesty, never fabricated):
      * ``verified``    recorded segments were enumerated in the recent window (retrievable);
      * ``empty``       the archive is queryable but has NO footage in the window (recording
                        off, or a brand-new recorder) — capability present, proof negative;
      * ``unsupported`` this recorder/driver cannot enumerate an archive at all;
      * ``unknown``     the recorder was unreachable or answered ambiguously.
    """
    now = now or datetime.now(timezone.utc)
    window_seconds = max(60, int(window_seconds))
    start = now - timedelta(seconds=window_seconds)
    minutes = round(window_seconds / 60)
    proof = {"status": "unknown", "channel": str(channel), "window_seconds": window_seconds,
             "segments_found": 0, "sample": [], "detail": ""}

    # Capability gate: a driver with no archive enumeration (non-Dahua, or an explicitly
    # unsupported segment capability) can never prove footage — say so plainly, don't guess.
    cap = None
    try:
        if hasattr(driver, "historical_capability"):
            cap = driver.historical_capability() or {}
    except Exception:  # noqa: BLE001 — capability probing must not crash the proof
        cap = None
    if not hasattr(driver, "enumerate_historical_events") or (
            isinstance(cap, dict) and cap.get("segments") == "unsupported"):
        proof["status"] = "unsupported"
        proof["detail"] = "This recorder does not expose a searchable recording archive."
        return proof

    try:
        result = driver.enumerate_historical_events(channel, start, now, None, limit) or {}
    except Exception:  # noqa: BLE001 — an unreachable/ambiguous recorder is honest-unknown
        proof["detail"] = "The recorder did not answer the archive search."
        return proof

    if str(result.get("status")) != "supported":
        proof["detail"] = "The recorder was reachable but the archive search was inconclusive."
        return proof

    events = [e for e in (result.get("events") or []) if e]
    proof["segments_found"] = len(events)
    if events:
        proof["status"] = "verified"
        proof["sample"] = [e.get("segment") or {} for e in events[:3]]
        proof["detail"] = (f"{len(events)} recorded segment(s) retrievable on channel "
                           f"{channel} in the last {minutes} min.")
    else:
        proof["status"] = "empty"
        proof["detail"] = (f"No recorded footage found on channel {channel} in the last "
                           f"{minutes} min — confirm the recorder is recording this channel.")
    return proof


def install() -> None:
    """Install the validated-shape archive + recovery implementation on DahuaDriver.

    Keeping this as an explicit production-entrypoint patch makes packaging deterministic while
    preserving the field-evidence boundary in dahua.py.
    """
    DahuaDriver.get_clip = get_clip
    DahuaDriver.enumerate_historical_events = (
        lambda self, channel, start, end, cursor=None, limit=500:
        enumerate_historical_events(self, channel, start, end, cursor, limit))
    # Recorded-segment replay is fed segment times from enumerate_historical_events, which the
    # recorder's own clock stamped.
    DahuaDriver.get_recorded_segment = (
        lambda self, channel, start, end: {
            "status": "supported", "bytes": get_clip(self, channel, start, end, clock=RECORDER_CLOCK)})
    DahuaDriver.historical_capability = lambda self: historical_capability(self)


__all__ = ["find_recordings", "has_recording", "get_clip", "enumerate_historical_events",
           "historical_capability", "prove_recorder_archive", "ARCHIVE_PROOF_WINDOW", "install",
           "AGENT_CLOCK", "RECORDER_CLOCK"]

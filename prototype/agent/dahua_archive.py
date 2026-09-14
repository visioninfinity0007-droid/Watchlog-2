"""Dahua recorder archive search + bounded clip download.

This module is deliberately separate from the live-event driver until the path is
field-validated on the Al-Khalid DH-XVR1B08-I. The packaged release imports it
explicitly and installs :func:`get_clip` on ``DahuaDriver``.

Safety / truth rules:
* read-only CGI calls only;
* WatchLog channel numbers are converted to Dahua's native zero-based indexes;
* the recorder's own clock is read first so UTC cloud timestamps are converted
  to recorder-local wall time rather than silently requesting the wrong footage;
* mediaFileFind must prove a recording exists in the requested window before
  loadfile is allowed to transfer bytes;
* downloads are bounded to the same 32 MiB pilot limit as incident_evidence;
* ambiguous clock/search/download responses fail closed with DriverError.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Iterable

import requests
from requests.auth import HTTPBasicAuth

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable, explain
from drivers.dahua import DahuaDriver

MAX_CLIP_BYTES = 32 * 1024 * 1024
DOWNLOAD_TIMEOUT = 90
FINDER_COUNT = 100

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
        normalized = value.replace("Z", "+0000")
        for fmt in _TIME_FORMATS:
            try:
                parsed = datetime.strptime(normalized, fmt)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except ValueError:
                continue
    raise DriverError("recorder current time was not parseable; refusing ambiguous archive request")


def _localize_window(driver: DahuaDriver, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise DriverError("archive request timestamps must be timezone-aware")
    if end <= start:
        raise DriverError("archive request end must be after start")

    device_now = _parse_device_clock(_text(driver, "/cgi-bin/global.cgi", params={"action": "getCurrentTime"}))
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    offset = device_now - utc_now

    # Recorder clocks may differ by a few seconds. Nearest minute preserves the
    # timezone offset without baking transient clock skew into historical lookup.
    offset_seconds = round(offset.total_seconds() / 60.0) * 60
    if abs(offset_seconds) > 15 * 3600:
        raise DriverError("recorder clock offset is implausible; refusing archive request")

    local_start = start.astimezone(timezone.utc).replace(tzinfo=None)
    local_end = end.astimezone(timezone.utc).replace(tzinfo=None)
    from datetime import timedelta
    delta = timedelta(seconds=offset_seconds)
    return local_start + delta, local_end + delta


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


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


def find_recordings(driver: DahuaDriver, channel: str, start: datetime, end: datetime,
                    *, max_items: int = FINDER_COUNT) -> list[dict]:
    """Search recorder archive for DAV recordings overlapping ``start..end``.

    Public WatchLog channels are 1-based; Dahua CGI channel indexes are 0-based.
    Returned metadata is recorder-native and used only to prove the requested
    channel/time has media before download.
    """
    try:
        native_channel = int(str(channel)) - 1
    except ValueError as error:
        raise DriverError(f"invalid camera channel: {channel}") from error
    if native_channel < 0:
        raise DriverError(f"invalid camera channel: {channel}")

    local_start, local_end = _localize_window(driver, start, end)
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


def has_recording(driver: DahuaDriver, channel: str, start: datetime, end: datetime) -> bool:
    return bool(find_recordings(driver, channel, start, end, max_items=1))


def _read_bounded(response, max_bytes: int = MAX_CLIP_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=256 * 1024):
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


def get_clip(driver: DahuaDriver, channel: str, start: datetime, end: datetime) -> bytes | None:
    """Retrieve a bounded recorder-native DAV clip for one requested window."""
    try:
        native_channel = int(str(channel)) - 1
    except ValueError as error:
        raise DriverError(f"invalid camera channel: {channel}") from error
    if native_channel < 0:
        raise DriverError(f"invalid camera channel: {channel}")

    local_start, local_end = _localize_window(driver, start, end)
    # Search first. This both proves the archive has media in the requested
    # channel/window and prevents a download call for an empty period.
    if not find_recordings(driver, channel, start, end, max_items=1):
        return None

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
        timeout=max(driver.timeout, DOWNLOAD_TIMEOUT),
    )
    # A streamed response holds the underlying connection open until it is fully
    # consumed OR explicitly closed. _read_bounded may raise (empty / oversized /
    # error-body) or return early, so the response is ALWAYS closed here — a leaked
    # streamed connection would eventually exhaust the recorder's session pool.
    try:
        return _read_bounded(response)
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
    """
    try:
        recs = find_recordings(driver, channel, start, end, max_items=min(max(1, int(limit)), FINDER_COUNT))
    except (NvrUnreachable, NvrAuthFailed):
        return {"status": "unknown", "events": [], "next_cursor": None}
    except DriverError:
        # An ambiguous clock/search response failed closed upstream — unknown, not unsupported.
        return {"status": "unknown", "events": [], "next_cursor": None}
    events = []
    for r in recs:
        st = r.get("StartTime") or r.get("BeginTime") or r.get("startTime")
        et = r.get("EndTime") or r.get("endTime")
        path = r.get("FilePath") or r.get("filepath")
        events.append({
            "ts": st,
            "type": "recorded_segment",
            "device_event_id": path or f"{channel}:{st}:{et}",
            "channel": str(channel),
            "segment": {"start": st, "end": et, "path": path},
        })
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
    DahuaDriver.get_recorded_segment = (
        lambda self, channel, start, end: {"status": "supported", "bytes": get_clip(self, channel, start, end)})
    DahuaDriver.historical_capability = lambda self: historical_capability(self)


__all__ = ["find_recordings", "has_recording", "get_clip", "enumerate_historical_events",
           "historical_capability", "prove_recorder_archive", "ARCHIVE_PROOF_WINDOW", "install"]

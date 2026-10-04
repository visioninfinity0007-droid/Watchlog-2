"""Hikvision ISAPI archive search and bounded video download.

Uses the documented ContentMgmt search/download flow:
  POST /ISAPI/ContentMgmt/search -> playbackURI
  POST /ISAPI/ContentMgmt/download -> recorded bytes

Everything is read-only and bounded. The implementation deliberately fails closed:
a firmware that does not support the endpoint returns unknown/unsupported rather than
fabricating recovered evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import requests
from requests.auth import HTTPBasicAuth

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable, explain
from drivers.hikvision import HikvisionDriver, HIKVISION_HTTP_LOCK

MAX_CLIP_BYTES = 32 * 1024 * 1024
SEARCH_LIMIT = 40
DOWNLOAD_TIMEOUT = (5, 30)
CLIP_TOTAL_SECONDS = 90
MAX_DOWNLOAD_CANDIDATES = 2
ARCHIVE_PROOF_WINDOW = 1800
# HTTP answers that say the endpoint or method itself is absent (an affirmative rejection).
NOT_SUPPORTED_HTTP = (404, 405, 501)


class ArchiveRejected(DriverError):
    """The recorder answered an archive request with a definitive refusal.

    ``affirmative`` marks an answer that the operation is not supported (HTTP 404/405/501 or
    an ISAPI notSupport status). Any other refusal says nothing about capability.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 affirmative: bool = False, reason: str = "rejected") -> None:
        super().__init__(message)
        self.status = status
        self.affirmative = affirmative
        self.reason = reason


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compact(value: datetime) -> str:
    return _utc(value).strftime("%Y%m%dT%H%M%SZ")


def _track(channel: str, kind: str = "stream") -> int:
    try:
        n = int(str(channel))
    except (TypeError, ValueError) as exc:
        raise DriverError("invalid Hikvision channel") from exc
    suffix = {"stream": 1, "sub_stream": 2, "picture": 3}.get(kind, 1)
    return n * 100 + suffix


def _error_head(response) -> str:
    """The start of an error answer, for classification and the local log."""
    try:
        chunk = next(iter(response.iter_content(chunk_size=2048)), b"")
    except Exception:  # noqa: BLE001 — an unreadable error body classifies as a plain refusal
        return ""
    return (chunk or b"")[:2048].decode("utf-8", "replace")


def _not_supported(text: str) -> bool:
    return "notsupport" in (text or "").lower()


def _post(driver: HikvisionDriver, path: str, body: str, *, stream=False, timeout=None):
    url = driver.base_url + path
    with HIKVISION_HTTP_LOCK:
        try:
            response = driver.s.post(
                url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
                stream=stream,
                timeout=timeout or driver.timeout,
            )
        except requests.RequestException as error:
            raise NvrUnreachable(f"{url}: {explain(error)}") from error

        if response.status_code == 401:
            challenge = (response.headers.get("WWW-Authenticate") or "").lower()
            if "basic" in challenge and "digest" not in challenge:
                driver.s.auth = HTTPBasicAuth(driver.username, driver.password)
                response.close()
                try:
                    response = driver.s.post(
                        url,
                        data=body.encode("utf-8"),
                        headers={"Content-Type": "application/xml"},
                        stream=stream,
                        timeout=timeout or driver.timeout,
                    )
                except requests.RequestException as error:
                    raise NvrUnreachable(f"{url}: {explain(error)}") from error
        if response.status_code >= 400:
            status = response.status_code
            detail = _error_head(response)
            response.close()
            # ISAPI answers an unsupported operation with 403 + notSupport; that is not a login fault.
            if status in (401, 403) and not _not_supported(detail):
                raise NvrAuthFailed(
                    f"{url}: HTTP {status} — recorder rejected the username or password"
                )
            message = f"{url}: HTTP {status} {detail[:180]}".strip()
            affirmative = status in NOT_SUPPORTED_HTTP or _not_supported(detail)
            if not affirmative and (status >= 500 or status in (408, 429)):
                raise DriverError(message)      # busy or failing right now: retryable
            raise ArchiveRejected(message, status=status, affirmative=affirmative)
        driver.last_activity_monotonic = __import__("time").monotonic()
        return response


def _strip(root: ET.Element) -> ET.Element:
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def _search_body(channel: str, start: datetime, end: datetime, offset: int, limit: int) -> str:
    # Hikvision recorder firmware is strict about this legacy schema.
    # Many NVRs expect the historical misspelling searchResultPostion and
    # require a video selector plus a recording metadata descriptor before
    # returning playbackURI rows.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CMSearchDescription version="1.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<searchID>{uuid.uuid4()}</searchID>'
        f'<trackIDList><trackID>{_track(channel)}</trackID></trackIDList>'
        '<timeSpanList><timeSpan>'
        f'<startTime>{_iso(start)}</startTime><endTime>{_iso(end)}</endTime>'
        '</timeSpan></timeSpanList>'
        '<contentTypeList><contentType>video</contentType></contentTypeList>'
        f'<maxResults>{min(SEARCH_LIMIT, max(1, int(limit)))}</maxResults>'
        f'<searchResultPostion>{max(0, int(offset))}</searchResultPostion>'
        '<metadataList>'
        '<metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>'
        '</metadataList>'
        '</CMSearchDescription>'
    )

def search_recordings(driver: HikvisionDriver, channel: str, start: datetime, end: datetime,
                      *, offset: int = 0, limit: int = SEARCH_LIMIT) -> dict:
    if end <= start:
        raise DriverError("invalid Hikvision archive time window")
    response = _post(driver, "/ISAPI/ContentMgmt/search",
                     _search_body(channel, start, end, offset, limit), timeout=(8, 30))
    try:
        root = _strip(ET.fromstring(response.content))
    except ET.ParseError as error:
        raise ArchiveRejected(f"Hikvision archive search returned invalid XML: {error}",
                              reason="invalid_response") from error
    finally:
        response.close()

    if root.tag == "ResponseStatus":
        # Some firmware answers HTTP 200 with an ISAPI error status instead of results.
        raise ArchiveRejected(
            "Hikvision archive search refused: "
            + (root.findtext("statusString") or root.findtext("subStatusCode") or "").strip(),
            affirmative=_not_supported(root.findtext("subStatusCode") or ""))

    status = (root.findtext("responseStatusStrg") or root.findtext("responseStatus")
              or "").strip().upper()
    matches = []
    for item in root.findall(".//searchMatchItem"):
        playback = (item.findtext(".//playbackURI") or "").strip()
        st = (item.findtext(".//startTime") or "").strip()
        et = (item.findtext(".//endTime") or "").strip()
        if not (st and et):
            continue
        matches.append({
            "track_id": (item.findtext(".//trackID") or str(_track(channel))).strip(),
            "start": st,
            "end": et,
            "playback_uri": playback,
        })

    more = status == "MORE"
    next_offset = offset + len(matches) if more and matches else None
    return {"status": "supported", "matches": matches, "next_offset": next_offset,
            "response_status": status or ("OK" if matches else "NO MATCHES")}


def _segment_id(channel, row: dict) -> str:
    """Stable dedupe key for one recorded segment. The playbackURI names the recorder's LAN
    address, so only a digest of it ever leaves this module."""
    source = row.get("playback_uri") or f"{channel}:{row['start']}:{row['end']}"
    return "hik:" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def enumerate_historical_events(driver: HikvisionDriver, channel, start, end,
                                cursor=None, limit: int = 500) -> dict:
    """Recorded segments overlapping [start, end), for recovery's footage backfill.

    The rows are recording SEGMENTS (footage windows), not recorder events, so the capability
    reports events as unsupported. A definitive refusal becomes a status ('unsupported' only
    for an affirmative rejection); a transient failure (unreachable, recorder busy) raises so
    the caller keeps the interval retryable.
    """
    offset = int(cursor or 0)
    try:
        page = search_recordings(driver, str(channel), start, end, offset=offset,
                                 limit=min(SEARCH_LIMIT, max(1, int(limit))))
    except NvrAuthFailed:
        return {"status": "unknown", "events": [], "next_cursor": None, "reason": "auth"}
    except ArchiveRejected as error:
        return {"status": "unsupported" if error.affirmative else "unknown", "events": [],
                "next_cursor": None, "reason": error.reason}
    events = []
    for row in page["matches"]:
        events.append({
            "ts": row["start"],
            "type": "recorded_segment",
            "device_event_id": _segment_id(channel, row),
            "channel": str(channel),
            "segment": {
                "start": row["start"],
                "end": row["end"],
            },
        })
    nxt = str(page["next_offset"]) if page.get("next_offset") is not None else None
    return {"status": "supported", "events": events, "next_cursor": nxt}


def _read_download_response(response, *, deadline=None) -> bytes | None:
    try:
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if deadline is not None and time.monotonic() >= deadline:
                raise DriverError("Hikvision incident footage retrieval exceeded the time budget")
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_CLIP_BYTES:
                raise DriverError("Hikvision incident footage exceeds the 32 MiB limit")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            return None
        head = data[:500].lower()
        if (b"<responsestatus" in head or b"<html" in head or
                b"<!doctype" in head or b"<cmsearchresult" in head):
            return None
        return data
    finally:
        response.close()


def _download_uri(driver: HikvisionDriver, playback_uri: str, *, deadline=None) -> bytes | None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<downloadRequest version="1.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<playbackURI>{escape(playback_uri)}</playbackURI>'
        '</downloadRequest>'
    )
    url = driver.base_url + "/ISAPI/ContentMgmt/download"

    # Firmware families differ here: older RaCM documents use GET-with-body
    # while newer NVR examples also accept POST-with-body. Try both, using
    # only the recorder-returned playback URI and keeping the transfer bounded.
    last_error = None
    with HIKVISION_HTTP_LOCK:
        for method in ("GET", "POST"):
            if deadline is not None and time.monotonic() >= deadline:
                return None
            remaining = ((deadline - time.monotonic()) if deadline is not None else
                         float(DOWNLOAD_TIMEOUT[1]))
            timeout = (DOWNLOAD_TIMEOUT[0], max(5, min(DOWNLOAD_TIMEOUT[1], remaining)))
            try:
                response = driver.s.request(
                    method, url, data=body.encode("utf-8"),
                    headers={"Content-Type": "application/xml"},
                    stream=True, timeout=timeout,
                )
            except requests.RequestException as error:
                last_error = error
                continue

            if response.status_code == 401:
                challenge = (response.headers.get("WWW-Authenticate") or "").lower()
                if "basic" in challenge and "digest" not in challenge:
                    driver.s.auth = HTTPBasicAuth(driver.username, driver.password)
                    response.close()
                    try:
                        response = driver.s.request(
                            method, url, data=body.encode("utf-8"),
                            headers={"Content-Type": "application/xml"},
                            stream=True, timeout=timeout,
                        )
                    except requests.RequestException as error:
                        last_error = error
                        continue
            if response.status_code in (401, 403):
                response.close()
                raise NvrAuthFailed(
                    f"{url}: HTTP {response.status_code} — recorder rejected the username or password"
                )
            if response.status_code >= 400:
                response.close()
                continue

            driver.last_activity_monotonic = __import__("time").monotonic()
            data = _read_download_response(response, deadline=deadline)
            if data:
                return data

    if last_error is not None:
        raise NvrUnreachable(f"{url}: {explain(last_error)}") from last_error
    return None

def _by_time_uri(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> str:
    host = urlparse(driver.base_url).hostname or "127.0.0.1"
    return (
        f"rtsp://{host}/Streaming/tracks/{_track(channel)}/"
        f"?starttime={_compact(start)}&endtime={_compact(end)}"
    )


def get_clip(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> bytes | None:
    """Download a bounded incident window.

    Search first and use the recorder-returned playbackURI. That URI often
    carries firmware-specific name/size metadata required by ContentMgmt.
    Only if search yields no usable URI do we try a generic by-time URI.
    """
    if end <= start:
        raise DriverError("invalid Hikvision incident footage time window")

    deadline = time.monotonic() + CLIP_TOTAL_SECONDS
    search_error = None
    try:
        result = search_recordings(
            driver, str(channel), start, end, offset=0, limit=MAX_DOWNLOAD_CANDIDATES)
        attempted = 0
        for row in result.get("matches") or []:
            uri = row.get("playback_uri")
            if not uri or attempted >= MAX_DOWNLOAD_CANDIDATES:
                continue
            attempted += 1
            try:
                data = _download_uri(driver, uri, deadline=deadline)
                if data:
                    return data
            except (DriverError, NvrUnreachable):
                if time.monotonic() >= deadline:
                    break
                continue
    except (DriverError, NvrUnreachable) as error:
        search_error = error

    # Compatibility fallback for firmware that supports download-by-time but
    # returns no usable search row. It shares the SAME total deadline.
    if time.monotonic() < deadline:
        try:
            data = _download_uri(
                driver, _by_time_uri(driver, str(channel), start, end), deadline=deadline)
            if data:
                return data
        except (DriverError, NvrUnreachable):
            pass

    if search_error is not None:
        raise search_error
    return None

def historical_capability(driver: HikvisionDriver = None) -> dict:
    # Recorded segments are searchable; the recorder's own event log is not searched, and a
    # recording segment is not a recorder event.
    return {"events": "unsupported", "snapshots": "unsupported", "segments": "supported"}


def prove_recorder_archive(driver: HikvisionDriver, channel, *, now=None,
                           window_seconds: int = ARCHIVE_PROOF_WINDOW, limit: int = 8) -> dict:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(seconds=max(60, int(window_seconds)))
    proof = {"status": "unknown", "channel": str(channel), "segments_found": 0,
             "sample": [], "detail": ""}
    try:
        result = enumerate_historical_events(driver, channel, start, now, None, limit)
    except Exception:
        proof["detail"] = "The recorder did not answer the Hikvision archive search."
        return proof
    if result.get("status") != "supported":
        if result.get("reason") == "auth":
            proof["detail"] = "The recorder archive rejected the configured login."
        elif result.get("status") == "unsupported":
            proof["status"] = "unsupported"
            proof["detail"] = "The recorder archive search is not supported on this firmware."
        else:
            proof["detail"] = "The recorder answered the Hikvision archive search without a usable result."
        return proof
    rows = result.get("events") or []
    proof["segments_found"] = len(rows)
    proof["sample"] = [(r.get("segment") or {}) for r in rows[:3]]
    if rows:
        proof["status"] = "verified"
        proof["detail"] = f"{len(rows)} recorded segment(s) found through Hikvision ISAPI."
    else:
        proof["status"] = "empty"
        proof["detail"] = "Archive API worked, but no recorded footage was found in the proof window."
    return proof


def install() -> None:
    HikvisionDriver.get_clip = get_clip
    HikvisionDriver.enumerate_historical_events = (
        lambda self, channel, start, end, cursor=None, limit=500:
        enumerate_historical_events(self, channel, start, end, cursor, limit)
    )
    HikvisionDriver.get_recorded_segment = (
        lambda self, channel, start, end:
        {"status": "supported", "bytes": get_clip(self, channel, start, end)}
    )
    HikvisionDriver.historical_capability = lambda self: historical_capability(self)


__all__ = [
    "search_recordings", "enumerate_historical_events", "get_clip",
    "historical_capability", "prove_recorder_archive", "install", "ArchiveRejected",
    "MAX_CLIP_BYTES", "SEARCH_LIMIT", "CLIP_TOTAL_SECONDS", "MAX_DOWNLOAD_CANDIDATES",
]

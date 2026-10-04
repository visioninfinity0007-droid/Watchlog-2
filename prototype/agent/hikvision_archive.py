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
import re
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
CLIP_LOCK_WAIT_SECONDS = 120
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


class ClipError(DriverError):
    """A typed incident-footage outcome.

    The message is customer-safe and never carries the recorder address; ``detail`` is for
    the local log only. Only ClipUnsupported is a capability verdict.
    """

    unsupported = False
    default_reason = "The recorder did not return footage for this window."

    def __init__(self, reason: str | None = None, *, detail: str = "",
                 affirmative: bool = False) -> None:
        super().__init__(reason or self.default_reason)
        self.detail = detail
        self.affirmative = affirmative or self.unsupported


class ClipNotReturned(ClipError):
    """The recorder answered without footage: refused, empty, or an error page."""


class ClipUnsupported(ClipError):
    unsupported = True
    default_reason = ("This recorder does not expose on-demand incident footage through the "
                      "validated WatchLog path.")


class ClipNoRecording(ClipError):
    default_reason = "The recorder found no recorded footage for this window."


class ClipTooLarge(ClipError):
    default_reason = "The footage for this window is larger than the 32 MiB download limit."


class ClipTimedOut(ClipError):
    default_reason = "The recorder did not finish sending the footage in time. Request it again."


class ClipUnreachable(ClipError, NvrUnreachable):
    default_reason = ("The recorder stopped responding while sending the footage. "
                      "Request it again.")


class ClipAuthRejected(ClipError, NvrAuthFailed):
    default_reason = ("The recorder refused the WatchLog login for recorded footage. Check that "
                      "the account is allowed to play back recordings.")


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
        try:
            content = response.content
        except requests.RequestException as error:
            raise NvrUnreachable(
                f"Hikvision archive search answer was cut off: {explain(error)}") from error
        try:
            root = _strip(ET.fromstring(content))
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


def _read_download_response(response, *, deadline=None) -> bytes:
    try:
        chunks = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ClipTimedOut(detail="budget spent during transfer")
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_CLIP_BYTES:
                    raise ClipTooLarge(detail="transfer over the byte cap")
                chunks.append(chunk)
        except (requests.RequestException, OSError) as error:
            # A stall, reset or truncated body mid-transfer. requests names the recorder
            # address in these messages, so only the error type is kept.
            raise ClipUnreachable(detail=f"transfer broke: {type(error).__name__}") from error
        data = b"".join(chunks)
        if not data:
            raise ClipNotReturned(detail="empty body")
        head = data[:500].lower()
        if (b"<responsestatus" in head or b"<html" in head or
                b"<!doctype" in head or b"<cmsearchresult" in head):
            raise ClipNotReturned(detail="error body", affirmative=b"notsupport" in head)
        return data
    finally:
        response.close()


def _download_uri(driver: HikvisionDriver, playback_uri: str, *, deadline=None) -> bytes:
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
    refused = []
    transport = None
    with HIKVISION_HTTP_LOCK:
        for method in ("GET", "POST"):
            if deadline is not None and time.monotonic() >= deadline:
                raise ClipTimedOut(detail="budget spent before download")
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
                transport = ClipUnreachable(detail=f"{method}: {type(error).__name__}")
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
                        transport = ClipUnreachable(detail=f"{method}: {type(error).__name__}")
                        continue
            if response.status_code >= 400:
                status = response.status_code
                detail = _error_head(response)
                response.close()
                if status in (401, 403) and not _not_supported(detail):
                    raise ClipAuthRejected(detail=f"{method}: HTTP {status}")
                refused.append(ClipNotReturned(
                    detail=f"{method}: HTTP {status}",
                    affirmative=status in NOT_SUPPORTED_HTTP or _not_supported(detail)))
                continue

            driver.last_activity_monotonic = __import__("time").monotonic()
            try:
                return _read_download_response(response, deadline=deadline)
            except ClipNotReturned as error:
                refused.append(error)       # empty or error body: the other method may serve it
            # A spent budget, an oversize export or a broken transfer propagates: this method
            # works, and repeating the transfer with the other one would only spend the budget.

    if transport is not None:
        raise transport
    raise ClipNotReturned(detail="; ".join(e.detail for e in refused),
                          affirmative=bool(refused) and all(e.affirmative for e in refused))

def _by_time_uri(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> str:
    host = urlparse(driver.base_url).hostname or "127.0.0.1"
    return (
        f"rtsp://{host}/Streaming/tracks/{_track(channel)}/"
        f"?starttime={_compact(start)}&endtime={_compact(end)}"
    )


_MAIN_TRACK = re.compile(r"(/tracks/\d+?)01(?=[/?]|$)", re.IGNORECASE)


def _sub_stream_uri(uri: str) -> str | None:
    """The same window on the channel's sub stream (track N02), or None if the URI names no
    main-stream track."""
    smaller, count = _MAIN_TRACK.subn(r"\g<1>02", uri, count=1)
    return smaller if count else None


def _clip_outcome(failures: list, *, no_recording: bool) -> ClipError:
    """The most actionable failure. 'Unsupported' needs every answer to be an affirmative
    rejection; anything less certain stays a retryable failure."""
    for kind in (ClipTooLarge, ClipUnreachable):
        for failure in failures:
            if isinstance(failure, kind):
                return failure
    detail = "; ".join(failure.detail for failure in failures if failure.detail)
    if no_recording:
        return ClipNoRecording(detail=detail)
    if failures and all(failure.affirmative for failure in failures):
        return ClipUnsupported(detail=detail)
    return ClipNotReturned(detail=detail)


def get_clip(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> bytes:
    """Download a bounded incident window.

    Search first and use the recorder-returned playbackURI. That URI often
    carries firmware-specific name/size metadata required by ContentMgmt.
    Only if search yields no usable URI do we try a generic by-time URI.

    Every failure raises a typed ClipError. Only an affirmative rejection is 'unsupported'; a
    spent time budget, an oversize export, a refused login, a broken transfer or an empty
    answer stays a retryable failure.
    """
    if end <= start:
        raise DriverError("invalid Hikvision incident footage time window")

    # Recovery, the archive scan and recording proofs share this lock. Waiting for it must not
    # spend this request's download budget, so the deadline starts once the lock is held, and
    # the wait itself is bounded.
    if not HIKVISION_HTTP_LOCK.acquire(timeout=CLIP_LOCK_WAIT_SECONDS):
        raise ClipTimedOut("The recorder was busy with other footage work. Request it again.",
                           detail="archive lock wait")
    try:
        deadline = time.monotonic() + CLIP_TOTAL_SECONDS
        return _clip_within_deadline(driver, str(channel), start, end, deadline)
    finally:
        HIKVISION_HTTP_LOCK.release()


def _clip_within_deadline(driver: HikvisionDriver, channel: str, start: datetime,
                          end: datetime, deadline: float) -> bytes:
    failures = []
    sources = []
    searched = False
    try:
        result = search_recordings(
            driver, channel, start, end, offset=0, limit=MAX_DOWNLOAD_CANDIDATES)
        searched = True
        for row in result.get("matches") or []:
            uri = row.get("playback_uri")
            if uri and len(sources) < MAX_DOWNLOAD_CANDIDATES:
                sources.append(uri)
    except NvrAuthFailed as error:
        raise ClipAuthRejected(detail="search") from error
    except ArchiveRejected as error:
        failures.append(ClipNotReturned(detail=f"search refused ({error.status})",
                                        affirmative=error.affirmative))
    except NvrUnreachable:
        failures.append(ClipUnreachable(detail="search unreachable"))
    except DriverError:
        failures.append(ClipNotReturned(detail="search failed"))
    matched = len(sources)

    # Compatibility fallback for firmware that supports download-by-time but
    # returns no usable search row. It shares the SAME total deadline.
    sources.append(_by_time_uri(driver, channel, start, end))

    smaller_tried = False
    while sources:
        uri = sources.pop(0)
        try:
            return _download_uri(driver, uri, deadline=deadline)
        except (ClipAuthRejected, ClipTimedOut):
            raise       # the same login on every source, or no budget left: stop here
        except ClipTooLarge as error:
            if smaller_tried:
                raise
            failures.append(error)
            smaller = _sub_stream_uri(uri)
            if smaller:
                # One retry of the same window on the lower-bitrate sub stream.
                smaller_tried = True
                sources.insert(0, smaller)
        except ClipError as error:
            failures.append(error)

    raise _clip_outcome(failures, no_recording=searched and not matched)


def get_recorded_segment(driver: HikvisionDriver, channel, start, end) -> dict:
    """Recovery's bounded clip, with a typed status instead of 'supported' without bytes."""
    try:
        return {"status": "supported", "bytes": get_clip(driver, channel, start, end)}
    except ClipError as error:
        return {"status": "unsupported" if error.unsupported else "unknown", "bytes": None,
                "reason": str(error)}


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
        lambda self, channel, start, end: get_recorded_segment(self, channel, start, end)
    )
    HikvisionDriver.historical_capability = lambda self: historical_capability(self)


__all__ = [
    "search_recordings", "enumerate_historical_events", "get_clip", "get_recorded_segment",
    "historical_capability", "prove_recorder_archive", "install",
    "ArchiveRejected", "ClipError", "ClipNotReturned", "ClipUnsupported", "ClipNoRecording",
    "ClipTooLarge", "ClipTimedOut", "ClipUnreachable", "ClipAuthRejected",
    "MAX_CLIP_BYTES", "SEARCH_LIMIT", "CLIP_TOTAL_SECONDS", "MAX_DOWNLOAD_CANDIDATES",
]

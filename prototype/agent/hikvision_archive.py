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
import os
import re
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import requests
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import HTTPError as Urllib3Error

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable, explain
from drivers.hikvision import HikvisionDriver, recorder_http_lock

MAX_CLIP_BYTES = 32 * 1024 * 1024
SEARCH_LIMIT = 40
DOWNLOAD_TIMEOUT = (5, 30)
READ_SIZE = 64 * 1024                # most bytes one download read asks for
CLIP_TOTAL_SECONDS = 90
CLIP_LOCK_WAIT_SECONDS = 120
MAX_DOWNLOAD_CANDIDATES = 2
ARCHIVE_PROOF_WINDOW = 1800
# A bounded export may start on an earlier key frame. Beyond this slack a clip is a whole
# recording segment, not the requested window.
CLIP_WINDOW_SLACK_SECONDS = 15
PROBE_TIMEOUT_SECONDS = 15
# No real video fits 32 MiB over this long; a longer reported duration is a timestamp jump
# (e.g. a PTS wrap), so the clip's timing stays unknown instead of being rejected.
PROBE_MAX_PLAUSIBLE_SECONDS = 4 * 3600
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


class ClipInvalid(ClipError):
    default_reason = "The recorder returned data that is not playable footage for this window."


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


def _parse(value: str) -> datetime | None:
    """A search row's time in UTC, or None when it is unreadable or names no zone. A zone-less
    row time is recorder-local with an unknown offset, so it is never assumed to be UTC."""
    try:
        value = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return value.astimezone(timezone.utc) if value.tzinfo is not None else None


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


def _http_lock(driver: HikvisionDriver):
    """This recorder's archive lock: shared by every transport to it, never by another recorder."""
    return recorder_http_lock(driver.base_url)


def _post(driver: HikvisionDriver, path: str, body: str, *, stream=False, timeout=None):
    url = driver.base_url + path
    with _http_lock(driver):
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
    incomplete = 0          # rows without a time span: skipped, but not proof of absence
    for item in root.findall(".//searchMatchItem"):
        playback = (item.findtext(".//playbackURI") or "").strip()
        st = (item.findtext(".//startTime") or "").strip()
        et = (item.findtext(".//endTime") or "").strip()
        if not (st and et):
            incomplete += 1
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
            "incomplete": incomplete,
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


def _body_reads(response):
    """Yield a streamed body one read at a time, each read returning whatever has arrived.

    requests' iter_content blocks until a whole chunk has arrived, so a slow stream would reach
    the deadline check only once per chunk. urllib3's read1 returns after at most one socket read,
    and a socket read waits at most the read timeout. A urllib3 without read1 gets READ_SIZE chunks.
    """
    read1 = getattr(getattr(response, "raw", None), "read1", None)
    if not callable(read1):
        yield from response.iter_content(chunk_size=READ_SIZE)
        return
    while True:
        chunk = read1(READ_SIZE, decode_content=True)
        if not chunk:
            return
        yield chunk


def _read_download_response(response, *, deadline=None) -> bytes:
    """Read a streamed download within the clip's total deadline, checked after every read: a
    download that keeps trickling data never outlives the budget by more than one read timeout."""
    try:
        chunks = []
        total = 0
        try:
            for chunk in _body_reads(response):
                if deadline is not None and time.monotonic() >= deadline:
                    raise ClipTimedOut(detail="budget spent during transfer")
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_CLIP_BYTES:
                    raise ClipTooLarge(detail="transfer over the byte cap")
                chunks.append(chunk)
        except (requests.RequestException, OSError, Urllib3Error) as error:
            # A stall, reset or truncated body mid-transfer. requests and urllib3 name the
            # recorder address in these messages, so only the error type is kept. read1 reads
            # below requests, so urllib3's errors arrive unwrapped.
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
    with _http_lock(driver):
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


_TIME_PARAM = re.compile(r"([?&](starttime|endtime)=)([^&]*)", re.IGNORECASE)
_MAIN_TRACK = re.compile(r"(/tracks/\d+?)01(?=[/?]|$)", re.IGNORECASE)


def _window_uri(playback_uri: str, start: datetime, end: datetime) -> str:
    """Bound a recorder playbackURI to the requested window.

    Search rows describe whole recording segments, so the URI's times cover the segment.
    Keep the recorder's own locator metadata (name, size) and replace only starttime/endtime,
    in the URI's own notation, adding them when the URI has none.
    """
    wanted = {"starttime": start, "endtime": end}
    found = set()

    def bounded(match):
        key = match.group(2).lower()
        found.add(key)
        fmt = _iso if "-" in match.group(3) else _compact
        return match.group(1) + fmt(wanted[key])

    uri = _TIME_PARAM.sub(bounded, playback_uri)
    missing = [key for key in ("starttime", "endtime") if key not in found]
    if missing:
        uri += ("&" if "?" in uri else "?") + "&".join(
            f"{key}={_compact(wanted[key])}" for key in missing)
    return uri


def _sub_stream_uri(uri: str) -> str | None:
    """The same window on the channel's sub stream (track N02), or None if the URI names no
    main-stream track."""
    smaller, count = _MAIN_TRACK.subn(r"\g<1>02", uri, count=1)
    return smaller if count else None


def _overlaps(row: dict, start: datetime, end: datetime) -> bool | None:
    """Whether a search row's span overlaps the window; None when the span is unreadable or
    zone-less and so cannot be judged either way."""
    row_start, row_end = _parse(row.get("start")), _parse(row.get("end"))
    if row_start is None or row_end is None:
        return None
    return row_start < _utc(end) and row_end > _utc(start)


def _probe_clip(data: bytes) -> dict | None:
    """Inspect a downloaded clip with the bundled FFmpeg (it ships without ffprobe).

    Returns {'video': bool, 'duration': seconds or None}, or None when the bundled tool is
    unavailable or could not run; the clip is then neither confirmed nor rejected.
    """
    try:
        from recovery_ai import _ffmpeg_exe
        exe = _ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None
    if not exe:
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        # With no output file FFmpeg describes the input and exits non-zero; that is expected.
        done = subprocess.run(
            [exe, "-hide_banner", "-nostdin", "-i", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=PROBE_TIMEOUT_SECONDS, check=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
    except Exception:  # noqa: BLE001
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    text = done.stderr.decode("utf-8", "replace")
    duration = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    return {
        "video": bool(re.search(r"Stream #\d+:\d+\S*: Video:", text)),
        "duration": (int(duration.group(1)) * 3600 + int(duration.group(2)) * 60
                     + float(duration.group(3))) if duration else None,
    }


def _check_clip(data: bytes, start: datetime, end: datetime) -> bytes:
    """Where the bundled FFmpeg allows, confirm the bytes are video for this window."""
    probe = _probe_clip(data)
    if probe is None:
        return data
    if not probe.get("video"):
        raise ClipInvalid(detail="no video stream")
    window = (_utc(end) - _utc(start)).total_seconds()
    duration = probe.get("duration")
    if duration is not None and duration > PROBE_MAX_PLAUSIBLE_SECONDS:
        duration = None
    if duration is not None and duration > window + max(CLIP_WINDOW_SLACK_SECONDS, window / 4):
        # The recorder exported a whole recording segment, not the requested window.
        raise ClipInvalid("The recorder returned footage that does not match the requested window.",
                          detail=f"{duration:.0f}s clip for a {window:.0f}s window")
    return data


def _clip_outcome(failures: list, *, no_recording: bool) -> ClipError:
    """The most actionable failure. 'Unsupported' needs every answer to be an affirmative
    rejection; anything less certain stays a retryable failure."""
    for kind in (ClipTooLarge, ClipInvalid, ClipUnreachable):
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

    Search first and use the recorder-returned playbackURI, bounded to the requested window.
    That URI often carries firmware-specific name/size metadata required by ContentMgmt.
    Rows whose readable span lies outside the window are ignored; a row whose span cannot be
    judged is still tried. Only if no row yields footage do we try a generic by-time URI. A clip
    is returned once the bundled FFmpeg, where available, confirms it is video of about the
    window's length.

    Every failure raises a typed ClipError. Only an affirmative rejection is 'unsupported'; a
    spent time budget, an oversize export, a refused login, a broken transfer or an empty
    answer stays a retryable failure. 'No recording' needs a complete search answer whose rows
    all lie readably outside the window.
    """
    if end <= start:
        raise DriverError("invalid Hikvision incident footage time window")

    # Recovery, the archive scan and recording proofs on THIS recorder share its lock; other
    # recorders have their own (MNVR-025). Waiting for it must not spend this request's
    # download budget, so the deadline starts once the lock is held, and the wait itself is
    # bounded.
    lock = _http_lock(driver)
    if not lock.acquire(timeout=CLIP_LOCK_WAIT_SECONDS):
        raise ClipTimedOut("The recorder was busy with other footage work. Request it again.",
                           detail="archive lock wait")
    try:
        deadline = time.monotonic() + CLIP_TOTAL_SECONDS
        return _clip_within_deadline(driver, str(channel), start, end, deadline)
    finally:
        lock.release()


def _clip_within_deadline(driver: HikvisionDriver, channel: str, start: datetime,
                          end: datetime, deadline: float) -> bytes:
    failures = []
    sources = []
    absent = False
    try:
        result = search_recordings(
            driver, channel, start, end, offset=0, limit=MAX_DOWNLOAD_CANDIDATES)
        # The search shows no recording only when the answer is complete (no rows without a
        # span, no further page) and every row lies readably outside the window. A row that
        # cannot be judged, or one without a playbackURI, leaves the absence unknown.
        absent = not result.get("incomplete") and result.get("next_offset") is None
        for row in result.get("matches") or []:
            if _overlaps(row, start, end) is False:
                continue
            absent = False
            uri = row.get("playback_uri")
            if uri and len(sources) < MAX_DOWNLOAD_CANDIDATES:
                sources.append(_window_uri(uri, start, end))
    except NvrAuthFailed as error:
        raise ClipAuthRejected(detail="search") from error
    except ArchiveRejected as error:
        failures.append(ClipNotReturned(detail=f"search refused ({error.status})",
                                        affirmative=error.affirmative))
    except NvrUnreachable:
        failures.append(ClipUnreachable(detail="search unreachable"))
    except DriverError:
        failures.append(ClipNotReturned(detail="search failed"))

    # Compatibility fallback for firmware that supports download-by-time but
    # returns no usable search row. It shares the SAME total deadline.
    sources.append(_by_time_uri(driver, channel, start, end))

    smaller_tried = False
    while sources:
        uri = sources.pop(0)
        try:
            return _check_clip(_download_uri(driver, uri, deadline=deadline), start, end)
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

    raise _clip_outcome(failures, no_recording=absent)


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
    "ClipTooLarge", "ClipTimedOut", "ClipInvalid", "ClipUnreachable", "ClipAuthRejected",
    "MAX_CLIP_BYTES", "SEARCH_LIMIT", "CLIP_TOTAL_SECONDS", "MAX_DOWNLOAD_CANDIDATES",
]

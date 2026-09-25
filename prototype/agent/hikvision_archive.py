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
DOWNLOAD_TIMEOUT = (8, 90)
ARCHIVE_PROOF_WINDOW = 1800


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
        if response.status_code in (401, 403):
            response.close()
            raise NvrAuthFailed(
                f"{url}: HTTP {response.status_code} — recorder rejected the username or password"
            )
        if response.status_code >= 400:
            try:
                detail = response.text[:180]
            except Exception:
                detail = ""
            response.close()
            raise DriverError(f"{url}: HTTP {response.status_code} {detail}".strip())
        driver.last_activity_monotonic = __import__("time").monotonic()
        return response


def _strip(root: ET.Element) -> ET.Element:
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def _search_body(channel: str, start: datetime, end: datetime, offset: int, limit: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<CMSearchDescription version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<searchID>{uuid.uuid4()}</searchID>'
        f'<trackIDList><trackID>{_track(channel)}</trackID></trackIDList>'
        '<timeSpanList><timeSpan>'
        f'<startTime>{_iso(start)}</startTime><endTime>{_iso(end)}</endTime>'
        '</timeSpan></timeSpanList>'
        f'<searchResultPosition>{max(0, int(offset))}</searchResultPosition>'
        f'<maxResults>{min(SEARCH_LIMIT, max(1, int(limit)))}</maxResults>'
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
        raise DriverError(f"Hikvision archive search returned invalid XML: {error}") from error
    finally:
        response.close()

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


def enumerate_historical_events(driver: HikvisionDriver, channel, start, end,
                                cursor=None, limit: int = 500) -> dict:
    offset = int(cursor or 0)
    page = search_recordings(driver, str(channel), start, end, offset=offset,
                             limit=min(SEARCH_LIMIT, max(1, int(limit))))
    events = []
    for row in page["matches"]:
        events.append({
            "ts": row["start"],
            "type": "recorded_segment",
            "device_event_id": (
                row["playback_uri"] or
                f"hik:{channel}:{row['start']}:{row['end']}"
            ),
            "channel": str(channel),
            "segment": {
                "start": row["start"],
                "end": row["end"],
                "playback_uri": row["playback_uri"],
            },
        })
    nxt = str(page["next_offset"]) if page.get("next_offset") is not None else None
    return {"status": "supported", "events": events, "next_cursor": nxt}


def _download_uri(driver: HikvisionDriver, playback_uri: str) -> bytes | None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<downloadRequest version="1.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f'<playbackURI>{escape(playback_uri)}</playbackURI>'
        '</downloadRequest>'
    )
    # Hold the cross-driver lock through the WHOLE transfer. _post itself uses
    # the same RLock, so this is re-entrant in this thread but prevents the live
    # alert collector from opening a second Hikvision session mid-download.
    with HIKVISION_HTTP_LOCK:
        response = _post(driver, "/ISAPI/ContentMgmt/download", body,
                         stream=True, timeout=DOWNLOAD_TIMEOUT)
        try:
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_CLIP_BYTES:
                    raise DriverError("Hikvision incident footage exceeds the 32 MiB limit")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                return None
            head = data[:300].lower()
            if b"<responsestatus" in head or b"<html" in head or b"<!doctype" in head:
                return None
            return data
        finally:
            response.close()


def _by_time_uri(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> str:
    host = urlparse(driver.base_url).hostname or "127.0.0.1"
    return (
        f"rtsp://{host}/Streaming/tracks/{_track(channel)}/"
        f"?starttime={_compact(start)}&endtime={_compact(end)}"
    )


def get_clip(driver: HikvisionDriver, channel: str, start: datetime, end: datetime) -> bytes | None:
    """Download a bounded incident window.

    Prefer Hikvision's documented download-by-time playback URI. If firmware requires
    a search-returned URI (common older NVRs), fall back to the first overlapping match.
    """
    if end <= start:
        raise DriverError("invalid Hikvision incident footage time window")
    try:
        data = _download_uri(driver, _by_time_uri(driver, str(channel), start, end))
        if data:
            return data
    except DriverError:
        # Search-returned URI is the compatibility fallback below.
        pass

    result = search_recordings(driver, str(channel), start, end, offset=0, limit=8)
    for row in result.get("matches") or []:
        uri = row.get("playback_uri")
        if not uri:
            continue
        try:
            data = _download_uri(driver, uri)
            if data:
                return data
        except DriverError:
            continue
    return None


def historical_capability(driver: HikvisionDriver = None) -> dict:
    return {"events": "supported", "snapshots": "unsupported", "segments": "supported"}


def prove_recorder_archive(driver: HikvisionDriver, channel, *, now=None,
                           window_seconds: int = ARCHIVE_PROOF_WINDOW, limit: int = 8) -> dict:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(seconds=max(60, int(window_seconds)))
    proof = {"status": "unknown", "channel": str(channel), "segments_found": 0,
             "sample": [], "detail": ""}
    try:
        result = enumerate_historical_events(driver, channel, start, now, None, limit)
    except NvrAuthFailed:
        proof["detail"] = "The recorder archive rejected the configured login."
        return proof
    except Exception:
        proof["detail"] = "The recorder did not answer the Hikvision archive search."
        return proof
    if result.get("status") != "supported":
        proof["detail"] = "The recorder archive search is not supported on this firmware."
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
    "historical_capability", "prove_recorder_archive", "install",
    "MAX_CLIP_BYTES", "SEARCH_LIMIT",
]

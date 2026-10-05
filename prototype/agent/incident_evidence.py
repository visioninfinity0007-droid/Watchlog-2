"""On-demand incident-footage worker for the packaged WatchLog agent.

The worker polls WatchLog outbound, claims at most one request, retrieves a
bounded recorder-native clip locally, and uploads it in small authenticated RPC
chunks. It is intentionally idle when migration 0040 is not deployed.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import os
import re
import subprocess
import tempfile
import threading
from urllib.parse import urlparse

import requests

import recovery_ai
import watchlog_agent as core
from drivers import DriverError, NvrDriver

POLL_SECONDS = 15
BACKEND_MISSING_RETRY_SECONDS = 300
CHUNK_BYTES = 512 * 1024
MAX_CLIP_BYTES = 32 * 1024 * 1024
STILL_POLL_SECONDS = 15
STILL_MAX_BYTES = 3 * 1024 * 1024      # matches the 0058 bounded still limit
REMUX_TIMEOUT_SECONDS = 60
UNSUPPORTED_FOOTAGE = ("This recorder does not expose on-demand incident footage through the "
                       "validated WatchLog path.")
# Customer-visible still outcomes. Driver and library text (endpoints, HTTP codes, exception
# names) is not customer language, so a failed still is always one of these; the detail goes
# to the agent log.
STILL_FAILED = "Recorder could not provide a still for this camera."
UNSUPPORTED_STILL = ("This recorder does not provide incident stills through the validated "
                     "WatchLog path.")
# open_archive_driver keeps the ONVIF driver for these recorders only when the vendor-native
# archive attempt failed (timeout, refused login), so their missing footage is not a verdict.
NATIVE_ARCHIVE_VENDORS = ("dahua", "hikvision")
# URLs, LAN addresses and requests' connection-pool text all name the recorder.
_RECORDER_ADDRESS = re.compile(
    r"[a-z][a-z0-9+.-]*://|\bhost=|connectionpool|\b\d{1,3}(?:\.\d{1,3}){3}\b"
    r"|\[[0-9a-f]*:[0-9a-f:]*\]",
    re.IGNORECASE,
)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _backend_missing(error: Exception) -> bool:
    text = str(error).lower()
    return "wl_agent_claim_clip_requests" in text and (
        "schema cache" in text or "function" in text or "404" in text
    )


def _recorder_hosts(cfg) -> tuple:
    try:
        host = urlparse(str(getattr(cfg, "nvr_url", "") or "")).hostname
    except ValueError:
        host = None
    return (host,) if host else ()


def _safe_reason(error: Exception, hosts=(),
                 redacted: str = "Recorder could not export the requested footage window.") -> str:
    lines = str(error).splitlines()
    first = lines[0] if lines else ""
    if isinstance(error, DriverError):
        text = first[:220]
    else:
        text = f"{type(error).__name__}: {first[:180]}" if first else ""
    # Do not leak local recorder URLs, host names or LAN addresses in cloud-visible error text.
    low = text.lower()
    if _RECORDER_ADDRESS.search(text) or any(host.lower() in low for host in hosts if host):
        return redacted
    return text or "Recorder could not provide this footage."


def _is_unsupported(error: Exception) -> bool:
    # A capability verdict needs an affirmative rejection; any other failure stays retryable.
    return isinstance(error, NotImplementedError) or getattr(error, "unsupported", False) is True


def _implements(driver, method: str) -> bool:
    """True when the driver class provides ``method`` beyond the NvrDriver stub."""
    impl = getattr(type(driver), method, None)
    return impl is not None and impl is not getattr(NvrDriver, method, None)


def _no_footage_outcome(driver, info) -> tuple[bool, str]:
    """(p_unsupported, reason) when the archive driver returned no footage and raised nothing."""
    if _implements(driver, "get_clip"):
        return False, "The recorder returned no footage for the requested window."
    vendor = str(getattr(info, "vendor", "") or "").lower()
    if any(name in vendor for name in NATIVE_ARCHIVE_VENDORS):
        return False, "The recorder's footage service could not be opened. Request it again."
    return True, UNSUPPORTED_FOOTAGE


def _remux_mp4(data: bytes) -> bytes | None:
    """Stream-copy a clip into MP4 with the bundled FFmpeg; None if that is not possible."""
    exe = recovery_ai._ffmpeg_exe()
    if not exe:
        return None
    src = out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
            tmp.write(data)
            src = tmp.name
        out = src + ".mp4"
        # Video is copied unchanged. Audio is copied too, or re-encoded to AAC when MP4
        # cannot carry the recorder's audio codec.
        for audio in (["-c:a", "copy"], ["-c:a", "aac"]):
            done = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src,
                 "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", *audio,
                 "-movflags", "+faststart", "-f", "mp4", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=REMUX_TIMEOUT_SECONDS, check=False,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            )
            if done.returncode == 0 and os.path.exists(out):
                with open(out, "rb") as handle:
                    return handle.read() or None
        return None
    except Exception:  # noqa: BLE001 — the caller reports a clip it cannot label
        return None
    finally:
        for path in (src, out):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _playable_clip(data: bytes, driver_name: str) -> tuple[bytes, str, str]:
    """(bytes, content type, extension), labelled by the clip's container, not the driver.

    The cloud accepts only MP4 and Dahua DAV (migration 0040). Any other container, such as
    a Hikvision program-stream export, is stream-copied into MP4 without re-encoding video.
    """
    kind = recovery_ai.media_kind(data[:16])
    if kind == "mp4":
        return data, "video/mp4", "mp4"
    if kind == "dav":
        return data, "video/x-dav", "dav"
    remuxed = _remux_mp4(data)
    if remuxed and recovery_ai.media_kind(remuxed[:16]) == "mp4":
        return remuxed, "video/mp4", "mp4"
    if driver_name == "dahua-cgi":
        # loadfile.cgi is Dahua's DAV export; an unrecognised header keeps that documented label.
        return data, "video/x-dav", "dav"
    raise DriverError("The recorder's footage could not be prepared as a playable video file.")


def _upload(cloud, state, request_id: str, data: bytes, driver_name: str) -> None:
    if not data:
        raise DriverError("recorder returned no incident footage")
    data, content_type, extension = _playable_clip(data, driver_name)
    if len(data) > MAX_CLIP_BYTES:
        raise DriverError("incident footage exceeds the 32 MiB pilot limit")
    digest = hashlib.sha256(data).hexdigest()
    for seq, offset in enumerate(range(0, len(data), CHUNK_BYTES)):
        chunk = data[offset:offset + CHUNK_BYTES]
        cloud.call(
            "wl_agent_upload_clip_chunk",
            p_agent_id=state["agent_id"],
            p_agent_key=state["agent_key"],
            p_request_id=request_id,
            p_sequence_no=seq,
            p_data_b64=base64.b64encode(chunk).decode("ascii"),
        )
    cloud.call(
        "wl_agent_complete_clip",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_request_id=request_id,
        p_content_type=content_type,
        p_file_extension=extension,
        p_sha256=digest,
        p_total_bytes=len(data),
    )


def footage_worker(cfg, state: dict, stop: threading.Event) -> None:
    cloud = core.Cloud(cfg.supabase_url, cfg.publishable_key)
    hosts = _recorder_hosts(cfg)
    missing_backend_logged = False
    while not stop.is_set():
        try:
            requests_list = cloud.call(
                "wl_agent_claim_clip_requests",
                p_agent_id=state["agent_id"],
                p_agent_key=state["agent_key"],
                p_limit=1,
            ) or []
            missing_backend_logged = False
        except (RuntimeError, requests.RequestException) as error:
            if _backend_missing(error):
                if not missing_backend_logged:
                    core.log("incident footage: backend 0040 not deployed; worker idle")
                    missing_backend_logged = True
                stop.wait(BACKEND_MISSING_RETRY_SECONDS)
            else:
                core.log("incident footage: request poll failed; will retry: "
                         + str(error).splitlines()[0][:160])
                stop.wait(POLL_SECONDS)
            continue

        if not requests_list:
            stop.wait(POLL_SECONDS)
            continue

        for row in requests_list:
            request_id = str(row.get("request_id") or "")
            channel = str(row.get("channel") or "")
            if not request_id or not channel:
                continue
            driver = None
            try:
                # Footage/archive APIs are vendor-specific. A site's proven live path may
                # still be ONVIF; recorded media gets one bounded native-vendor attempt without
                # changing the live monitoring driver or exporting credentials.
                driver, info = core.open_archive_driver(cfg)
                start = _parse_time(row["start_at"])
                end = _parse_time(row["end_at"])
                core.log(
                    f"incident footage: retrieving {int((end-start).total_seconds())}s "
                    f"from ch{channel} via {driver.name}"
                )
                data = driver.get_clip(channel, start, end)
                if not data:
                    unsupported, reason = _no_footage_outcome(driver, info)
                    cloud.call(
                        "wl_agent_fail_clip",
                        p_agent_id=state["agent_id"],
                        p_agent_key=state["agent_key"],
                        p_request_id=request_id,
                        p_reason=reason,
                        p_unsupported=unsupported,
                    )
                    core.log(
                        f"incident footage: {info.vendor} {info.model or ''} returned no footage via "
                        f"{driver.name} ({'unsupported' if unsupported else 'failed'})"
                    )
                    continue
                _upload(cloud, state, request_id, data, driver.name)
                core.log(
                    f"incident footage: uploaded {len(data) // 1024} KB for request {request_id[:8]}"
                )
            except Exception as error:  # noqa: BLE001
                reason = _safe_reason(error, hosts)
                try:
                    cloud.call(
                        "wl_agent_fail_clip",
                        p_agent_id=state["agent_id"],
                        p_agent_key=state["agent_key"],
                        p_request_id=request_id,
                        p_reason=reason,
                        p_unsupported=_is_unsupported(error),
                    )
                except Exception:
                    pass
                detail = getattr(error, "detail", "")
                core.log(
                    f"incident footage: request {request_id[:8]} failed: "
                    f"{type(error).__name__}: {str(error)[:140]}" + (f" [{detail}]" if detail else "")
                )
            finally:
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:
                        pass


def _still_backend_missing(error: Exception) -> bool:
    text = str(error).lower()
    return "wl_agent_claim_incident_stills" in text and (
        "schema cache" in text or "function" in text or "404" in text
    )


def stills_worker(cfg, state: dict, stop: threading.Event) -> None:
    """Fulfil SERVER-AUTHORIZED incident still-evidence tasks (migration 0058).

    The server decides which incident/camera/timestamp needs a still and creates the task; this
    worker only CLAIMS authorized work for its own site, captures ONE bounded JPEG from the local
    recorder and uploads it with a sha256 for integrity. It never chooses the tenant/site/camera —
    those come from the claimed task. Idle when 0058 is not deployed; truthful unsupported/failure.
    """
    cloud = core.Cloud(cfg.supabase_url, cfg.publishable_key)
    missing_backend_logged = False
    while not stop.is_set():
        try:
            requests_list = cloud.call(
                "wl_agent_claim_incident_stills",
                p_agent_id=state["agent_id"], p_agent_key=state["agent_key"], p_limit=2,
            ) or []
            missing_backend_logged = False
        except (RuntimeError, requests.RequestException) as error:
            if _still_backend_missing(error):
                if not missing_backend_logged:
                    core.log("incident stills: backend 0058 not deployed; worker idle")
                    missing_backend_logged = True
                stop.wait(BACKEND_MISSING_RETRY_SECONDS)
            else:
                core.log("incident stills: claim failed; will retry: "
                         + str(error).splitlines()[0][:160])
                stop.wait(STILL_POLL_SECONDS)
            continue

        if not requests_list:
            stop.wait(STILL_POLL_SECONDS)
            continue

        for row in requests_list:
            request_id = str(row.get("request_id") or "")
            channel = str(row.get("channel") or "")
            if not request_id or not channel:
                continue
            driver = None
            try:
                driver, info = core.open_driver(cfg)
                raw = driver.get_snapshot(channel)
                if not raw:
                    # Only a transport with no still path at all is a capability verdict. An empty
                    # answer from a still-capable driver (a timeout it swallowed, or an ONVIF
                    # channel it could not resolve) stays retryable under 0058's attempt budget.
                    unsupported = not _implements(driver, "get_snapshot")
                    cloud.call("wl_agent_fail_incident_still",
                               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                               p_request_id=request_id,
                               p_reason=(UNSUPPORTED_STILL if unsupported else
                                         "This recorder returned no still for the incident window."),
                               p_unsupported=unsupported)
                    core.log(f"incident stills: {info.vendor} ch{channel} returned no image via "
                             f"{driver.name} ({'unsupported' if unsupported else 'will retry'})")
                    continue
                if len(raw) > STILL_MAX_BYTES:
                    cloud.call("wl_agent_fail_incident_still",
                               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                               p_request_id=request_id,
                               p_reason="Still exceeded the 3 MiB evidence limit.", p_unsupported=False)
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                cloud.call("wl_agent_upload_incident_still",
                           p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                           p_request_id=request_id,
                           p_image_b64=base64.b64encode(raw).decode("ascii"),
                           p_content_type="image/jpeg", p_sha256=digest,
                           p_captured_at=core.iso(core.now_utc()))
                core.log(f"incident stills: uploaded {len(raw) // 1024} KB for request {request_id[:8]}")
            except Exception as error:  # noqa: BLE001
                unsupported = _is_unsupported(error)
                try:
                    cloud.call("wl_agent_fail_incident_still",
                               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                               p_request_id=request_id,
                               p_reason=UNSUPPORTED_STILL if unsupported else STILL_FAILED,
                               p_unsupported=unsupported)
                except Exception:  # noqa: BLE001
                    pass
                core.log(f"incident stills: request {request_id[:8]} failed: "
                         f"{type(error).__name__}: {str(error)[:140]}")
            finally:
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:  # noqa: BLE001
                        pass


def wrap_cmd_run(original):
    """Start the incident evidence workers (footage + stills) beside the normal runtime."""
    def wrapped(cfg, state, cloud, once, device=None, channels=None):
        if once:
            return original(cfg, state, cloud, once, device, channels)
        stop = threading.Event()
        workers = [
            threading.Thread(target=footage_worker, args=(cfg, state, stop),
                             daemon=True, name="incident-footage"),
            threading.Thread(target=stills_worker, args=(cfg, state, stop),
                             daemon=True, name="incident-stills"),
        ]
        for worker in workers:
            worker.start()
        try:
            return original(cfg, state, cloud, once, device, channels)
        finally:
            stop.set()
            for worker in workers:
                worker.join(timeout=5)
    return wrapped

"""On-demand incident-footage worker for the packaged WatchLog agent.

The worker polls WatchLog outbound, claims at most one request, retrieves a
bounded recorder-native clip locally, and uploads it in small authenticated RPC
chunks. It is intentionally idle when migration 0040 is not deployed.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import threading

import requests

import watchlog_agent as core
from drivers import DriverError

POLL_SECONDS = 15
BACKEND_MISSING_RETRY_SECONDS = 300
CHUNK_BYTES = 512 * 1024
MAX_CLIP_BYTES = 32 * 1024 * 1024
STILL_POLL_SECONDS = 15
STILL_MAX_BYTES = 3 * 1024 * 1024      # matches the 0058 bounded still limit


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _backend_missing(error: Exception) -> bool:
    text = str(error).lower()
    return "wl_agent_claim_clip_requests" in text and (
        "schema cache" in text or "function" in text or "404" in text
    )


def _safe_reason(error: Exception) -> str:
    if isinstance(error, DriverError):
        text = str(error).splitlines()[0][:220]
    else:
        text = f"{type(error).__name__}: {str(error).splitlines()[0][:180]}"
    # Do not leak local recorder URLs in cloud-visible error text.
    if "http://" in text or "https://" in text:
        return "Recorder could not export the requested footage window."
    return text or "Recorder could not provide this footage."


def _upload(cloud, state, request_id: str, data: bytes, driver_name: str) -> None:
    if not data:
        raise DriverError("recorder returned no incident footage")
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
    if driver_name == "dahua-cgi":
        content_type, extension = "video/x-dav", "dav"
    else:
        content_type, extension = "application/octet-stream", "dav"
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
                driver, info = core.open_driver(cfg)
                start = _parse_time(row["start_at"])
                end = _parse_time(row["end_at"])
                core.log(
                    f"incident footage: retrieving {int((end-start).total_seconds())}s "
                    f"from ch{channel} via {driver.name}"
                )
                data = driver.get_clip(channel, start, end)
                if not data:
                    cloud.call(
                        "wl_agent_fail_clip",
                        p_agent_id=state["agent_id"],
                        p_agent_key=state["agent_key"],
                        p_request_id=request_id,
                        p_reason="This recorder does not expose on-demand incident footage through the validated WatchLog path.",
                        p_unsupported=True,
                    )
                    core.log(
                        f"incident footage: {info.vendor} {info.model or ''} returned unsupported/no footage"
                    )
                    continue
                _upload(cloud, state, request_id, data, driver.name)
                core.log(
                    f"incident footage: uploaded {len(data) // 1024} KB for request {request_id[:8]}"
                )
            except Exception as error:  # noqa: BLE001
                reason = _safe_reason(error)
                try:
                    cloud.call(
                        "wl_agent_fail_clip",
                        p_agent_id=state["agent_id"],
                        p_agent_key=state["agent_key"],
                        p_request_id=request_id,
                        p_reason=reason,
                        p_unsupported=isinstance(error, NotImplementedError),
                    )
                except Exception:
                    pass
                core.log(
                    f"incident footage: request {request_id[:8]} failed: "
                    f"{type(error).__name__}: {str(error)[:140]}"
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
                    cloud.call("wl_agent_fail_incident_still",
                               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                               p_request_id=request_id,
                               p_reason="This recorder returned no still for the incident window.",
                               p_unsupported=True)
                    core.log(f"incident stills: {info.vendor} ch{channel} returned no image")
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
                reason = _safe_reason(error)
                try:
                    cloud.call("wl_agent_fail_incident_still",
                               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                               p_request_id=request_id, p_reason=reason,
                               p_unsupported=isinstance(error, NotImplementedError))
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
    def wrapped(cfg, state, cloud, once, device=None):
        if once:
            return original(cfg, state, cloud, once, device)
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
            return original(cfg, state, cloud, once, device)
        finally:
            stop.set()
            for worker in workers:
                worker.join(timeout=5)
    return wrapped

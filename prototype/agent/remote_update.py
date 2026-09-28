#!/usr/bin/env python3
"""Secure remote-maintenance/update worker for the packaged WatchLog agent.

The site connector remains outbound-only. The cloud can request "apply the latest
signed production release"; it cannot provide an arbitrary executable or shell
command. The agent independently fetches its configured HTTPS release manifest,
verifies the Ed25519 signature, downloads the manifest-selected package, verifies
SHA-256 + size, and stages it locally.

Actual binary replacement happens on the NEXT launcher pass, after the running
agent has exited cleanly. run-agent.ps1 invokes apply-remote-update.ps1 before
starting watchlog-agent.exe. That separation avoids a self-updater killing the
very process performing the replacement and gives us a deterministic rollback
point.
"""
from __future__ import annotations

import _thread
import json
import os
from pathlib import Path
import threading
import time

import requests

import updater
import wl_version

POLL_SECONDS = 30
BACKEND_MISSING_RETRY_SECONDS = 300
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
REMOTE_UPDATE_CAPABILITY = "remote_update_v1"


def _root(cfg) -> Path:
    return cfg.state_path.parent / "remote-update"


def _pending_path(cfg) -> Path:
    return _root(cfg) / "pending.json"


def _result_path(cfg) -> Path:
    return _root(cfg) / "result.json"


def _package_path(cfg) -> Path:
    return _root(cfg) / "watchlog-agent.next.exe"


def _backup_path() -> Path:
    import watchlog_agent as core
    return core.base_dir() / "watchlog-agent.exe.remote.bak"


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _backend_missing(error: Exception) -> bool:
    text = str(error).lower()
    return "wl_agent_claim_update_request" in text and (
        "schema cache" in text or "function" in text or "404" in text
    )


def _agent_args(state: dict) -> dict:
    return {"p_agent_id": state["agent_id"], "p_agent_key": state["agent_key"]}


def report_previous_result(cloud, state: dict, cfg) -> bool:
    """Publish the launcher's transactional result after the new/rolled-back agent starts."""
    path = _result_path(cfg)
    if not path.exists():
        return False
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    # A successful binary swap is not success yet. Keep the rollback image
    # until the new runtime has stayed alive for the launcher's health window.
    if row.get("ok"):
        health_not_before = str(row.get("health_not_before") or "")
        if health_not_before:
            try:
                from datetime import datetime, timezone
                healthy_at = datetime.fromisoformat(health_not_before.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < healthy_at.astimezone(timezone.utc):
                    return False
            except Exception:
                return False

    request_id = str(row.get("request_id") or "")
    if not request_id:
        return False
    try:
        cloud.call(
            "wl_agent_complete_update_request",
            **_agent_args(state),
            p_request_id=request_id,
            p_ok=bool(row.get("ok")),
            p_detail=str(row.get("detail") or "")[:500],
            p_applied_version=str(row.get("applied_version") or "")[:80] or None,
        )
    except Exception:
        return False

    # Cloud has durably recorded the result. The rollback copy is no longer needed.
    _safe_unlink(path)
    if row.get("ok"):
        _safe_unlink(_backup_path())
    return True


def _fetch_manifest(cfg) -> dict:
    if not cfg.update_url or not str(cfg.update_url).lower().startswith("https://"):
        raise RuntimeError("signed update manifest is not configured over HTTPS")
    response = requests.get(cfg.update_url, timeout=20)
    response.raise_for_status()
    manifest = updater.parse_manifest(response.text)
    signature_state = updater.verify_manifest_signature(manifest, cfg.update_public_key)
    plan = updater.plan_update(
        manifest,
        wl_version.version_string(),
        cfg.update_channel,
        signature_state=signature_state,
        require_signature=cfg.update_require_signature,
    )
    return plan


def _download_verified(cfg, plan: dict) -> Path:
    url = str(plan.get("url") or "")
    if not url.lower().startswith("https://"):
        raise RuntimeError("release package is not HTTPS")
    root = _root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    part = root / "watchlog-agent.next.exe.part"
    final = _package_path(cfg)
    _safe_unlink(part)

    total = 0
    with requests.get(url, timeout=180, stream=True) as response:
        response.raise_for_status()
        with open(part, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise RuntimeError("release package exceeds the 512 MiB safety limit")
                handle.write(chunk)

    ok, detail = updater.verify_payload(part, plan.get("sha256") or "", plan.get("size"))
    if not ok:
        _safe_unlink(part)
        raise RuntimeError(detail)
    os.replace(part, final)
    return final


def stage_latest(cloud, state: dict, cfg, request: dict) -> dict:
    """Stage one cloud-authorized update; never accepts an arbitrary cloud package URL."""
    request_id = str(request.get("request_id") or "")
    if not request_id:
        raise RuntimeError("update request is missing an id")

    plan = _fetch_manifest(cfg)
    action = plan.get("action")
    if action == "up-to-date":
        cloud.call(
            "wl_agent_complete_update_request",
            **_agent_args(state),
            p_request_id=request_id,
            p_ok=True,
            p_detail="already up to date",
            p_applied_version=wl_version.version_string(),
        )
        return {"restart": False, "detail": "already up to date"}

    if action != "update":
        reason = str(plan.get("reason") or "update blocked")
        cloud.call(
            "wl_agent_complete_update_request",
            **_agent_args(state),
            p_request_id=request_id,
            p_ok=False,
            p_detail=reason[:500],
            p_applied_version=wl_version.version_string(),
        )
        return {"restart": False, "detail": reason}

    package = _download_verified(cfg, plan)
    pending = {
        "schema": "watchlog.remote_update.v1",
        "request_id": request_id,
        "target_version": str(plan["target"]),
        "sha256": str(plan["sha256"]).lower(),
        "size": int(package.stat().st_size),
        "package_name": package.name,
        "staged_at": int(time.time()),
    }
    _write_json_atomic(_pending_path(cfg), pending)
    cloud.call(
        "wl_agent_stage_update_request",
        **_agent_args(state),
        p_request_id=request_id,
        p_target_version=pending["target_version"],
        p_sha256=pending["sha256"],
    )
    return {"restart": True, "detail": f"staged {pending['target_version']}"}


def update_worker(cfg, state: dict, cloud, stop: threading.Event) -> None:
    """Poll for signed-update requests. Never executes arbitrary cloud commands."""
    missing_logged = False
    while not stop.is_set():
        try:
            report_previous_result(cloud, state, cfg)
            request = cloud.call(
                "wl_agent_claim_update_request",
                **_agent_args(state),
            )
            cfg.remote_update_last_poll_monotonic = time.monotonic()
            try:
                import watchlog_agent as core
                core.update_runtime_health(remote_update_poll_at=core.iso(core.now_utc()))
            except Exception:
                pass
            missing_logged = False
        except (RuntimeError, requests.RequestException) as error:
            if _backend_missing(error):
                if not missing_logged:
                    import watchlog_agent as core
                    core.log("remote update: backend not deployed; worker idle")
                    missing_logged = True
                stop.wait(BACKEND_MISSING_RETRY_SECONDS)
            else:
                stop.wait(POLL_SECONDS)
            continue
        except Exception:
            stop.wait(POLL_SECONDS)
            continue

        if not request:
            stop.wait(POLL_SECONDS)
            continue

        try:
            result = stage_latest(cloud, state, cfg, request)
            import watchlog_agent as core
            core.log("remote update: " + result.get("detail", "request processed"))
            if result.get("restart"):
                # enhanced_cmd_run catches KeyboardInterrupt and performs its normal
                # spool/thread cleanup. The launcher then applies the staged binary
                # before it starts WatchLog again.
                _thread.interrupt_main()
                return
        except Exception as error:  # noqa: BLE001
            request_id = str((request or {}).get("request_id") or "")
            if request_id:
                try:
                    cloud.call(
                        "wl_agent_complete_update_request",
                        **_agent_args(state),
                        p_request_id=request_id,
                        p_ok=False,
                        p_detail=(f"{type(error).__name__}: {str(error)}")[:500],
                        p_applied_version=wl_version.version_string(),
                    )
                except Exception:
                    pass
            stop.wait(POLL_SECONDS)


def wrap_cmd_run(original):
    """Run remote-update polling beside the normal WatchLog runtime."""
    def wrapped(cfg, state, cloud, once, device=None, channels=None):
        if once:
            try:
                report_previous_result(cloud, state, cfg)
            except Exception:
                pass
            return original(cfg, state, cloud, once, device, channels)

        stop = threading.Event()
        worker = threading.Thread(
            target=update_worker,
            args=(cfg, state, cloud, stop),
            daemon=True,
            name="remote-update",
        )
        worker.start()
        try:
            return original(cfg, state, cloud, once, device, channels)
        finally:
            stop.set()
            worker.join(timeout=5)
    return wrapped


__all__ = [
    "REMOTE_UPDATE_CAPABILITY", "report_previous_result", "stage_latest",
    "update_worker", "wrap_cmd_run",
]

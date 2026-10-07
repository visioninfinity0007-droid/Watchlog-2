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

Success semantics (5.1.1). A verified binary swap is not success. When staging, the old Agent
records its own recorder baseline (remote-update/baseline.json: which recorders its protected
runtime-health proved live). After the swap, the NEW Agent commits the update only when, inside
the bounded commit window apply-remote-update.ps1 set:
  * it runs the applied version (and the release's build SHA when the manifest names one);
  * it has a cloud heartbeat and a remote-update poll written after the swap (not future-dated);
  * every recorder that was live before the update is live again (offline before may stay
    offline, reported as such).
Committing deletes the rollback image at once, so a later failure to report to the cloud can
never revert a proven update. Not proven by the deadline: the Agent asks the launcher to roll
back (rollback_requested + restart); run-agent.ps1 restores the verified image and the restored
Agent reports the rollback only after proving itself with a fresh heartbeat and update poll.
Reports that cannot reach the cloud are retried for REPORT_GIVE_UP_SECONDS, then dropped.
"""
from __future__ import annotations

import _thread
from datetime import datetime, timedelta, timezone
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
COMMIT_WINDOW_SECONDS = 900          # apply-remote-update.ps1 clamps it to 300..1800
FUTURE_SKEW_SECONDS = 120            # a marker further ahead than this proves nothing
HEALTH_NOT_BEFORE_MAX_SECONDS = 600  # a health_not_before further ahead is ignored
ROLLBACK_PROOF_SECONDS = 600         # restored Agent's time to prove itself before reporting
REPORT_GIVE_UP_SECONDS = 7 * 24 * 3600
LIVE_BEFORE_SECONDS = 150            # recorder proof at (or just before) the last heartbeat


def _root(cfg) -> Path:
    return cfg.state_path.parent / "remote-update"


def _pending_path(cfg) -> Path:
    return _root(cfg) / "pending.json"


def _result_path(cfg) -> Path:
    return _root(cfg) / "result.json"


def _package_path(cfg) -> Path:
    return _root(cfg) / "watchlog-agent.next.exe"


def _baseline_path(cfg) -> Path:
    return _root(cfg) / "baseline.json"


def _health_path(cfg) -> Path:
    # The protected runtime-health proof (Secrets ACL), beside agent_state.json in production
    # (watchlog_agent.runtime_health_path()).
    return cfg.state_path.parent / "Secrets" / "runtime-health.json"


def _backup_path() -> Path:
    import watchlog_agent as core
    return core.base_dir() / "watchlog-agent.exe.remote.bak"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _fresh(value, not_before: datetime, now: datetime) -> bool:
    """A marker proves something happened after not_before only when it is not older than
    that and not further in the future than FUTURE_SKEW_SECONDS."""
    stamp = _parse_utc(value)
    return bool(stamp and not_before <= stamp <= now + timedelta(seconds=FUTURE_SKEW_SECONDS))


def _version_key(value) -> tuple[int, int, int]:
    return updater.parse_version(str(value or ""))


def capture_baseline(health: dict | None, now: datetime) -> dict:
    """What the running (old) Agent's protected runtime-health proved just before staging:
    the "live before" half of the per-recorder success rule. judged=False when that Agent has
    no recent heartbeat, in which case no recorder can be required."""
    health = health or {}
    beat = _parse_utc(health.get("heartbeat_at"))
    judged = bool(beat and now - timedelta(minutes=15) <= beat
                  <= now + timedelta(seconds=FUTURE_SKEW_SECONDS))
    since = (beat - timedelta(seconds=LIVE_BEFORE_SECONDS)) if beat else now
    rows = []
    for row in health.get("recorders") or []:
        if isinstance(row, dict) and row.get("local_id"):
            rows.append({"local_id": str(row["local_id"]),
                         "live": bool(judged and row.get("live")
                                      and _fresh(row.get("last_live_at"), since, now))})
    return {
        "judged": judged,
        "multi_recorder": bool(health.get("multi_recorder")),
        "recorder_live": bool(judged and _fresh(health.get("recorder_seen_at"), since, now)),
        "recorders": rows,
        "from_version": str(health.get("agent_version") or ""),
        "from_build_sha": str(health.get("build_sha") or ""),
    }


def commit_verdict(row: dict, baseline: dict | None, health: dict | None,
                   now: datetime) -> tuple[bool, list[str], list[str]]:
    """(committed, failures, still-offline recorders) for an applied, uncommitted update."""
    applied = (_parse_utc(row.get("applied_at")) or _parse_utc(row.get("completed_at")) or now)
    if not health:
        return False, ["the new Agent has written no runtime health"], []
    failures: list[str] = []
    if _version_key(health.get("agent_version")) != _version_key(row.get("applied_version")):
        failures.append(f"running version {health.get('agent_version')!r} is not the applied "
                        f"{row.get('applied_version')!r}")
    want_sha = str((baseline or {}).get("target_build_sha") or "").lower()
    have_sha = str(health.get("build_sha") or "").lower()
    if want_sha and have_sha and not (have_sha.startswith(want_sha) or want_sha.startswith(have_sha)):
        failures.append(f"running build {have_sha[:12]} is not the released build {want_sha[:12]}")
    if not _fresh(health.get("heartbeat_at"), applied, now):
        failures.append("no cloud heartbeat since the update")
    if not _fresh(health.get("remote_update_poll_at"), applied, now):
        failures.append("remote-update polling not proven since the update")
    offline: list[str] = []
    if baseline and baseline.get("judged"):
        if baseline.get("multi_recorder"):
            now_rows = {str(r.get("local_id")): r for r in health.get("recorders") or []
                        if isinstance(r, dict)}
            for before in baseline.get("recorders") or []:
                local_id = str(before.get("local_id"))
                after = now_rows.get(local_id) or {}
                live_now = bool(after.get("live")) and _fresh(after.get("last_live_at"), applied, now)
                if before.get("live") and not live_now:
                    failures.append(f"recorder {local_id} was live before the update and is not live now")
                elif not live_now:
                    offline.append(local_id)
        else:
            live_now = _fresh(health.get("recorder_seen_at"), applied, now)
            if baseline.get("recorder_live") and not live_now:
                failures.append("the recorder was live before the update and has not been seen since")
            elif not live_now:
                offline.append("recorder")
    return not failures, failures, offline


def prove_previous(row: dict, health: dict | None, now: datetime) -> tuple[bool, list[str]]:
    """After a launcher rollback: the restored Agent itself, heartbeating and polling."""
    since = _parse_utc(row.get("rollback_at")) or now
    if not health:
        return False, ["no runtime health"]
    missing = []
    previous = row.get("previous_version")
    if previous and _version_key(health.get("agent_version")) != _version_key(previous):
        missing.append(f"running version {health.get('agent_version')!r} is not the previous {previous!r}")
    if not _fresh(health.get("heartbeat_at"), since, now):
        missing.append("no cloud heartbeat since the rollback")
    if not _fresh(health.get("remote_update_poll_at"), since, now):
        missing.append("no update poll since the rollback")
    return not missing, missing


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


def _log(message: str) -> None:
    try:
        import watchlog_agent as core
        core.log(message)
    except Exception:
        pass


def report_previous_result(cloud, state: dict, cfg, *, _now: datetime | None = None,
                           _health: dict | None = None, _baseline: dict | None = None,
                           _backup: Path | None = None):
    """Judge, then publish, the launcher's transactional result.

    Returns True when a result was reported, False when there is nothing to report yet, and
    "restart" when the new version failed its commit gate: the caller must exit so the
    launcher can restore the previous Agent from the verified rollback image."""
    path = _result_path(cfg)
    if not path.exists():
        return False
    row = _read_json(path)
    if row is None:
        return False
    now = _now or _now_utc()
    health = _health if _health is not None else _read_json(_health_path(cfg))
    backup = _backup or _backup_path()
    completed = _parse_utc(row.get("completed_at"))
    if completed is None:
        try:
            completed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            completed = now

    if row.get("ok") and not row.get("committed"):
        not_before = _parse_utc(row.get("health_not_before"))
        # A health_not_before far in the future is not a reason to wait forever.
        if (not_before and now < not_before
                and not_before <= now + timedelta(seconds=HEALTH_NOT_BEFORE_MAX_SECONDS)):
            return False
        baseline = _baseline
        if baseline is None:
            baseline = _read_json(_baseline_path(cfg))
            if baseline and str(baseline.get("request_id") or "") != str(row.get("request_id") or ""):
                baseline = None
        ok, failures, offline = commit_verdict(row, baseline, health, now)
        if ok:
            note = (f"; {len(offline)} recorder(s) offline before the update are still offline"
                    if offline else "")
            row.update(committed=True, committed_at=_iso(now),
                       detail=(f"remote update {row.get('applied_version')} committed: version, "
                               f"cloud heartbeat, update polling and every recorder live before "
                               f"the update proven{note}"))
            _write_json_atomic(path, row)
            # Disarm the rollback now: a later cloud-report failure must never revert it.
            _safe_unlink(backup)
            _safe_unlink(_baseline_path(cfg))
        else:
            deadline = _parse_utc(row.get("commit_deadline")) or (
                completed + timedelta(seconds=COMMIT_WINDOW_SECONDS))
            if now < deadline:
                return False
            detail = ("the new version did not prove its health within the commit window: "
                      + "; ".join(failures))
            if backup.exists():
                row.update(ok=False, rollback_requested=True, detail=detail[:480])
                _write_json_atomic(path, row)
                return "restart"
            row.update(ok=False, detail=(detail + "; no rollback image was available")[:480])
            _write_json_atomic(path, row)
    elif (row.get("rollback_applied") and not row.get("rollback_proven")
          and not row.get("rollback_unproven")):
        proven, missing = prove_previous(row, health, now)
        rolled_back_at = _parse_utc(row.get("rollback_at")) or completed
        if proven:
            row.update(rollback_proven=True, detail=(
                f"{row.get('detail') or 'remote update rolled back'}; previous version "
                f"{row.get('previous_version') or ''} proven running (fresh heartbeat and update "
                "polling)")[:480])
        elif now < rolled_back_at + timedelta(seconds=ROLLBACK_PROOF_SECONDS):
            return False
        else:
            row.update(rollback_unproven=True, detail=(
                f"{row.get('detail') or 'remote update rolled back'}; previous version NOT "
                f"proven running: {'; '.join(missing)}")[:480])
        _write_json_atomic(path, row)

    request_id = str(row.get("request_id") or "")
    if not request_id:
        if now - completed > timedelta(seconds=REPORT_GIVE_UP_SECONDS):
            _safe_unlink(path)
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
        if now - completed > timedelta(seconds=REPORT_GIVE_UP_SECONDS):
            _log("remote update: result could not be reported for 7 days; dropped (see agent.log)")
            _safe_unlink(path)
        return False

    # Cloud has durably recorded the result. Nothing is armed any more.
    _safe_unlink(path)
    _safe_unlink(_baseline_path(cfg))
    if row.get("ok"):
        _safe_unlink(backup)
    return True


def _now_utc() -> datetime:
    return _now()


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
        # This path replaces the Agent only: refuse a release that needs the Repair package.
        installed_components=updater.installed_components_version(),
    )
    return plan


def _protect_staging(root: Path) -> None:
    """SYSTEM + Administrators only, verified, before anything is staged (T0-SEC1).

    %ProgramData% lets a standard user create files in a new folder; run-agent.ps1 applies
    what is staged here as SYSTEM. apply-remote-update.ps1 refuses a stage in a folder that
    is not protected or files not owned by SYSTEM/Administrators, so an update that cannot
    be protected here is not staged at all. Leftovers this Agent did not write are removed."""
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        return
    import windows_secret
    try:
        windows_secret.ensure_secure_dir(root)
    except Exception as error:                        # noqa: BLE001
        raise RuntimeError(f"update staging folder could not be protected: "
                           f"{type(error).__name__}") from error
    for leftover in ("pending.json", "watchlog-agent.next.exe", "watchlog-agent.next.exe.part"):
        _safe_unlink(root / leftover)


def _download_verified(cfg, plan: dict) -> Path:
    url = str(plan.get("url") or "")
    if not url.lower().startswith("https://"):
        raise RuntimeError("release package is not HTTPS")
    root = _root(cfg)
    _protect_staging(root)
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
    now = _now_utc()
    # The "live before" half of the success rule, from this (old) Agent's own protected proof.
    baseline = capture_baseline(_read_json(_health_path(cfg)), now)
    baseline.update(schema="watchlog.remote_update_baseline.v1", request_id=request_id,
                    captured_at=_iso(now), target_version=str(plan["target"]),
                    target_build_sha=str(plan.get("build_sha") or ""))
    _write_json_atomic(_baseline_path(cfg), baseline)
    pending = {
        "schema": "watchlog.remote_update.v1",
        "request_id": request_id,
        "target_version": str(plan["target"]),
        "sha256": str(plan["sha256"]).lower(),
        "size": int(package.stat().st_size),
        "package_name": package.name,
        "staged_at": int(time.time()),
        "commit_window_sec": COMMIT_WINDOW_SECONDS,
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
            if report_previous_result(cloud, state, cfg) == "restart":
                # The applied update failed its commit gate: exit so the launcher restores the
                # previous Agent from the verified rollback image.
                _log("remote update: the new version did not prove its health; restarting for rollback")
                _thread.interrupt_main()
                return
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
    "REMOTE_UPDATE_CAPABILITY", "capture_baseline", "commit_verdict", "prove_previous",
    "report_previous_result", "stage_latest", "update_worker", "wrap_cmd_run",
]

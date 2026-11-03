#!/usr/bin/env python3
"""Remote maintenance through Site Control (5.1.2): outbound-only, read tier.

The Agent claims these like every Site Control read (wl_agent_claim_command) and completes
them with a structured result; nothing listens, nothing writes to a recorder.

    run_full_acceptance_test  the bounded full acceptance suite (full_acceptance.run_suite),
                              one section per recorder (all configured recorders when
                              params.all_recorders, else the command's recorder)
    run_recording_check       the suite's Recording checks only
    run_archive_check         the suite's archive search (+ optional clip) only
    collect_diagnostics       the redacted support-bundle manifest and a bounded (64 KiB)
                              redacted tail of agent.log
    refresh_inventory         re-read the recorder's channels and resync WatchLog's cameras
                              through the existing sync RPC
    refresh_capabilities      run the recorder capability sync now
    reconnect_recorder        ask that recorder's collector to drop and reopen its session,
                              and report whether a new session was observed
    restart_agent             graceful restart: the Agent exits with RESTART_EXIT_CODE after
                              the command is completed; run-agent.ps1 restarts it

The module also keeps the small in-process registry the live collectors register with, so a
command can see a recorder's runtime evidence (event stream, recovery cycle) and signal its
collector, plus the process-wide restart request the run loops honour.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

MAINTENANCE_ACTIONS = (
    "run_full_acceptance_test", "run_recording_check", "run_archive_check",
    "collect_diagnostics", "refresh_inventory", "refresh_capabilities",
    "reconnect_recorder", "restart_agent",
)

RESTART_EXIT_CODE = 75               # EX_TEMPFAIL: run-agent.ps1 restarts on every exit code
RESTART_GRACE_SECONDS = 3.0          # let the completion reach the cloud and the log flush
RESTART_FORCE_AFTER_SECONDS = 90.0   # a run loop that cannot exit cleanly is ended hard
RECONNECT_CONFIRM_SECONDS = 45.0
DIAGNOSTICS_LOG_MAX_BYTES = 64 * 1024
DIAGNOSTICS_LOG_READ_BYTES = 512 * 1024
DIAGNOSTICS_FILE_MAX_BYTES = 8 * 1024
ARCHIVE_CHECK_BUDGET_SECONDS = 120.0
RECORDING_CHECK_BUDGET_SECONDS = 150.0


# ---------------------------------------------------------------------------
# in-process runtime registry (collectors register their holder)
# ---------------------------------------------------------------------------

_RUNTIMES: dict = {}
_RUNTIMES_LOCK = threading.Lock()


def _key(recorder_id) -> str:
    return str(recorder_id or "").strip()


def register_runtime(cfg, holder: dict | None) -> None:
    """Called by a live collector: its holder becomes visible to Site Control commands."""
    if holder is None:
        return
    holder.setdefault("reconnect_event", threading.Event())
    with _RUNTIMES_LOCK:
        _RUNTIMES[_key(getattr(cfg, "recorder_cloud_id", None))] = holder


def unregister_all() -> None:
    with _RUNTIMES_LOCK:
        _RUNTIMES.clear()


def runtime_holder(recorder_id) -> dict | None:
    """The live holder of this recorder, or of the singleton runtime when it is the only one."""
    with _RUNTIMES_LOCK:
        holder = _RUNTIMES.get(_key(recorder_id))
        if holder is None and len(_RUNTIMES) == 1 and "" in _RUNTIMES:
            holder = _RUNTIMES[""]
        return holder


class SessionStop:
    """A stop signal for ONE recorder session: set when the Agent stops, or when a
    reconnect_recorder command asks this recorder's collector to drop its session.

    Duck-types threading.Event for the drivers (is_set / wait)."""

    def __init__(self, stop, holder: dict | None):
        self._stop = stop
        self._reconnect = (holder or {}).get("reconnect_event")

    def is_set(self) -> bool:
        return bool(self._stop.is_set() or (self._reconnect is not None
                                            and self._reconnect.is_set()))

    def set(self) -> None:
        """Setting it stops the Agent, exactly as setting the stop it wraps did before."""
        self._stop.set()

    def wait(self, timeout: float | None = None) -> bool:
        if self._reconnect is None:
            return self._stop.wait(timeout)
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while not self.is_set():
            left = 0.25 if deadline is None else min(0.25, deadline - time.monotonic())
            if left <= 0:
                break
            self._stop.wait(left)
        return self.is_set()


def take_reconnect(holder: dict | None) -> bool:
    """True (once) when a reconnect was requested for this holder's recorder."""
    event = (holder or {}).get("reconnect_event")
    if event is not None and event.is_set():
        event.clear()
        holder["reconnect_count"] = int(holder.get("reconnect_count") or 0) + 1
        return True
    return False


# ---------------------------------------------------------------------------
# restart request
# ---------------------------------------------------------------------------

_RESTART = threading.Event()


def restart_requested() -> bool:
    return _RESTART.is_set()


def check_restart() -> None:
    """Run loops call this every tick: a requested restart ends the run cleanly."""
    if _RESTART.is_set():
        raise SystemExit(RESTART_EXIT_CODE)


def request_restart(log=print, *, force_after: float = RESTART_FORCE_AFTER_SECONDS,
                    _exit=os._exit) -> None:
    """Ask the run loop to exit with RESTART_EXIT_CODE; end the process hard if it cannot."""
    log(f"site control: restart requested; the Agent exits with code {RESTART_EXIT_CODE} "
        "and the launcher starts it again")
    _RESTART.set()

    def force():
        log("site control: run loop did not exit for the requested restart; exiting now")
        _exit(RESTART_EXIT_CODE)
    timer = threading.Timer(force_after, force)
    timer.daemon = True
    timer.start()


def _schedule_restart(log=print, delay: float = RESTART_GRACE_SECONDS) -> threading.Timer:
    timer = threading.Timer(delay, request_restart, kwargs={"log": log})
    timer.daemon = True
    timer.start()
    return timer


def _reset_restart_for_tests() -> None:
    _RESTART.clear()


# ---------------------------------------------------------------------------
# the Agent's acceptance environment
# ---------------------------------------------------------------------------

def _core():
    import watchlog_agent as core
    return core


class AgentEnv:
    """full_acceptance.Env bound to the running Agent."""

    def __init__(self, cfg, state: dict | None, cloud, *, command_id=None):
        self.cfg = cfg
        self._state = dict(state or {})
        self.cloud = cloud
        self.command_id = command_id

    def now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def agent_meta(self) -> dict:
        import wl_version
        meta = wl_version.build_metadata() or {}
        return {"version": meta.get("version") or getattr(_core(), "AGENT_VERSION", None),
                "build_sha": meta.get("build_sha") or None,
                "build_channel": meta.get("build_channel") or meta.get("channel")}

    def state(self) -> dict:
        return self._state

    def cloud_auth(self) -> dict:
        if self.cloud is None:
            raise RuntimeError("WatchLog cloud is not configured on this PC")
        return self.cloud.call("wl_agent_preflight_auth", p_agent_id=self._state["agent_id"],
                               p_agent_key=self._state["agent_key"]) or {}

    def runtime_health(self) -> dict | None:
        try:
            path = _core().runtime_health_path()
            if not path.exists():
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def base_config(self):
        return self.cfg

    def open_driver(self, cfg):
        return _core()._open_driver_unverified(cfg)

    def open_archive(self, cfg, live):
        return _core().open_archive_driver(cfg, live=live)

    def holder(self, recorder_id):
        return runtime_holder(recorder_id)

    def spool_probe(self, cfg) -> dict:
        path = Path(getattr(cfg, "spool_path"))
        if not path.exists():
            return {"exists": False}
        from spool import Spool
        spool = Spool(path, getattr(cfg, "spool_max_rows", 0) or 0)
        try:
            return {"exists": True, "depth": spool.count(), "max_rows": spool.max_rows,
                    "overflow": bool(spool.pending_recovery_gap())}
        finally:
            spool.close()

    def analytics_status(self) -> dict | None:
        path = getattr(self.cfg, "analytics_status_path", None)
        if not path:
            return None
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def fetch_manifest(self, url: str) -> str:
        import requests
        if not str(url).lower().startswith("https://"):
            raise RuntimeError("update manifest URL is not HTTPS")
        import full_acceptance
        with requests.get(url, timeout=(5, 15), stream=True) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > full_acceptance.MANIFEST_MAX_BYTES:
                    raise RuntimeError("update manifest exceeds 1 MiB")
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8-sig")


def build_targets(cfg, recorder_id, all_recorders: bool) -> list:
    """The recorder sections to test: every configured recorder (registry) or just one,
    each with its own bound Config (credential, driver, spool), as Site Control routes."""
    import full_acceptance
    import recorder_runtime
    try:
        rows = recorder_runtime._configured_rows()
    except Exception:  # noqa: BLE001 — an unreadable registry falls through to routing
        rows = []
    if all_recorders and rows:
        contexts = recorder_runtime.load_contexts(cfg, degrade_credential_errors=True)
        return [full_acceptance.Target(ctx.cloud_recorder_id, ctx.display_name, ctx.config,
                                       primary=bool(ctx.is_primary)) for ctx in contexts]
    job_cfg = recorder_runtime.config_for_cloud_recorder(cfg, recorder_id)
    rid = recorder_id or getattr(job_cfg, "recorder_cloud_id", None)
    return [full_acceptance.Target(rid, getattr(job_cfg, "recorder_display_name", None)
                                   or "Recorder", job_cfg, primary=True)]


def _bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def run_acceptance(cfg, state, cloud, *, recorder_id=None, all_recorders=False,
                   include_clip=True, only=None, channels=None, budget_seconds=None,
                   command_id=None, env=None) -> dict:
    import full_acceptance
    targets = build_targets(cfg, recorder_id, all_recorders)
    env = env or AgentEnv(cfg, state, cloud, command_id=command_id)
    return full_acceptance.run_suite(
        env, targets, include_clip=include_clip, only=only, channels=channels,
        budget_seconds=budget_seconds or full_acceptance.DEFAULT_BUDGET_SECONDS)


# ---------------------------------------------------------------------------
# collect_diagnostics
# ---------------------------------------------------------------------------

def _ini_section(cfg) -> dict:
    import configparser
    path = getattr(cfg, "_ini_path", None)
    if not path:
        path = _core().base_dir() / "watchlog.ini"
    path = Path(path)
    if not path.exists():
        return {}
    ini = configparser.ConfigParser()
    try:
        ini.read(path, encoding="utf-8-sig")
    except configparser.Error:
        return {}
    return dict(ini.items("watchlog")) if ini.has_section("watchlog") else {}


def _read_tail(path: Path, max_bytes: int) -> tuple[str, int]:
    size = path.stat().st_size
    with open(path, "rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes and "\n" in text:
        text = text.split("\n", 1)[1]      # drop the partial first line
    return text, size


def agent_log_path(cfg) -> Path:
    state_path = getattr(cfg, "state_path", None)
    if state_path:
        candidate = Path(state_path).parent / "agent.log"
        if candidate.exists():
            return candidate
    return _core().default_state_dir() / "agent.log"


def collect_diagnostics(cfg, state: dict | None, *, log_path: Path | None = None,
                        setup_log: str | None = None, max_log_bytes: int = DIAGNOSTICS_LOG_MAX_BYTES
                        ) -> dict:
    """The support bundle as a manifest (name, bytes, sha256) plus its small redacted files,
    and a redacted tail of agent.log of at most ``max_log_bytes``. No secret: the bundle is
    assembled by allowlist (support_bundle), and every text that leaves here is additionally
    stripped of URLs, credentials and IP addresses."""
    import support_bundle
    import wl_version

    if setup_log is None:
        setup_log = ""
        try:
            from setup_backend import programdata_dir
            path = programdata_dir() / "setup.log"
            if path.exists():
                setup_log, _size = _read_tail(path, DIAGNOSTICS_LOG_READ_BYTES)
        except Exception:  # noqa: BLE001
            setup_log = ""
    spool_count = None
    try:
        spool_path = Path(getattr(cfg, "spool_path"))
        if spool_path.exists():
            from spool import Spool
            spool = Spool(spool_path, getattr(cfg, "spool_max_rows", 0) or 0)
            try:
                spool_count = spool.count()
            finally:
                spool.close()
    except Exception:  # noqa: BLE001
        spool_count = None

    files = support_bundle.collect(_ini_section(cfg), state=state or {}, setup_log=setup_log,
                                   build_meta=wl_version.build_metadata(),
                                   spool_count=spool_count)
    manifest, contents = [], {}
    for name, text in files.items():
        safe = support_bundle.redact_text(text)
        raw = safe.encode("utf-8")
        manifest.append({"name": name, "bytes": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest()})
        if name != "setup_log.txt" and len(raw) <= DIAGNOSTICS_FILE_MAX_BYTES:
            contents[name] = safe

    path = Path(log_path) if log_path else agent_log_path(cfg)
    tail, size, truncated = "", 0, False
    if path.exists():
        raw_tail, size = _read_tail(path, DIAGNOSTICS_LOG_READ_BYTES)
        tail, truncated = support_bundle.redact_log_tail(raw_tail, max_bytes=max_log_bytes)
        truncated = truncated or size > DIAGNOSTICS_LOG_READ_BYTES
    return {
        "manifest": manifest,
        "files": contents,
        "agent_log": {"present": path.exists(), "bytes_on_disk": size,
                      "tail_bytes": len(tail.encode("utf-8")), "truncated": truncated,
                      "tail": tail},
    }


# ---------------------------------------------------------------------------
# inventory / capabilities / reconnect
# ---------------------------------------------------------------------------

def refresh_inventory(job_cfg, state: dict, cloud, *, holder=None, open_driver=None) -> dict:
    """Re-read the recorder's channels and resync WatchLog's cameras through the same RPC the
    Agent's startup uses (wl_sync_recorder_cameras for a registry recorder, wl_sync_cameras for
    the 5.0.x singleton)."""
    core = _core()
    open_fn = open_driver or core.open_driver
    driver, _info = open_fn(job_cfg)
    try:
        channels = [{"channel": str(c.channel), "name": getattr(c, "name", None)}
                    for c in core._synced_inventory(driver)]
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass
    if not channels:
        return {"ok": False, "error": "the recorder reported no camera channels",
                "data": {"channels": 0}}
    recorder_id = str(getattr(job_cfg, "recorder_cloud_id", "") or "").strip()
    if recorder_id:
        mapping = cloud.call("wl_sync_recorder_cameras", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_recorder_id=recorder_id,
                             p_cameras=channels)
    else:
        mapping = cloud.call("wl_sync_cameras", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_cameras=channels)
    mapping = {str(k): str(v) for k, v in (mapping or {}).items()} \
        if isinstance(mapping, dict) else {}
    chans = sorted({c["channel"] for c in channels})
    unmapped = [c for c in chans if c not in mapping]
    if holder is not None and mapping and not unmapped:
        holder["camera_mapping"] = dict(mapping)
        holder["camera_sync_signature"] = tuple(chans)
        holder["synced_channels"] = [{"channel": c, "camera_id": mapping[c]} for c in chans]
    data = {"recorder_id": recorder_id or None, "channels": len(chans),
            "mapped": len([c for c in chans if c in mapping]), "unmapped_channels": unmapped[:32],
            "runtime_updated": bool(holder is not None and mapping and not unmapped)}
    if unmapped:
        return {"ok": False, "error": "WatchLog did not map every recorder channel", "data": data}
    return {"ok": True, "data": data}


def refresh_capabilities(job_cfg, state: dict, cloud, *, open_driver=None) -> dict:
    import capability_sync
    core = _core()
    recorder_id = str(getattr(job_cfg, "recorder_cloud_id", "") or "").strip() or None
    lines: list = []
    ok = capability_sync.sync_once(job_cfg, state, cloud, open_driver or core.open_driver,
                                   recorder_id=recorder_id, log=lines.append)
    data = {"recorder_id": recorder_id, "reported": bool(ok)}
    if not ok:
        return {"ok": False, "error": "the recorder capabilities could not be read or reported",
                "data": data}
    return {"ok": True, "data": data}


def reconnect_recorder(recorder_id, *, confirm_seconds: float = RECONNECT_CONFIRM_SECONDS,
                       sleep=time.sleep, monotonic=time.monotonic) -> dict:
    """Signal the recorder's collector to drop its session and reopen it.

    Succeeds only on positive evidence: the collector consumed the request and a NEW recorder
    session (a fresh login + probe, i.e. a different live driver) is up. Never closes a driver
    from this thread; the collector ends its own session through its SessionStop."""
    holder = runtime_holder(recorder_id)
    if holder is None:
        return {"ok": False, "error": "no live collector for this recorder in this Agent",
                "data": {"requested": False}}
    event = holder.setdefault("reconnect_event", threading.Event())
    before = int(holder.get("reconnect_count") or 0)
    old_driver = holder.get("live_driver")
    requested_at = monotonic()
    event.set()
    deadline = requested_at + max(0.0, confirm_seconds)
    dropped = reopened = False
    while True:
        dropped = int(holder.get("reconnect_count") or 0) > before
        new_driver = holder.get("live_driver")
        reopened = dropped and new_driver is not None and new_driver is not old_driver
        if reopened or monotonic() >= deadline:
            break
        sleep(0.5)
    stream = holder.get("event_stream") if isinstance(holder.get("event_stream"), dict) else {}
    data = {"requested": True, "session_dropped": dropped, "session_reopened": reopened,
            "stream_connected": stream.get("connected"),
            "waited_s": int(monotonic() - requested_at)}
    if reopened:
        return {"ok": True, "data": data}
    return {"ok": False, "data": data,
            "error": ("the session was dropped but no new session was observed yet"
                      if dropped else "the collector did not drop its session in time")}


def _remote_update_blocks_restart(cfg) -> str | None:
    """A restart must never disturb an in-flight remote update: a staged package would be
    applied by the launcher, and an armed rollback would treat a short run as a failure."""
    try:
        import remote_update
        if remote_update._pending_path(cfg).exists():
            return "a staged remote update is waiting to be applied"
        result = remote_update._read_json(remote_update._result_path(cfg))
        if result and remote_update._backup_path().exists() and not (
                result.get("committed") or result.get("rollback_applied")
                or result.get("superseded")):
            return "a remote update is inside its commit window"
    except Exception:  # noqa: BLE001 — cannot tell: refuse rather than guess
        return "the remote update state could not be read"
    return None


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def execute(cfg, state: dict, cloud, cmd: dict, *, log=print) -> dict:
    """Run one claimed maintenance command. -> {"ok", "data", "error", "after_complete"}.

    ``after_complete`` (restart_agent only) runs once the completion reached the cloud."""
    import full_acceptance
    import recorder_runtime

    action = cmd.get("action")
    params = cmd.get("params") if isinstance(cmd.get("params"), dict) else {}
    recorder_id = cmd.get("recorder_id") or params.get("recorder_id")
    if action not in MAINTENANCE_ACTIONS:
        return {"ok": False, "error": "unsupported_read_action", "data": None}

    if action in ("run_full_acceptance_test", "run_recording_check", "run_archive_check"):
        channel = params.get("channel")
        channels = [str(c) for c in params.get("channels") or []] or (
            [str(channel)] if channel else None)
        if action == "run_full_acceptance_test":
            kwargs = {"include_clip": _bool(params.get("include_clip"), True), "only": None,
                      "budget_seconds": full_acceptance.DEFAULT_BUDGET_SECONDS}
        elif action == "run_recording_check":
            kwargs = {"include_clip": False, "only": full_acceptance.RECORDING_CHECK_ONLY,
                      "budget_seconds": RECORDING_CHECK_BUDGET_SECONDS}
        else:
            kwargs = {"include_clip": _bool(params.get("include_clip"), False),
                      "only": full_acceptance.ARCHIVE_CHECK_ONLY,
                      "budget_seconds": ARCHIVE_CHECK_BUDGET_SECONDS}
            channels = channels or None
        result = run_acceptance(cfg, state, cloud, recorder_id=recorder_id,
                                all_recorders=_bool(params.get("all_recorders"), False),
                                channels=channels, command_id=cmd.get("id"), **kwargs)
        # The command succeeded when the suite ran; the verdict is in the result.
        return {"ok": True, "data": result}

    if action == "collect_diagnostics":
        return {"ok": True, "data": collect_diagnostics(cfg, state)}

    if action == "restart_agent":
        why = _remote_update_blocks_restart(cfg)
        if why:
            return {"ok": False, "error": why, "data": {"restart_scheduled": False}}
        return {"ok": True,
                "data": {"restart_scheduled": True, "exit_code": RESTART_EXIT_CODE,
                         "launcher": "run-agent.ps1 restarts the Agent after any exit"},
                "after_complete": lambda: _schedule_restart(log)}

    if action == "reconnect_recorder":
        return reconnect_recorder(recorder_id)

    job_cfg = recorder_runtime.config_for_cloud_recorder(cfg, recorder_id)
    if action == "refresh_inventory":
        return refresh_inventory(job_cfg, state, cloud, holder=runtime_holder(recorder_id))
    return refresh_capabilities(job_cfg, state, cloud)


__all__ = [
    "MAINTENANCE_ACTIONS", "RESTART_EXIT_CODE", "AgentEnv", "SessionStop", "build_targets",
    "check_restart", "collect_diagnostics", "execute", "reconnect_recorder", "refresh_inventory",
    "refresh_capabilities", "register_runtime", "request_restart", "restart_requested",
    "run_acceptance", "runtime_holder", "take_reconnect",
]

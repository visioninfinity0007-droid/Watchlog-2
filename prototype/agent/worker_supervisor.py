"""Worker registry and supervisor for the Agent's long-running threads (5.1.2).

Before 5.1.2 a worker thread that raised was logged once and ended for the life of the
process, while the process kept heartbeating: the cloud saw a live Agent whose collector,
health or Site Control worker had died. This module closes that gap.

Every long-running worker is registered with:

* a canonical ``worker`` name (``KNOWN_WORKERS``, the same list migration 0162 accepts);
* its recorder scope: the local recorder id (``recorder_local_id``) and the recorder's cloud
  id (``recorder_id``), or None for a site-level worker;
* its criticality, and the longest quiet time it may have between ticks (``stall_after``);
* the start callable and its arguments, exactly as ``threading.Thread`` takes them.

``supervised(...)`` returns a ``SupervisedWorker``, which quacks like ``threading.Thread``
(``start``, ``join``, ``is_alive``, ``name``, ``_args``), so the runtimes replace
``threading.Thread(`` with ``worker_supervisor.supervised(`` and keep their graceful-stop code.

A worker reports progress from its own thread with the module-level ``tick()`` /
``success()`` / ``record_error()`` calls; outside a supervised thread they do nothing. A
worker that ends on purpose says so with ``complete()`` or ``disable()`` before it returns.

``Supervisor.check()`` (called from the run loop, about once a second) detects

* death: the thread is no longer alive and did not end on purpose, and the runtime is not
  stopping. The worker is restarted after an exponential backoff (5 s doubling, capped at
  5 min). More than ``MAX_RESTARTS_PER_HOUR`` restarts in a rolling hour marks it ``failed``:
  it is not started again in this run and stays reported as failed;
* stall: a live thread that has not ticked within ``stall_after``. It is reported
  ``stalled`` and asked to stop (its own stop event is set). It is restarted only after that
  thread has actually ended, so two copies of one worker never run at the same time. A
  thread that never returns stays ``stalled``, which is the truth.

Each incarnation gets its own stop event, linked to the runtime's stop event: setting the
runtime's stop still stops every worker at once, and a stalled incarnation can be stopped
alone. Recorder-scoped workers are separate registry entries with their own backoff and
restart budget: recorder A's failing worker never restarts or affects recorder B's.

Health is never reported without evidence: a worker is ``healthy`` only while its thread is
alive, it has ticked within its stall window, no error was recorded since its last success,
and (where a probe is registered, e.g. the collector's event stream) the probe says so.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

STATES = ("starting", "running", "stalled", "restarting", "failed", "completed", "disabled")

# Canonical worker names. Migration 0162 (wl_report_agent_runtime) accepts exactly these.
KNOWN_WORKERS = (
    "collector", "health", "recovery", "periodic_stills", "credential_watch",
    "analytics", "archive", "site_control", "recorder_check",
    "incident_footage", "incident_stills", "remote_update", "capability_sync",
)

# Workers without which the site's live monitoring truth is wrong: events (collector),
# camera/recorder health (health) and the analytics sampler that also holds the site's
# single-authority lease (analytics). The Agent is reported healthy only while every enabled
# critical worker is.
CRITICAL_WORKERS = frozenset({"collector", "health", "analytics"})
# Workers that end on purpose after doing their job once.
ONE_SHOT_WORKERS = frozenset({"capability_sync", "recorder_check", "credential_watch"})

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 300.0
MAX_RESTARTS_PER_HOUR = 6
RESTART_WINDOW_SECONDS = 3600.0
# An incarnation that ran this long before dying starts its backoff from the base again.
STABLE_RESET_SECONDS = 600.0
ERROR_MAX_CHARS = 300

_local = threading.local()


def stall_after(worker: str, cfg=None) -> float | None:
    """The longest a live worker may go without a tick before it is reported stalled.

    Generous on purpose: each window is several of the worker's own loop periods plus its
    longest bounded operation, so a slow recorder or a long archive read is not a stall. The
    collector has none: it blocks on its event stream while a recorder is quiet, so its
    evidence is the stream itself (runtime_status.collector_probe). One-shot workers have
    none either."""
    def seconds(name, default):
        try:
            return float(getattr(cfg, name, None) or default)
        except (TypeError, ValueError):
            return float(default)

    if worker == "health":
        return max(900.0, 3.0 * (1.2 * seconds("health_seconds", 300) + 60.0))
    if worker == "recovery":
        # One claimed interval is read in bounded, throttled chunks and can take a while.
        return max(7200.0, 3.0 * seconds("recovery_seconds", 300))
    if worker == "analytics":
        return 300.0                    # 0.1 s loop; config poll and stills are time-bounded
    if worker == "archive":
        return 3600.0                   # one bounded historical scan per cycle
    if worker == "site_control":
        return max(1800.0, 3.0 * seconds("site_control_seconds", 15))
    if worker == "remote_update":
        return 3600.0                   # a staged update download is the longest step
    if worker in ("periodic_stills", "incident_footage", "incident_stills"):
        return 900.0
    return None


def policy(worker: str, cfg=None) -> dict:
    """Registration defaults for a worker: criticality, stall window, one-shot."""
    return {
        "critical": worker in CRITICAL_WORKERS,
        "stall_after": stall_after(worker, cfg),
        "one_shot": worker in ONE_SHOT_WORKERS,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def redacted_error(error) -> str:
    """``Type: first line`` with URLs, credentials and recorder addresses removed."""
    if error is None:
        return ""
    if isinstance(error, BaseException):
        text = str(error)
        prefix = type(error).__name__
    else:
        text, prefix = str(error), ""
    try:
        import nvr_health
        clean = nvr_health.redact(text)
    except Exception:  # noqa: BLE001 — never report raw text if redaction is unavailable
        clean = ""
    out = f"{prefix}: {clean}" if prefix and clean else (prefix or clean)
    return out[:ERROR_MAX_CHARS]


def _default_log(message: str) -> None:
    try:
        import agent_core
        agent_core.log(message)
    except Exception:  # noqa: BLE001
        pass


class _StopFanout:
    """Sets every linked child event once the parent (the runtime's stop) is set."""

    def __init__(self, parent: threading.Event) -> None:
        self.parent = parent
        self._children: set = set()
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name="worker-stop-relay").start()

    def _run(self) -> None:
        self.parent.wait()
        with self._lock:
            children = list(self._children)
        for child in children:
            child.set()

    def add(self, child: threading.Event) -> None:
        with self._lock:
            self._children.add(child)
        if self.parent.is_set():
            child.set()

    def discard(self, child: threading.Event) -> None:
        with self._lock:
            self._children.discard(child)


class SupervisedWorker:
    """One registered worker. Thread-like: start(), join(), is_alive(), name, _args."""

    def __init__(self, supervisor: "Supervisor", worker: str, target: Callable, args=(),
                 kwargs=None, *, name: str | None = None, stop: threading.Event | None = None,
                 recorder_local_id=None, recorder_id=None, critical: bool = False,
                 stall_after: float | None = None, one_shot: bool = False,
                 enabled=True, probe: Callable | None = None, daemon: bool = True) -> None:
        if worker not in KNOWN_WORKERS:
            raise ValueError(f"unknown worker name {worker!r}")
        self.supervisor = supervisor
        self.worker = worker
        self._target = target
        self._args = tuple(args or ())
        self._kwargs = dict(kwargs or {})
        self.name = name or worker
        self.daemon = daemon
        self.stop = stop
        self.recorder_local_id = None if recorder_local_id is None else str(recorder_local_id)
        self.recorder_id = None if recorder_id in (None, "") else str(recorder_id)
        self.critical = bool(critical)
        self.stall_after = float(stall_after) if stall_after else None
        self.one_shot = bool(one_shot)
        self._enabled = enabled
        self.probe = probe

        self._lock = threading.RLock()
        self._started = False
        self._joined = False
        self._thread: threading.Thread | None = None
        self._inc_stop: threading.Event | None = None
        self._incarnation = 0
        self._retired = False
        self._exit = None                 # ("returned"|"raised", error) of the current incarnation
        self._exit_intent = None          # ("completed"|"disabled"|"failed", reason) said by the worker
        self.state = "starting"
        self.enabled = True
        self.started_at: float | None = None
        self.last_tick: float | None = None
        self.last_success_tick: float | None = None
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self._error_since_success = False
        self.restart_count = 0
        self.stall_count = 0
        self._consecutive_failures = 0
        self._restart_times: list[float] = []
        self._next_start_at: float | None = None
        self.reason: str | None = None

    # -- registry key ---------------------------------------------------------------
    @property
    def key(self) -> tuple:
        return (self.worker, self.recorder_local_id or self.recorder_id)

    # -- Thread-like API ------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("threads can only be started once")
            self._started = True
            enabled = self._enabled() if callable(self._enabled) else bool(self._enabled)
            if not enabled:
                self.enabled = False
                self._set_state("disabled")
                return
            self._spawn()

    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def join(self, timeout: float | None = None) -> None:
        with self._lock:
            self._joined = True           # the runtime is stopping it: never restart it again
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # -- incarnations ---------------------------------------------------------------
    def _spawn(self) -> None:
        self._incarnation += 1
        incarnation = self._incarnation
        inc_stop = None
        args, kwargs = self._args, self._kwargs
        if self.stop is not None:
            inc_stop = threading.Event()
            self.supervisor._link(self.stop, inc_stop)
            args = tuple(inc_stop if a is self.stop else a for a in self._args)
            kwargs = {k: (inc_stop if v is self.stop else v) for k, v in self._kwargs.items()}
        self._inc_stop = inc_stop
        self._retired = False
        self._exit = None
        self._exit_intent = None
        self.started_at = self.supervisor.clock()
        self.last_tick = None
        self._set_state("starting")
        thread = threading.Thread(target=self._run, args=(incarnation, inc_stop, args, kwargs),
                                  daemon=self.daemon, name=self.name)
        self._thread = thread
        thread.start()

    def _run(self, incarnation: int, inc_stop, args, kwargs) -> None:
        _local.handle = self
        _local.incarnation = incarnation
        outcome = ("returned", None)
        try:
            self._target(*args, **kwargs)
        except BaseException as error:  # noqa: BLE001 — recorded; the supervisor restarts it
            outcome = ("raised", error)
            self.supervisor.log(f"worker {self.label}: ended by {redacted_error(error)}")
        finally:
            with self._lock:
                if incarnation == self._incarnation:
                    self._exit = outcome
            if inc_stop is not None and self.stop is not None:
                self.supervisor._unlink(self.stop, inc_stop)
            _local.handle = None

    # -- calls from the worker's own thread ----------------------------------------
    def _tick(self, incarnation: int, success: bool) -> None:
        with self._lock:
            if incarnation != self._incarnation:
                return                    # a retired incarnation's late tick proves nothing
            now = self.supervisor.clock()
            self.last_tick = now
            if success:
                self.last_success_tick = now
                self.last_success_at = self.supervisor.wall()
                self._error_since_success = False
            if self.state == "starting" and not self._retired:
                self._set_state("running")

    def _record_error(self, incarnation: int, error) -> None:
        with self._lock:
            if incarnation != self._incarnation:
                return
            self.last_error = redacted_error(error) or None
            self._error_since_success = True
            self.supervisor._changed()

    def _intent(self, incarnation: int, kind: str, reason) -> None:
        with self._lock:
            if incarnation == self._incarnation:
                self._exit_intent = (kind, None if reason is None else str(reason)[:200])

    # -- supervision ----------------------------------------------------------------
    @property
    def label(self) -> str:
        scope = self.recorder_local_id or (self.recorder_id or "")[:8]
        return f"{self.worker}[{scope}]" if scope else self.worker

    def _stopping(self) -> bool:
        return self._joined or (self.stop is not None and self.stop.is_set())

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.supervisor._changed()

    def check(self, now: float) -> None:
        with self._lock:
            if not self._started or self.state in ("disabled", "failed", "completed"):
                return
            stopping = self._stopping()
            if self.is_alive():
                if stopping or self._retired or not self.stall_after:
                    return
                since = self.last_tick if self.last_tick is not None else self.started_at
                if since is not None and now - since > self.stall_after:
                    self.stall_count += 1
                    self._retired = True
                    self.last_error = f"stalled: no progress for {int(now - since)}s"
                    self._set_state("stalled")
                    if self._inc_stop is not None:
                        self._inc_stop.set()   # ask this incarnation to end; restart after it does
                    self.supervisor.log(f"worker {self.label}: {self.last_error}; "
                                        "stopping it for a restart")
                return

            if self.state == "restarting":
                if stopping:
                    self._set_state("completed")
                elif self._next_start_at is not None and now >= self._next_start_at:
                    self.restart_count += 1
                    self._restart_times.append(now)
                    self.supervisor.log(f"worker {self.label}: restarting "
                                        f"(restart {self.restart_count})")
                    self._spawn()
                return

            if self._thread is None:
                return
            kind, error = self._exit or ("returned", None)
            if stopping:
                self._set_state("completed")     # ended with the runtime
                return
            if self._exit_intent is not None and kind == "returned":
                self.reason = self._exit_intent[1]
                if self._exit_intent[0] == "disabled":
                    self.enabled = False
                if self._exit_intent[0] == "failed":
                    self.last_error = self.reason or "worker cannot run"
                    self._error_since_success = True
                self._set_state(self._exit_intent[0])
                return
            if kind == "returned" and self.one_shot:
                self._set_state("completed")
                return

            # Unexpected death, or the end of a stalled incarnation.
            if kind == "raised":
                self.last_error = redacted_error(error) or "worker raised"
            elif not self._retired:
                self.last_error = "worker exited unexpectedly"
            self._error_since_success = True
            if (not self._retired and self.started_at is not None
                    and now - self.started_at >= STABLE_RESET_SECONDS):
                self._consecutive_failures = 0
            self._consecutive_failures += 1
            self._restart_times = [t for t in self._restart_times
                                   if now - t < RESTART_WINDOW_SECONDS]
            if len(self._restart_times) >= self.supervisor.max_restarts_per_hour:
                self._set_state("failed")
                self.supervisor.log(f"worker {self.label}: failed; restarted "
                                    f"{len(self._restart_times)} time(s) in the last hour, "
                                    "not restarting it again in this run")
                return
            delay = min(self.supervisor.backoff_cap,
                        self.supervisor.backoff_base * (2 ** (self._consecutive_failures - 1)))
            self._next_start_at = now + delay
            self._set_state("restarting")
            self.supervisor.log(f"worker {self.label}: not running ({self.last_error}); "
                                f"restarting in {int(delay)}s")

    def snapshot(self, now: float) -> dict:
        with self._lock:
            alive = self.is_alive()
            state = self.state
            healthy = (state == "running" and alive and not self._retired
                       and not self._error_since_success)
            if state == "completed":
                # A worker that finished on purpose is healthy only with proof it succeeded.
                healthy = self.last_success_at is not None and not self._error_since_success
            if healthy and self.stall_after and state == "running":
                healthy = (self.last_tick is not None
                           and now - self.last_tick <= self.stall_after)
            detail = {}
            if self.probe is not None and state not in ("disabled", "completed"):
                try:
                    verdict = self.probe()
                except Exception:  # noqa: BLE001 — an unreadable probe is not evidence
                    verdict = {"healthy": False, "probe": "unavailable"}
                if isinstance(verdict, dict):
                    probe_healthy = bool(verdict.get("healthy"))
                    detail.update({k: v for k, v in verdict.items() if k != "healthy"})
                else:
                    probe_healthy = bool(verdict)
                healthy = healthy and probe_healthy
            if self.stall_after:
                detail["stall_after_s"] = int(self.stall_after)
            if self.last_tick is not None:
                detail["last_tick_age_s"] = int(max(0.0, now - self.last_tick))
            if self.stall_count:
                detail["stall_count"] = self.stall_count
            if self.reason:
                detail["reason"] = self.reason
            if self.recorder_local_id:
                detail["local_id"] = self.recorder_local_id
            return {
                "worker": self.worker,
                "recorder_id": self.recorder_id,
                "state": state,
                "enabled": bool(self.enabled and state != "disabled"),
                "healthy": bool(healthy),
                "critical": self.critical,
                "alive": alive,
                "last_success_at": _iso(self.last_success_at),
                "last_error": self.last_error,
                "restart_count": self.restart_count,
                "detail": detail,
            }

    def fresh_success(self, within: float, now: float) -> bool:
        with self._lock:
            return bool(self.is_alive() and not self._retired
                        and self.state == "running"
                        and self.last_success_tick is not None
                        and now - self.last_success_tick <= within)


class Supervisor:
    """The process's worker registry. ``check()`` restarts dead and stalled workers."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = _utc_now, log: Callable[[str], None] | None = None,
                 backoff_base: float = BACKOFF_BASE_SECONDS,
                 backoff_cap: float = BACKOFF_CAP_SECONDS,
                 max_restarts_per_hour: int = MAX_RESTARTS_PER_HOUR) -> None:
        self.clock = clock
        self.wall = wall
        self._log = log
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.max_restarts_per_hour = int(max_restarts_per_hour)
        self._lock = threading.RLock()
        self._workers: dict[tuple, SupervisedWorker] = {}
        self._fanouts: dict[int, _StopFanout] = {}
        self.version = 0

    def log(self, message: str) -> None:
        try:
            (self._log or _default_log)(message)
        except Exception:  # noqa: BLE001
            pass

    def _changed(self) -> None:
        self.version += 1

    def _link(self, parent: threading.Event, child: threading.Event) -> None:
        with self._lock:
            fanout = self._fanouts.get(id(parent))
            if fanout is None or fanout.parent is not parent:
                fanout = self._fanouts[id(parent)] = _StopFanout(parent)
        fanout.add(child)

    def _unlink(self, parent: threading.Event, child: threading.Event) -> None:
        with self._lock:
            fanout = self._fanouts.get(id(parent))
        if fanout is not None and fanout.parent is parent:
            fanout.discard(child)

    def supervised(self, worker: str, target: Callable, args=(), kwargs=None, *, cfg=None,
                   **options) -> SupervisedWorker:
        """Register a worker. ``policy(worker, cfg)`` supplies criticality, stall window and
        one-shot defaults; explicit options override them."""
        merged = policy(worker, cfg)
        merged.update(options)
        handle = SupervisedWorker(self, worker, target, args, kwargs, **merged)
        with self._lock:
            previous = self._workers.get(handle.key)
            if (previous is not None and previous.is_alive()
                    and not previous._stopping()):
                self.log(f"worker {handle.label}: registered again while an earlier copy "
                         "is still running; supervising the new one")
            self._workers[handle.key] = handle
            self._changed()
        return handle

    def workers(self) -> list[SupervisedWorker]:
        with self._lock:
            return list(self._workers.values())

    def get(self, worker: str, recorder=None) -> SupervisedWorker | None:
        with self._lock:
            return self._workers.get((worker, None if recorder is None else str(recorder)))

    def check(self, now: float | None = None) -> int:
        """Detect dead/stalled workers and restart them when due. Returns the state version."""
        now = self.clock() if now is None else float(now)
        for handle in self.workers():
            try:
                handle.check(now)
            except Exception as error:  # noqa: BLE001 — one worker's bookkeeping never stops the rest
                self.log(f"worker {handle.label}: supervision error "
                         f"{redacted_error(error)}")
        return self.version

    def snapshot(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else float(now)
        rows = []
        for handle in self.workers():
            if not handle._started:
                continue                  # built but never started in this run (e.g. --once)
            rows.append(handle.snapshot(now))
        return rows

    def poller_fresh(self, worker: str, within: float, now: float | None = None) -> bool | None:
        """True when a started site-level ``worker`` is alive, running and succeeded within
        ``within`` seconds; False when it is registered but not; None when not registered."""
        now = self.clock() if now is None else float(now)
        handles = [h for h in self.workers() if h.worker == worker and h._started]
        if not handles:
            return None
        return any(h.fresh_success(within, now) for h in handles)

    def forget_stopped(self) -> None:
        """Drop workers whose runtime has stopped (end of a run), and their stop relays."""
        with self._lock:
            for key, handle in list(self._workers.items()):
                if handle._stopping() and not handle.is_alive():
                    del self._workers[key]
            for key, fanout in list(self._fanouts.items()):
                if fanout.parent.is_set():
                    del self._fanouts[key]
            self._changed()


_DEFAULT = Supervisor()


def default() -> Supervisor:
    return _DEFAULT


def reset_default(**options) -> Supervisor:
    """A fresh process registry (tests, or a clean restart inside one process)."""
    global _DEFAULT
    _DEFAULT = Supervisor(**options)
    return _DEFAULT


def supervised(worker: str, target: Callable, args=(), kwargs=None, **options) -> SupervisedWorker:
    """Register ``target`` with the process supervisor; returns a Thread-like handle."""
    return default().supervised(worker, target, args, kwargs, **options)


# -- calls a worker makes from its own thread (no-ops elsewhere) ------------------------
def _current():
    handle = getattr(_local, "handle", None)
    return handle, getattr(_local, "incarnation", 0)


def tick() -> None:
    """The worker's loop is alive (call once per loop iteration)."""
    handle, incarnation = _current()
    if handle is not None:
        handle._tick(incarnation, success=False)


def success() -> None:
    """The worker just did its job successfully (e.g. a cloud poll answered)."""
    handle, incarnation = _current()
    if handle is not None:
        handle._tick(incarnation, success=True)


def record_error(error) -> None:
    """A fault the worker caught and survived; the worker is unhealthy until its next success."""
    handle, incarnation = _current()
    if handle is not None:
        handle._record_error(incarnation, error)


def complete(reason: str | None = None) -> None:
    """The worker is about to return on purpose: report it completed, never restart it."""
    handle, incarnation = _current()
    if handle is not None:
        handle._intent(incarnation, "completed", reason)


def disable(reason: str | None = None) -> None:
    """The worker is about to return because it is turned off here: report it disabled."""
    handle, incarnation = _current()
    if handle is not None:
        handle._intent(incarnation, "disabled", reason)


def fail(reason: str | None = None) -> None:
    """The worker is about to return because it cannot run here at all (restarting it would
    not help): report it failed at once, with the reason, and never restart it."""
    handle, incarnation = _current()
    if handle is not None:
        handle._intent(incarnation, "failed", reason)


__all__ = [
    "CRITICAL_WORKERS", "KNOWN_WORKERS", "ONE_SHOT_WORKERS", "STATES", "Supervisor",
    "SupervisedWorker", "complete", "default", "disable", "fail", "policy", "record_error",
    "redacted_error", "reset_default", "stall_after", "success", "supervised", "tick",
]

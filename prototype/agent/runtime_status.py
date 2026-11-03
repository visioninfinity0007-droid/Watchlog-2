"""Agent runtime status and capability reporting to the cloud (5.1.2).

Two reporters run from the Agent's main loop (the loop that already heartbeats), never from
a worker thread, so neither depends on analytics being enabled or alive:

* ``CapabilityReporter`` calls ``wl_agent_report_capabilities`` on start, every
  ``CAPABILITY_REPORT_SECONDS`` and as soon as the list changes. ``site_control_runtime``
  and ``remote_update_v1`` are advertised only while their pollers are alive in the worker
  registry and recently succeeded (``analytics_agent.runtime_capabilities``).
* ``RuntimeStatusReporter`` calls ``wl_report_agent_runtime`` (migration 0162) every
  ``STATUS_REPORT_SECONDS`` and immediately when a worker's state or health, or a
  recorder's event stream or login availability, changes. A server without 0162
  (PGRST202 / 42883 / HTTP 404) turns reporting off for the rest of the run.

The payload (``build_payload``) is the cloud's evidence that each worker is alive and
healthy: worker rows from the supervisor, one row per recorder (event stream up/down/unknown,
login unavailable, queue depth, upload degraded) and agent-level fields (queue depth and
capacity, queue overflow, last successful event upload and heartbeat). It carries identity
and states only: no address, login, display name or unredacted error text.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

import worker_supervisor

STATUS_REPORT_SECONDS = 60.0
STATUS_MIN_GAP_SECONDS = 5.0          # change-triggered reports are at most this frequent
CAPABILITY_REPORT_SECONDS = 300.0
CAPABILITY_MIN_GAP_SECONDS = 15.0
CAPABILITY_RETRY_SECONDS = 60.0
RECORDER_LIVE_SECONDS = 150.0
MAX_WORKER_ROWS = 200
MAX_RECORDER_ROWS = 64
MAX_DETAIL_KEYS = 12
SCHEMA = "agent_runtime_status_v1"


def _log(message: str) -> None:
    try:
        import agent_core
        agent_core.log(message)
    except Exception:  # noqa: BLE001
        pass


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def rpc_missing(error: BaseException) -> bool:
    """The server has no such RPC (older database): PostgREST PGRST202, SQLSTATE 42883."""
    code = str(getattr(error, "code", "") or "")
    status = getattr(error, "status", None)
    if code in ("PGRST202", "42883") or status == 404:
        return True
    text = str(error)
    return "PGRST202" in text or "42883" in text


def _recorder_live(holder: dict, clock: float) -> bool:
    driver = holder.get("live_driver")
    activity = float(getattr(driver, "last_activity_monotonic", 0.0) or 0.0)
    seen = float(holder.get("recorder_live_at") or 0.0)
    latest = max(activity, seen)
    return bool(latest and clock - latest < RECORDER_LIVE_SECONDS)


def event_stream_state(holder: dict | None, clock: float | None = None) -> str:
    """'up' only on recent event-stream activity (evidence); 'down' when the stream reported
    a drop or an error, or was live before and went quiet; otherwise 'unknown'."""
    holder = holder or {}
    clock = time.monotonic() if clock is None else clock
    if _recorder_live(holder, clock):
        return "up"
    stream = holder.get("event_stream") or {}
    if (stream.get("connected") is False or stream.get("last_error")
            or holder.get("recorder_live_at")):
        return "down"
    return "unknown"


def collector_probe(holder: dict, clock: Callable[[], float] = time.monotonic) -> Callable:
    """Evidence for the collector worker: its recorder's event stream is live."""
    def probe() -> dict:
        state = event_stream_state(holder, clock())
        return {"healthy": state == "up", "event_stream": state}
    return probe


class RecorderSource:
    """What the reporter reads for one recorder: its cloud id, holder, config and queue."""

    def __init__(self, recorder_id=None, holder: dict | None = None, cfg=None, spool=None) -> None:
        self.recorder_id = None if recorder_id in (None, "") else str(recorder_id)
        self.holder = holder if holder is not None else {}
        self.cfg = cfg
        self.spool = spool


def _spool_facts(spool) -> dict:
    out = {"spool_depth": None, "spool_capacity": None, "spool_overflow": None}
    if spool is None:
        return out
    try:
        out["spool_depth"] = int(spool.count())
    except Exception:  # noqa: BLE001 — unreadable queue: depth unknown, never zero
        pass
    try:
        out["spool_capacity"] = int(getattr(spool, "max_rows", 0) or 0) or None
    except Exception:  # noqa: BLE001
        pass
    try:
        out["spool_overflow"] = spool.pending_recovery_gap() is not None
    except Exception:  # noqa: BLE001
        pass
    return out


def _credential_unavailable(source: RecorderSource) -> bool:
    cfg = source.cfg
    return bool(source.holder.get("credential_unavailable")
                or getattr(cfg, "credential_error", None)
                or (getattr(cfg, "legacy_credential_error", None)
                    and not getattr(cfg, "recorder_local_id", None)))


def recorder_row(source: RecorderSource, clock: float) -> dict:
    holder = source.holder
    stream = holder.get("event_stream") or {}
    facts = _spool_facts(source.spool)
    error = stream.get("last_error")
    detail = {}
    if error:
        detail["event_stream_error"] = worker_supervisor.redacted_error(str(error))
    if stream.get("last_frame_at"):
        detail["last_frame_at"] = _iso(stream.get("last_frame_at"))
    local_id = getattr(source.cfg, "recorder_local_id", None)
    if local_id:
        detail["local_id"] = str(local_id)
    return {
        "recorder_id": source.recorder_id,
        "event_stream": event_stream_state(holder, clock),
        "credential_unavailable": _credential_unavailable(source),
        "spool_depth": facts["spool_depth"],
        "spool_overflow": facts["spool_overflow"],
        "upload_degraded": bool(holder.get("upload_degraded")),
        "detail": detail,
    }


def _bounded_detail(detail) -> dict:
    if not isinstance(detail, dict):
        return {}
    out = {}
    for key in list(detail)[:MAX_DETAIL_KEYS]:
        value = detail[key]
        if isinstance(value, str):
            value = value[:200]
        elif not isinstance(value, (int, float, bool)) and value is not None:
            value = str(value)[:200]
        out[str(key)[:64]] = value
    return out


def build_payload(supervisor: worker_supervisor.Supervisor, recorders: Iterable[RecorderSource],
                  *, clock: float | None = None, cloud_proof: dict | None = None) -> dict:
    """The ``p_status`` document for wl_report_agent_runtime (bounded, redacted)."""
    import wl_version

    clock = time.monotonic() if clock is None else clock
    workers = []
    for row in supervisor.snapshot(clock)[:MAX_WORKER_ROWS]:
        workers.append({
            "worker": row["worker"],
            "recorder_id": row["recorder_id"],
            "state": row["state"],
            "enabled": row["enabled"],
            "healthy": row["healthy"],
            "critical": row["critical"],
            "last_success_at": row["last_success_at"],
            "last_error": (worker_supervisor.redacted_error(row["last_error"])
                           if row["last_error"] else None),
            "restart_count": int(row["restart_count"]),
            "detail": _bounded_detail(row["detail"]),
        })
    recorder_rows = [recorder_row(source, clock) for source in list(recorders)[:MAX_RECORDER_ROWS]]

    depths = [r["spool_depth"] for r in recorder_rows]
    capacities = []
    for source in recorders:
        try:
            capacity = int(getattr(source.spool, "max_rows", 0) or 0)
        except (TypeError, ValueError):
            capacity = 0
        if capacity:
            capacities.append(capacity)
    proof = cloud_proof or {}
    critical = [w for w in workers if w["critical"] and w["enabled"]]
    agent = {
        # Unknown (None) when any recorder's queue could not be read: never a made-up zero.
        "spool_depth": (sum(depths) if depths and all(d is not None for d in depths) else None),
        "spool_capacity": sum(capacities) if capacities else None,
        "spool_overflow": any(bool(r["spool_overflow"]) for r in recorder_rows),
        "cloud_upload_last_success_at": _iso(proof.get("upload_ok_at")),
        "heartbeat_last_success_at": _iso(proof.get("heartbeat_ok_at")),
        "workers_total": len(workers),
        "workers_unhealthy": sum(1 for w in workers if w["enabled"] and not w["healthy"]
                                 and w["state"] != "completed"),
        "critical_unhealthy": sum(1 for w in critical if not w["healthy"]),
        "healthy": bool(critical) and all(w["healthy"] for w in critical),
    }
    return {
        "schema": SCHEMA,
        "agent_version": str(getattr(wl_version, "VERSION", "") or "")[:40],
        "workers": workers,
        "recorders": recorder_rows,
        "agent": agent,
    }


def change_signature(supervisor: worker_supervisor.Supervisor,
                     recorders: Iterable[RecorderSource], clock: float) -> tuple:
    rows = tuple(sorted(
        (r["worker"], r["recorder_id"] or "", r["state"], r["healthy"])
        for r in supervisor.snapshot(clock)))
    recs = tuple(sorted(
        (s.recorder_id or "", event_stream_state(s.holder, clock), _credential_unavailable(s))
        for s in recorders))
    return rows, recs


class RuntimeStatusReporter:
    def __init__(self, cloud, state: dict, supervisor: worker_supervisor.Supervisor,
                 recorders: Callable[[], list], *, interval: float = STATUS_REPORT_SECONDS,
                 cloud_proof: dict | None = None) -> None:
        self.cloud = cloud
        self.state = state
        self.supervisor = supervisor
        self.recorders = recorders
        self.interval = float(interval)
        self.cloud_proof = cloud_proof
        self.disabled = False
        self.next_due = 0.0
        self.last_attempt = None
        self.last_failure = None
        self.signature = None
        self.reports = 0

    def maybe_report(self, clock: float) -> bool:
        if self.disabled:
            return False
        sources = list(self.recorders() or [])
        signature = change_signature(self.supervisor, sources, clock)
        changed = self.signature is not None and signature != self.signature
        due = clock >= self.next_due
        if changed and self.last_attempt is not None and clock - self.last_attempt < STATUS_MIN_GAP_SECONDS:
            changed = False               # flapping: the next due/settled report carries it
        if changed and self.last_failure is not None and clock - self.last_failure < self.interval:
            changed = False               # cloud unreachable: never stall the run loop on retries
        if not (due or changed):
            return False
        self.last_attempt = clock
        self.next_due = clock + self.interval
        payload = build_payload(self.supervisor, sources, clock=clock,
                                cloud_proof=self.cloud_proof)
        try:
            self.cloud.call("wl_report_agent_runtime", p_agent_id=self.state["agent_id"],
                            p_agent_key=self.state["agent_key"], p_status=payload)
        except Exception as error:  # noqa: BLE001 — reporting never disturbs monitoring
            if rpc_missing(error):
                self.disabled = True
                _log("runtime status: this WatchLog server does not accept runtime status "
                     "yet; reporting is off for this run")
            else:
                self.last_failure = clock
                _log("runtime status: report failed, will retry: "
                     + worker_supervisor.redacted_error(error))
            return False
        self.last_failure = None
        self.signature = signature
        self.reports += 1
        return True


class CapabilityReporter:
    def __init__(self, cloud, state: dict, compute: Callable[[], list], *,
                 interval: float = CAPABILITY_REPORT_SECONDS) -> None:
        self.cloud = cloud
        self.state = state
        self.compute = compute
        self.interval = float(interval)
        self.next_due = 0.0
        self.last_attempt = None
        self.last_sent = None
        self.reports = 0

    def maybe_report(self, clock: float) -> bool:
        caps = sorted(set(self.compute() or []))
        changed = self.last_sent is not None and caps != self.last_sent
        if changed and self.last_attempt is not None and clock - self.last_attempt < CAPABILITY_MIN_GAP_SECONDS:
            changed = False
        if not (clock >= self.next_due or changed):
            return False
        self.last_attempt = clock
        try:
            self.cloud.call("wl_agent_report_capabilities", p_agent_id=self.state["agent_id"],
                            p_agent_key=self.state["agent_key"], p_capabilities=caps)
        except Exception as error:  # noqa: BLE001 — an older server or an outage: retry later
            self.next_due = clock + CAPABILITY_RETRY_SECONDS
            _log("capabilities: report failed, will retry: "
                 + worker_supervisor.redacted_error(error))
            return False
        self.next_due = clock + self.interval
        self.last_sent = caps
        self.reports += 1
        return True


class RuntimeMonitor:
    """The main loop's one call per iteration: supervise, then report capabilities/status."""

    def __init__(self, cloud, state: dict, cfg, recorders: Callable[[], list], *,
                 supervisor: worker_supervisor.Supervisor | None = None,
                 capabilities: Callable[[], list] | None = None,
                 cloud_proof: dict | None = None) -> None:
        self.supervisor = supervisor or worker_supervisor.default()
        if cloud_proof is None:
            try:
                import agent_core
                cloud_proof = agent_core.CLOUD_PROOF
            except Exception:  # noqa: BLE001
                cloud_proof = {}
        if capabilities is None:
            def capabilities():
                import analytics_agent
                return analytics_agent.runtime_capabilities(cfg, self.supervisor)
        # capabilities=False: a runtime that never advertised capabilities (the bare
        # prototype loop) must not overwrite them with an empty list.
        self.capabilities = (CapabilityReporter(cloud, state, capabilities)
                             if capabilities is not False else None)
        self.status = RuntimeStatusReporter(cloud, state, self.supervisor, recorders,
                                            cloud_proof=cloud_proof)

    def step(self, clock: float | None = None) -> None:
        clock = time.monotonic() if clock is None else clock
        for action in (lambda: self.supervisor.check(clock),
                       lambda: (self.capabilities.maybe_report(clock)
                                if self.capabilities is not None else None),
                       lambda: self.status.maybe_report(clock)):
            try:
                action()
            except Exception as error:  # noqa: BLE001 — never stops the run loop
                _log("runtime monitor: " + worker_supervisor.redacted_error(error))


__all__ = [
    "CapabilityReporter", "RecorderSource", "RuntimeMonitor", "RuntimeStatusReporter",
    "build_payload", "collector_probe", "event_stream_state", "recorder_row", "rpc_missing",
]

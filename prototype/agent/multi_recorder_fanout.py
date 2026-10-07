"""True multi-recorder worker fan-out.

Site-level singletons:
- Agent heartbeat / coverage monitor
- analytics authority + recorder-aware analytics sampler
- archive analytics poller
- Site Control poller
- incident evidence / remote update remain outer release wrappers

Recorder-scoped workers:
- live collector + event spool
- camera/NVR health + durable health store
- archive recovery (only after explicit recorder channel sync)
- periodic stills (5.0.28 NEW-L2), stamped with the recorder's recorder_id

This module is activated only after multi_recorder_orchestrator has bound EVERY
configured local recorder to a cloud recorder UUID under contract v4.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import periodic_stills
import site_maintenance
import watchlog_agent as core
from spool import Spool

# A recorder whose login cannot be read on this PC is held, never contacted with an
# empty login; its credential file is re-checked this often for a repair by Setup.
CREDENTIAL_RECHECK_SECONDS = 30.0


class LockedDetector:
    """Serialize access to one packaged detector shared across recorder workers."""

    def __init__(self, detector):
        self._detector = detector
        self._lock = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._detector, name)

    def detect(self, *args, **kwargs):
        with self._lock:
            return self._detector.detect(*args, **kwargs)

    def classify_event(self, *args, **kwargs):
        with self._lock:
            return self._detector.classify_event(*args, **kwargs)


@dataclass
class RecorderWorkers:
    prepared: object
    cfg: object
    channels: list[dict]
    spool: object
    holder: dict
    resume_evt: threading.Event
    collector: threading.Thread
    health: threading.Thread
    recovery: threading.Thread | None
    # Set only for a recorder held for its login (MNVR-009): its workers wait on
    # credential_ready, which credential_watch sets once Setup has repaired it.
    credential_ready: threading.Event | None = None
    credential_watch: threading.Thread | None = None
    # This recorder's periodic still producer (run mode only).
    stills: threading.Thread | None = None


def _channel_rows(prepared) -> list[dict]:
    """Attach canonical camera IDs to the recorder's explicitly synced channels."""
    mapping = prepared.camera_mapping if isinstance(prepared.camera_mapping, dict) else {}
    rows = []
    for row in prepared.channels or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        channel = str(item.get("channel") or "")
        if not channel:
            continue
        camera_id = mapping.get(channel)
        if camera_id:
            item["camera_id"] = str(camera_id)
        rows.append(item)
    return rows


def _fresh(holder: dict, clock: float | None = None) -> bool:
    now = time.monotonic() if clock is None else float(clock)
    drv = holder.get("live_driver")
    activity = float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0)
    seen = float(holder.get("recorder_live_at") or 0.0)
    return bool(
        (activity and now - activity < 150.0)
        or (seen and now - seen < 150.0)
    )


def _seed_inventory(holder: dict, item, cfg) -> list[dict]:
    """Seed a recorder's holder with the camera inventory its preflight synced."""
    import camera_health

    channels = _channel_rows(item)
    mon_channels = [str(c["channel"]) for c in channels if c.get("channel")]
    holder["camera_mapping"] = (
        {str(k): str(v) for k, v in item.camera_mapping.items()}
        if isinstance(item.camera_mapping, dict) else {}
    )
    holder["synced_channels"] = list(channels) if holder["camera_mapping"] else []
    holder["camera_sync_signature"] = (
        tuple(sorted(str(c["channel"]) for c in channels if c.get("channel")))
        if holder["camera_mapping"] else None
    )
    if holder.get("monitor") is None:
        holder["monitor"] = (
            camera_health.CameraHealthMonitor(
                mon_channels,
                batch_size=cfg.health_batch,
                concurrency=cfg.health_concurrency,
            )
            if mon_channels else None
        )
    return channels


def _adopt_late_probe(item, holder: dict, cfg) -> None:
    """A preflight probe that finished after the workers started (MNVR-021).

    The health cycle may already have synced this recorder's cameras after a
    reconnect; that newer inventory is kept."""
    if holder.get("camera_mapping") or not isinstance(item.camera_mapping, dict):
        return
    _seed_inventory(holder, item, cfg)
    core.log(f"multi-recorder: {getattr(cfg, 'recorder_display_name', 'recorder')} "
             "preflight finished; camera inventory ready")


def _upload_fault(error: BaseException) -> str:
    """A recorder's local queue fault, for the log and runtime health: the exception type
    and its redacted first line (no address, no login)."""
    import nvr_health
    detail = nvr_health.redact(str(error))
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _mark_upload_degraded(unit, error: BaseException) -> None:
    """This recorder's queue could not be read or uploaded: hold it degraded, keep the others.

    A corrupt queued row or a local database fault repeats every cycle, so it is logged when
    it first appears or changes, not every time."""
    reason = _upload_fault(error)
    if unit.holder.get("upload_degraded") != reason:
        core.log(f"ERROR: recorder {str(unit.cfg.recorder_cloud_id)[:8]} cannot upload its "
                 f"queued events ({reason}); the other recorders keep uploading")
    unit.holder["upload_degraded"] = reason


def _upload_unit(cloud, state: dict, unit) -> None:
    """Upload one recorder's queue. No fault in it ever leaves this function (contract §8)."""
    if unit.spool is None:
        return                     # its queue never opened: build_worker_sets marked it
    try:
        core.upload_once(cloud, state, unit.spool)
    except (RuntimeError, requests.RequestException) as error:
        core.log(
            f"ERROR: recorder {str(unit.cfg.recorder_cloud_id)[:8]} "
            "upload failed, will retry: "
            + str(error).splitlines()[0][:180]
        )
        return
    except Exception as error:  # noqa: BLE001 — a corrupt row or sqlite fault is this recorder's
        _mark_upload_degraded(unit, error)
        return
    if unit.holder.pop("upload_degraded", None):
        core.log(f"recorder {str(unit.cfg.recorder_cloud_id)[:8]} is uploading its queued "
                 "events again")


def _queued(units) -> int:
    total = 0
    for unit in units:
        try:
            total += unit.spool.count() if unit.spool is not None else 0
        except Exception:  # noqa: BLE001 — a count is only reported
            pass
    return total


def _idle(*_args, **_kwargs) -> None:
    """Stands in for a worker that needs the recorder's queue when that queue cannot open."""


def _gated(target, ready: threading.Event, stop: threading.Event):
    """Run ``target`` only once the recorder's login is readable."""
    def run(*args, **kwargs):
        while not ready.wait(1.0):
            if stop.is_set():
                return
        if not stop.is_set():
            target(*args, **kwargs)
    return run


def _watch_credential(cfg, holder: dict, ready: threading.Event,
                      stop: threading.Event) -> None:
    """Hold one recorder until Setup repairs its unreadable login, then release it.

    The recorder stays UNVERIFIED meanwhile: nothing connects to it and nothing is
    reported for it. Only a changed credential file is decrypted again."""
    seen = getattr(cfg, "credential_generation_seen", None)
    while not stop.wait(CREDENTIAL_RECHECK_SECONDS):
        try:
            generation = core._credential_generation_for_cfg(cfg)
            if generation == seen:
                continue
            seen = generation
            core._reload_credential_for_cfg(cfg)
        except Exception:  # noqa: BLE001 — still unreadable; keep holding
            continue
        cfg.credential_generation_seen = generation
        cfg.credential_error = None
        holder.pop("credential_unavailable", None)
        core.log(f"multi-recorder: {getattr(cfg, 'recorder_display_name', 'recorder')} "
                 "login is readable again; monitoring it now")
        ready.set()
        return


def build_worker_sets(prepared_recorders, state: dict, cloud,
                      stop: threading.Event) -> list[RecorderWorkers]:
    """Build but do not start one recorder-scoped worker set per recorder."""
    out = []
    cloud_ids = []
    for item in prepared_recorders:
        cfg = item.context.config
        recorder_id = str(getattr(cfg, "recorder_cloud_id", "") or "")
        if not recorder_id:
            raise RuntimeError("prepared recorder is missing cloud recorder identity")
        cloud_ids.append(recorder_id)

        Path(cfg.spool_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.health_store_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.last_live_path).parent.mkdir(parents=True, exist_ok=True)

        holder = item.context.holder
        holder["recorder_cloud_id"] = recorder_id
        holder["monitor"] = None
        channels = _seed_inventory(holder, item, cfg)
        if getattr(item, "pending", False) and callable(getattr(item, "on_ready", None)):
            # Its preflight probe is still running in its own thread (MNVR-021).
            item.on_ready(lambda done, h=holder, c=cfg: _adopt_late_probe(done, h, c))

        # A recorder whose login is unreadable is bound but held (MNVR-009).
        credential_ready = credential_watch = None
        collector_target = core.collector
        health_target = core.health_worker
        recovery_target = core.recovery_worker
        stills_target = periodic_stills.periodic_still_worker
        if getattr(cfg, "credential_error", None):
            holder["credential_unavailable"] = True
            credential_ready = threading.Event()
            credential_watch = threading.Thread(
                target=_watch_credential,
                args=(cfg, holder, credential_ready, stop),
                daemon=True,
                name=f"credential-{recorder_id[:8]}",
            )
            collector_target = _gated(collector_target, credential_ready, stop)
            health_target = _gated(health_target, credential_ready, stop)
            recovery_target = _gated(recovery_target, credential_ready, stop)
            stills_target = _gated(stills_target, credential_ready, stop)

        try:
            spool = Spool(cfg.spool_path, cfg.spool_max_rows)
        except Exception as error:  # noqa: BLE001 — one recorder's queue never stops the others
            # Without a queue its events, recovered intervals and stills have nowhere to go:
            # those workers stand idle and it is reported degraded; health still runs.
            spool = None
            holder["upload_degraded"] = _upload_fault(error)
            core.log(f"ERROR: recorder {recorder_id[:8]} event queue cannot be opened "
                     f"({holder['upload_degraded']}); the other recorders keep running")
            collector_target = recovery_target = stills_target = _idle
        resume_evt = threading.Event()
        collector = threading.Thread(
            target=collector_target,
            args=(cfg, spool, stop, holder),
            daemon=True,
            name=f"collector-{recorder_id[:8]}",
        )
        health = threading.Thread(
            target=health_target,
            args=(cfg, state, cloud, holder, stop, resume_evt),
            daemon=True,
            name=f"health-{recorder_id[:8]}",
        )

        # Always create the recovery worker, but feed it the CURRENT explicitly
        # synced channel set. If preflight saw zero cameras, recovery waits
        # fail-closed until health_cycle later syncs inventory after reconnect.
        recovery = threading.Thread(
            target=recovery_target,
            args=(
                cfg, state, cloud, stop, spool,
                (lambda h=holder: h.get("synced_channels") or []),
                holder,
            ),
            daemon=True,
            name=f"recovery-{recorder_id[:8]}",
        )
        # One periodic still producer per recorder: its own driver, spool, credential and
        # back-off, sampling by this recorder's own camera choices.
        still_profiles = periodic_stills.recorder_camera_profiles(
            cfg, continuity_owner=bool(getattr(item.context, "continuity_owner", False)))
        # The collector's in-stream sampler (Hikvision) uses the same camera choices.
        holder["still_profiles"] = still_profiles
        stills = threading.Thread(
            target=stills_target,
            args=(cfg, spool, stop, channels),
            kwargs={
                "profiles": still_profiles,
                "label": getattr(cfg, "recorder_display_name", None) or recorder_id[:8],
            },
            daemon=True,
            name=f"periodic-stills-{recorder_id[:8]}",
        )
        out.append(RecorderWorkers(
            prepared=item,
            cfg=cfg,
            channels=channels,
            spool=spool,
            holder=holder,
            resume_evt=resume_evt,
            collector=collector,
            health=health,
            recovery=recovery,
            credential_ready=credential_ready,
            credential_watch=credential_watch,
            stills=stills,
        ))

    if len(cloud_ids) != len(set(cloud_ids)):
        _close(out)
        raise RuntimeError("duplicate cloud recorder identity in worker fan-out")
    return out


def _primary_device(units):
    for unit in units:
        if getattr(unit.prepared.context, "is_primary", False):
            return unit.prepared.device
    return units[0].prepared.device if units else None


def _recorder_live_state(units, clock: float, stamp: str) -> tuple[int, list[dict]]:
    """(live count, per-recorder rows) for the protected runtime-health proof (MNVR-040).

    ``last_live_at`` is the last heartbeat at which that recorder's event stream was live,
    the same rule as the site's recorder_seen_at. Rows carry identity only: no address,
    login or display name."""
    live_count, rows = 0, []
    for unit in units:
        # A recorder whose queued events cannot be uploaded is not live: its events do not
        # reach WatchLog, so it must not advance the Repair/Upgrade proof.
        live = _fresh(unit.holder, clock) and not unit.holder.get("upload_degraded")
        if live:
            live_count += 1
            unit.holder["live_seen_at"] = stamp
        ctx = unit.prepared.context
        row = {
            "local_id": (getattr(ctx, "local_id", None)
                         or getattr(unit.cfg, "recorder_local_id", None)),
            "recorder_id": str(unit.cfg.recorder_cloud_id),
            "continuity_owner": bool(getattr(ctx, "continuity_owner", False)),
            "live": live,
            "last_live_at": unit.holder.get("live_seen_at"),
        }
        if unit.holder.get("credential_unavailable"):
            row["credential"] = "unavailable"
        if unit.holder.get("upload_degraded"):
            row["upload"] = "degraded"
            row["upload_reason"] = unit.holder["upload_degraded"]
        # This recorder's own event-stream state, in the single-recorder heartbeat's shape
        # (redacted error, ONVIF counters): which recorder's stream is down, and why.
        stream = core._event_stream_health(unit.holder.get("event_stream"))
        if stream is not None:
            row["event_stream"] = stream
        rows.append(row)
    return live_count, rows


def _write_live_marker(unit) -> None:
    """Per-recorder last_live marker while recovery is off (MNVR-040).

    With recovery on, the outage-aware writer keeps it. With recovery off nothing opens
    outage intervals from it, so it is simply the recorder's latest live time: the
    Repair/Upgrade gate reads it as that recorder's proof."""
    import recovery
    recovery.persist_last_live(unit.cfg.last_live_path, core.now_utc())


def _close(units) -> None:
    for unit in units:
        try:
            if unit.spool is not None:
                unit.spool.close()
        except Exception:
            pass
        store = unit.holder.get("store")
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def _stream_last_live_writer():
    """The shipped loop's last_live rule (analytics_agent._persist_stream_last_live)."""
    import analytics_agent
    return analytics_agent._persist_stream_last_live


def run(base_cfg, state: dict, cloud, *, once: bool, prepared_recorders,
        detector, analytics_worker, archive_worker, last_live_writer=None,
        recorder_check=None) -> None:
    """Run the site with independent recorder workers under one site authority.

    ``last_live_writer(cfg, holder, clock)`` keeps one recorder's last_live.json; it defaults
    to the single-recorder loop's event-stream rule.

    ``recorder_check(stop, restart)`` finishes in the background a recorder binding startup
    could not complete (the site started from its saved bindings while WatchLog was
    unreachable, MNVR-009). Setting ``restart["reason"]`` ends the run loop for a clean
    start, which fails closed."""
    import monitoring_coverage as coverage

    if len(prepared_recorders) < 2:
        raise RuntimeError("multi-recorder fan-out requires at least two prepared recorders")
    persist_last_live = last_live_writer or _stream_last_live_writer()

    stop = threading.Event()
    units = build_worker_sets(prepared_recorders, state, cloud, stop)

    original_build = core.vision.build
    shared_detector = LockedDetector(detector) if detector is not None else None
    core.vision.build = lambda _cfg, _log: shared_detector

    authority = {"ok": True}
    analytic = threading.Thread(
        target=analytics_worker,
        args=(base_cfg, state, shared_detector, stop, authority),
        daemon=True,
        name="analytics-site",
    )
    archive = threading.Thread(
        target=archive_worker,
        args=(base_cfg, state, stop, authority, shared_detector),
        daemon=True,
        name="archive-site",
    )
    sitectl = threading.Thread(
        target=core.command_worker,
        args=(base_cfg, state, cloud, stop),
        daemon=True,
        name="sitecontrol-site",
    )

    restart = {}
    checker = (threading.Thread(target=recorder_check, args=(stop, restart),
                                daemon=True, name="recorder-check")
               if recorder_check is not None and not once else None)

    try:
        for unit in units:
            unit.collector.start()
            if unit.credential_watch is not None:
                unit.credential_watch.start()
                core.log(
                    f"multi-recorder: {unit.cfg.recorder_display_name} login cannot be "
                    "read on this PC; it stays unverified until WatchLog Setup repairs it"
                )
        analytic.start()

        if once:
            core.log(
                f"multi-recorder: collecting from {len(units)} recorder(s) for "
                f"{core.ONCE_COLLECT_SECONDS}s..."
            )
            stop.wait(core.ONCE_COLLECT_SECONDS)
            stop.set()
            for unit in units:
                unit.collector.join(timeout=5)
            analytic.join(timeout=5)
            for unit in units:
                _upload_unit(cloud, state, unit)
                if unit.credential_ready is None or unit.credential_ready.is_set():
                    core.health_cycle(cloud, state, unit.cfg, unit.holder)
            live_count, recorder_rows = _recorder_live_state(
                units, time.monotonic(), core.iso(core.now_utc()))
            core.heartbeat(
                cloud, state, _primary_device(units),
                recorder_live=(live_count == len(units)),
            )
            core.update_runtime_health(
                recorders_total=len(units),
                recorders_live=live_count,
                multi_recorder=True,
                recorders=recorder_rows,
            )
            return

        for unit in units:
            unit.health.start()
            unit.recovery.start()
            if unit.stills is not None:
                unit.stills.start()
            if not unit.holder.get("synced_channels") and unit.prepared.error:
                core.log(
                    f"multi-recorder: {unit.cfg.recorder_display_name} "
                    "recovery waiting for explicit camera inventory sync"
                )

        archive.start()
        sitectl.start()
        if checker is not None:
            checker.start()

        cov = coverage.CoverageMonitor(loop_period=1.0)
        core.log(
            f"running: {len(units)} recorder worker set(s), one site authority, "
            "outbound only. Ctrl-C to stop."
        )
        next_upload = next_heartbeat = 0.0
        last_wall = time.time()

        while True:
            if restart.get("reason"):
                core.log(f"recorder: {restart['reason']}")
                raise SystemExit(restart["reason"])
            site_maintenance.check_restart()             # restart_agent (Site Control)
            clock = time.monotonic()
            now_wall = time.time()
            gap = cov.tick(last_wall, now_wall)
            last_wall = now_wall
            if gap is not None:
                core.log(
                    f"resume: site not observed for ~{int(gap.ended_at-gap.started_at)}s "
                    "(site PC sleep/suspend); reconciling every recorder now"
                )
                for unit in units:
                    unit.resume_evt.set()
            cov.report_pending(cloud, state)

            if clock >= next_upload:
                next_upload = clock + base_cfg.upload_seconds
                if not authority.get("ok", True):
                    queued = _queued(units)
                    core.log(
                        f"events: standby (not lease authority); holding {queued} event(s)"
                    )
                else:
                    for unit in units:
                        _upload_unit(cloud, state, unit)

            if clock >= next_heartbeat:
                next_heartbeat = clock + base_cfg.heartbeat_seconds
                live_count, recorder_rows = _recorder_live_state(
                    units, clock, core.iso(core.now_utc()))
                try:
                    # recorder_seen_at in the legacy local runtime-health document
                    # advances only when the complete configured recorder set is live.
                    core.heartbeat(
                        cloud, state, _primary_device(units),
                        recorder_live=(live_count == len(units)),
                    )
                except (RuntimeError, requests.RequestException) as error:
                    core.log(
                        "ERROR: heartbeat failed, will retry: "
                        + str(error).splitlines()[0][:200]
                    )
                # Each recorder's live state goes into the protected runtime-health proof
                # at every heartbeat, cloud reachable or not (MNVR-040).
                core.update_runtime_health(
                    recorders_total=len(units),
                    recorders_live=live_count,
                    multi_recorder=True,
                    recorders=recorder_rows,
                )

                # Per-recorder outage clocks. A failed B never freezes A. Each moves only
                # with that recorder's event-stream activity (never a probe), to the time of
                # that activity, and never over an outage its recovery has not opened yet.
                # With recovery off the marker is still kept, as that recorder's live proof.
                for unit, row in zip(units, recorder_rows):
                    try:
                        if getattr(unit.cfg, "recovery_enabled", False):
                            persist_last_live(unit.cfg, unit.holder, clock)
                        elif row["live"]:
                            _write_live_marker(unit)
                    except Exception:
                        pass

            time.sleep(1)

    except KeyboardInterrupt:
        core.log("stopping...")
    finally:
        stop.set()
        for unit in units:
            unit.resume_evt.set()
        for unit in units:
            unit.collector.join(timeout=5)
            if unit.health.is_alive():
                unit.health.join(timeout=5)
            if unit.recovery.is_alive():
                unit.recovery.join(timeout=5)
            if unit.stills is not None and unit.stills.is_alive():
                unit.stills.join(timeout=5)
            if unit.credential_watch is not None and unit.credential_watch.is_alive():
                unit.credential_watch.join(timeout=5)
        if checker is not None and checker.is_alive():
            checker.join(timeout=5)
        if analytic.is_alive():
            analytic.join(timeout=5)
        if archive.is_alive():
            archive.join(timeout=5)
        if sitectl.is_alive():
            sitectl.join(timeout=5)
        _close(units)
        core.vision.build = original_build
        core.log("stopped")

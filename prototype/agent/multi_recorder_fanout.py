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

This module is activated only after multi_recorder_orchestrator has bound EVERY
configured local recorder to a cloud recorder UUID under contract v4.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import watchlog_agent as core
from spool import Spool


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


def build_worker_sets(prepared_recorders, state: dict, cloud,
                      stop: threading.Event) -> list[RecorderWorkers]:
    """Build but do not start one recorder-scoped worker set per recorder."""
    import camera_health

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

        channels = _channel_rows(item)
        mon_channels = [str(c["channel"]) for c in channels if c.get("channel")]
        holder = item.context.holder
        holder["recorder_cloud_id"] = recorder_id
        holder["monitor"] = (
            camera_health.CameraHealthMonitor(
                mon_channels,
                batch_size=cfg.health_batch,
                concurrency=cfg.health_concurrency,
            )
            if mon_channels else None
        )

        spool = Spool(cfg.spool_path, cfg.spool_max_rows)
        resume_evt = threading.Event()
        collector = threading.Thread(
            target=core.collector,
            args=(cfg, spool, stop, holder),
            daemon=True,
            name=f"collector-{recorder_id[:8]}",
        )
        health = threading.Thread(
            target=core.health_worker,
            args=(cfg, state, cloud, holder, stop, resume_evt),
            daemon=True,
            name=f"health-{recorder_id[:8]}",
        )

        # Recovery requires explicit recorder channels. A recorder that was
        # unreachable during preflight still gets live+health retry workers, but
        # recovery must not guess a channel until a later explicit sync exists.
        recovery = None
        if channels and item.camera_mapping:
            recovery = threading.Thread(
                target=core.recovery_worker,
                args=(cfg, state, cloud, stop, spool, channels, holder),
                daemon=True,
                name=f"recovery-{recorder_id[:8]}",
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
        ))

    if len(cloud_ids) != len(set(cloud_ids)):
        for unit in out:
            unit.spool.close()
        raise RuntimeError("duplicate cloud recorder identity in worker fan-out")
    return out


def _primary_device(units):
    for unit in units:
        if getattr(unit.prepared.context, "is_primary", False):
            return unit.prepared.device
    return units[0].prepared.device if units else None


def _close(units) -> None:
    for unit in units:
        try:
            unit.spool.close()
        except Exception:
            pass
        store = unit.holder.get("store")
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def run(base_cfg, state: dict, cloud, *, once: bool, prepared_recorders,
        detector, analytics_worker, archive_worker) -> None:
    """Run the site with independent recorder workers under one site authority."""
    import monitoring_coverage as coverage

    if len(prepared_recorders) < 2:
        raise RuntimeError("multi-recorder fan-out requires at least two prepared recorders")

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

    primary_device = _primary_device(units)

    try:
        for unit in units:
            unit.collector.start()
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
                try:
                    core.upload_once(cloud, state, unit.spool)
                except RuntimeError as error:
                    core.log(
                        f"ERROR: recorder {str(unit.cfg.recorder_cloud_id)[:8]} "
                        f"upload failed: {error}"
                    )
                core.health_cycle(cloud, state, unit.cfg, unit.holder)
            live_count = sum(1 for unit in units if _fresh(unit.holder))
            core.heartbeat(
                cloud, state, primary_device,
                recorder_live=(live_count == len(units)),
            )
            core.update_runtime_health(
                recorders_total=len(units),
                recorders_live=live_count,
                multi_recorder=True,
            )
            return

        for unit in units:
            unit.health.start()
            if unit.recovery is not None:
                unit.recovery.start()
            elif unit.prepared.error:
                core.log(
                    f"multi-recorder: {unit.context.display_name if hasattr(unit, 'context') else unit.cfg.recorder_display_name} "
                    "recovery deferred until camera inventory is explicitly synced"
                )

        archive.start()
        sitectl.start()

        cov = coverage.CoverageMonitor(loop_period=1.0)
        core.log(
            f"running: {len(units)} recorder worker set(s), one site authority, "
            "outbound only. Ctrl-C to stop."
        )
        next_upload = next_heartbeat = 0.0
        last_wall = time.time()

        while True:
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
                    queued = sum(unit.spool.count() for unit in units)
                    core.log(
                        f"events: standby (not lease authority); holding {queued} event(s)"
                    )
                else:
                    for unit in units:
                        try:
                            core.upload_once(cloud, state, unit.spool)
                        except (RuntimeError, requests.RequestException) as error:
                            core.log(
                                f"ERROR: recorder {str(unit.cfg.recorder_cloud_id)[:8]} "
                                "upload failed, will retry: "
                                + str(error).splitlines()[0][:180]
                            )

            if clock >= next_heartbeat:
                next_heartbeat = clock + base_cfg.heartbeat_seconds
                live_count = sum(1 for unit in units if _fresh(unit.holder, clock))
                try:
                    # recorder_seen_at in the legacy local runtime-health document
                    # advances only when the complete configured recorder set is live.
                    core.heartbeat(
                        cloud, state, primary_device,
                        recorder_live=(live_count == len(units)),
                    )
                    core.update_runtime_health(
                        recorders_total=len(units),
                        recorders_live=live_count,
                        multi_recorder=True,
                    )
                except (RuntimeError, requests.RequestException) as error:
                    core.log(
                        "ERROR: heartbeat failed, will retry: "
                        + str(error).splitlines()[0][:200]
                    )

                # Per-recorder outage clocks. A failed B never freezes A.
                if base_cfg.recovery_enabled:
                    for unit in units:
                        if not _fresh(unit.holder, clock):
                            continue
                        try:
                            import recovery as recovery_mod
                            recovery_mod.persist_last_live(
                                unit.cfg.last_live_path, core.now_utc()
                            )
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
            if unit.recovery is not None and unit.recovery.is_alive():
                unit.recovery.join(timeout=5)
        if analytic.is_alive():
            analytic.join(timeout=5)
        if archive.is_alive():
            archive.join(timeout=5)
        if sitectl.is_alive():
            sitectl.join(timeout=5)
        _close(units)
        core.vision.build = original_build
        core.log("stopped")

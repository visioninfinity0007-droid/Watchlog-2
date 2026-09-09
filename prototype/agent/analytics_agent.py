#!/usr/bin/env python3
"""WatchLog Site Agent with Analytics Studio runtime.

The proven recorder/event collector remains in watchlog_agent.py. This entrypoint
adds an outbound-only analytics worker and the analytics-aware first-run setup.
Analytics rules are pulled from WatchLog, cached locally, evaluated against
on-site inference, and uploaded through a separate durable spool.
"""

from __future__ import annotations

import base64
import configparser
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests
from PIL import Image

import analytics
import analytics_setup
import watchlog_agent as core
from action_runtime import ActionRuntime
from archive_runtime import ArchiveRuntime
from drivers import DriverError
from lease_client import LeaseClient
from runtime import AgentRuntime
from spool import Spool

from wl_version import VERSION as AGENT_VERSION  # single source of truth (was a stale 0.3.0 that overrode core)
ANALYTICS_UPLOAD_BATCH = 500
STATUS_WRITE_SECONDS = 30
SNAPSHOT_REQUESTS_PER_POLL = 2
ARCHIVE_POLL_SECONDS = 120                        # background historical scan; lower priority than live
ARCHIVE_BACKEND_MISSING_RETRY_SECONDS = 600       # 0055 not deployed -> idle, retry rarely
# What THIS runtime can execute. Advertised to the server (0059) so it never treats a feature as
# usable before a compatible agent reports it. This is runtime capability, NOT field-proven hardware.
RUNTIME_CAPABILITIES = ["operations_runtime", "operations_extended_primitives",
                        "operations_evidence_still", "operations_evidence_clip",
                        "archive_processing", "multi_agent_fencing", "recorder_probe_v2"]


class Config(core.Config):
    def __init__(self):
        super().__init__()
        section = {}
        ini = configparser.ConfigParser()
        ini_path = core.base_dir() / "watchlog.ini"
        if ini_path.exists():
            try:
                ini.read(ini_path, encoding="utf-8-sig")
                if ini.has_section("watchlog"):
                    section = dict(ini.items("watchlog"))
            except configparser.Error:
                pass

        def get(key, default=None):
            return os.environ.get("WATCHLOG_" + key.upper()) or section.get(key) or default

        state_dir = self.state_path.parent
        self.analytics_enabled = str(get("analytics", "true")).strip().lower() \
            not in ("0", "false", "no", "off")
        self.analytics_config_path = Path(get("analytics_config_file") or
                                          state_dir / "analytics_config.json")
        self.analytics_spool_path = Path(get("analytics_spool_file") or
                                         state_dir / "analytics_spool.sqlite")
        self.analytics_bootstrap_marker = Path(get("analytics_bootstrap_marker") or
                                                state_dir / "analytics_bootstrap_sent.json")
        self.analytics_status_path = Path(get("analytics_status_file") or
                                          state_dir / "analytics_status.json")
        self.analytics_poll_seconds = max(10, int(get("analytics_poll_seconds") or 30))
        self.analytics_upload_seconds = max(5, int(get("analytics_upload_seconds") or 15))
        try:
            self.analytics_max_fps = min(15.0, max(0.25,
                float(get("analytics_max_fps") or 4.0)))
        except (TypeError, ValueError):
            self.analytics_max_fps = 4.0
        self.bootstrap_site_type = str(get("site_type") or "").strip()
        try:
            raw_profiles = get("camera_profiles_json") or "[]"
            self.bootstrap_camera_profiles = json.loads(raw_profiles)
            if not isinstance(self.bootstrap_camera_profiles, list):
                self.bootstrap_camera_profiles = []
        except (TypeError, json.JSONDecodeError):
            self.bootstrap_camera_profiles = []


class FairSampler:
    """One-at-a-time fair scheduler with a hard aggregate inference cap."""

    def __init__(self, max_fps: float):
        self.max_fps = max(0.25, float(max_fps))
        self.next_due = {}
        self.last_global = 0.0

    def reset(self):
        self.next_due.clear()
        self.last_global = 0.0

    def effective_plan(self, plan):
        active = max(1, len(plan))
        fair_floor = active / self.max_fps
        return [(str(channel), float(requested), max(float(requested), fair_floor))
                for channel, requested in plan]

    def choose(self, plan, now):
        if not plan:
            return None
        if now - self.last_global < 1.0 / self.max_fps:
            return None
        effective = self.effective_plan(plan)
        active_channels = {row[0] for row in effective}
        for old in list(self.next_due):
            if old not in active_channels:
                del self.next_due[old]
        due = [row for row in effective if now >= self.next_due.get(row[0], 0.0)]
        if not due:
            return None
        due.sort(key=lambda row: (self.next_due.get(row[0], 0.0), row[0]))
        channel, requested, interval = due[0]
        self.next_due[channel] = now + interval
        self.last_global = now
        return channel, requested, interval

    def summary(self, plan):
        effective = self.effective_plan(plan)
        if not effective:
            return {"active_cameras": 0, "requested_min_seconds": None,
                    "effective_max_seconds": None, "quality": "idle"}
        requested = min(row[1] for row in effective)
        effective_max = max(row[2] for row in effective)
        if effective_max <= 2.0:
            quality = "full"
        elif effective_max <= 5.0:
            quality = "reduced"
        else:
            quality = "capacity_limited"
        return {
            "active_cameras": len(effective),
            "requested_min_seconds": round(requested, 2),
            "effective_max_seconds": round(effective_max, 2),
            "max_inferences_per_second": round(self.max_fps, 2),
            "quality": quality,
        }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def analytics_upload_once(cloud: core.Cloud, state: dict, spool: Spool) -> int:
    ids, rows = spool.take(ANALYTICS_UPLOAD_BATCH)
    if not ids:
        return 0
    res = cloud.call("wl_ingest_analytic_events",
                     p_agent_id=state["agent_id"],
                     p_agent_key=state["agent_key"],
                     p_events=rows)
    spool.ack(ids)
    core.log("analytics: uploaded "
             f"{res.get('received', 0)} measurements, "
             f"{res.get('inserted', 0)} new, "
             f"{res.get('promoted_incidents', 0)} promoted; "
             f"{spool.count()} queued")
    return int(res.get("inserted") or 0)


def _open_analytics_driver(cfg):
    driver, info = core.open_driver(cfg)
    core.log(f"analytics: recorder ready via {driver.name}: "
             f"{info.vendor} {info.model or ''}".rstrip())
    return driver


def _send_bootstrap_once(cloud, state, cfg):
    """Send site/camera classifications captured by setup exactly once."""
    if cfg.analytics_bootstrap_marker.exists():
        return False
    if not cfg.bootstrap_site_type and not cfg.bootstrap_camera_profiles:
        _write_json_atomic(cfg.analytics_bootstrap_marker, {
            "site_id": state.get("site_id"), "skipped": True,
            "reason": "no installer analytics profile",
        })
        return False
    res = cloud.call("wl_agent_bootstrap_analytics",
                     p_agent_id=state["agent_id"],
                     p_agent_key=state["agent_key"],
                     p_site_type=cfg.bootstrap_site_type or "custom",
                     p_camera_profiles=cfg.bootstrap_camera_profiles)
    _write_json_atomic(cfg.analytics_bootstrap_marker, {
        "site_id": state.get("site_id"),
        "sent_at": core.iso(core.now_utc()),
        "version": res.get("version"),
        "updated_cameras": res.get("updated_cameras", 0),
    })
    core.log("analytics: installer profile synced: "
             f"site={cfg.bootstrap_site_type or 'custom'}, "
             f"{res.get('updated_cameras', 0)} camera(s)")
    return True


def _service_snapshot_requests(cloud, state, driver, requests_list):
    completed = 0
    for request_row in (requests_list or [])[:SNAPSHOT_REQUESTS_PER_POLL]:
        channel = str(request_row.get("channel") or "")
        camera_id = request_row.get("camera_id")
        if not channel or not camera_id:
            continue
        try:
            raw = driver.get_snapshot(channel)
            if not raw:
                core.log(f"analytics: config snapshot ch{channel} returned no image")
                continue
            if len(raw) > core.SNAPSHOT_MAX_BYTES:
                core.log(f"analytics: config snapshot ch{channel} too large; skipped")
                continue
            cloud.call("wl_upload_config_snapshot",
                       p_agent_id=state["agent_id"],
                       p_agent_key=state["agent_key"],
                       p_camera_id=camera_id,
                       p_image_b64=base64.b64encode(raw).decode("ascii"),
                       p_content_type="image/jpeg")
            completed += 1
            core.log(f"analytics: uploaded configuration still for ch{channel}")
        except Exception as error:
            core.log(f"analytics: configuration still ch{channel} failed: "
                     f"{type(error).__name__}: {str(error)[:120]}")
    return completed


def _status_payload(version, sampler, plan, spool, counters, detector):
    summary = sampler.summary(plan)
    summary.update({
        "agent_version": AGENT_VERSION,
        "config_version": int(version or 0),
        "updated_at": core.iso(core.now_utc()),
        "measurements_queued": spool.count(),
        "samples_ok": counters["samples_ok"],
        "sample_errors": counters["sample_errors"],
        "last_sample_at": counters.get("last_sample_at"),
        "last_config_at": counters.get("last_config_at"),
        "detector_available": bool(detector is not None and
                                   getattr(detector, "available", True)),
    })
    return summary


def analytics_worker(cfg: Config, state: dict, detector,
                     stop: threading.Event, authority: dict = None) -> None:
    """Long-running local analytics sampler. Never opens an inbound socket.

    Owns the single-authority lease (refreshes it on the config cadence) and publishes the
    resulting authority into the shared `authority` holder, so the event-upload loop and the
    archive worker can fence their authoritative writes on the SAME lease without any
    cross-thread lease mutation (this thread is the only one that calls refresh/validate)."""
    if not cfg.analytics_enabled:
        core.log("analytics: disabled by configuration")
        return

    cloud = core.Cloud(cfg.supabase_url, cfg.publishable_key)
    spool = Spool(cfg.analytics_spool_path)
    cached = analytics.load_config(cfg.analytics_config_path)
    version = int(cached.get("version") or 0)
    engine = analytics.AnalyticsEngine(log=core.log)
    engine.configure(cached.get("config"))
    # The top-level orchestration seam: the engine's per-frame output flows through the
    # runtime, which dispatches configured evidence actions (idempotent + restart-safe) and
    # fences authoritative uploads behind the single-authority lease. Multi-agent stays OFF
    # unless the site opts in (the flag rides in the config payload, 0056), so by default the
    # lease is trivially authoritative and behaviour is unchanged. Evidence (capture_still/
    # request_footage) is SERVER-authorized when the incident is emitted and fulfilled by the
    # evidence workers (incident_evidence.py); the frame-time runtime only acknowledges it.
    lease = LeaseClient(cloud, state["agent_id"], state["agent_key"],
                        feature_enabled=bool((cached.get("config") or {}).get("multi_agent_enabled")),
                        log=core.log)
    actions = ActionRuntime(log=core.log)
    runtime = AgentRuntime(cloud=cloud, state=state, engine=engine, lease=lease, actions=actions,
                           dedup_path=cfg.analytics_config_path.parent / "analytics_action_dedup.json",
                           log=core.log)
    sampler = FairSampler(cfg.analytics_max_fps)
    counters = {"samples_ok": 0, "sample_errors": 0,
                "last_sample_at": None, "last_config_at": None}
    if cached.get("config"):
        core.log(f"analytics: loaded cached config v{version}")

    driver = None
    next_config = 0.0
    next_upload = 0.0
    next_status = 0.0
    warned_detector = False
    last_capacity_signature = None

    try:
        while not stop.is_set():
            clock = time.monotonic()

            if clock >= next_config:
                next_config = clock + cfg.analytics_poll_seconds
                try:
                    if _send_bootstrap_once(cloud, state, cfg):
                        version = 0
                    payload = cloud.call("wl_agent_analytics_config",
                                         p_agent_id=state["agent_id"],
                                         p_agent_key=state["agent_key"],
                                         p_known_version=version)
                    counters["last_config_at"] = core.iso(core.now_utc())
                    # The single-authority lease flag rides on every poll (0056), even when the
                    # config version is unchanged, so a fencing toggle takes effect within one
                    # cycle. Feature OFF -> refresh() is a no-op and stays authoritative.
                    lease.set_feature_enabled(bool(payload.get("multi_agent_enabled")))
                    runtime.refresh_lease()
                    # publish the single-authority verdict for the event/archive fences
                    if authority is not None:
                        authority["ok"] = lease.is_authoritative()
                    # advertise what this runtime can execute; an older server (no 0059) or a
                    # transient error just leaves the capability unadvertised -> server treats it
                    # as unsupported, which is the safe default.
                    try:
                        cloud.call("wl_agent_report_capabilities", p_agent_id=state["agent_id"],
                                   p_agent_key=state["agent_key"], p_capabilities=RUNTIME_CAPABILITIES)
                    except (RuntimeError, requests.RequestException):
                        pass
                    if payload.get("changed") and payload.get("config"):
                        version = int(payload.get("version") or version)
                        analytics.save_config(cfg.analytics_config_path, version,
                                              payload["config"])
                        engine.configure(payload["config"])
                        sampler.reset()
                        core.log(f"analytics: config updated to v{version}; "
                                 f"{len(engine.sample_plan())} camera(s) active")
                    snapshot_requests = payload.get("snapshot_requests") or []
                    if snapshot_requests:
                        if driver is None:
                            driver = _open_analytics_driver(cfg)
                        _service_snapshot_requests(
                            cloud, state, driver, snapshot_requests)
                except (RuntimeError, requests.RequestException, DriverError) as error:
                    core.log("analytics: config poll failed; cached rules remain active: "
                             + str(error).splitlines()[0][:180])
                except Exception as error:
                    core.log(f"analytics: config error {type(error).__name__}: "
                             f"{str(error)[:160]}")

            plan = engine.sample_plan()
            capacity = sampler.summary(plan)
            capacity_signature = (capacity.get("active_cameras"),
                                  capacity.get("effective_max_seconds"),
                                  capacity.get("quality"))
            if capacity_signature != last_capacity_signature:
                last_capacity_signature = capacity_signature
                if plan:
                    core.log("analytics: sampling capacity: "
                             f"{capacity['active_cameras']} camera(s), cap "
                             f"{cfg.analytics_max_fps:g} fps, max effective interval "
                             f"{capacity['effective_max_seconds']}s, quality={capacity['quality']}")

            if plan and detector is None:
                if not warned_detector:
                    warned_detector = True
                    core.log("analytics: AI detector unavailable; measurement sampling paused. "
                             "Incident collection continues with its normal fail-open policy.")
            elif plan:
                choice = sampler.choose(plan, clock)
                if choice:
                    channel, _requested, _effective = choice
                    try:
                        if driver is None:
                            driver = _open_analytics_driver(cfg)
                        raw = driver.get_snapshot(channel)
                        if raw:
                            found = detector.detect(raw)
                            if found is not None:
                                image = Image.open(io.BytesIO(raw))
                                image.load()
                                when = core.now_utc()
                                # on_frame runs the engine AND dispatches configured evidence
                                # actions for exception firings (idempotent + cooldown-guarded).
                                events = runtime.on_frame(
                                    channel, found, image.size, when)
                                for event in events:
                                    spool.add(event)
                                dropped = spool.trim()
                                if dropped:
                                    core.log("analytics: spool capacity exceeded; "
                                             f"dropped {dropped} oldest measurements")
                                counters["samples_ok"] += 1
                                counters["last_sample_at"] = core.iso(when)
                    except (DriverError, requests.RequestException) as error:
                        counters["sample_errors"] += 1
                        core.log(f"analytics: sampler ch{channel} failed: "
                                 f"{str(error)[:140]}")
                        if driver is not None:
                            try:
                                driver.close()
                            except Exception:
                                pass
                            driver = None
                    except Exception as error:
                        counters["sample_errors"] += 1
                        core.log(f"analytics: sampler ch{channel} error: "
                                 f"{type(error).__name__}: {str(error)[:140]}")

            if clock >= next_upload:
                next_upload = clock + cfg.analytics_upload_seconds
                # Fence the authoritative analytic-event write. With multi-agent OFF this is
                # always authoritative; ON, only the lease holder uploads — a superseded/standby
                # agent holds its spool (no loss, no duplicate incidents) until it regains authority.
                # authoritative() re-validates the fencing generation, so republish it here (15s
                # cadence) to keep the event/archive fences current between config polls.
                auth_ok = runtime.authoritative()
                if authority is not None:
                    authority["ok"] = auth_ok
                if not auth_ok:
                    core.log("analytics: standby (not lease authority); "
                             f"holding {spool.count()} measurement(s), upload deferred")
                else:
                    try:
                        analytics_upload_once(cloud, state, spool)
                    except (RuntimeError, requests.RequestException) as error:
                        core.log("analytics: upload failed, will retry: "
                                 + str(error).splitlines()[0][:180])

            if clock >= next_status:
                next_status = clock + STATUS_WRITE_SECONDS
                try:
                    _write_json_atomic(
                        cfg.analytics_status_path,
                        _status_payload(version, sampler, plan, spool,
                                        counters, detector))
                except OSError as error:
                    core.log(f"analytics: status write failed: {error}")

            stop.wait(0.1)
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
        try:
            stopped = _status_payload(version, sampler, engine.sample_plan(),
                                      spool, counters, detector)
            stopped["stopped_at"] = core.iso(core.now_utc())
            _write_json_atomic(cfg.analytics_status_path, stopped)
        except OSError:
            pass
        spool.close()


def _archive_backend_missing(error: Exception) -> bool:
    text = str(error).lower()
    return "wl_agent_claim_archive_scans" in text and (
        "schema cache" in text or "function" in text or "404" in text)


def archive_worker(cfg: Config, state: dict, stop: threading.Event,
                   authority: dict = None) -> None:
    """Background historical-scan workload (migrations 0051/0055).

    Runs in its own daemon thread on a slow cadence so it is LOWER priority than live
    monitoring: bounded to one scan per cycle, interruptible via the shared stop event, and
    it never blocks the collector or the analytics sampler. Idle (long back-off) when 0055 is
    not deployed. Recorder-archive retrieval is not hardware-validated, so the runtime reports
    a claimed scan with an honest status ('failed: retrieval unavailable') rather than ever
    fabricating a recovered result. Recovered candidates carry the fixed 0051 provenance label.
    """
    if not cfg.analytics_enabled:
        return
    cloud = core.Cloud(cfg.supabase_url, cfg.publishable_key)
    # Retrieval + offline analysis are injected as unavailable until a future agent release
    # proves recorder playback on real hardware; the runtime then fails scans honestly.
    archive = ArchiveRuntime(cloud=cloud, state=state,
                             retrieve_frames=None, analyze=None, log=core.log)
    runtime = AgentRuntime(cloud=cloud, state=state,
                           engine=analytics.AnalyticsEngine(log=core.log),
                           archive=archive, log=core.log)
    missing_logged = False
    while not stop.is_set():
        # Archive reprocessing is an authoritative write; only the lease holder runs it. A
        # standby stays idle here (no duplicate recovered results) until it becomes authority.
        if authority is not None and not authority.get("ok", True):
            stop.wait(ARCHIVE_POLL_SECONDS)
            continue
        try:
            processed = runtime.poll_archive(limit=1)   # bounded: at most one scan per cycle
            missing_logged = False
            for row in processed:
                core.log(f"analytics: archive scan {str(row.get('scan_id', '?'))[:8]} "
                         f"-> {row.get('status')} ({row.get('candidates', 0)} candidate(s))")
        except (RuntimeError, requests.RequestException) as error:
            if _archive_backend_missing(error):
                if not missing_logged:
                    core.log("analytics: archive execution backend (0055) not deployed; worker idle")
                    missing_logged = True
                stop.wait(ARCHIVE_BACKEND_MISSING_RETRY_SECONDS)
                continue
            core.log("analytics: archive poll failed; will retry: "
                     + str(error).splitlines()[0][:160])
        except Exception as error:                      # noqa: BLE001 — never kill the thread
            core.log(f"analytics: archive error {type(error).__name__}: {str(error)[:140]}")
        stop.wait(ARCHIVE_POLL_SECONDS)


def enhanced_cmd_run(cfg: Config, state: dict, cloud: core.Cloud, once: bool,
                     device=None) -> None:
    """Core event loop plus analytics worker, sharing one detector instance."""
    original_build = core.vision.build
    detector = original_build(cfg, core.log)
    core.vision.build = lambda _cfg, _log: detector

    spool = Spool(cfg.spool_path)
    core.log(f"spool: {cfg.spool_path} ({spool.count()} queued)")

    # Shared single-authority signal: the analytics worker owns the lease and publishes its
    # verdict here; the event-upload loop and the archive worker fence their authoritative
    # writes on it. Defaults to authoritative (multi-agent OFF -> unchanged single-agent).
    authority = {"ok": True}

    stop = threading.Event()
    collector = threading.Thread(target=core.collector,
                                 args=(cfg, spool, stop),
                                 daemon=True, name="collector")
    analytic = threading.Thread(target=analytics_worker,
                                args=(cfg, state, detector, stop, authority),
                                daemon=True, name="analytics")
    archive = threading.Thread(target=archive_worker,
                               args=(cfg, state, stop, authority),
                               daemon=True, name="archive")
    collector.start()
    analytic.start()

    if once:
        core.log(f"collecting for {core.ONCE_COLLECT_SECONDS}s...")
        stop.wait(core.ONCE_COLLECT_SECONDS)
        stop.set()
        collector.join(timeout=5)
        analytic.join(timeout=5)
        try:
            core.upload_once(cloud, state, spool)
        except RuntimeError as error:
            core.log(f"ERROR: upload failed: {error}")
        core.heartbeat(cloud, state, device)
        spool.close()
        core.vision.build = original_build
        return

    archive.start()   # background historical scan; lower priority, run mode only
    core.log(f"running: events every {cfg.upload_seconds}s, analytics enabled, "
             f"heartbeat every {cfg.heartbeat_seconds}s, outbound only. Ctrl-C to stop.")
    next_upload = next_heartbeat = 0.0
    try:
        while True:
            clock = time.monotonic()
            if clock >= next_upload:
                next_upload = clock + cfg.upload_seconds
                # Fence the authoritative event write (native recorder events + incidents) on the
                # single-authority lease. A standby holds its spool — no duplicate events — until
                # it becomes authority. Heartbeat below is deliberately NOT fenced: a standby must
                # keep signalling liveness to stay eligible to take the lease over.
                if not authority.get("ok", True):
                    core.log(f"events: standby (not lease authority); holding "
                             f"{spool.count()} event(s), upload deferred")
                else:
                    try:
                        core.upload_once(cloud, state, spool)
                    except (RuntimeError, requests.RequestException) as error:
                        core.log("ERROR: upload failed, will retry: "
                                 + str(error).splitlines()[0][:200])
            if clock >= next_heartbeat:
                next_heartbeat = clock + cfg.heartbeat_seconds
                try:
                    core.heartbeat(cloud, state, device)
                except (RuntimeError, requests.RequestException) as error:
                    core.log("ERROR: heartbeat failed, will retry: "
                             + str(error).splitlines()[0][:200])
            time.sleep(1)
    except KeyboardInterrupt:
        core.log("stopping...")
    finally:
        stop.set()
        collector.join(timeout=5)
        analytic.join(timeout=5)
        archive.join(timeout=5)
        spool.close()
        core.vision.build = original_build
        core.log("stopped")


def _cleanup_analytics(cfg):
    paths = (
        cfg.analytics_config_path,
        cfg.analytics_spool_path,
        cfg.analytics_bootstrap_marker,
        cfg.analytics_status_path,
        cfg.analytics_spool_path.with_name(cfg.analytics_spool_path.name + "-wal"),
        cfg.analytics_spool_path.with_name(cfg.analytics_spool_path.name + "-shm"),
    )
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                core.log(f"deleted {path}")
        except OSError as error:
            core.log(f"WARNING: could not delete {path}: {error}")


def main():
    core.AGENT_VERSION = AGENT_VERSION
    core.Config = Config
    core.cmd_run = enhanced_cmd_run
    core.setup_wizard.run = analytics_setup.run

    was_reset = "--reset" in sys.argv
    was_status = "--status" in sys.argv
    core.main()

    if was_reset or was_status:
        cfg = Config()
        if was_reset:
            _cleanup_analytics(cfg)
        elif was_status:
            cached = analytics.load_config(cfg.analytics_config_path)
            core.log(f"analytics  config v{cached.get('version', 0)} at "
                     f"{cfg.analytics_config_path}")
            core.log("analytics  installer profile " +
                     ("synced" if cfg.analytics_bootstrap_marker.exists() else "pending"))
            if cfg.analytics_status_path.exists():
                try:
                    status = json.loads(cfg.analytics_status_path.read_text(encoding="utf-8"))
                    core.log("analytics  sampling "
                             f"{status.get('quality', 'unknown')} | "
                             f"{status.get('active_cameras', 0)} camera(s) | "
                             f"{status.get('effective_max_seconds', '-')}s max interval")
                except (OSError, json.JSONDecodeError):
                    pass
            if cfg.analytics_spool_path.exists():
                analytics_spool = Spool(cfg.analytics_spool_path)
                core.log(f"analytics  {analytics_spool.count()} measurements queued at "
                         f"{cfg.analytics_spool_path}")
                analytics_spool.close()


if __name__ == "__main__":
    main()

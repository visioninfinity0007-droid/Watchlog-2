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
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

import analytics
import analytics_setup
import watchlog_agent as core
import recorder_runtime
import recorder_registry
import recorder_analytics
import multi_recorder_orchestrator
import multi_recorder_fanout
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
REGISTRY_RECHECK_SECONDS = 60                     # held on an unusable recorders.json
RECORDER_RECHECK_SECONDS = (30, 60, 120, 300)     # background recorder check; last repeats
# What THIS runtime can execute. Advertised to the server (0059) so it never treats a feature as
# usable before a compatible agent reports it. This is runtime capability, NOT field-proven hardware.
RUNTIME_CAPABILITIES = ["operations_runtime", "operations_extended_primitives",
                        "operations_evidence_still", "operations_evidence_clip",
                        "archive_processing", "multi_agent_fencing", "recorder_probe_v2",
                        "config_snapshot_requests"]

def runtime_capabilities(cfg) -> list[str]:
    caps = list(RUNTIME_CAPABILITIES)
    now = time.monotonic()

    site_poll = getattr(cfg, "site_control_last_poll_monotonic", None)
    site_fresh_for = max(60.0, float(getattr(cfg, "site_control_seconds", 15)) * 3.0)
    if (getattr(cfg, "site_control_enabled", False)
            and site_poll is not None and now - float(site_poll) <= site_fresh_for):
        caps.append("site_control_runtime")

    update_ready = bool(getattr(cfg, "update_url", "")) and (
        not getattr(cfg, "update_require_signature", True)
        or bool(getattr(cfg, "update_public_key", ""))
    )
    update_poll = getattr(cfg, "remote_update_last_poll_monotonic", None)
    if (update_ready and update_poll is not None
            and now - float(update_poll) <= max(90.0, 3.0 * 30.0)):
        caps.append("remote_update_v1")
    return caps


# Recorder liveness window, the same one the recovery worker uses for recorder_is_live.
RECORDER_LIVE_SECONDS = 150.0


def _recorder_stream_live(holder: dict, clock: float) -> bool:
    """True while the recorder's event stream showed activity in the last 150 s.

    Hikvision and Dahua drivers stamp last_activity_monotonic on every frame, keep-alives
    included, and on a 2xx answer only while that stream stays open (a 200 that ends before
    any chunk is taken back); the collector carries the last activity into recorder_live_at
    when it drops a driver. A recorder whose probe
    answers while its event stream is down is therefore NOT live: recorder_seen_at (the
    Repair/Upgrade proof) does not advance. Drivers that cannot report their stream keep
    the collector's transport stamp."""
    driver = holder.get("live_driver")
    activity = float(getattr(driver, "last_activity_monotonic", 0.0) or 0.0)
    latest = max(activity, float(holder.get("recorder_live_at") or 0.0))
    return bool(latest and clock - latest < RECORDER_LIVE_SECONDS)


def _stream_seen_at(stream: dict | None) -> datetime | None:
    """Wall time of the event stream's latest activity, or None when the driver reports
    none. The 2xx answer (connected_at) counts only while that stream is still open: a
    stream that ended without a frame must not move last_live forward."""
    stream = stream or {}
    keys = ("connected_at", "last_frame_at") if stream.get("connected") else ("last_frame_at",)
    stamps = []
    for key in keys:
        raw = stream.get(key)
        if raw:
            try:
                stamps.append(datetime.fromisoformat(str(raw)))
            except ValueError:
                pass
    return max(stamps) if stamps else None


def _persist_stream_last_live(cfg, holder: dict, clock: float) -> None:
    """Keep last_live.json at the recorder's latest event-stream activity.

    This loop replaces core.cmd_run, which was the only periodic writer, so without this
    last_live was never written and restart, reboot and outage gaps never opened recovery.
    It is written only while the event stream is live, with the time of its last activity:
    never from a probe, and never for a driver that cannot report its stream. With no file
    yet this seeds it and opens no interval. A stored value older than the outage
    threshold is a gap recovery_worker has not opened yet; it opens it and then moves
    last_live on itself, so overwriting it here would erase the outage."""
    if not getattr(cfg, "recovery_enabled", False) or not _recorder_stream_live(holder, clock):
        return
    seen = _stream_seen_at(holder.get("event_stream"))
    if seen is None:
        return
    import recovery
    try:
        last_live = recovery.read_last_live(cfg.last_live_path)
        if last_live is not None and (seen <= last_live or recovery.detect_outage(
                last_live, seen, cfg.recovery_threshold_seconds)):
            return
        recovery.persist_last_live(cfg.last_live_path, seen)
    except Exception as error:                      # noqa: BLE001 — never stop the loop
        core.log(f"recovery: last_live not persisted ({type(error).__name__})")


class Config(core.Config):
    def __init__(self, *args, **kwargs):
        # Preserve the core Config constructor contract. Existing-site staged
        # preflight passes an explicit installed watchlog.ini path plus
        # read_only_credentials=True through the frozen production entrypoint.
        super().__init__(*args, **kwargs)
        section = {}
        ini = configparser.ConfigParser()
        ini_path = getattr(self, "_ini_path", core.base_dir() / "watchlog.ini")
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


def _service_snapshot_requests(cloud, state, cfg, driver, requests_list):
    completed = 0
    for request_row in (requests_list or [])[:SNAPSHOT_REQUESTS_PER_POLL]:
        channel = str(request_row.get("channel") or "")
        camera_id = request_row.get("camera_id")
        if not channel or not camera_id:
            continue
        job_driver = None
        close_job_driver = False
        try:
            job_cfg = recorder_runtime.config_for_cloud_recorder(
                cfg, request_row.get("recorder_id")
            )
            if job_cfg is cfg and driver is not None:
                job_driver = driver
            else:
                job_driver = _open_analytics_driver(job_cfg)
                close_job_driver = True

            raw = job_driver.get_snapshot(channel)
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
        finally:
            if close_job_driver and job_driver is not None:
                try:
                    job_driver.close()
                except Exception:
                    pass
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
    # Control engine/runtime owns the one site-level lease. Recorder-specific
    # inference state lives in RecorderAnalyticsMux below.
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
    mux = recorder_analytics.RecorderAnalyticsMux(
        cloud=cloud, state=state, lease=lease, actions=actions,
        dedup_dir=cfg.analytics_config_path.parent, log=core.log)
    mux.configure(cached.get("config"))
    sampler = FairSampler(cfg.analytics_max_fps)
    counters = {"samples_ok": 0, "sample_errors": 0,
                "last_sample_at": None, "last_config_at": None}
    if cached.get("config"):
        core.log(f"analytics: loaded cached config v{version}")

    drivers = {}
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
                                   p_agent_key=state["agent_key"], p_capabilities=runtime_capabilities(cfg))
                    except (RuntimeError, requests.RequestException):
                        pass
                    if payload.get("changed") and payload.get("config"):
                        version = int(payload.get("version") or version)
                        analytics.save_config(cfg.analytics_config_path, version,
                                              payload["config"])
                        engine.configure(payload["config"])
                        mux.configure(payload["config"])
                        sampler.reset()
                        # Config changes may add/move cameras across recorders. Close
                        # cached transports so every next sample resolves fresh identity.
                        for _drv in list(drivers.values()):
                            try:
                                _drv.close()
                            except Exception:
                                pass
                        drivers.clear()
                        core.log(f"analytics: config updated to v{version}; "
                                 f"{mux.camera_count} camera(s) active")
                    snapshot_requests = payload.get("snapshot_requests") or []
                    if snapshot_requests:
                        # Each request resolves its own recorder. A dead primary
                        # recorder must not block a healthy secondary-recorder snapshot.
                        _service_snapshot_requests(
                            cloud, state, cfg, None, snapshot_requests)
                except (RuntimeError, requests.RequestException, DriverError) as error:
                    core.log("analytics: config poll failed; cached rules remain active: "
                             + str(error).splitlines()[0][:180])
                except Exception as error:
                    core.log(f"analytics: config error {type(error).__name__}: "
                             f"{str(error)[:160]}")

            plan = mux.sample_plan()
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
                    target, _requested, _effective = choice
                    recorder_id, channel = mux.resolve_target(target)
                    driver_key = recorder_id or "__legacy__"
                    try:
                        driver = drivers.get(driver_key)
                        if driver is None:
                            job_cfg = recorder_runtime.config_for_cloud_recorder(
                                cfg, recorder_id
                            )
                            driver = _open_analytics_driver(job_cfg)
                            drivers[driver_key] = driver
                        raw = driver.get_snapshot(channel)
                        if raw:
                            found = detector.detect(raw)
                            if found is not None:
                                image = Image.open(io.BytesIO(raw))
                                image.load()
                                when = core.now_utc()
                                # One site authority, recorder-isolated engine/tracker state.
                                events = mux.on_frame(
                                    target, found, image.size, when)
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
                        core.log(f"analytics: sampler recorder={recorder_id or 'legacy'} "
                                 f"ch{channel} failed: {str(error)[:140]}")
                        driver = drivers.pop(driver_key, None)
                        if driver is not None:
                            try:
                                driver.close()
                            except Exception:
                                pass
                    except Exception as error:
                        counters["sample_errors"] += 1
                        core.log(f"analytics: sampler recorder={recorder_id or 'legacy'} "
                                 f"ch{channel} error: {type(error).__name__}: "
                                 f"{str(error)[:140]}")

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
        for driver in list(drivers.values()):
            try:
                driver.close()
            except Exception:
                pass
        drivers.clear()
        try:
            stopped = _status_payload(version, sampler, mux.sample_plan(),
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


def _archive_dt(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _archive_scan_handlers(cfg: Config, detector, stop: threading.Event):
    """Build bounded recorder-frame retrieval + the same local analytics used live."""
    import recovery_ai

    cached = analytics.load_config(cfg.analytics_config_path)
    config = cached.get("config") or {}
    camera_rows = {
        str(row.get("id")): row
        for row in (config.get("cameras") or [])
        if row.get("id") and row.get("channel")
    }
    engines = {}

    def retrieve_frames(camera_id, from_ts, to_ts):
        camera = camera_rows.get(str(camera_id))
        if camera is None:
            core.log(f"analytics: archive camera {str(camera_id)[:8]} not present in local config")
            return None
        channel = str(camera["channel"])
        driver = None
        attempted_segments = 0
        frames = []
        try:
            job_cfg = recorder_runtime.config_for_cloud_recorder(
                cfg, camera.get("recorder_id")
            )
            driver, _info = core.open_archive_driver(job_cfg)
            enumerate_fn = getattr(driver, "enumerate_historical_events", None)
            if not callable(enumerate_fn):
                return None
            start, end = _archive_dt(from_ts), _archive_dt(to_ts)
            cursor = None
            max_frames = max(1, min(int(cfg.recovery_ai_max_frames), 40))
            while len(frames) < max_frames and not stop.is_set():
                page = enumerate_fn(channel, start, end, cursor=cursor, limit=100) or {}
                if page.get("status") != "supported":
                    return None
                rows = page.get("events") or []
                for row in rows:
                    attempted_segments += 1
                    segment = row.get("segment") or {}
                    ts = segment.get("start") or row.get("ts")
                    if not ts:
                        continue
                    frame = recovery_ai.recovered_frame(driver, channel, ts)
                    if frame:
                        frames.append((frame, _archive_dt(ts).astimezone(timezone.utc)
                                      .isoformat().replace("+00:00", "Z")))
                    if len(frames) >= max_frames:
                        break
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            # Segments existed but none decoded: retrieval is unavailable, not "zero activity".
            if attempted_segments and not frames:
                return None
            return frames
        except Exception as error:  # noqa: BLE001
            core.log(f"analytics: archive retrieval ch{channel} failed: "
                     f"{type(error).__name__}: {str(error)[:120]}")
            return None
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass

    def analyze_frame(camera_id, jpeg, ts, rule_ids):
        if detector is None:
            return []
        camera = camera_rows.get(str(camera_id))
        if camera is None:
            return []
        requested = tuple(sorted(str(x) for x in (rule_ids or [])))
        recorder_id = str(camera.get("recorder_id") or "")
        engine_key = (recorder_id, requested)
        engine = engines.get(engine_key)
        if engine is None:
            filtered = json.loads(json.dumps(config))
            if recorder_id:
                filtered["cameras"] = [
                    row for row in (filtered.get("cameras") or [])
                    if str(row.get("recorder_id") or "") == recorder_id
                ]
            if requested:
                wanted = set(requested)
                for row in filtered.get("cameras") or []:
                    row["rules"] = [
                        rule for rule in (row.get("rules") or [])
                        if str(rule.get("id")) in wanted
                    ]
            engine = analytics.AnalyticsEngine(log=core.log)
            engine.configure(filtered)
            engines[engine_key] = engine
        try:
            detections = detector.detect(jpeg)
            if detections is None:
                return []
            image = Image.open(io.BytesIO(jpeg))
            image.load()
            events = engine.process(
                str(camera["channel"]), detections, image.size, _archive_dt(ts))
            out = []
            for event in events:
                meta = event.get("metadata") or {}
                out.append({
                    "result_type": event["event_type"],
                    "recovered_at": event["occurred_at"],
                    "confidence": meta.get("confidence"),
                })
            return out
        except Exception as error:  # noqa: BLE001
            core.log(f"analytics: archive analysis failed: {type(error).__name__}: "
                     f"{str(error)[:120]}")
            return []

    return retrieve_frames, analyze_frame


def archive_worker(cfg: Config, state: dict, stop: threading.Event,
                   authority: dict = None, detector=None) -> None:
    """Background historical scan using bounded vendor archive reads + local analytics."""
    if not cfg.analytics_enabled:
        return
    cloud = core.Cloud(cfg.supabase_url, cfg.publishable_key)
    retrieve_frames, analyze_frame = _archive_scan_handlers(cfg, detector, stop)
    archive = ArchiveRuntime(cloud=cloud, state=state,
                             retrieve_frames=retrieve_frames, analyze=analyze_frame, log=core.log)
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


# Recorder-local fields a bound Config carries (see recorder_runtime._bound_config).
_BOUND_RECORDER_FIELDS = (
    "nvr_url", "nvr_driver", "nvr_username", "nvr_password",
    "recorder_local_id", "recorder_cloud_id", "recorder_display_name",
    "recorder_state_dir", "spool_path", "health_store_path", "last_live_path",
)


def _adopt_single_recorder(cfg, prepared) -> tuple[list[dict], dict]:
    """Make the shared process Config the one configured recorder's bound Config.

    The same cfg object is already held by the incident-evidence workers and is
    handed to every thread below, so afterwards every recorder-routed job for the
    site's recorder resolves to it, events are stamped with its recorder_id and
    health/recovery use the recorder RPCs. The registry row supplies the address
    and credential. The single configured recorder is always the continuity
    owner, so its spool, health store and last-live marker stay at the
    historical singleton paths.
    """
    bound = prepared.context.config
    for name in _BOUND_RECORDER_FIELDS:
        setattr(cfg, name, getattr(bound, name))
    channels = multi_recorder_fanout._channel_rows(prepared)
    mapping = (
        {str(k): str(v) for k, v in prepared.camera_mapping.items()}
        if isinstance(prepared.camera_mapping, dict) else {}
    )
    holder_seed = {
        "recorder_cloud_id": cfg.recorder_cloud_id,
        "camera_mapping": mapping,
        "synced_channels": list(channels) if mapping else [],
        "camera_sync_signature": (
            tuple(sorted(str(c["channel"]) for c in channels)) if mapping else None
        ),
    }
    return channels, holder_seed


def _recorder_contract_absent(error: Exception) -> bool:
    """True only when the database definitively has no multi-recorder contract RPC
    (a database from before the recorder foundation: no recorders, no recorder_id)."""
    return (isinstance(error, core.CloudError)
            and getattr(error, "fn", "") == "wl_multi_recorder_agent_contract"
            and (getattr(error, "code", None) == "PGRST202"
                 or getattr(error, "status", None) == 404))


def _adopt_registry_recorder_unbound(cfg) -> None:
    """One configured recorder on a database without recorders: the registry stays the
    authority for address and login, but no cloud recorder identity exists, so the
    legacy recorder-less RPCs are used (recorder_cloud_id stays unset). No binding
    can land in this process, so a job naming a recorder fails at once instead of
    waiting for one (recorder_runtime.config_for_cloud_recorder)."""
    (ctx,) = recorder_runtime.load_contexts(cfg)
    for name in _BOUND_RECORDER_FIELDS:
        if name != "recorder_cloud_id":
            setattr(cfg, name, getattr(ctx.config, name))
    cfg.recorder_cloud_id = None
    cfg.recorder_backend_absent = True


def _preflight_transient(error: Exception) -> bool:
    """True when a preflight failure says nothing about the recorder identities: the
    cloud could not be reached or answered 5xx/429, or this PC is not the site's
    current Agent (a standby). A refusal or contract mismatch is definitive."""
    if isinstance(error, requests.RequestException):
        return True
    if not isinstance(error, core.CloudError):
        return False
    status = int(getattr(error, "status", 0) or 0)
    return (status >= 500 or status == 429
            or (getattr(error, "code", None) == "42501"
                and "authority" in str(getattr(error, "message", ""))))


def _prepared_from_saved_identity(cfg):
    """The one configured recorder as already bound in recorders.json, or None when it
    has no saved cloud identity (or cannot be loaded). No cloud call is made."""
    try:
        contexts = recorder_runtime.load_contexts(cfg)
    except Exception:  # noqa: BLE001 — startup then fails closed
        return None
    if len(contexts) != 1 or not contexts[0].cloud_recorder_id:
        return None
    return multi_recorder_orchestrator.PreparedRecorder(
        context=contexts[0], device=None, channels=[], capabilities=None,
        camera_mapping=None,
    )


def _retry_recorder_preflight(cfg, state: dict, cloud, stop: threading.Event,
                              restart: dict, mode: str) -> None:
    """Finish in the background the recorder check startup could not complete.

    "bind": startup ran on the one recorder's saved identity because WatchLog did
    not answer. A confirmed identity ends the retry; a definitive refusal asks the
    run loop for a clean restart, so startup fails closed rather than running on an
    identity WatchLog rejects. Cameras are bound by the health cycle as usual.
    "contract": startup found a database without recorders. Once WatchLog offers
    the recorder contract its jobs carry recorder ids this unbound runtime cannot
    serve, so ask for a clean restart that binds the recorder.
    """
    attempt = 0
    while not stop.wait(RECORDER_RECHECK_SECONDS[min(attempt, len(RECORDER_RECHECK_SECONDS) - 1)]):
        attempt += 1
        try:
            multi_recorder_orchestrator.require_cloud_contract(cloud, state)
            if mode == "bind":
                multi_recorder_orchestrator.bind_cloud_identities(
                    cloud, state, recorder_runtime.load_contexts(cfg))
        except Exception as error:  # noqa: BLE001
            if mode == "contract" or _preflight_transient(error):
                continue
            restart["reason"] = (
                "recorder check refused the saved recorder identity "
                f"({type(error).__name__}: {str(error).splitlines()[0][:160]}); "
                "restarting WatchLog")
            return
        if mode == "contract":
            restart["reason"] = ("WatchLog now supports recorder identity; "
                                 "restarting WatchLog to bind this recorder")
        else:
            core.log("recorder: saved recorder identity confirmed with WatchLog")
        return


def _report_retained_queues(cfg) -> None:
    """Say on every start what a disabled recorder still has queued on this PC.

    WatchLog rejects new uploads for a disabled recorder, so its queue is kept
    (never deleted) and uploads only if the recorder is re-enabled. Reporting it
    keeps that backlog visible instead of silently stranded."""
    try:
        state_parent = Path(cfg.state_path).parent
        for row in recorder_registry.recorders():
            if row.get("is_configured"):
                continue
            path = recorder_runtime.recorder_state_dir(state_parent, row["local_id"]) / "spool.sqlite"
            if not path.exists():
                continue
            queue = Spool(path)
            try:
                queued = queue.count()
            finally:
                queue.close()
            if queued:
                core.log(f"recorder: {row['display_name']} is disabled; {queued} queued "
                         "event(s) retained on this PC, uploaded only if it is re-enabled")
    except Exception as error:  # noqa: BLE001 — a report must never stop monitoring
        core.log(f"recorder: retained-queue check skipped: {type(error).__name__}")


def _hold_for_registry_repair(error: Exception) -> None:
    """recorders.json exists but is malformed or untrusted: fail that recorder set closed.

    Exiting would only make the launcher restart the Agent into the same failure
    every 15 s forever. Instead monitor nothing (no recorder connection and no
    heartbeat, so nothing is reported as watched), publish a clear local status,
    and re-check periodically. Once Setup has repaired or quarantined the file,
    exit so the launcher starts one clean runtime."""
    reason = f"{type(error).__name__}: {str(error).splitlines()[0][:160]}"
    core.log("ERROR: the recorder configuration on this PC cannot be trusted or read "
             f"({reason}); monitoring is stopped until WatchLog Setup repairs it")
    core.update_runtime_health(recorder_registry="needs_repair",
                               recorder_registry_reason=reason)
    while True:
        time.sleep(REGISTRY_RECHECK_SECONDS)
        try:
            recorder_registry.recorders()
        except Exception:  # noqa: BLE001 — still unusable; keep holding
            continue
        core.update_runtime_health(recorder_registry="ok", recorder_registry_reason=None)
        raise SystemExit("recorder configuration is valid again; restarting WatchLog")


def enhanced_cmd_run(cfg: Config, state: dict, cloud: core.Cloud, once: bool,
                     device=None, channels=None) -> None:
    """Core event loop plus analytics worker.

    Whenever a validated local registry configures 1..N recorders, startup is
    recorder-aware: cloud contract v4 preflight binds every registry row
    (including disabled ones) through wl_sync_recorders before any worker starts.
    More than one configured recorder activates true worker fan-out; exactly one
    runs the singleton loop below, bound to that recorder's cloud identity. With
    no registry the historical 5.0.x singleton runtime continues unchanged.
    Failure never falls back to the primary recorder or to an unbound singleton;
    the one exception is a single configured recorder on a database that has no
    recorders at all, which runs on the legacy recorder-less RPCs. A single recorder
    whose cloud identity is already saved keeps monitoring under it when the cloud
    cannot be reached (or this PC is a standby) and finishes the check in the
    background; an unbound row or a definitive refusal still fails closed.
    """
    try:
        configured_recorders = [
            row for row in recorder_registry.recorders()
            if row.get("is_configured")
        ]
    except Exception as error:
        if recorder_registry.registry_path().exists():
            if once:   # a one-shot run is not relaunched: report and stop
                raise SystemExit(
                    "FATAL: the recorder configuration on this PC cannot be trusted or "
                    "read; monitoring stopped until WatchLog Setup repairs it."
                ) from error
            _hold_for_registry_repair(error)
        configured_recorders = []

    holder_seed = {}
    recorder_bound = False
    prepared = None
    recheck = None        # background recorder check still owed: "bind" or "contract"
    if configured_recorders:
        try:
            prepared = multi_recorder_orchestrator.prepare_recorders(
                cfg, state, cloud, core.open_driver
            )
        except Exception as error:
            if len(configured_recorders) == 1 and _recorder_contract_absent(error):
                # Unambiguous: that database has no recorder identity to bind to and
                # sends no recorder_id; the 5.0.x recorder-less RPCs serve this site.
                _adopt_registry_recorder_unbound(cfg)
                recheck = "contract"
                core.log("recorder: this WatchLog site has no recorder-aware backend yet; "
                         "single-recorder runtime without recorder identity")
            else:
                # Network not ready at boot, an outage or a standby PC says nothing
                # about the recorder: a single recorder already bound in
                # recorders.json keeps monitoring under that saved identity.
                if len(configured_recorders) == 1 and _preflight_transient(error):
                    saved = _prepared_from_saved_identity(cfg)
                    prepared = [saved] if saved is not None else None
                if prepared is None:
                    raise SystemExit(
                        "FATAL: recorder preflight did not complete; monitoring "
                        "stopped rather than running an unbound or partial recorder set. "
                        f"({type(error).__name__}: {str(error)[:160]})"
                    ) from error
                recheck = "bind"
                core.log("recorder: WatchLog did not answer the recorder check "
                         f"({type(error).__name__}); monitoring under the saved recorder "
                         "identity and retrying in the background")

    if prepared is not None:
        if len(prepared) != len(configured_recorders):
            raise SystemExit(
                "FATAL: recorder preflight returned an incomplete recorder set."
            )
        if any(not getattr(item.context, "cloud_recorder_id", None) for item in prepared):
            raise SystemExit(
                "FATAL: one or more configured recorders have no cloud identity."
            )

        for item in prepared:
            if item.error:
                core.log(
                    f"multi-recorder: {item.context.display_name} preflight probe "
                    f"unavailable ({item.error}); its live/health workers will retry "
                    "independently"
                )
        _report_retained_queues(cfg)

        if len(prepared) > 1:
            detector = core.vision.build(cfg, core.log)
            return multi_recorder_fanout.run(
                cfg, state, cloud, once=once,
                prepared_recorders=prepared,
                detector=detector,
                analytics_worker=analytics_worker,
                archive_worker=archive_worker,
                last_live_writer=_persist_stream_last_live,
            )

        channels, holder_seed = _adopt_single_recorder(cfg, prepared[0])
        device = prepared[0].device or device
        recorder_bound = True
        core.log(f"recorder: {cfg.recorder_display_name} bound as "
                 f"{str(cfg.recorder_cloud_id)[:8]}; single-recorder runtime")

    # Singleton runtime: the historical 5.0.x path, or bound to the one configured recorder.
    import camera_health
    original_build = core.vision.build
    detector = original_build(cfg, core.log)
    core.vision.build = lambda _cfg, _log: detector

    spool = Spool(cfg.spool_path, cfg.spool_max_rows)   # per-deployment buffer cap, as core.cmd_run
    core.log(f"spool: {cfg.spool_path} ({spool.count()} queued)")

    # Shared single-authority signal: the analytics worker owns the lease and publishes its
    # verdict here; the event-upload loop and the archive worker fence their authoritative
    # writes on it. Defaults to authoritative (multi-agent OFF -> unchanged single-agent).
    authority = {"ok": True}

    # Phase-A operational health: the collector (native faults) and the health worker (active
    # probes) drive the SAME per-camera machines via this holder. Seeded from the channels found
    # at startup; (re)built lazily by the health cycle once enumeration succeeds if we started with
    # none. Without this the agent reports NO camera/NVR health and the portal reads UNKNOWN.
    mon_channels = [str(c.get("channel")) for c in (channels or []) if c.get("channel")]
    holder = {"monitor": camera_health.CameraHealthMonitor(
        mon_channels, batch_size=cfg.health_batch, concurrency=cfg.health_concurrency)
        if mon_channels else None}
    holder.update(holder_seed)
    # A bound recorder never guesses recovery channels: it follows the explicitly
    # synced inventory, which the health cycle refreshes after a reconnect.
    recovery_channels = ((lambda h=holder: h.get("synced_channels") or [])
                         if recorder_bound else channels)

    stop = threading.Event()
    restart = {}          # set by the background recorder check: exit for a clean start
    collector = threading.Thread(target=core.collector,
                                 args=(cfg, spool, stop, holder),
                                 daemon=True, name="collector")
    analytic = threading.Thread(target=analytics_worker,
                                args=(cfg, state, detector, stop, authority),
                                daemon=True, name="analytics")
    archive = threading.Thread(target=archive_worker,
                               args=(cfg, state, stop, authority, detector),
                               daemon=True, name="archive")
    recovery = threading.Thread(target=core.recovery_worker,
                                args=(cfg, state, cloud, stop, spool, recovery_channels, holder),
                                daemon=True, name="recovery")
    # Health probing runs on its OWN thread so a stalled probe can never delay heartbeat/upload.
    import monitoring_coverage as coverage   # local module; NOT the PyPI 'coverage' tool
    resume_evt = threading.Event()
    cov = coverage.CoverageMonitor(loop_period=1.0)
    health = threading.Thread(target=core.health_worker,
                              args=(cfg, state, cloud, holder, stop, resume_evt),
                              daemon=True, name="health")
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
        recorder_live = _recorder_stream_live(holder, time.monotonic())
        core.heartbeat(cloud, state, device, recorder_live=recorder_live,
                       event_stream=holder.get("event_stream"))
        core.health_cycle(cloud, state, cfg, holder)
        spool.close()
        core.vision.build = original_build
        return

    archive.start()   # background historical scan; lower priority, run mode only
    recovery.start()  # automatic LIVE-gap reconciliation from recorder archive
    health.start()    # Phase-A camera/NVR health probing on its own thread
    sitectl = threading.Thread(target=core.command_worker, args=(cfg, state, cloud, stop),
                               daemon=True, name="sitecontrol")
    sitectl.start()   # Site Control read plane (H6); thread exits at once unless enabled
    recorder_check = threading.Thread(target=_retry_recorder_preflight,
                                      args=(cfg, state, cloud, stop, restart, recheck),
                                      daemon=True, name="recorder-check")
    if recheck:
        recorder_check.start()   # finish the recorder check startup could not
    core.log(f"running: events every {cfg.upload_seconds}s, analytics enabled, "
             f"heartbeat every {cfg.heartbeat_seconds}s, health every ~{cfg.health_seconds}s, "
             f"outbound only. Ctrl-C to stop.")
    next_upload = next_heartbeat = 0.0
    last_wall = time.time()
    try:
        while True:
            if restart.get("reason"):
                core.log(f"recorder: {restart['reason']}")
                raise SystemExit(restart["reason"])
            clock = time.monotonic()
            now_wall = time.time()
            # Suspend/resume detection (site PC sleep) — same rule as watchlog_agent.cmd_run:
            # a big wall-clock jump across the ~1 s loop means WatchLog was NOT observing.
            gap = cov.tick(last_wall, now_wall)
            last_wall = now_wall
            if gap is not None:
                core.log(f"resume: site not observed for ~{int(gap.ended_at - gap.started_at)}s "
                         f"(site PC sleep/suspend); reconciling recorder health now")
                resume_evt.set()                        # immediate health reconciliation
            cov.report_pending(cloud, state)            # best-effort; retries while cloud down
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
                    recorder_live = _recorder_stream_live(holder, clock)
                    core.heartbeat(cloud, state, device, recorder_live=recorder_live,
                                   event_stream=holder.get("event_stream"))
                except (RuntimeError, requests.RequestException) as error:
                    core.log("ERROR: heartbeat failed, will retry: "
                             + str(error).splitlines()[0][:200])
                # Persist RECORDER observation, not PC/cloud liveness: a cloud outage does
                # not stop it (events still spool); a dead event stream does.
                _persist_stream_last_live(cfg, holder, clock)
            time.sleep(1)
    except KeyboardInterrupt:
        core.log("stopping...")
    finally:
        stop.set()
        resume_evt.set()                                # wake the health thread so it can exit
        collector.join(timeout=5)
        analytic.join(timeout=5)
        archive.join(timeout=5)
        recovery.join(timeout=5)
        health.join(timeout=5)
        sitectl.join(timeout=5)
        if recorder_check.is_alive():
            recorder_check.join(timeout=5)
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

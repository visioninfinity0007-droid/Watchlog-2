"""Periodic still producer: one timed still per configured camera, every ~300 s.

The deployed Hikvision Agents at HASCO Head Office and Chai Wala emit a timed still for every
configured camera about every 300 s, and the canonical database consumes them (0125/0126
wl_vision_claim_snapshots_v2 filters payload source 'periodic_snapshot'; the vision workers
review the stills; restaurant visual analytics and daily reports are built from them). This
module is the canonical producer, so an upgrade does not remove a site's only timed stills.

Contract, derived from production rows (read-only), reproduced exactly:

  event_type       'visual_sample'
  channel          the recorder channel; the server maps it to the camera (wl_ingest_events)
  device_event_id  '<vendor>-sample-<channel>-<floor(epoch(device_ts) / 30)>'
                   (the server dedupe key becomes site:channel:device_event_id)
  device_ts        the Agent's UTC clock when the still came back (not a recorder clock)
  agent_ts         the Agent's UTC clock when the row was spooled
  payload          exactly {"sample": true, "source": "periodic_snapshot", "vendor": <vendor>}
  snapshot_b64     the JPEG inline; wl_ingest_events stores it as the event's snapshot row
                   (captured_at = device_ts) and the snapshot trigger queues visual review

Every production row carries a still: a sample with no still is never emitted. Cameras are
staggered evenly across one cadence (8 cameras -> one still every ~37.5 s), as in production.

Multi-recorder Agent: a recorder bound to a cloud recorder also stamps its recorder_id on every
still (as on every other event it spools), so the server namespaces the dedupe key by recorder
(0154) and two recorders' channel 1 never collide. The fan-out runs one producer per recorder,
each on its own driver, spool, credential, back-off and Monitor/Ignore choices.

Field status: IMPLEMENTED_UNVERIFIED until seen on site. The still comes from the driver's
own get_snapshot, so the same code serves hikvision-isapi, dahua-cgi and onvif; nothing here
assumes anything about a recorder model.
"""
from __future__ import annotations

import base64
import configparser
import json
import math
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import nvr_health
import server_capture
import watchlog_agent as core
import worker_supervisor
from drivers import DriverError
from drivers.base import Event, NvrAuthFailed, NvrUnreachable

EVENT_TYPE = "visual_sample"
SOURCE = "periodic_snapshot"
DEFAULT_CADENCE_SECONDS = 300
MAX_CADENCE_SECONDS = 3600
# Never faster than the incident-still limit (core.SNAPSHOT_MIN_INTERVAL) nor than this floor.
MIN_CADENCE_SECONDS = 60
# Per-camera interval jitter: a fraction of the cadence each way (300 s -> 285..315 s).
JITTER_FRACTION = 0.05
# Two periodic requests never reach the recorder closer together than this.
MIN_SPACING_SECONDS = 2.0
# The first still waits this long after start, so the event stream connects first.
STARTUP_DELAY_SECONDS = 30.0
# Recorder unreachable / refusing: exponential back-off between reconnect attempts.
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 900.0
# A refused login escalates on the collector's confirmed-auth schedule (core, 5/15/30 min),
# entered at 15 min, so the stills never add more failed logins than the collector's own.
AUTH_BACKOFF_SECONDS = tuple(s for s in core._AUTH_BACKOFF_SECONDS
                             if s >= BACKOFF_MAX_SECONDS) or (BACKOFF_MAX_SECONDS,)
# While an auth back-off runs, look this often for Setup rewriting the recorder credential.
CREDENTIAL_CHECK_SECONDS = 5.0
# Re-read the recorder's channel list on the open driver this often.
REENUMERATE_SECONDS = 3600.0
# Stop adding timed stills while this many rows wait to upload (a long cloud outage), so they
# can never push recorder events out of the spool. Capped at a quarter of the spool limit.
SPOOL_HIGH_WATER_ROWS = 2000
DEVICE_ID_BUCKET_SECONDS = 30
TICK_SECONDS = 1.0
JPEG_MAGIC = b"\xff\xd8"
# A further recorder's camera choices, written by Setup (setup_backend) in its recorder folder.
RECORDER_CAMERA_PROFILES_NAME = "camera_profiles.json"
RECORDER_CAMERA_PROFILES_SCHEMA = "watchlog.recorder_camera_profiles.v1"


def _flag(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def load_settings(cfg) -> dict:
    """{"enabled", "cadence_seconds", "floor_seconds"} from WATCHLOG_* / watchlog.ini.

    No cloud field controls this cadence in production (restaurant profile intervals are not
    applied by the deployed Agents), so it is a local setting: periodic_stills (default on)
    and periodic_still_seconds (default 300, clamped to [max(60, snapshot_min_interval), 3600]).
    Stills switched off with snapshots = false switch this off too."""
    section = {}
    ini_path = getattr(cfg, "_ini_path", None)
    if ini_path is not None:
        try:
            ini = configparser.ConfigParser()
            ini.read(ini_path, encoding="utf-8-sig")
            if ini.has_section("watchlog"):
                section = dict(ini.items("watchlog"))
        except (configparser.Error, OSError):
            section = {}

    def get(key):
        return os.environ.get("WATCHLOG_" + key.upper()) or section.get(key)

    try:
        incident_floor = int(getattr(cfg, "snapshot_min_interval", core.SNAPSHOT_MIN_INTERVAL))
    except (TypeError, ValueError):
        incident_floor = core.SNAPSHOT_MIN_INTERVAL
    floor = max(MIN_CADENCE_SECONDS, incident_floor)
    try:
        cadence = int(get("periodic_still_seconds") or DEFAULT_CADENCE_SECONDS)
    except (TypeError, ValueError):
        cadence = DEFAULT_CADENCE_SECONDS
    cadence = min(MAX_CADENCE_SECONDS, max(floor, cadence))
    enabled = (_flag(get("periodic_stills"), True)
               and bool(getattr(cfg, "snapshots", True)))
    return {"enabled": enabled, "cadence_seconds": cadence, "floor_seconds": min(floor, cadence)}


def vendor_family(driver) -> str:
    """'hikvision' / 'dahua' / 'onvif': the same vendor word the drivers put in event payloads."""
    name = str(getattr(driver, "name", "") or "").strip().lower()
    return name.split("-", 1)[0] or "unknown"


def device_event_id(vendor: str, channel: str, captured: datetime) -> str:
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    bucket = math.floor(captured.timestamp() / DEVICE_ID_BUCKET_SECONDS)
    return f"{vendor}-sample-{channel}-{bucket}"


def build_event(vendor: str, channel: str, raw: bytes, captured: datetime) -> Event:
    channel = str(channel)
    return Event(channel=channel, event_type=EVENT_TYPE, device_ts=captured,
                 device_event_id=device_event_id(vendor, channel, captured),
                 payload={"sample": True, "source": SOURCE, "vendor": vendor},
                 snapshot_b64=base64.b64encode(raw).decode("ascii"))


def usable_still(raw) -> bool:
    return (isinstance(raw, (bytes, bytearray)) and len(raw) > 0
            and len(raw) <= core.SNAPSHOT_MAX_BYTES and bytes(raw[:2]) == JPEG_MAGIC)


def _channel_of(row):
    if isinstance(row, dict):
        return row.get("channel")
    return getattr(row, "channel", None)


def configured_channels(listed, startup, profiles) -> list[str]:
    """Channels this Agent may sample: present and enabled on the recorder, and not marked
    Ignore (monitored = false) by the operator. The recorder's current list wins; the
    channels enumerated at start are the fallback when it cannot be read. Same rule as
    setup_backend.merge_camera_config and the local Site Status view."""
    excluded = set()
    for prof in profiles or []:
        if not isinstance(prof, dict):
            continue
        ch = str(prof.get("channel") or "").strip()
        if ch and not bool(prof.get("monitored", prof.get("analytics_enabled", True))):
            excluded.add(ch)
    source = listed if listed else startup
    out = []
    for row in source or []:
        ch = _channel_of(row)
        if ch is None or str(ch).strip() == "":
            continue
        enabled = row.get("enabled", True) if isinstance(row, dict) else getattr(row, "enabled", True)
        ch = str(ch).strip()
        if enabled is False or ch in excluded or ch in out:
            continue
        out.append(ch)
    return out


class Schedule:
    """Per-camera due times: one still per camera per cadence, staggered evenly, jittered,
    never faster than the floor, and never two requests closer than min_spacing."""

    def __init__(self, cadence: float, *, floor: float = MIN_CADENCE_SECONDS,
                 jitter: float = JITTER_FRACTION, min_spacing: float = MIN_SPACING_SECONDS,
                 rng: random.Random | None = None) -> None:
        self.cadence = float(cadence)
        self.floor = float(min(floor, cadence))
        self.jitter = float(jitter)
        self.min_spacing = float(min_spacing)
        self.rng = rng or random.Random()
        self.next_due: dict[str, float] = {}
        self.last_request = None

    def interval(self) -> float:
        return max(self.floor, self.cadence * (1.0 + self.rng.uniform(-self.jitter, self.jitter)))

    def set_channels(self, channels, now: float, *, start: float | None = None,
                     restagger: bool = False) -> None:
        channels = list(dict.fromkeys(str(c) for c in channels))
        if restagger:
            # Spread the cameras over one cadence again, by rotation rather than list order:
            # the camera waiting longest goes first (never sampled before all), so a recorder
            # that drops again partway through a round never starves the end of the list. A
            # camera keeps its own due time when that is later than its slot, so restaggering
            # never samples a camera sooner than its own interval.
            previous = self.next_due
            order = sorted(channels, key=lambda c: previous.get(c, -math.inf))
            base = now if start is None else start
            spacing = self.cadence / len(order) if order else 0.0
            self.next_due = {}
            for index, channel in enumerate(order):
                slot = base + index * spacing
                self.next_due[channel] = max(slot, previous.get(channel, slot))
            return
        for old in list(self.next_due):
            if old not in channels:
                del self.next_due[old]
        new = [c for c in channels if c not in self.next_due]
        if not new:
            return
        if not self.next_due:
            base = now if start is None else start
            spacing = self.cadence / len(new)
            for index, channel in enumerate(new):
                self.next_due[channel] = base + index * spacing
        else:
            for channel in new:
                self.next_due[channel] = now + self.rng.uniform(0.0, self.cadence)

    def due(self, now: float):
        if not self.next_due:
            return None
        if self.last_request is not None and now - self.last_request < self.min_spacing:
            return None
        channel, when = min(self.next_due.items(), key=lambda kv: (kv[1], kv[0]))
        return channel if now >= when else None

    def done(self, channel: str, now: float) -> None:
        self.last_request = now
        if channel in self.next_due:
            self.next_due[channel] = now + self.interval()

    def skip(self, channel: str, now: float) -> None:
        """Next turn for a camera this round did not ask the recorder about (no spacing)."""
        if channel in self.next_due:
            self.next_due[channel] = now + self.interval()

    def seconds_until_due(self, now: float) -> float:
        if not self.next_due:
            return TICK_SECONDS
        wait = min(self.next_due.values()) - now
        if self.last_request is not None:
            wait = max(wait, self.last_request + self.min_spacing - now)
        return max(0.0, wait)


class Backoff:
    """Recorder-level back-off: doubles per consecutive failure, capped; an auth refusal
    waits at least the maximum and escalates (15 -> 30 min) like the collector's breaker, so
    stills never add to a recorder lockout."""

    def __init__(self, base: float = BACKOFF_BASE_SECONDS, cap: float = BACKOFF_MAX_SECONDS,
                 rng: random.Random | None = None) -> None:
        self.base, self.cap = float(base), float(cap)
        self.rng = rng or random.Random()
        self.failures = 0
        self.auth_failures = 0
        self.auth = False
        self.until = 0.0

    def fail(self, now: float, *, auth: bool = False) -> float:
        self.failures += 1
        self.auth = bool(auth)
        if auth:
            self.auth_failures += 1
            step = AUTH_BACKOFF_SECONDS[min(self.auth_failures - 1, len(AUTH_BACKOFF_SECONDS) - 1)]
            delay = max(self.cap, float(step))
        else:
            delay = min(self.cap, self.base * (2 ** (self.failures - 1)))
        delay *= 1.0 + self.rng.uniform(0.0, 0.1)
        self.until = now + delay
        return delay

    def reset(self) -> None:
        self.failures = 0
        self.auth_failures = 0
        self.auth = False
        self.until = 0.0


def _close(driver) -> None:
    if driver is None:
        return
    try:
        driver.close()
    except Exception:                                       # noqa: BLE001
        pass


def recorder_camera_profiles(cfg, *, continuity_owner: bool) -> list:
    """The Monitor/Ignore choices of the recorder ``cfg`` is bound to.

    watchlog.ini camera_profiles_json is the continuity recorder's channel-keyed list (the
    singleton runtime's); it cannot describe a second recorder that also has a channel 1. A
    further recorder's choices are in its own recorder folder. With none readable, every
    present and enabled channel of that recorder is sampled, as with no choices in 5.0.28."""
    if continuity_owner:
        return list(getattr(cfg, "camera_profiles", None) or [])
    state_dir = getattr(cfg, "recorder_state_dir", None)
    if not state_dir:
        return []
    try:
        doc = json.loads((Path(state_dir) / RECORDER_CAMERA_PROFILES_NAME)
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if (not isinstance(doc, dict) or doc.get("schema") != RECORDER_CAMERA_PROFILES_SCHEMA
            or str(doc.get("local_id") or "") != str(getattr(cfg, "recorder_local_id", "") or "")
            or not isinstance(doc.get("profiles"), list)):
        return []
    return list(doc["profiles"])


def _reload_credential(cfg, log) -> None:
    if getattr(cfg, "recorder_local_id", None):
        # A registry recorder's own DPAPI credential, never the singleton one.
        def loader():
            core._reload_credential_for_cfg(cfg)
    else:
        loader = getattr(cfg, "load_recorder_credential", None)
    if not callable(loader):
        return
    try:
        loader()
    except (Exception, SystemExit) as error:                 # noqa: BLE001 — retry decides
        log(f"recorder credential could not be reloaded ({type(error).__name__})")


def _recorder_down(driver):
    """Re-check the recorder after a camera's still failed: the error when the recorder itself
    is unreachable or refusing (or cannot be proven healthy), None when it answered."""
    try:
        driver.probe()
    except Exception as error:                              # noqa: BLE001 — not proven healthy
        return error
    return None


def _list_channels(driver, log):
    try:
        return list(driver.list_channels() or [])
    except Exception as error:                              # noqa: BLE001
        log(f"channel list unavailable ({type(error).__name__}); "
            "using the channels found at start")
        return []


def _high_water(spool) -> int:
    try:
        cap = int(getattr(spool, "max_rows", 0) or 0)
    except (TypeError, ValueError):
        cap = 0
    return min(SPOOL_HIGH_WATER_ROWS, cap // 4) if cap > 0 else SPOOL_HIGH_WATER_ROWS


class StreamStillSampler:
    """Timed stills taken BETWEEN live alert-stream slices, on the live driver's own session.

    Field Build 69/75 and shipped 5.0.26 (Hikvision DS-7608NI-Q1): a separate still session
    beside the open alertStream made the recorder refuse logins, so each bounded stream slice
    was followed by ONE rotating still on the same session. This is that behaviour with the
    canonical contract of this module: the same configured-camera rule (Monitor/Ignore), the
    same ~300 s per-camera cadence and staggering, the same spool high-water pause and the
    same event row (build_event, recorder_id stamped). The driver calls it between slices
    (``between_slices``); it returns at most one event and never raises."""

    def __init__(self, cfg, driver, spool, *, profiles=None, startup=None, label=None,
                 clock=time.monotonic, wall=None, rng: random.Random | None = None) -> None:
        self.cfg, self.driver, self.spool = cfg, driver, spool
        self.profiles = getattr(cfg, "camera_profiles", None) if profiles is None else profiles
        self.startup = list(startup or [])
        self.name = f"periodic stills ({label})" if label else "periodic stills"
        self.clock, self.wall = clock, wall or core.now_utc
        settings = load_settings(cfg)
        self.enabled = bool(settings["enabled"])
        self.schedule = Schedule(settings["cadence_seconds"], floor=settings["floor_seconds"],
                                 rng=rng or random.Random())
        self.vendor = vendor_family(driver)
        self.next_enumerate = 0.0
        self.started = None
        self.paused_logged = False

    def _log(self, text: str) -> None:
        core.log(f"{self.name}: {text}")

    def __call__(self) -> list:
        try:
            return self._sample()
        except Exception as error:                          # noqa: BLE001 — never ends monitoring
            self._log(f"still skipped ({type(error).__name__}: {nvr_health.redact(str(error))})")
            return []

    def _sample(self) -> list:
        if not self.enabled or self.spool is None:
            return []
        now = self.clock()
        if self.started is None:
            self.started = now
        if now >= self.next_enumerate:
            self.next_enumerate = now + REENUMERATE_SECONDS
            found = configured_channels(_list_channels(self.driver, self._log), self.startup,
                                        self.profiles)
            # No start-up delay: the sampler first runs after a whole live slice, so the stream
            # is already proven (field Build 69 took its first still after the first slice).
            first = not self.schedule.next_due
            self.schedule.set_channels(found, now, start=now, restagger=first)
        channel = self.schedule.due(now)
        if channel is None:
            return []
        if server_capture.owned_by_server(getattr(self.cfg, "recorder_cloud_id", None), channel,
                                          now):
            # The server is scheduling this camera's stills; ours would duplicate them.
            self.schedule.skip(channel, now)
            return []
        waiting = self.spool.count()
        if waiting >= _high_water(self.spool):
            self.schedule.done(channel, now)
            if not self.paused_logged:
                self.paused_logged = True
                self._log(f"paused while {waiting} events wait to upload; "
                          "recorder events keep priority")
            return []
        if self.paused_logged:
            self.paused_logged = False
            self._log("resumed")
        try:
            raw = self.driver.get_snapshot(channel)
        except Exception as error:                          # noqa: BLE001 — camera-level
            raw = None
            self._log(f"ch{channel} gave no still "
                      f"({type(error).__name__}: {nvr_health.redact(str(error))})")
        captured = self.wall()
        self.schedule.done(channel, now)
        if not usable_still(raw):
            return []
        event = build_event(self.vendor, channel, bytes(raw), captured)
        return [event.with_recorder_id(getattr(self.cfg, "recorder_cloud_id", None))]


def samples_in_stream(cfg, driver=None) -> bool:
    """True when this recorder's stills come from its live stream (StreamStillSampler), so the
    separate worker must not open a second session to it."""
    if driver is not None:
        return bool(getattr(driver, "samples_in_stream", False))
    return str(getattr(cfg, "nvr_driver", "") or "").strip().lower() == "hikvision-isapi"


def periodic_still_worker(cfg, spool, stop: threading.Event, channels=None, *,
                          open_driver=None, clock=time.monotonic, wall=None,
                          rng: random.Random | None = None, profiles=None,
                          label: str | None = None) -> None:
    """Long-running timed-still producer. Outbound only; one recorder request at a time.

    Each still goes through the normal spool, so it waits out a cloud outage and uploads with
    the next batch (fenced by the same single-authority rule as every other event). No still,
    a non-JPEG answer or an oversized one produces no event. An unreachable or refusing
    recorder backs the worker off; a camera that cannot give a still is simply tried again at
    its next turn.

    ``profiles`` are this recorder's camera choices (default: cfg.camera_profiles, the
    single-recorder list); ``label`` names the recorder in log lines (the fan-out passes its
    display name, never its address)."""
    name = f"periodic stills ({label})" if label else "periodic stills"

    def log(text: str) -> None:
        core.log(f"{name}: {text}")

    if profiles is None:
        profiles = getattr(cfg, "camera_profiles", None)
    settings = load_settings(cfg)
    if not settings["enabled"]:
        log("disabled by configuration")
        worker_supervisor.disable("periodic stills are off in this configuration")
        return
    if not str(getattr(cfg, "nvr_url", "") or "").strip():
        worker_supervisor.disable("no recorder address configured")
        return
    if samples_in_stream(cfg):
        log("stills come from the live event stream on its own session; no second session")
        worker_supervisor.complete("stills are taken in the live event stream")
        return
    opener = open_driver or core.open_driver
    wall = wall or core.now_utc
    rng = rng or random.Random()
    cadence = settings["cadence_seconds"]
    startup = list(channels or [])
    schedule = Schedule(cadence, floor=settings["floor_seconds"], rng=rng)
    backoff = Backoff(rng=rng)
    driver = None
    vendor = "unknown"
    cameras: list[str] = []
    camera_failures = 0
    next_enumerate = 0.0
    first_open = True
    restagger = True
    paused_logged = False
    credential_gen = core._credential_generation(cfg)
    next_credential_check = 0.0
    log(f"one still per configured camera every ~{cadence}s")

    try:
        while not stop.is_set():
            worker_supervisor.tick()
            now = clock()
            try:
                if now < backoff.until:
                    if backoff.auth and now >= next_credential_check:
                        # Setup fixed the password: retry now with it, not in 15-30 min.
                        next_credential_check = now + CREDENTIAL_CHECK_SECONDS
                        generation = core._credential_generation(cfg)
                        if generation != credential_gen:
                            credential_gen = generation
                            log("recorder credential changed in Setup; retrying now")
                            _reload_credential(cfg, log)
                            backoff.reset()
                            continue
                    stop.wait(min(TICK_SECONDS, backoff.until - now))
                    continue

                if driver is None:
                    try:
                        driver, _info = opener(cfg)
                    except (DriverError, requests.RequestException, RuntimeError, OSError,
                            SystemExit) as error:
                        delay = backoff.fail(now, auth=core._is_auth_failure(error))
                        log(f"recorder not reachable; next try in {int(delay)}s "
                            f"({nvr_health.redact(str(error))})")
                        continue
                    if samples_in_stream(cfg, driver):
                        # An auto-detected recorder that samples in its live stream.
                        log("stills come from the live event stream on its own session; "
                            "no second session")
                        _close(driver)
                        driver = None
                        worker_supervisor.complete("stills are taken in the live event stream")
                        return
                    vendor = vendor_family(driver)
                    credential_gen = core._credential_generation(cfg)
                    next_enumerate = 0.0
                    # Fresh connection (start, or after a back-off): spread the cameras over
                    # one cadence again instead of firing every overdue camera at once.
                    restagger = True

                if now >= next_enumerate:
                    next_enumerate = now + REENUMERATE_SECONDS
                    found = configured_channels(_list_channels(driver, log), startup,
                                                profiles)
                    if restagger:
                        start = now + (STARTUP_DELAY_SECONDS if first_open else 0.0)
                        schedule.set_channels(found, now, start=start, restagger=True)
                    elif found != cameras:
                        schedule.set_channels(found, now)
                    cameras = found
                    first_open = False
                    restagger = False
                    if not cameras:
                        log("no configured camera to sample; "
                                 f"checking again in {int(cadence)}s")
                        _close(driver)
                        driver = None
                        backoff.until = now + cadence
                        continue

                channel = schedule.due(now)
                if channel is None:
                    stop.wait(min(TICK_SECONDS, max(0.05, schedule.seconds_until_due(now))))
                    continue
                if server_capture.owned_by_server(getattr(cfg, "recorder_cloud_id", None),
                                                  channel, now):
                    # The server is scheduling this camera's stills; ours would duplicate.
                    schedule.skip(channel, now)
                    continue

                waiting = spool.count()
                if waiting >= _high_water(spool):
                    schedule.done(channel, now)
                    if not paused_logged:
                        paused_logged = True
                        log(f"paused while {waiting} events wait to "
                                 "upload; recorder events keep priority")
                    continue
                if paused_logged:
                    paused_logged = False
                    log("resumed")

                try:
                    raw = driver.get_snapshot(channel)
                except (NvrUnreachable, NvrAuthFailed, requests.RequestException) as error:
                    # One camera timing out or refused (an account without preview rights on
                    # that channel) is a camera fault unless a recorder re-check fails too,
                    # the same rule as camera_health.classify_snapshot_probe.
                    upper = _recorder_down(driver)
                    if upper is not None:
                        # Not this camera's fault and no still: it keeps its due time, so it is
                        # first in the rotation when the recorder is back.
                        schedule.last_request = now
                        delay = backoff.fail(now, auth=isinstance(upper, NvrAuthFailed)
                                             or core._is_auth_failure(upper))
                        log(f"recorder stopped answering (ch{channel}); "
                                 f"next try in {int(delay)}s ({nvr_health.redact(str(upper))})")
                        _close(driver)
                        driver = None
                        camera_failures = 0
                        continue
                    raw = None
                    log(f"ch{channel} gave no still "
                             f"({type(error).__name__}: {nvr_health.redact(str(error))})")
                except Exception as error:                  # noqa: BLE001 — camera-level
                    raw = None
                    log(f"ch{channel} gave no still "
                             f"({type(error).__name__}: {nvr_health.redact(str(error))})")
                captured = wall()
                schedule.done(channel, now)

                if not usable_still(raw):
                    if raw:
                        log(f"ch{channel} still discarded "
                                 f"({len(raw) // 1024} KB, not a JPEG within the size limit)")
                    camera_failures += 1
                    # Every configured camera failing in a row is the recorder, not a camera.
                    if camera_failures >= max(3, len(cameras)):
                        delay = backoff.fail(now)
                        log(f"{camera_failures} stills in a row failed; "
                                 f"pausing {int(delay)}s")
                        _close(driver)
                        driver = None
                        camera_failures = 0
                    continue

                camera_failures = 0
                backoff.reset()
                event = build_event(vendor, channel, bytes(raw), captured)
                # Stamped like every other event of a bound recorder (the collector's rule);
                # the no-registry path keeps the 5.0.28 row exactly.
                event = event.with_recorder_id(getattr(cfg, "recorder_cloud_id", None))
                spool.add(event.to_json(wall()))
                worker_supervisor.success()
                dropped = spool.trim()
                if dropped:
                    core.log(f"WARNING: spool over capacity, dropped {dropped} oldest events")
            except Exception as error:                      # noqa: BLE001 — never end the thread
                core.worker_fault(name, error)
                _close(driver)
                driver = None
                backoff.fail(now)
    finally:
        _close(driver)

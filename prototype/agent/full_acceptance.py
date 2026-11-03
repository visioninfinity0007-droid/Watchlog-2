#!/usr/bin/env python3
"""Full acceptance test (5.1.2): one bounded, read-only proof of every capability on a site.

Run remotely as the Site Control read action ``run_full_acceptance_test`` (and the subsets
``run_recording_check`` / ``run_archive_check``), or locally with
``watchlog-agent.exe --acceptance-json PATH``. Both use :func:`run_suite`.

Owner rule: no capability is PASS because code exists. Every check returns

* ``PASS``        only with positive evidence gathered now (or runtime evidence that is fresh);
* ``FAIL``        the capability was exercised and answered negatively or with an error;
* ``UNKNOWN``     it could not be judged (with the reason): a prerequisite failed, the time
                  budget ran out, the answer was ambiguous, or the runtime is not observable;
* ``UNSUPPORTED`` this hardware/driver or this site's configuration does not offer it.

UNKNOWN is never promoted to PASS.

The suite is multi-recorder aware: each configured recorder gets its own section, run with
that recorder's own Config, credential and driver (the same routing Site Control uses), in
parallel, under ONE overall time budget (at most 180 s). Every individual check has its own
timeout as well, and a probe that overruns is abandoned on a daemon thread. Nothing here
writes to a recorder: it reads identity, channels, faults, storage, a live still per camera,
an archive search per camera and, optionally, one tiny (6 s) archive clip export.

Result size is bounded: image and clip bytes are never included (only sizes and SHA-256),
cameras and recorders are capped, error text is redacted and truncated, and the whole
document is trimmed below MAX_RESULT_BYTES.

This module imports only the standard library at import time (the operator tool reuses
:func:`format_table`); recorder/archive helpers are imported where they are used.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

PASS, FAIL, UNKNOWN, UNSUPPORTED = "PASS", "FAIL", "UNKNOWN", "UNSUPPORTED"
STATUSES = (PASS, FAIL, UNKNOWN, UNSUPPORTED)
SCOPES = ("agent", "recorder", "camera")
SCHEMA = "watchlog.acceptance.v1"

DEFAULT_BUDGET_SECONDS = 170.0
MAX_BUDGET_SECONDS = 180.0
MIN_CHECK_SECONDS = 1.0          # a check is not started with less budget than this left
SECTION_JOIN_GRACE_SECONDS = 2.0
CLIP_RESERVE_SECONDS = 30.0      # per-camera checks leave this much budget for the clip

TIMEOUTS = {
    "cloud": 20.0, "file": 5.0, "remote_update": 20.0, "recorder_open": 30.0,
    "channels": 20.0, "faults": 15.0, "storage": 15.0, "snapshot": 12.0,
    "archive_open": 30.0, "recording": 25.0, "clip": 60.0, "spool": 10.0,
}

RECORDING_WINDOW_MINUTES = 15
CLIP_SECONDS = 6                 # the evidence clip export is at most this long
CLIP_SETTLE_SECONDS = 90         # ... and ends this far before now (recorder index settle)
CLIP_MAX_BYTES = 16 * 1024 * 1024
STILL_MAX_BYTES = 3 * 1024 * 1024  # incident_evidence.STILL_MAX_BYTES (migration 0058)
MAX_CAMERAS_PER_RECORDER = 64
MAX_RECORDERS = 16
MAX_RESULT_BYTES = 256 * 1024
ERROR_MAX_CHARS = 200
STREAM_FRESH_SECONDS = 150.0     # same rule as the runtime's recorder liveness
HEARTBEAT_STALE_FLOOR_SECONDS = 180.0
ANALYTICS_SAMPLE_MAX_AGE_SECONDS = 900.0
ANALYTICS_STATUS_MAX_AGE_SECONDS = 300.0
RECOVERY_CYCLE_FLOOR_SECONDS = 600.0
SITE_CONTROL_POLL_FLOOR_SECONDS = 120.0
MANIFEST_MAX_BYTES = 1024 * 1024

# Check names (the display labels the operator table prints).
AGENT, CLOUD_AUTH, HEARTBEAT = "Agent", "Cloud auth", "Heartbeat"
LOCAL_ANALYTICS, SITE_CONTROL, REMOTE_UPDATE = "Local analytics", "Site Control", "Remote update"
RECORDER_AUTH, RECORDER_IDENTITY = "Recorder auth", "Recorder identity"
EVENT_STREAM, NATIVE_EVENTS = "Event stream", "Native events"
CAMERA_INVENTORY, STORAGE = "Camera inventory", "Storage"
SNAPSHOT, RECORDING = "Snapshot", "Recording"
ARCHIVE_SEARCH, EVIDENCE_STILL, EVIDENCE_CLIP = "Archive search", "Evidence still", "Evidence clip"
SPOOL, RECOVERY = "Spool", "Recovery"

AGENT_CHECKS = (AGENT, CLOUD_AUTH, HEARTBEAT, LOCAL_ANALYTICS, SITE_CONTROL, REMOTE_UPDATE)
RECORDER_CHECKS = (RECORDER_AUTH, RECORDER_IDENTITY, EVENT_STREAM, NATIVE_EVENTS, SPOOL,
                   RECOVERY, CAMERA_INVENTORY, STORAGE, ARCHIVE_SEARCH, EVIDENCE_STILL,
                   EVIDENCE_CLIP)
CAMERA_CHECKS = (SNAPSHOT, RECORDING)
ALL_CHECKS = AGENT_CHECKS + RECORDER_CHECKS + CAMERA_CHECKS

RECORDING_CHECK_ONLY = frozenset({RECORDER_AUTH, RECORDER_IDENTITY, RECORDING})
ARCHIVE_CHECK_ONLY = frozenset({RECORDER_AUTH, RECORDER_IDENTITY, RECORDING, ARCHIVE_SEARCH,
                                EVIDENCE_CLIP})


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def sanitise(error) -> str:
    """Cloud-safe error text: no URL, credential or recorder address, one line, bounded."""
    if error is None:
        return ""
    text = error if isinstance(error, str) else (str(error) or type(error).__name__)
    try:
        import nvr_health
        text = nvr_health.redact(text) or (type(error).__name__ if not isinstance(error, str)
                                            else "")
    except Exception:  # noqa: BLE001 — without the redactor only the type name is safe
        text = text if isinstance(error, str) and "://" not in text else type(error).__name__
    return text[:ERROR_MAX_CHARS]


def _channel_key(channel) -> tuple:
    text = str(channel)
    return (0, int(text), "") if text.isdigit() else (1, 0, text)


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF header, or None. Reads headers only, no decode."""
    if not data or data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in sof:
            if i + 9 > n:
                return None
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return (width, height) if width and height else None
        if marker == 0xDA or length < 2:
            return None
        i += 2 + length
    return None


def clip_container(head: bytes) -> str | None:
    """The recorded-media container named by its leading bytes, or None if unrecognised."""
    if not head:
        return None
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    if b"DHAV" in head[:16]:
        return "dav"
    if head[:4] == b"IMKH":
        return "hikvision-ps"
    if head[:4] == b"\x00\x00\x01\xba":
        return "mpeg-ps"
    if head[:1] == b"\x47" and len(head) > 188 and head[188:189] == b"\x47":
        return "mpeg-ts"
    return None


def _implements(driver, method: str) -> bool:
    """True when ``driver`` overrides the base NvrDriver default for ``method``."""
    try:
        from drivers.base import NvrDriver
    except Exception:  # noqa: BLE001
        return callable(getattr(driver, method, None))
    fn = getattr(type(driver), method, None)
    if fn is None:
        return callable(getattr(driver, method, None))
    return fn is not getattr(NvrDriver, method, None)


class _Unsupported(Exception):
    """The recorder/driver affirmatively does not offer this capability."""


class _Inconclusive(Exception):
    """The recorder answered, but not in a way that proves anything."""


# ---------------------------------------------------------------------------
# check records and the budgeted runner
# ---------------------------------------------------------------------------

def make_check(name: str, scope: str, status: str, *, recorder_id=None, channel=None,
               supported=None, enabled=None, evidence=None, error=None,
               last_success_at=None, duration_ms: int = 0) -> dict:
    """One check in the result schema. ``error`` is the FAIL error or the UNKNOWN/UNSUPPORTED
    reason; it is sanitised here so no caller can leak an address or credential."""
    if status not in STATUSES:
        status, error = UNKNOWN, f"invalid status {status!r}"
    if supported is None:
        supported = {PASS: True, FAIL: True, UNSUPPORTED: False}.get(status)
    if enabled is None:
        enabled = None if status == UNKNOWN else True
    return {
        "name": name,
        "scope": scope,
        "recorder_id": str(recorder_id) if recorder_id else None,
        "channel": str(channel) if channel is not None else None,
        "status": status,
        "supported": supported,
        "enabled": enabled,
        "healthy": True if status == PASS else (False if status == FAIL else None),
        "last_success_at": last_success_at,
        "last_error": (sanitise(error) or None) if status != PASS else None,
        "evidence": dict(evidence or {}),
        "duration_ms": int(duration_ms),
    }


class Budget:
    def __init__(self, seconds: float, monotonic=time.monotonic):
        self._monotonic = monotonic
        self.started = monotonic()
        self.deadline = self.started + float(seconds)

    def remaining(self, reserve: float = 0.0) -> float:
        return self.deadline - reserve - self._monotonic()

    def elapsed_ms(self) -> int:
        return int((self._monotonic() - self.started) * 1000)


def _timed(fn, timeout: float):
    """Run ``fn()`` on a daemon thread. -> ("ok", value) | ("error", exc) | ("timeout", None).

    A probe that wedges (a recorder that accepts TCP and never answers) is abandoned; the
    thread is a daemon and every driver call underneath has its own socket timeouts."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — SystemExit from a missing config too
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True, name="acceptance-probe")
    worker.start()
    worker.join(max(0.0, timeout))
    if worker.is_alive():
        return "timeout", None
    if "error" in box:
        return "error", box["error"]
    return "ok", box.get("value")


class _Runner:
    """Runs checks against one shared Budget and records them in order (into ``checks``,
    which the caller may hold so a section abandoned at the deadline keeps what it has)."""

    def __init__(self, budget: Budget, now, *, only=None, reserve: float = 0.0,
                 checks: list | None = None):
        self.budget = budget
        self.now = now
        self.only = frozenset(only) if only else None
        self.reserve = reserve
        self.checks: list[dict] = checks if checks is not None else []

    def wanted(self, name: str) -> bool:
        return self.only is None or name in self.only

    def add(self, check: dict) -> dict:
        if self.wanted(check["name"]):
            self.checks.append(check)
        return check

    def skip(self, name, scope, reason, **ids) -> dict:
        return self.add(make_check(name, scope, UNKNOWN, error=reason, **ids))

    def probe(self, name: str, timeout: float, fn, *, reserve: float | None = None):
        """Call ``fn`` under min(timeout, budget left). -> (state, value, duration_ms)."""
        left = self.budget.remaining(self.reserve if reserve is None else reserve)
        if left < MIN_CHECK_SECONDS:
            return "budget", None, 0
        started = time.monotonic()
        state, value = _timed(fn, min(float(timeout), left))
        return state, value, int((time.monotonic() - started) * 1000)

    def run(self, name: str, scope: str, timeout: float, fn, *, reserve=None,
            required: bool = False, **ids) -> dict:
        """Run one check. ``fn()`` returns (status, extra) where extra holds make_check kwargs.

        A check outside ``only`` is not run, unless ``required`` (a prerequisite such as the
        recorder login or the identity gate): it then runs but is not recorded."""
        if not self.wanted(name) and not required:
            return make_check(name, scope, UNKNOWN, error="not requested", **ids)
        state, value, ms = self.probe(name, timeout, fn, reserve=reserve)
        if state == "budget":
            return self.skip(name, scope, "time_budget_exhausted", **ids)
        if state == "timeout":
            return self.add(make_check(name, scope, UNKNOWN, duration_ms=ms,
                                       error=f"timed out after {int(timeout)}s", **ids))
        if state == "error":
            return self.add(make_check(name, scope, FAIL, duration_ms=ms, error=value, **ids))
        status, extra = value
        extra = dict(extra or {})
        if status == PASS and "last_success_at" not in extra:
            extra["last_success_at"] = _iso(self.now())
        return self.add(make_check(name, scope, status, duration_ms=ms, **ids, **extra))


# ---------------------------------------------------------------------------
# environment + targets
# ---------------------------------------------------------------------------

@dataclass
class Target:
    """One recorder section: its cloud id, display name and bound Config."""
    recorder_id: str | None
    name: str
    cfg: object
    primary: bool = False
    holder: dict | None = field(default=None, repr=False)


class Env:
    """I/O the suite needs. The Agent binds the real implementation
    (site_maintenance.AgentEnv); tests bind fakes. Every method may raise."""

    command_id: str | None = None

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    def agent_meta(self) -> dict:                       # {version, build_sha, build_channel}
        raise NotImplementedError

    def state(self) -> dict:
        return {}

    def cloud_auth(self) -> dict:                       # wl_agent_preflight_auth answer
        raise NotImplementedError

    def runtime_health(self) -> dict | None:            # Secrets/runtime-health.json
        return None

    def base_config(self):
        return None

    def open_driver(self, cfg):                          # (driver, DeviceInfo), unverified
        raise NotImplementedError

    def open_archive(self, cfg, live):                   # (archive driver, info)
        return live

    def holder(self, recorder_id) -> dict | None:        # live runtime holder, in-process
        return None

    def spool_probe(self, cfg) -> dict:                  # {exists, depth, max_rows, overflow}
        raise NotImplementedError

    def analytics_status(self) -> dict | None:
        return None

    def fetch_manifest(self, url: str) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# agent-scope checks
# ---------------------------------------------------------------------------

def _age(now: datetime, stamp) -> float | None:
    parsed = _parse_utc(stamp)
    return None if parsed is None else max(0.0, (now - parsed).total_seconds())


def _agent_checks(env: Env, run: _Runner) -> None:
    now = env.now()

    def agent():
        meta = env.agent_meta() or {}
        evidence = {k: meta.get(k) for k in ("version", "build_sha", "build_channel")}
        if not meta.get("version"):
            return FAIL, {"error": "agent version unavailable", "evidence": evidence}
        if not meta.get("build_sha"):
            return UNKNOWN, {"error": "build_sha_not_stamped", "evidence": evidence}
        return PASS, {"evidence": evidence}
    run.run(AGENT, "agent", TIMEOUTS["file"], agent)

    def cloud():
        state = env.state() or {}
        if not state.get("agent_id") or not state.get("agent_key"):
            return FAIL, {"error": "this site is not enrolled (no local agent identity)"}
        auth = env.cloud_auth() or {}
        same = (str(auth.get("agent_id") or "") == str(state.get("agent_id"))
                and str(auth.get("site_id") or "") == str(state.get("site_id") or ""))
        evidence = {"agent_id": auth.get("agent_id"), "site_id": auth.get("site_id")}
        if not same:
            return FAIL, {"error": "cloud answered for a different agent/site", "evidence": evidence}
        return PASS, {"evidence": evidence}
    run.run(CLOUD_AUTH, "agent", TIMEOUTS["cloud"], cloud)

    health_box: dict = {}

    def heartbeat():
        health = env.runtime_health()
        health_box["health"] = health
        stamp = (health or {}).get("heartbeat_at")
        age = _age(now, stamp)
        if age is None:
            return UNKNOWN, {"error": "no heartbeat recorded by the running Agent"}
        cfg = env.base_config()
        limit = max(3.0 * float(getattr(cfg, "heartbeat_seconds", 60) or 60),
                    HEARTBEAT_STALE_FLOOR_SECONDS)
        evidence = {"heartbeat_at": stamp, "age_s": int(age), "stale_after_s": int(limit)}
        if age > limit:
            return FAIL, {"error": f"last heartbeat {int(age)}s ago", "evidence": evidence}
        return PASS, {"evidence": evidence, "last_success_at": stamp}
    run.run(HEARTBEAT, "agent", TIMEOUTS["file"], heartbeat)

    def analytics():
        cfg = env.base_config()
        enabled = getattr(cfg, "analytics_enabled", None)
        if enabled is False:
            return UNSUPPORTED, {"error": "local analytics is disabled on this site",
                                 "enabled": False, "supported": None}
        if enabled is None:
            return UNKNOWN, {"error": "this Agent build has no local analytics runtime"}
        status = env.analytics_status()
        if not status:
            return UNKNOWN, {"error": "no analytics status written by the running Agent"}
        evidence = {k: status.get(k) for k in ("detector_available", "last_sample_at",
                                                "updated_at", "samples_ok", "sample_errors",
                                                "active_cameras")}
        updated_age = _age(now, status.get("updated_at"))
        if updated_age is None or updated_age > ANALYTICS_STATUS_MAX_AGE_SECONDS:
            return FAIL, {"error": "analytics worker status is stale", "evidence": evidence}
        if not status.get("detector_available"):
            return FAIL, {"error": "the on-site detector is not loaded", "evidence": evidence}
        sample_age = _age(now, status.get("last_sample_at"))
        if sample_age is None:
            return UNKNOWN, {"error": "no analytics sample taken yet", "evidence": evidence}
        evidence["sample_age_s"] = int(sample_age)
        if sample_age > ANALYTICS_SAMPLE_MAX_AGE_SECONDS:
            return FAIL, {"error": f"last analytics sample {int(sample_age)}s ago",
                          "evidence": evidence}
        return PASS, {"evidence": evidence, "last_success_at": status.get("last_sample_at")}
    run.run(LOCAL_ANALYTICS, "agent", TIMEOUTS["file"], analytics)

    def site_control():
        if env.command_id:
            return PASS, {"evidence": {"command_id": str(env.command_id),
                                       "via": "site_control"}}
        health = health_box.get("health") if "health" in health_box else env.runtime_health()
        stamp = (health or {}).get("site_control_poll_at")
        age = _age(now, stamp)
        if age is None:
            return UNKNOWN, {"error": "no Site Control poll recorded by the running Agent"}
        cfg = env.base_config()
        limit = max(4.0 * float(getattr(cfg, "site_control_seconds", 15) or 15),
                    SITE_CONTROL_POLL_FLOOR_SECONDS)
        evidence = {"poll_at": stamp, "age_s": int(age)}
        if age > limit:
            return FAIL, {"error": f"Site Control last polled {int(age)}s ago",
                          "evidence": evidence}
        return PASS, {"evidence": evidence, "last_success_at": stamp}
    run.run(SITE_CONTROL, "agent", TIMEOUTS["file"], site_control)

    def remote_update():
        cfg = env.base_config()
        url = str(getattr(cfg, "update_url", "") or "").strip()
        key = str(getattr(cfg, "update_public_key", "") or "").strip()
        evidence = {"https": url.lower().startswith("https://"), "key_present": bool(key),
                    "channel": getattr(cfg, "update_channel", None)}
        if not url:
            return UNSUPPORTED, {"error": "no update manifest URL configured",
                                 "enabled": False, "supported": None, "evidence": evidence}
        if not evidence["https"]:
            return FAIL, {"error": "update manifest URL is not HTTPS", "evidence": evidence}
        if not key:
            return FAIL, {"error": "no update signature key configured", "evidence": evidence}
        import updater
        manifest = updater.parse_manifest(env.fetch_manifest(url))
        verified = updater.verify_manifest_signature(manifest, key)
        evidence["signature"] = {True: "verified", False: "invalid", None: "unverifiable"}[verified]
        channel = str(getattr(cfg, "update_channel", "") or "")
        evidence["release_listed"] = bool(updater.select_release(manifest, channel)) \
            if channel else None
        if verified is True:
            return PASS, {"evidence": evidence}
        if verified is False:
            return FAIL, {"error": "update manifest signature does not verify",
                          "evidence": evidence}
        return UNKNOWN, {"error": "manifest signature could not be verified on this PC",
                         "evidence": evidence}
    run.run(REMOTE_UPDATE, "agent", TIMEOUTS["remote_update"], remote_update)


# ---------------------------------------------------------------------------
# recorder-scope runtime evidence (live holder in-process, else runtime-health.json)
# ---------------------------------------------------------------------------

def _runtime_view(env: Env, target: Target) -> dict:
    """Event-stream/recovery evidence for one recorder from the running Agent.

    {"source": "holder"|"runtime_health"|None, ...}. In-process (remote command) this is the
    collector's own holder; from the CLI it is the protected runtime-health proof."""
    holder = target.holder if target.holder is not None else env.holder(target.recorder_id)
    if holder is not None:
        return {"source": "holder", "holder": holder}
    health = env.runtime_health() or {}
    rows = health.get("recorders")
    if isinstance(rows, list) and rows:
        for row in rows:
            if isinstance(row, dict) and target.recorder_id \
                    and str(row.get("recorder_id")) == str(target.recorder_id):
                return {"source": "runtime_health", "row": row, "health": health}
        return {"source": None}
    if health:
        return {"source": "runtime_health", "row": {
            "event_stream": health.get("event_stream"),
            "recorder_seen_at": health.get("recorder_seen_at"),
        }, "health": health, "singleton": True}
    return {"source": None}


def _stream_checks(env: Env, run: _Runner, target: Target, view: dict) -> None:
    rid = target.recorder_id
    now = env.now()
    mono = env.monotonic()

    def event_stream():
        if view["source"] == "holder":
            holder = view["holder"]
            drv = holder.get("live_driver")
            stamps = [s for s in (float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0),
                                  float(holder.get("recorder_live_at") or 0.0)) if s]
            if not stamps:
                return FAIL, {"error": "the live collector has not reached this recorder"}
            age = max(0.0, mono - max(stamps))
            evidence = {"activity_age_s": int(age), "connected_driver": drv is not None}
            if age >= STREAM_FRESH_SECONDS:
                return FAIL, {"error": f"no live stream activity for {int(age)}s",
                              "evidence": evidence}
            return PASS, {"evidence": evidence,
                          "last_success_at": _iso(now - timedelta(seconds=age))}
        if view["source"] == "runtime_health":
            row = view["row"]
            if view.get("singleton"):
                age = _age(now, row.get("recorder_seen_at"))
                if age is None:
                    return FAIL, {"error": "the running Agent has not seen this recorder live"}
                evidence = {"recorder_seen_at": row.get("recorder_seen_at"), "age_s": int(age)}
                limit = STREAM_FRESH_SECONDS + HEARTBEAT_STALE_FLOOR_SECONDS
                if age > limit:
                    return FAIL, {"error": f"recorder last live {int(age)}s ago",
                                  "evidence": evidence}
                return PASS, {"evidence": evidence, "last_success_at": row.get("recorder_seen_at")}
            evidence = {"live": row.get("live"), "last_live_at": row.get("last_live_at")}
            heartbeat_age = _age(now, (view.get("health") or {}).get("heartbeat_at"))
            if heartbeat_age is None or heartbeat_age > HEARTBEAT_STALE_FLOOR_SECONDS:
                return UNKNOWN, {"error": "the running Agent's recorder view is stale",
                                 "evidence": evidence}
            if row.get("live") is True:
                return PASS, {"evidence": evidence, "last_success_at": row.get("last_live_at")}
            return FAIL, {"error": "this recorder's live stream is down", "evidence": evidence}
        return UNKNOWN, {"error": "runtime_not_observable"}
    run.run(EVENT_STREAM, "recorder", TIMEOUTS["file"], event_stream, recorder_id=rid)

    def native_events():
        if view["source"] == "holder":
            stream = view["holder"].get("event_stream")
        elif view["source"] == "runtime_health":
            stream = view["row"].get("event_stream")
        else:
            return UNKNOWN, {"error": "runtime_not_observable"}
        if not isinstance(stream, dict):
            return UNKNOWN, {"error": "the collector has not reported its event subscription"}
        connected = stream.get("connected")
        evidence = {"connected": connected, "connected_at": stream.get("connected_at"),
                    "last_frame_at": stream.get("last_frame_at")}
        frame_age = _age(now, stream.get("last_frame_at"))
        if frame_age is not None:
            evidence["last_event_age_s"] = int(frame_age)
        if connected is True:
            return PASS, {"evidence": evidence,
                          "last_success_at": stream.get("last_frame_at")
                          or stream.get("connected_at")}
        if connected is False:
            return FAIL, {"error": stream.get("last_error") or "event subscription is down",
                          "evidence": evidence}
        return UNKNOWN, {"error": "this driver does not report its event subscription",
                         "evidence": evidence}
    run.run(NATIVE_EVENTS, "recorder", TIMEOUTS["file"], native_events, recorder_id=rid)


def _spool_recovery_checks(env: Env, run: _Runner, target: Target, view: dict) -> None:
    rid, cfg = target.recorder_id, target.cfg
    holder = view.get("holder") if view["source"] == "holder" else None

    def spool():
        if holder is not None and holder.get("upload_degraded"):
            return FAIL, {"error": f"event queue unavailable: {holder['upload_degraded']}"}
        probe = env.spool_probe(cfg) or {}
        if not probe.get("exists"):
            return UNKNOWN, {"error": "the event queue file has not been created yet"}
        depth, cap = int(probe.get("depth") or 0), int(probe.get("max_rows") or 0)
        evidence = {"depth": depth, "max_rows": cap, "overflow_pending": bool(probe.get("overflow"))}
        if probe.get("overflow"):
            return FAIL, {"error": "the event queue overflowed; an archive recovery is pending",
                          "evidence": evidence}
        if cap and depth >= int(cap * 0.9):
            return FAIL, {"error": f"the event queue is near capacity ({depth}/{cap})",
                          "evidence": evidence}
        return PASS, {"evidence": evidence}
    run.run(SPOOL, "recorder", TIMEOUTS["spool"], spool, recorder_id=rid)

    def recovery():
        if not getattr(cfg, "recovery_enabled", True):
            return UNSUPPORTED, {"error": "outage recovery is disabled on this site",
                                 "enabled": False, "supported": None}
        if holder is None:
            return UNKNOWN, {"error": "runtime_not_observable"}
        if holder.get("recovery_waiting_for_inventory"):
            return UNKNOWN, {"error": "recovery is waiting for the camera inventory"}
        evidence = {"last_ok_at": holder.get("recovery_last_ok_at")}
        if holder.get("recovery_last_error"):
            return FAIL, {"error": f"last recovery cycle failed: {holder['recovery_last_error']}",
                          "evidence": evidence}
        cycle = holder.get("recovery_cycle_at")
        if not cycle:
            return UNKNOWN, {"error": "no recovery cycle observed yet", "evidence": evidence}
        age = max(0.0, env.monotonic() - float(cycle))
        limit = max(2.0 * float(getattr(cfg, "recovery_seconds", 300) or 300) + 120.0,
                    RECOVERY_CYCLE_FLOOR_SECONDS)
        evidence["last_cycle_age_s"] = int(age)
        if age > limit:
            return FAIL, {"error": f"recovery worker has not cycled for {int(age)}s",
                          "evidence": evidence}
        return PASS, {"evidence": evidence,
                      "last_success_at": holder.get("recovery_last_ok_at")
                      or _iso(env.now() - timedelta(seconds=age))}
    run.run(RECOVERY, "recorder", TIMEOUTS["file"], recovery, recorder_id=rid)


# ---------------------------------------------------------------------------
# recorder + camera checks against the live device
# ---------------------------------------------------------------------------

def _fingerprint_serial(identity_fingerprint) -> str | None:
    text = str(identity_fingerprint or "").strip()
    if not text.lower().startswith("serial:"):
        return None
    return text[len("serial:"):].strip().upper() or None


def _archive_search(archive, channel, start: datetime, end: datetime) -> dict:
    """One bounded archive search on ``channel``.

    -> {"found": n, "method": ..., "latest_end": datetime|None}. Dahua uses
    dahua_archive.find_recordings, Hikvision hikvision_archive.search_recordings, any other
    archive transport (e.g. an ONVIF-mapped native reader) its enumerate_historical_events.
    Raises _Unsupported (affirmative), _Inconclusive (ambiguous) or a DriverError."""
    name = getattr(archive, "name", "")
    mapped = hasattr(archive, "channel_map")
    if name == "dahua-cgi" and not mapped:
        import dahua_archive
        rows = dahua_archive.find_recordings(archive, str(channel), start, end, max_items=2)
        return {"found": len(rows), "method": "dahua_media_file_find", "latest_end": None}
    if name == "hikvision-isapi" and not mapped:
        import hikvision_archive
        try:
            res = hikvision_archive.search_recordings(archive, str(channel), start, end, limit=2)
        except hikvision_archive.ArchiveRejected as error:
            if getattr(error, "affirmative", False):
                raise _Unsupported("archive search not supported on this firmware") from error
            raise
        rows = [r for r in res.get("matches") or []
                if hikvision_archive._overlaps(r, start, end) is not False]
        if not rows and (res.get("incomplete") or res.get("next_offset") is not None):
            raise _Inconclusive("archive answer incomplete")
        ends = [hikvision_archive._parse(r.get("end")) for r in rows]
        ends = [e for e in ends if e is not None]
        return {"found": len(rows), "method": "hikvision_isapi_search",
                "latest_end": max(ends) if ends else None}
    cap = None
    if hasattr(archive, "historical_capability"):
        cap = archive.historical_capability() or {}
    if not hasattr(archive, "enumerate_historical_events") or (
            isinstance(cap, dict) and cap.get("segments") == "unsupported"):
        raise _Unsupported("this recorder transport exposes no searchable archive")
    page = archive.enumerate_historical_events(str(channel), start, end, None, 2) or {}
    status = str(page.get("status") or "")
    if status == "unsupported":
        raise _Unsupported("this recorder transport exposes no searchable archive")
    if status != "supported":
        raise _Inconclusive(f"archive answer {status or 'empty'}")
    events = [e for e in page.get("events") or [] if e]
    ends = [_parse_utc((e.get("segment") or {}).get("end")) for e in events
            if isinstance(e, dict)]
    ends = [e for e in ends if e is not None]
    return {"found": len(events), "method": "enumerate",
            "latest_end": max(ends) if ends else None}


def _close(driver) -> None:
    if driver is None:
        return
    try:
        driver.close()
    except Exception:  # noqa: BLE001
        pass


def _channel_row(channel) -> dict:
    if isinstance(channel, dict):
        return {"channel": str(channel.get("channel")),
                "enabled": bool(channel.get("enabled", True))}
    return {"channel": str(getattr(channel, "channel", channel)),
            "enabled": bool(getattr(channel, "enabled", True))}


def _recorder_section(env: Env, target: Target, budget: Budget, *, only, include_clip: bool,
                      channels_filter, checks: list | None = None,
                      hardware: dict | None = None) -> tuple[list[dict], dict]:
    """All checks of one recorder, in a fixed order. -> (checks, hardware).

    ``checks`` and ``hardware`` are filled as the section runs, so a section the deadline
    abandons still reports what it proved."""
    rid, cfg = target.recorder_id, target.cfg
    clip_wanted = include_clip and (only is None or EVIDENCE_CLIP in only)
    reserve = CLIP_RESERVE_SECONDS if clip_wanted else 0.0
    run = _Runner(budget, env.now, only=only, checks=checks)
    if hardware is None:
        hardware = {}
    hardware.update({"vendor": None, "model": None, "firmware": None, "serial": None})
    view = _runtime_view(env, target)
    holder = view.get("holder") if view["source"] == "holder" else None
    mapping = {}
    if holder is not None and isinstance(holder.get("camera_mapping"), dict):
        mapping = {str(k): str(v) for k, v in holder["camera_mapping"].items()}
    opened: dict = {}

    # -- Recorder auth (always runs: every device check needs the session) ----------
    def auth():
        if getattr(cfg, "credential_error", None):
            return FAIL, {"error": "the recorder login cannot be read on this PC"}
        try:
            from drivers.base import NvrAuthFailed
        except Exception:  # noqa: BLE001
            NvrAuthFailed = ()  # noqa: N806
        try:
            driver, info = env.open_driver(cfg)
        except NvrAuthFailed as error:
            return FAIL, {"error": "the recorder rejected the login: " + sanitise(error),
                          "evidence": {"auth": "rejected"}}
        opened["driver"], opened["info"] = driver, info
        for key in ("vendor", "model", "firmware", "serial"):
            hardware[key] = getattr(info, key, None)
        return PASS, {"evidence": {"vendor": hardware["vendor"], "model": hardware["model"],
                                   "firmware": hardware["firmware"],
                                   "driver": getattr(driver, "name", None)}}
    auth_check = run.run(RECORDER_AUTH, "recorder", TIMEOUTS["recorder_open"], auth,
                         required=True, recorder_id=rid)
    driver, info = opened.get("driver"), opened.get("info")
    if auth_check["status"] != PASS and driver is not None:
        # The open finished after the check was abandoned: never use that late session.
        _close(driver)
        driver = info = None
    blocked = None if driver is not None else "recorder_not_open"

    # -- Recorder identity (always runs: never test the wrong device) ----------------
    def identity():
        expected = _fingerprint_serial(getattr(cfg, "recorder_identity_fingerprint", None))
        observed = str(getattr(info, "serial", "") or "").strip().upper() or None
        evidence = {"observed_serial": observed, "registry_serial": expected}
        if expected and observed:
            if expected != observed:
                return FAIL, {"error": "the device reports a different serial than the "
                                       "registered recorder", "evidence": evidence}
            return PASS, {"evidence": evidence}
        if not observed:
            return UNKNOWN, {"error": "the recorder does not report a serial number",
                             "evidence": evidence}
        return UNKNOWN, {"error": "no serial is pinned for this recorder in the registry",
                         "evidence": evidence}
    if blocked:
        run.skip(RECORDER_IDENTITY, "recorder", blocked, recorder_id=rid)
    else:
        ident = run.run(RECORDER_IDENTITY, "recorder", TIMEOUTS["file"], identity,
                        required=True, recorder_id=rid)
        if ident["status"] == FAIL:
            blocked = "recorder_identity_mismatch"

    # -- runtime evidence (no device needed) -----------------------------------------
    _stream_checks(env, run, target, view)
    _spool_recovery_checks(env, run, target, view)

    # -- Camera inventory ---------------------------------------------------------------
    live_channels: list = []

    def inventory():
        rows = [_channel_row(c) for c in (driver.list_channels() or [])]
        live_channels.extend(rows)
        reported = {r["channel"] for r in rows}
        evidence = {"recorder_channels": len(rows),
                    "enabled_channels": sum(1 for r in rows if r["enabled"])}
        if not mapping:
            return UNKNOWN, {"error": "the WatchLog camera list is not available to this check",
                             "evidence": evidence}
        missing = sorted(set(mapping) - reported, key=_channel_key)
        evidence.update({"cloud_cameras": len(mapping),
                         "unmapped_channels": len(reported - set(mapping))})
        if missing:
            evidence["missing_channels"] = missing[:32]
            return FAIL, {"error": f"{len(missing)} WatchLog camera(s) not reported by the "
                                   "recorder", "evidence": evidence}
        return PASS, {"evidence": evidence}
    device_checks = (CAMERA_INVENTORY, SNAPSHOT, RECORDING, ARCHIVE_SEARCH, EVIDENCE_STILL,
                     EVIDENCE_CLIP)
    need_channels = any(run.wanted(n) for n in device_checks)
    if blocked:
        run.skip(CAMERA_INVENTORY, "recorder", blocked, recorder_id=rid)
    elif need_channels:
        run.run(CAMERA_INVENTORY, "recorder", TIMEOUTS["channels"], inventory, required=True,
                recorder_id=rid)

    # -- current faults (input to the camera checks) --------------------------------------
    video_loss: set = set()
    if not blocked and need_channels:
        state, value, _ms = run.probe("faults", TIMEOUTS["faults"],
                                      lambda: driver.current_faults() or {})
        if state == "ok" and isinstance(value, dict) and value.get("supported"):
            video_loss = {str(c) for c in value.get("video_loss") or []}

    # -- Storage ------------------------------------------------------------------------------
    def storage():
        status = driver.storage_status() or {}
        state = status.get("state")
        evidence = {k: v for k, v in status.items()
                    if k in ("state", "reason", "disks", "total_mb", "free_mb")
                    and not isinstance(v, (dict, list))}
        if not status.get("supported"):
            return UNSUPPORTED, {"error": "this driver cannot read recorder storage",
                                 "evidence": evidence}
        if state == "ok":
            return PASS, {"evidence": evidence}
        if state in ("fault", "degraded"):
            reason = status.get("reason")
            return FAIL, {"error": f"recorder storage {state}" + (f": {reason}" if reason else ""),
                          "evidence": evidence}
        return UNKNOWN, {"error": "recorder storage state unknown", "evidence": evidence}
    if blocked:
        run.skip(STORAGE, "recorder", blocked, recorder_id=rid)
    elif run.wanted(STORAGE):
        run.run(STORAGE, "recorder", TIMEOUTS["storage"], storage, recorder_id=rid)

    # -- which cameras: WatchLog's cameras for this recorder, else its enabled channels ----------
    if mapping:
        cams = sorted(set(mapping), key=_channel_key)
    else:
        ignored = {str(p.get("channel")) for p in (getattr(cfg, "camera_profiles", None) or [])
                   if isinstance(p, dict) and p.get("monitored") is False
                   and p.get("channel") is not None}
        cams = sorted({r["channel"] for r in live_channels if r["enabled"]} - ignored,
                      key=_channel_key)
    if channels_filter:
        wanted = {str(c) for c in channels_filter}
        cams = sorted(wanted, key=_channel_key) if blocked or not cams \
            else [c for c in cams if c in wanted] or sorted(wanted, key=_channel_key)
    untested = max(0, len(cams) - MAX_CAMERAS_PER_RECORDER)
    cams = cams[:MAX_CAMERAS_PER_RECORDER]
    reported = {r["channel"] for r in live_channels}

    def camera_blocker(ch):
        if blocked:
            return blocked
        if live_channels and ch not in reported:
            return "channel not reported by the recorder"
        if not live_channels:
            return "recorder channel list unavailable"
        return None

    # -- Snapshot (per camera) -------------------------------------------------------------------
    snaps: dict = {}
    for ch in cams:
        ids = {"recorder_id": rid, "channel": ch}
        why = camera_blocker(ch)
        if why:
            run.skip(SNAPSHOT, "camera", why, **ids)
            continue

        def snapshot(ch=ch):
            if ch in video_loss:
                return FAIL, {"error": "the recorder reports video loss on this camera",
                              "evidence": {"video_loss": True}}
            if not _implements(driver, "get_snapshot"):
                return UNSUPPORTED, {"error": "this driver cannot take a live still"}
            data = driver.get_snapshot(ch)
            if not data:
                return FAIL, {"error": "the recorder returned no image"}
            evidence = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if data[:3] != b"\xff\xd8\xff":
                return FAIL, {"error": "the recorder answered with something that is not a JPEG",
                              "evidence": evidence}
            dims = jpeg_dimensions(data)
            if dims:
                evidence["width"], evidence["height"] = dims
            snaps[ch] = len(data)
            return PASS, {"evidence": evidence}
        if run.wanted(SNAPSHOT) or run.wanted(EVIDENCE_STILL):
            run.run(SNAPSHOT, "camera", TIMEOUTS["snapshot"], snapshot, reserve=reserve,
                    required=True, **ids)

    # -- Recording (per camera): an archive search over the last 15 min --------------------------
    archive_box: dict = {}
    recordings: dict = {}
    latest_end: dict = {}
    want_archive = any(run.wanted(n) for n in (RECORDING, ARCHIVE_SEARCH, EVIDENCE_CLIP))
    if not blocked and want_archive and cams and live_channels:
        def open_archive():
            archive_box["archive"], _ainfo = env.open_archive(cfg, (driver, info))
            return True
        state, value, _ms = run.probe("archive_open", TIMEOUTS["archive_open"], open_archive,
                                      reserve=reserve)
        if state == "error":
            archive_box["error"] = value
        elif state in ("timeout", "budget"):
            archive_box["reason"] = ("time_budget_exhausted" if state == "budget"
                                     else "the recorder archive did not open in time")
            archive_box.pop("archive", None)
    archive = archive_box.get("archive")
    now = env.now()
    start = now - timedelta(minutes=RECORDING_WINDOW_MINUTES)
    for ch in cams:
        ids = {"recorder_id": rid, "channel": ch}
        why = camera_blocker(ch)
        if not want_archive:
            break
        if why:
            run.skip(RECORDING, "camera", why, **ids)
            continue
        if ch in video_loss:
            recordings[ch] = "skipped"
            run.add(make_check(RECORDING, "camera", UNKNOWN, error="video_loss",
                               evidence={"video_loss": True}, **ids))
            continue
        if archive is None:
            if archive_box.get("error") is not None:
                recordings[ch] = "error"
                run.add(make_check(RECORDING, "camera", FAIL, error=archive_box["error"], **ids))
            else:
                run.skip(RECORDING, "camera", archive_box.get("reason") or "archive_not_opened",
                         **ids)
            continue

        def recording(ch=ch):
            try:
                found = _archive_search(archive, ch, start, now)
            except _Unsupported as exc:
                recordings[ch] = "unsupported"
                return UNSUPPORTED, {"error": str(exc)}
            except _Inconclusive as exc:
                recordings[ch] = "inconclusive"
                return UNKNOWN, {"error": str(exc)}
            except BaseException:
                recordings[ch] = "error"
                raise
            evidence = {"segments_found": found["found"], "method": found["method"],
                        "window_minutes": RECORDING_WINDOW_MINUTES}
            if found["found"]:
                recordings[ch] = "found"
                if found.get("latest_end") is not None:
                    latest_end[ch] = found["latest_end"]
                return PASS, {"evidence": evidence}
            recordings[ch] = "empty"
            return FAIL, {"error": f"no recording found in the last {RECORDING_WINDOW_MINUTES} "
                                   "min", "evidence": evidence}
        run.run(RECORDING, "camera", TIMEOUTS["recording"], recording, reserve=reserve,
                required=True, **ids)

    # -- Archive search: at least one search was answered -----------------------------------------
    def archive_search():
        answered = sorted((c for c, v in recordings.items() if v in ("found", "empty")),
                          key=_channel_key)
        evidence = {"searches": len(recordings), "answered": len(answered)}
        if answered:
            evidence["channel"] = answered[0]
            return PASS, {"evidence": evidence}
        outcomes = set(recordings.values()) - {"skipped"}
        if outcomes and outcomes == {"unsupported"}:
            return UNSUPPORTED, {"error": "this recorder exposes no searchable archive",
                                 "evidence": evidence}
        if "error" in outcomes:
            return FAIL, {"error": "every archive search failed", "evidence": evidence}
        return UNKNOWN, {"error": archive_box.get("reason") or "no archive search was answered",
                         "evidence": evidence}
    if blocked:
        run.skip(ARCHIVE_SEARCH, "recorder", blocked, recorder_id=rid)
    elif run.wanted(ARCHIVE_SEARCH):
        run.run(ARCHIVE_SEARCH, "recorder", TIMEOUTS["file"], archive_search, recorder_id=rid)

    # -- Evidence still: the incident still path is driver.get_snapshot, 3 MiB bound -----------------
    def evidence_still():
        if snaps:
            ch = sorted(snaps, key=_channel_key)[0]
            evidence = {"channel": ch, "bytes": snaps[ch], "limit_bytes": STILL_MAX_BYTES}
            if snaps[ch] > STILL_MAX_BYTES:
                return FAIL, {"error": "the still exceeds the 3 MiB evidence limit",
                              "evidence": evidence}
            return PASS, {"evidence": evidence}
        statuses = {c["status"] for c in run.checks if c["name"] == SNAPSHOT}
        if statuses == {UNSUPPORTED}:
            return UNSUPPORTED, {"error": "this driver cannot take a still"}
        if statuses and statuses <= {FAIL}:
            return FAIL, {"error": "no camera produced a still"}
        return UNKNOWN, {"error": "no still was obtained"}
    if blocked:
        run.skip(EVIDENCE_STILL, "recorder", blocked, recorder_id=rid)
    elif run.wanted(EVIDENCE_STILL):
        run.run(EVIDENCE_STILL, "recorder", TIMEOUTS["file"], evidence_still, recorder_id=rid)

    # -- Evidence clip: one tiny (<= 6 s) archive export on a recorded camera ------------------------
    def evidence_clip(ch):
        if not _implements(archive, "get_clip"):
            return UNSUPPORTED, {"error": "this recorder transport cannot export footage"}
        end = env.now() - timedelta(seconds=CLIP_SETTLE_SECONDS)
        if ch in latest_end and latest_end[ch] - timedelta(seconds=2) < end:
            end = latest_end[ch] - timedelta(seconds=2)
        begin = end - timedelta(seconds=CLIP_SECONDS)
        evidence = {"channel": ch, "window_seconds": CLIP_SECONDS, "window_end": _iso(end)}
        try:
            data = archive.get_clip(ch, begin, end)
        except Exception as error:  # noqa: BLE001 — a typed ClipError says unsupported/no media
            if getattr(error, "unsupported", False):
                return UNSUPPORTED, {"error": "the recorder does not export footage",
                                     "evidence": evidence}
            if type(error).__name__ == "ClipNoRecording":
                return UNKNOWN, {"error": "no recording in the clip window", "evidence": evidence}
            raise
        if not data:
            # dahua_archive.get_clip searches first and returns None for an empty window.
            return UNKNOWN, {"error": "no recording in the clip window", "evidence": evidence}
        evidence.update({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if len(data) > CLIP_MAX_BYTES:
            return FAIL, {"error": "the exported clip exceeds the acceptance size bound",
                          "evidence": evidence}
        kind = clip_container(data[:512])
        evidence["container"] = kind
        if not kind:
            return FAIL, {"error": "the exported clip's container is not recognised",
                          "evidence": evidence}
        return PASS, {"evidence": evidence}
    if run.wanted(EVIDENCE_CLIP):
        recorded = sorted((c for c, v in recordings.items() if v == "found"), key=_channel_key)
        if not include_clip:
            run.add(make_check(EVIDENCE_CLIP, "recorder", UNKNOWN, enabled=False,
                               error="skipped: include_clip is false", recorder_id=rid))
        elif blocked:
            run.skip(EVIDENCE_CLIP, "recorder", blocked, recorder_id=rid)
        elif archive is None:
            run.skip(EVIDENCE_CLIP, "recorder", archive_box.get("reason") or "archive_not_opened",
                     recorder_id=rid)
        elif not recorded:
            run.skip(EVIDENCE_CLIP, "recorder", "no camera with a recent recording to export",
                     recorder_id=rid)
        else:
            run.run(EVIDENCE_CLIP, "recorder", TIMEOUTS["clip"],
                    lambda: evidence_clip(recorded[0]), reserve=0.0, recorder_id=rid)

    if untested:
        for check in run.checks:
            if check["name"] == CAMERA_INVENTORY:
                check["evidence"]["cameras_not_tested"] = untested

    if archive is not None and archive is not driver:
        _close(archive)
    _close(driver)
    return run.checks, hardware


# ---------------------------------------------------------------------------
# the suite
# ---------------------------------------------------------------------------

def _counts(checks: list[dict]) -> dict:
    out = {"passed": 0, "failed": 0, "unknown": 0, "unsupported": 0, "total": len(checks)}
    key = {PASS: "passed", FAIL: "failed", UNKNOWN: "unknown", UNSUPPORTED: "unsupported"}
    for check in checks:
        out[key[check["status"]]] += 1
    return out


def bound_result(result: dict, max_bytes: int = MAX_RESULT_BYTES) -> dict:
    """Trim evidence until the serialised document fits ``max_bytes``. Statuses, names and
    errors are always kept; evidence is dropped camera checks first, then everywhere."""
    def size():
        return len(json.dumps(result, separators=(",", ":"), default=str).encode("utf-8"))
    if size() <= max_bytes:
        return result
    result["truncated"] = True
    for scope in ("camera", "recorder", "agent"):
        for check in result.get("checks") or []:
            if check.get("scope") == scope:
                check["evidence"] = {}
        if size() <= max_bytes:
            return result
    for check in result.get("checks") or []:
        check["last_error"] = (check.get("last_error") or None) and check["last_error"][:60]
    if size() > max_bytes:
        # Still too big (an absurd camera count): keep the summary and the non-PASS checks.
        result["checks"] = [c for c in result.get("checks") or [] if c["status"] != PASS]
        result["checks_omitted_pass"] = True
    return result


def run_suite(env: Env, targets: list[Target], *, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
              include_clip: bool = True, only=None, channels=None) -> dict:
    """Run the acceptance suite. Never raises for a probe failure; returns the result document."""
    budget_seconds = max(MIN_CHECK_SECONDS, min(float(budget_seconds or DEFAULT_BUDGET_SECONDS),
                                                MAX_BUDGET_SECONDS))
    budget = Budget(budget_seconds, monotonic=env.monotonic)
    tested_at = env.now()
    only = frozenset(only) if only else None
    targets = list(targets or [])[:MAX_RECORDERS]

    agent_run = _Runner(budget, env.now, only=only)
    device_wanted = only is None or any(n in only for n in RECORDER_CHECKS + CAMERA_CHECKS)
    sections: list[dict] = [{"target": t, "checks": [], "hardware": {}}
                            for t in (targets if device_wanted else [])]

    def agent_part():
        if only is None or any(n in only for n in AGENT_CHECKS):
            _agent_checks(env, agent_run)

    def section_part(section):
        try:
            _recorder_section(env, section["target"], budget, only=only,
                              include_clip=include_clip, channels_filter=channels,
                              checks=section["checks"], hardware=section["hardware"])
        except Exception as error:  # noqa: BLE001 — one recorder never sinks the others
            section["checks"].append(make_check("Recorder section", "recorder", FAIL,
                                                error=error,
                                                recorder_id=section["target"].recorder_id))

    agent_thread = threading.Thread(target=agent_part, daemon=True, name="acceptance-agent")
    for section in sections:
        section["thread"] = threading.Thread(
            target=section_part, args=(section,), daemon=True,
            name=f"acceptance-{str(section['target'].recorder_id)[:8]}")
    threads = [agent_thread] + [s["thread"] for s in sections]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, budget.remaining()) + SECTION_JOIN_GRACE_SECONDS)

    checks = list(agent_run.checks)
    if agent_thread.is_alive():
        checks.append(make_check("Agent section", "agent", UNKNOWN,
                                 error="time_budget_exhausted"))
    recorders = []
    for section in sections:
        target = section["target"]
        sec_checks = list(section["checks"])
        if section["thread"].is_alive():
            sec_checks.append(make_check("Recorder section", "recorder", UNKNOWN,
                                         error="time_budget_exhausted",
                                         recorder_id=target.recorder_id))
        checks.extend(sec_checks)
        hardware = dict(section["hardware"]) or {"vendor": None, "model": None,
                                                 "firmware": None, "serial": None}
        recorders.append({
            "recorder_id": str(target.recorder_id) if target.recorder_id else None,
            "name": target.name,
            "primary": bool(target.primary),
            "hardware": hardware,
            "summary": _counts(sec_checks),
        })

    primary = next((r for r in recorders if r["primary"]), recorders[0] if recorders else None)
    meta = {}
    try:
        meta = env.agent_meta() or {}
    except Exception:  # noqa: BLE001
        meta = {}
    summary = _counts(checks)
    summary.update({
        "hardware": dict(primary["hardware"]) if primary else
        {"vendor": None, "model": None, "firmware": None, "serial": None},
        "agent": {"version": meta.get("version"), "build_sha": meta.get("build_sha")},
        "tested_at": _iso(tested_at),
        "recorders": len(recorders),
    })
    result = {
        "schema": SCHEMA,
        "summary": summary,
        "recorders": recorders,
        "checks": checks,
        "budget_seconds": budget_seconds,
        "duration_ms": budget.elapsed_ms(),
        "include_clip": bool(include_clip),
        "only": sorted(only) if only else None,
        "truncated": False,
    }
    return bound_result(result)


# ---------------------------------------------------------------------------
# operator table
# ---------------------------------------------------------------------------

LABEL_WIDTH = 24


def check_label(check: dict, recorder_names: dict | None = None, multi: bool = False) -> str:
    label = check.get("name") or "?"
    if check.get("scope") == "camera" and check.get("channel"):
        label = f"{label} ch{check['channel']}"
    if multi and check.get("scope") in ("recorder", "camera"):
        prefix = (recorder_names or {}).get(check.get("recorder_id")) \
            or str(check.get("recorder_id") or "")[:8]
        label = f"{prefix}: {label}"
    return label


def format_table(result: dict) -> list[str]:
    """The operator table: one ``<label> <STATUS>`` line per check, then the totals and the
    Hardware / Agent / Tested lines."""
    summary = result.get("summary") or {}
    recorders = result.get("recorders") or []
    names = {r.get("recorder_id"): r.get("name") for r in recorders}
    multi = len(recorders) > 1
    lines = []
    for check in result.get("checks") or []:
        lines.append(f"{check_label(check, names, multi):<{LABEL_WIDTH}} {check.get('status')}")
        if check.get("status") != PASS and check.get("last_error"):
            lines.append(f"{'':<{LABEL_WIDTH}}   {check['last_error']}")
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    if passed == total:
        lines.append(f"{passed}/{total} PASS")
    else:
        lines.append(f"{passed}/{total} PASS ({summary.get('failed', 0)} FAIL, "
                     f"{summary.get('unknown', 0)} UNKNOWN, "
                     f"{summary.get('unsupported', 0)} UNSUPPORTED)")
    hw = summary.get("hardware") or {}
    hw_text = " ".join(str(x) for x in (hw.get("vendor"), hw.get("model")) if x) or "unknown"
    if hw.get("firmware"):
        hw_text += f" firmware {hw['firmware']}"
    if hw.get("serial"):
        hw_text += f" serial {hw['serial']}"
    if multi:
        hw_text += f" (+{len(recorders) - 1} more recorder(s))"
    agent = summary.get("agent") or {}
    agent_text = str(agent.get("version") or "unknown")
    if agent.get("build_sha"):
        agent_text += f" (build {str(agent['build_sha'])[:12]})"
    lines.append(f"Hardware: {hw_text}")
    lines.append(f"Agent: {agent_text}")
    lines.append(f"Tested: {summary.get('tested_at') or 'unknown'}")
    return lines


__all__ = [
    "PASS", "FAIL", "UNKNOWN", "UNSUPPORTED", "STATUSES", "SCHEMA", "Env", "Target",
    "run_suite", "format_table", "make_check", "bound_result", "jpeg_dimensions",
    "clip_container", "AGENT_CHECKS", "RECORDER_CHECKS", "CAMERA_CHECKS", "ALL_CHECKS",
    "RECORDING_CHECK_ONLY", "ARCHIVE_CHECK_ONLY", "MAX_BUDGET_SECONDS", "MAX_RESULT_BYTES",
]

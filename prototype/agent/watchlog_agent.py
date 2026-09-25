#!/usr/bin/env python3
"""
WatchLog site agent.

Proves the thesis of the engagement: software installed on a machine we
will never touch, inside a network we have no access to, behind NAT and a
firewall we do not control, can enroll itself and report events to us
with no port forwarding, no VPN, and no inbound connection ever.

Every connection this process makes is OUTBOUND — to the NVR on the local
LAN, and to Supabase over HTTPS. It never binds a socket, never listens,
and needs no firewall rule.

Shape:

    driver thread  --> local SQLite spool --> uploader --> Supabase RPC
    (Hikvision /                             (drains,
     Dahua / ONVIF /                          at-least-once)
     mock)

The spool is why a dead internet link buffers instead of losing events.

Authentication carries only the PUBLISHABLE key, which is public by
design. Identity is the per-agent secret minted at enrollment and stored
server-side as a SHA-256 hash. A stolen build grants no database access.

Usage
    python watchlog_agent.py                 # enroll if needed, then run
    python watchlog_agent.py --probe         # identify the NVR, no cloud
    python watchlog_agent.py --once          # one drain + heartbeat, exit
    python watchlog_agent.py --enroll-only
    python watchlog_agent.py --status
    python watchlog_agent.py --reset

Config, in precedence order:
    1. environment variables  WATCHLOG_*
    2. watchlog.ini beside this script (or beside the .exe when frozen)

Requires: requests
"""

from __future__ import annotations

import argparse
import base64
import configparser
import json
import os
import platform
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import discover
import recorder_probe
import setup_wizard
import vision
import wsdiscovery
from drivers import DRIVERS, DriverError, autodetect, build

import credential_store
from wl_version import VERSION as AGENT_VERSION  # single source of truth

HEARTBEAT_SECONDS = 60
HEALTH_SECONDS = 300          # recorder assessment + camera health cycle, every 5 min (jittered)
HEALTH_BATCH = 4              # cameras probed per cycle (fair round-robin over cycles)
HEALTH_CONCURRENCY = 2        # strict cap on simultaneous snapshot probes — never the whole wall
RECONCILE_BATCH = 500         # max retained transitions/checkpoints per reconcile upload
NATIVE_FAULT_TYPES = {"video_loss"}   # native events that are an immediate camera OFFLINE
UPLOAD_SECONDS = 15
UPLOAD_BATCH = 200
HTTP_TIMEOUT = 30
DRIVER_RETRY_SECONDS = 20
ONCE_COLLECT_SECONDS = 25
RECORDER_PROBE_MAX_ATTEMPTS = 3   # bounded alternate-port attempts on a non-auth connect failure

# Incident stills. One per camera at most every SNAPSHOT_MIN_INTERVAL
# seconds: a busy gate can fire every few seconds, and an image per event
# would flood both the site uplink and the storage budget for no extra
# information.
SNAPSHOT_MIN_INTERVAL = 60
SNAPSHOT_MAX_BYTES = 2_000_000
# Faults where the camera is, by definition, not producing a usable
# picture. Asking anyway just blocks the event loop on a timeout.
NO_SNAPSHOT_EVENTS = {"video_loss", "disk_error", "disk_full"}
# Uploads are capped by BYTES as well as count - 200 events carrying
# stills would be a ~40 MB request.
UPLOAD_MAX_BYTES = 4_000_000


# --- helpers -----------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log(msg: str) -> None:
    print(f"{iso(now_utc())} [agent] {msg}", flush=True)


def mask(secret: str | None) -> str:
    """Secrets Gate: never print a credential in full, not even to a log."""
    if not secret:
        return "<unset>"
    return f"{secret[:6]}...{secret[-4:]} ({len(secret)} chars)"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_state_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"
    return Path.home() / ".watchlog"


# --- config ------------------------------------------------------------

class Config:
    def __init__(self) -> None:
        ini = configparser.ConfigParser()
        ini_path = base_dir() / "watchlog.ini"
        section: dict[str, str] = {}
        if ini_path.exists():
            # utf-8-sig, not utf-8: Notepad and PowerShell's Set-Content
            # both write a BOM, and configparser treats a leading ﻿ as
            # content before the section header and refuses the whole file.
            # An installer editing this on site will hit that.
            try:
                ini.read(ini_path, encoding="utf-8-sig")
            except configparser.Error as e:
                raise SystemExit(
                    f"FATAL: {ini_path} could not be read.\n  {e}\n"
                    f"It must start with the line [watchlog] and contain "
                    f"key = value pairs. Compare it against "
                    f"watchlog.ini.example.") from e
            if not ini.has_section("watchlog"):
                raise SystemExit(
                    f"FATAL: {ini_path} has no [watchlog] section. "
                    f"Compare it against watchlog.ini.example.")
            section = dict(ini.items("watchlog"))
            log(f"config file: {ini_path}")
        else:
            log(f"config file: none at {ini_path}, using environment only")

        def get(key: str, default: str | None = None) -> str | None:
            return (os.environ.get("WATCHLOG_" + key.upper())
                    or section.get(key) or default)

        self.supabase_url = (get("supabase_url") or "").rstrip("/")
        self.publishable_key = get("supabase_publishable_key") or ""
        self.enrollment_code = get("enrollment_code") or ""

        self.nvr_url = (get("nvr_url") or "").rstrip("/")
        self.nvr_username = get("nvr_username") or ""
        self.nvr_password = get("nvr_password") or ""
        self.nvr_driver = (get("nvr_driver") or "auto").strip().lower()
        # PC-free ("recorder push") destination, baked in by the build. Empty = the
        # feature is unavailable in this build and every push path no-ops.
        self.push_bridge_url = (get("push_bridge_url") or "").strip().rstrip("/")
        self._ini_path = ini_path
        # Production: the recorder credential lives in the encrypted split store
        # and is self-decrypted here, so every launch context resolves it the
        # same way. A corrupt/foreign store is fatal (no plaintext fallback).
        self.load_recorder_credential()
        self.snapshots = (str(get("snapshots") or "true").strip().lower()
                          not in ("0", "false", "no", "off"))
        self.snapshot_min_interval = int(
            get("snapshot_min_interval") or SNAPSHOT_MIN_INTERVAL)

        # Local false-alarm filtering. On by default: an unfiltered DVR
        # produces hundreds of motion events a night and the daily report
        # becomes unreadable, which defeats the point of the product.
        # Detection runs on this machine - no frame is ever sent to a
        # cloud model.
        self.detect = (str(get("detect") or "true").strip().lower()
                       not in ("0", "false", "no", "off"))
        self.detect_model = (get("detect_model") or "").strip() or None
        self.detect_confidence = (get("detect_confidence") or "").strip() or None
        self.detect_classes = (get("detect_classes") or "").strip() or None

        state_dir = Path(get("state_dir") or default_state_dir())
        self.state_path = Path(get("state_file") or (state_dir / "agent_state.json"))
        self.spool_path = Path(get("spool_file")
                               or (self.state_path.parent / "spool.sqlite"))
        # Buffer cap — sized per deployment (Edge boxes can buffer a longer outage). 0/unset
        # keeps the Spool default; the value is a row count, not bytes.
        self.spool_max_rows = int(get("spool_max_rows") or 0)
        # Durable LOCAL health store (increment 5) — separate from the event spool.
        self.health_store_path = Path(get("health_store_file")
                                      or (self.state_path.parent / "health.sqlite"))

        self.heartbeat_seconds = int(get("heartbeat_seconds") or HEARTBEAT_SECONDS)
        self.health_seconds = int(get("health_seconds") or HEALTH_SECONDS)
        self.health_batch = int(get("health_batch") or HEALTH_BATCH)
        self.health_concurrency = int(get("health_concurrency") or HEALTH_CONCURRENCY)
        self.upload_seconds = int(get("upload_seconds") or UPLOAD_SECONDS)
        # Site Control command plane (H6), read-only executor. OFF by default: a new
        # capability is never auto-enabled on a live site — enable per-site in the ini.
        self.site_control_enabled = str(get("site_control") or "false").strip().lower() == "true"
        self.site_control_seconds = int(get("site_control_seconds") or 15)
        # Automatic NVR outage recovery (0.4.4 §1/§2). READ-ONLY archive backfill of missed
        # intervals; ON by default (it never writes to the recorder, always yields to live
        # monitoring, and is throttled). Disable per-site with recovery_enabled = false.
        self.recovery_enabled = str(get("recovery_enabled") or "true").strip().lower() == "true"
        self.recovery_seconds = int(get("recovery_seconds") or 300)
        self.recovery_chunk_seconds = int(get("recovery_chunk_seconds") or 3600)
        self.recovery_throttle_seconds = float(get("recovery_throttle_seconds") or 2.0)
        self.recovery_threshold_seconds = int(get("recovery_threshold_seconds") or 180)
        self.recovery_live_backlog = int(get("recovery_live_backlog") or 500)
        self.last_live_path = Path(get("last_live_file") or (self.state_path.parent / "last_live.json"))
        # Deep recovery (§1): also run the on-site detector over recovered FOOTAGE (not just
        # recorder-native event replay). Bounded per chunk; degrades honestly when no frame/codec.
        self.recovery_ai_enabled = str(get("recovery_ai_enabled") or "true").strip().lower() == "true"
        self.recovery_ai_max_frames = int(get("recovery_ai_max_frames") or 40)
        # In-app updates (0.4.4 §13/§14). Check-for-updates is READ-ONLY and never auto-applies.
        # update_public_key authenticates the signed release manifest; with no key configured an
        # update is refused (trust nothing) unless update_require_signature is explicitly false.
        self.update_url = (get("update_url") or "").strip()
        self.update_channel = (get("update_channel") or "production").strip().lower()
        self.update_public_key = (get("update_public_key") or "").strip()
        self.update_require_signature = str(get("update_require_signature") or "true").strip().lower() == "true"
        # Camera configuration persisted at setup (channel/name/purpose/monitored) — the LOCAL source
        # of the monitored-vs-unused classification the Site Status panel renders.
        try:
            self.camera_profiles = json.loads(get("camera_profiles_json") or "[]") or []
        except Exception:  # noqa: BLE001 — a corrupt cache must never crash the agent
            self.camera_profiles = []

    def load_recorder_credential(self) -> None:
        """(Re)load the recorder credential from the encrypted split store so
        every launch context (SYSTEM task, terminal, --probe) resolves it
        identically. Called at startup and whenever Setup changes it (the
        interruptible auth breaker). A corrupt or foreign-machine store is fatal
        — never a plaintext fallback."""
        if os.name != "nt":
            return  # dev/lean builds use the env/ini values already set
        try:
            cred = credential_store.load_nvr_credential(self._ini_path)
        except credential_store.SecretError as exc:
            raise SystemExit(
                "FATAL: the recorder credential could not be read (corrupt, or a "
                f"blob copied from another machine). Repair WatchLog.\n  {exc}")
        if cred:
            self.nvr_username = cred.get("username") or self.nvr_username
            self.nvr_password = cred.get("password") or ""

    def require_cloud(self) -> None:
        missing = [n for n, v in (("supabase_url", self.supabase_url),
                                  ("supabase_publishable_key", self.publishable_key))
                   if not v]
        if missing:
            raise SystemExit(
                "FATAL: missing config: " + ", ".join(missing) + ".\n"
                "Set WATCHLOG_SUPABASE_URL / WATCHLOG_SUPABASE_PUBLISHABLE_KEY, "
                "or fill in " + str(base_dir() / "watchlog.ini") +
                " (copy watchlog.ini.example).")

    def require_nvr(self) -> None:
        # 37777/37778 are Dahua's binary SDK ports and 34567 is Xiongmai's.
        # None of them speak HTTP, so pointing the agent at one produces a
        # confusing timeout rather than an obvious "wrong port".
        for bad, why in ((":37777", "Dahua's binary SDK port"),
                         (":37778", "Dahua's binary SDK port"),
                         (":34567", "Xiongmai's binary port"),
                         (":554",   "the RTSP video port")):
            if self.nvr_url.endswith(bad):
                raise SystemExit(
                    f"FATAL: nvr_url points at port {bad[1:]}, which is "
                    f"{why} - not a web interface.\n"
                    f"WatchLog needs the recorder's HTTP port, usually 80. "
                    f"Find it on the recorder itself under "
                    f"Main Menu > Network > Port.")
        if not self.nvr_url:
            raise SystemExit(
                "FATAL: no nvr_url. Point it at the recorder on the local "
                "LAN, e.g. http://192.168.1.108")


# --- Supabase RPC ------------------------------------------------------

class CloudError(RuntimeError):
    """A 4xx/5xx from a cloud RPC, carrying the HTTP status and the server's error
    code (a PostgreSQL SQLSTATE like 28000/23503/23505, or a PostgREST code) so
    callers can classify the failure instead of string-matching. str() stays
    human-readable and still contains the message (existing log/heuristic code)."""
    def __init__(self, fn: str, status: int, code, message) -> None:
        self.fn = fn
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"{fn}: HTTP {status} {code or ''} {str(message)[:300]}".strip())


class Cloud:
    """
    The entire cloud surface: four SECURITY DEFINER functions.

    No table is ever addressed directly. The publishable key on its own
    grants nothing — RLS is on with no policies — so identity is the
    agent's own secret, passed to each call.
    """

    def __init__(self, url: str, key: str) -> None:
        self.rpc = url + "/rest/v1/rpc"
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        })

    def call(self, fn: str, **params):
        r = self.s.post(f"{self.rpc}/{fn}", json=params, timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            code = None
            msg = r.text
            try:
                body = r.json()
                msg = body.get("message", r.text)
                code = body.get("code")          # PG SQLSTATE or PostgREST code
            except ValueError:
                pass
            raise CloudError(fn, r.status_code, code, msg)
        return r.json() if r.text.strip() else None


# --- local state -------------------------------------------------------

def load_state(path: Path) -> dict | None:
    """Load non-secret state and inject the agent key from the encrypted store.
    "Enrolled" requires BOTH the state AND a decryptable agent key — a state file
    without a usable key is a half-installed state and is treated as unenrolled."""
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"WARNING: state file at {path} unreadable ({e}); treating as unenrolled")
        return None
    if os.name == "nt" and not state.get("agent_key"):
        try:
            key = credential_store.load_agent_key()
        except credential_store.SecretError as e:
            raise SystemExit(
                f"FATAL: the agent identity key is unreadable ({e}). Repair WatchLog.")
        if key:
            state["agent_key"] = key
        else:
            log("WARNING: agent_state.json present but no decryptable agent key; "
                "treating as unenrolled (half-installed state)")
            return None
    return state


def save_state(path: Path, state: dict) -> None:
    """Persist ONLY non-secret identity; the bearer agent key is written
    separately to the encrypted Secrets store, never into agent_state.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and state.get("agent_key"):
        credential_store.save_agent_key(state["agent_key"])   # encrypted + DACL-locked
    public = {k: v for k, v in state.items() if k != "agent_key"}
    public.setdefault("credential_store_version", credential_store.CREDENTIAL_STORE_VERSION)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(public, indent=2), encoding="utf-8")
    tmp.replace(path)
    if os.name != "nt":
        os.chmod(path, 0o600)
    # Memory law: read it back from disk, do not trust the write.
    if json.loads(path.read_text(encoding="utf-8")).get("agent_id") != state.get("agent_id"):
        raise RuntimeError("state write verification failed at " + str(path))


# --- driver ------------------------------------------------------------

def _parse_host_port_scheme(url):
    """Split a recorder URL into (host, port|None, scheme). Read-only, no I/O."""
    from urllib.parse import urlparse
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    parsed = urlparse(raw)
    return parsed.hostname or "", parsed.port, ("https" if parsed.scheme == "https" else "http")


def _effective_scheme_port(scheme, port):
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, int(port))


def _connect_recorder(cfg: Config, base_url: str):
    if cfg.nvr_driver in ("auto", ""):
        return autodetect(base_url, cfg.nvr_username, cfg.nvr_password, log=log)
    driver = build(cfg.nvr_driver, base_url, cfg.nvr_username, cfg.nvr_password)
    return driver, driver.probe()


def open_driver(cfg: Config):
    cfg.require_nvr()
    # Primary attempt: exactly the configured driver/URL — unchanged behaviour. When it
    # works (the normal case) nothing below runs.
    try:
        return _connect_recorder(cfg, cfg.nvr_url)
    except DriverError as primary:
        # An AUTH failure means the host/port is RIGHT and only the credential is wrong.
        # Never reprobe other ports then: it is pointless and risks a recorder lockout — let
        # the caller's auth backoff handle it.
        if _is_auth_failure(primary):
            raise
        # Bounded recorder-hardening fallback (workstream 5): try a small, vendor-ordered set
        # of KNOWN HTTP(S) ports on the SAME host with the SAME credential. No subnet scan, no
        # credential spraying, no binary SDK ports — only a handful of standard web ports, only
        # after the configured URL failed to connect/identify.
        try:
            host, port, scheme = _parse_host_port_scheme(cfg.nvr_url)
            if not host:
                raise primary
            if port and recorder_probe.classify_port(port):
                log(f"recorder: configured port {port} is the "
                    f"{recorder_probe.classify_port(port)} binary control port, not HTTP/ISAPI; "
                    "trying the standard web port(s)")
            vendor = "auto" if cfg.nvr_driver in ("auto", "") else cfg.nvr_driver
            primary_eff = _effective_scheme_port(scheme, port)
            attempts = 0
            for cand in recorder_probe.candidates(vendor, host, port):
                if cand.get("non_http"):                       # never HTTP-probe a binary SDK port
                    continue
                if (cand["scheme"], int(cand["port"])) == primary_eff:
                    continue                                   # already tried as the primary
                if attempts >= RECORDER_PROBE_MAX_ATTEMPTS:
                    break
                attempts += 1
                log(f"recorder: configured URL failed; trying {cand['base_url']}")
                try:
                    return _connect_recorder(cfg, cand["base_url"])
                except DriverError as alt:
                    if _is_auth_failure(alt):
                        # Found the recorder on this port; the credential is wrong. Stop —
                        # do NOT keep trying ports (that would be credential spraying).
                        raise alt
                    continue
        except DriverError:
            raise
        except Exception:                                       # noqa: BLE001 — never mask the real error
            raise primary
        raise primary


# --- enrollment --------------------------------------------------------

def enroll(cfg: Config, cloud: Cloud, device) -> dict:
    if not cfg.enrollment_code:
        raise SystemExit("FATAL: no enrollment code. Set WATCHLOG_ENROLLMENT_CODE "
                         "or put enrollment_code in watchlog.ini.")
    code = cfg.enrollment_code.strip()
    log("enrolling with the provided setup code")

    try:
        res = cloud.call(
            "wl_enroll",
            p_code=code,
            p_hostname=platform.node(),
            p_platform=f"{platform.system()} {platform.release()}",
            p_agent_version=AGENT_VERSION,
            p_device_vendor=device.vendor if device else None,
            p_device_model=device.model if device else None,
            p_device_driver=device.driver if device else None,
        )
    except RuntimeError as e:
        raise SystemExit(f"FATAL: {e}\nMint a fresh code and try again.") from e

    state = {
        "agent_id": res["agent_id"],
        "agent_key": res["agent_key"],
        "tenant_id": res["tenant_id"],
        "site_id": res["site_id"],
        "enrolled_at": iso(now_utc()),
        "agent_version": AGENT_VERSION,
    }
    save_state(cfg.state_path, state)
    log(f"enrolled: agent_id={state['agent_id']} site={state['site_id']}")
    log(f"agent identity stored (encrypted) at {cfg.state_path}")
    return state


# --- workers -----------------------------------------------------------

_AUTH_BACKOFF_SECONDS = (300, 900, 1800)   # 5, 15, 30 min for CONFIRMED auth failures


def _is_auth_failure(err: Exception) -> bool:
    s = str(err).lower()
    return ("rejected the username or password" in s or "http 401" in s
            or "http 403" in s or "invalid username or password" in s
            or "sender not authorized" in s)


def _reconnect_wait(stop: threading.Event, cfg: "Config", auth_failures: int,
                    last_gen: str) -> tuple[str, str]:
    """Interruptible backoff between driver reconnects. Returns (outcome, gen).

    Confirmed auth failures escalate 5->15->30 min so a wrong password never
    hammers the recorder (lockout risk); everything else uses the short
    DRIVER_RETRY_SECONDS. A credential change (Setup rewriting the DPAPI blob)
    wakes the wait immediately, reloads the credential and lets the caller retry
    now — never wait out 30 minutes after the operator fixes the password."""
    if auth_failures > 0:
        total = float(_AUTH_BACKOFF_SECONDS[min(auth_failures - 1, len(_AUTH_BACKOFF_SECONDS) - 1)])
        log(f"recorder authentication is failing; backing off {int(total) // 60} min "
            f"(will retry immediately if the credential is updated in Setup)")
    else:
        total = float(DRIVER_RETRY_SECONDS)
    waited, step = 0.0, 5.0
    while waited < total:
        if stop.wait(min(step, total - waited)):
            return "stop", last_gen
        waited += step
        gen = credential_store.credential_generation()
        if gen != last_gen:
            log("recorder credential changed in Setup; reloading and retrying now")
            cfg.load_recorder_credential()
            return "reload", gen
    return "timeout", last_gen


def collector(cfg: Config, spool, stop: threading.Event, holder: dict = None) -> None:
    """Driver thread. Never dies: on error it backs off and re-opens."""
    # Built once, outside the reconnect loop: loading the weights costs
    # seconds, and a flapping NVR must not re-pay that on every retry.
    detector = vision.build(cfg, log)
    auth_failures = 0
    last_gen = credential_store.credential_generation()
    while not stop.is_set():
        driver = None
        auth_error = False
        try:
            driver, info = open_driver(cfg)
            if holder is not None:
                # Publish recorder truth from the ACTUAL collector thread. Recovery
                # must never infer recorder liveness from the PC/cloud heartbeat.
                holder["live_driver"] = driver
                holder["recorder_live_at"] = time.monotonic()
                holder["recorder_live_wall"] = now_utc()
            log(f"driver {driver.name}: {info.vendor} {info.model or ''} "
                f"fw={info.firmware or '?'}".rstrip())
            if not driver.verified_against_hardware:
                log(f"NOTE: driver '{driver.name}' has not been verified against "
                    f"real hardware. Treat its output as unproven.")

            last_shot: dict[str, float] = {}

            for ev in driver.stream_events(stop):
                if stop.is_set():
                    break
                if holder is not None:
                    holder["recorder_live_at"] = time.monotonic()
                    holder["recorder_live_wall"] = now_utc()

                # The image is best-effort and strictly secondary. A
                # camera that hangs, refuses auth or returns junk must
                # cost us the picture, never the incident record.
                raw = None
                if cfg.snapshots and ev.event_type not in NO_SNAPSHOT_EVENTS:
                    clock = time.monotonic()
                    if clock - last_shot.get(ev.channel, 0.0) >= cfg.snapshot_min_interval:
                        last_shot[ev.channel] = clock
                        try:
                            raw = driver.get_snapshot(ev.channel)
                        except Exception as e:              # noqa: BLE001
                            raw = None
                            log(f"snapshot ch{ev.channel} failed: "
                                f"{type(e).__name__}: {str(e)[:120]}")
                        if raw and len(raw) <= SNAPSHOT_MAX_BYTES:
                            ev = ev.with_snapshot(
                                base64.b64encode(raw).decode("ascii"))
                            log(f"snapshot ch{ev.channel} {len(raw) // 1024} KB")
                        elif raw:
                            log(f"snapshot ch{ev.channel} discarded: "
                                f"{len(raw) // 1024} KB exceeds cap")
                            raw = None

                # False-alarm filter. Only events that carry a frame can be
                # judged; faults and video-loss arrive without one and are
                # always kept, because those are precisely the events that
                # say a camera has stopped working.
                if detector is not None and ev.event_type not in NO_SNAPSHOT_EVENTS:
                    keep, found = detector.classify_event(raw)
                    if not keep:
                        log(f"discarded ch{ev.channel} {ev.event_type}: "
                            f"no person/vehicle in frame")
                        continue
                    if found:
                        ev.payload["objects"] = [d.as_dict() for d in found]
                        ev.payload["detector"] = detector.model_name
                        log(f"kept ch{ev.channel}: "
                            f"{', '.join(sorted({d.label for d in found}))}")

                spool.add(ev.to_json(now_utc()))

                # A native VideoLoss/disconnect is an immediate camera OFFLINE — feed it to
                # the health monitor straight from the event stream (best-effort; never let a
                # health-side error disturb ingestion).
                if holder is not None and ev.event_type in NATIVE_FAULT_TYPES:
                    mon = holder.get("monitor")
                    if mon is not None:
                        try:
                            mon.record_native_fault(ev.channel)
                        except Exception:               # noqa: BLE001
                            pass

                dropped = spool.trim()
                if dropped:
                    log(f"WARNING: spool over capacity, dropped {dropped} "
                        f"oldest events")
        except (DriverError, requests.RequestException, RuntimeError) as e:
            auth_error = _is_auth_failure(e)
            # Log every line. The first line alone is "no driver recognised
            # the device", which tells whoever is reading the log nothing
            # they can act on; the per-driver reasons are the diagnosis.
            for line in str(e).splitlines():
                if line.strip():
                    log(f"ERROR: driver: {line.strip()[:200]}")
            log("run  watchlog-agent.exe --probe  to find out what is at "
                "that address")
        except SystemExit as e:
            # open_driver() exits on missing config. In a thread that would
            # end the thread silently, leaving an agent that heartbeats
            # forever and collects nothing with no explanation in the log.
            log(f"ERROR: driver not configured: {str(e).splitlines()[0][:200]}")
        except Exception as e:                       # noqa: BLE001
            log(f"ERROR: driver crashed: {type(e).__name__}: {e}")
        finally:
            if driver:
                if holder is not None and holder.get("live_driver") is driver:
                    holder.pop("live_driver", None)
                driver.close()
        if not stop.is_set():
            auth_failures = auth_failures + 1 if auth_error else 0
            if not auth_error:
                log(f"driver reconnecting in {DRIVER_RETRY_SECONDS}s")
            outcome, last_gen = _reconnect_wait(stop, cfg, auth_failures, last_gen)
            if outcome == "reload":
                auth_failures = 0       # fresh credential -> reset breaker, retry now


def upload_once(cloud: Cloud, state: dict, spool) -> int:
    ids, events = spool.take(UPLOAD_BATCH)
    if not ids:
        return 0

    # Trim the batch by payload size. Events carrying stills are ~200 KB
    # each, so a full count-based batch would be a multi-megabyte POST on
    # a site uplink. Always keep at least one, or a single oversized row
    # would wedge the queue forever.
    total, cut = 0, len(events)
    for i, ev in enumerate(events):
        total += len(ev.get("snapshot_b64") or "") + 512
        if total > UPLOAD_MAX_BYTES and i > 0:
            cut = i
            break
    ids, events = ids[:cut], events[:cut]

    res = cloud.call("wl_ingest_events", p_agent_id=state["agent_id"],
                     p_agent_key=state["agent_key"], p_events=events)
    # Only acknowledge after the server has committed.
    spool.ack(ids)
    shots = res.get("snapshots") or 0
    log(f"uploaded {res['received']}: {res['inserted']} new, "
        f"{res['skipped']} already stored"
        + (f", {shots} image(s)" if shots else "")
        + f"; {spool.count()} left in spool")
    return res["inserted"]


def heartbeat(cloud: Cloud, state: dict, device) -> None:
    cloud.call("wl_heartbeat", p_agent_id=state["agent_id"],
               p_agent_key=state["agent_key"], p_agent_version=AGENT_VERSION,
               p_device_vendor=device.vendor if device else None,
               p_device_model=device.model if device else None,
               p_device_driver=device.driver if device else None)
    # Non-secret installer readiness proof. A cloud heartbeat by itself is NOT
    # enough: Build 41 field evidence showed an agent could heartbeat forever while
    # the Hikvision recorder was unreachable, leaving WatchLog with zero camera data.
    # register-service.ps1 deletes any stale marker before starting the SYSTEM task,
    # therefore write a fresh marker only when THIS background process also proved a
    # real recorder identity during startup.
    if device is not None:
        try:
            ready = default_state_dir() / "background-ready.json"
            ready.parent.mkdir(parents=True, exist_ok=True)
            tmp = ready.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "agent_id": state.get("agent_id"),
                "version": AGENT_VERSION,
                "pid": os.getpid(),
                "heartbeat_at": iso(now_utc()),
                "recorder_connected": True,
                "recorder_vendor": getattr(device, "vendor", None),
                "recorder_model": getattr(device, "model", None),
                "recorder_driver": getattr(device, "driver", None),
            }, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, ready)
        except Exception:  # noqa: BLE001 — readiness evidence can never break monitoring
            pass
    log("heartbeat ok")


def _get_health_store(holder: dict, state: dict, cfg: Config):
    """Lazily open the durable local health store; a failure here must never break the loop."""
    store = holder.get("store")
    if store is None:
        try:
            import health_store
            store = health_store.HealthStore(cfg.health_store_path, agent_id=state["agent_id"])
            holder["store"] = store
        except Exception as e:                          # noqa: BLE001
            log(f"health store unavailable: {type(e).__name__}")
            return None
    return store


def persist_health(holder: dict, state: dict, cfg: Config, cam: dict, assessment: dict) -> None:
    """Increment 5: record observed camera transitions (on change) + an observation checkpoint
    into the LOCAL durable store BEFORE any cloud call, so a cloud/internet outage cannot lose
    them. Fully guarded — a persistence failure is logged, never raised."""
    store = _get_health_store(holder, state, cfg)
    if store is None:
        return
    try:
        device_ts = iso(datetime.now(timezone.utc))
        for c in cam.get("cameras", []):
            ch = c.get("channel")
            if ch is None:
                continue
            store.observe("camera", str(ch), c.get("health", "unknown"),
                          c.get("reason", "unknown"), c.get("source", "probe"), device_ts)
        # checkpoint: proof local monitoring continued this cycle (no raw probe/image data)
        store.checkpoint(device_ts,
                         nvr_state=(assessment.get("nvr") or {}).get("state", "unknown"),
                         cameras_observed=len(cam.get("cameras", [])), cycle_ok=True)
        store.compact()
    except Exception as e:                              # noqa: BLE001
        log(f"health persist skipped: {type(e).__name__}")


def persist_recording_storage(holder: dict, state: dict, rs: dict) -> None:
    """Increment 6: record recording/storage transitions into the LOCAL durable store (on change),
    so an HDD/recording change during a cloud outage survives and reconciles after reconnect. Fully
    guarded. The NVR recording ROLLUP is derived server-side (from per-channel + storage), so it is
    NOT persisted here; the per-channel camera_recording and the nvr_storage reads are the primary,
    durable signals."""
    store = holder.get("store")
    if store is None:
        return
    try:
        device_ts = iso(datetime.now(timezone.utc))
        for c in (rs.get("recording") or {}).get("channels", []):
            ch = c.get("channel")
            if ch is not None:
                store.observe("camera_recording", str(ch), c.get("state", "unknown"),
                              c.get("reason", "unknown"), "probe", device_ts)
        st = rs.get("storage") or {}
        store.observe("nvr_storage", str(state["agent_id"]), st.get("state", "unknown"),
                      st.get("reason", "unknown"), "probe", device_ts)
    except Exception as e:                              # noqa: BLE001
        log(f"recording/storage persist skipped: {type(e).__name__}")


# Server reconcile rejection categories (mirror wl_reconcile_recording_storage / reconcile_model).
# STRUCTURAL poison can never become valid on a resend, so it is quarantined AT ONCE (observable);
# anything else — notably unmapped_channel, where the camera may simply not be enrolled YET — is
# retried under a bounded policy (health_store.defer_transitions), then quarantined if it never maps.
_PERMANENT_REJECTIONS = frozenset({"missing_id", "wrong_layer", "missing_state",
                                   "invalid_timestamp", "invalid_sequence"})


_TX_DISPOSITION_KEYS = ("accepted_ids", "duplicate_ids", "rejected")
_CK_DISPOSITION_KEYS = ("checkpoints_accepted_ids", "checkpoints_duplicate_ids", "checkpoints_rejected_ids")


def _ack_transition_dispositions(store, sent: set, res: dict) -> list:
    """Return the transition ids to mark uploaded (server-confirmed accepted + duplicate) and park the
    rest by category — structural poison -> quarantine now (observable); transient (e.g.
    unmapped_channel) -> bounded retry. Shared by BOTH reconcile RPCs so the two durability paths are
    provably identical.

    FAIL CLOSED: the disposition contract must be PRESENT (all keys), distinct from present-but-empty.
    If any key is absent (an older/incomplete DB function, or an aggregate-only response like
    {"ok": true, "transitions_applied": 3}), acknowledge NOTHING and park nothing — acceptance is
    never inferred from ok/aggregate counts. Every server id is intersected with the ids ACTUALLY sent
    this RPC, so unknown/bogus/other-batch ids are ignored; a sent id in no list stays PENDING."""
    if not all(k in res for k in _TX_DISPOSITION_KEYS):
        return []
    done = (set(res["accepted_ids"] or []) | set(res["duplicate_ids"] or [])) & sent
    poison, transient = [], []
    for r in (res["rejected"] or []):
        rid = r.get("id") if isinstance(r, dict) else None
        if not rid or rid not in sent:
            continue
        (poison if r.get("reason") in _PERMANENT_REJECTIONS else transient).append((rid, r.get("reason")))
    store.quarantine_transitions(poison)
    store.defer_transitions(transient)
    return list(done)


def _ack_checkpoint_dispositions(store, cps: list, res: dict) -> None:
    """Acknowledge ONLY checkpoints the server accepted or deduped; quarantine rejected ones so a
    rejection under RPC success is neither silently marked uploaded nor retried forever.

    Identity is the epoch-qualified checkpoint_id (<agent>:<epoch>:cp:<seq>): build a map from the
    ids EXPORTED in this batch back to their local seq, and only ids that exactly match that map may
    mark a checkpoint uploaded — an id from another epoch (or a bare seq) acknowledges nothing. FAIL
    CLOSED the same way: absent/incomplete checkpoint disposition keys leave every checkpoint PENDING."""
    if not cps:
        return
    if not all(k in res for k in _CK_DISPOSITION_KEYS):
        return
    by_id = {c["id"]: c["seq"] for c in cps}
    acked = set(res["checkpoints_accepted_ids"] or []) | set(res["checkpoints_duplicate_ids"] or [])
    store.mark_checkpoints_uploaded([by_id[i] for i in acked if i in by_id])
    rej = [(by_id[r["id"]], r.get("reason")) for r in (res["checkpoints_rejected_ids"] or [])
           if isinstance(r, dict) and r.get("id") in by_id]
    store.quarantine_checkpoints(rej)


def reconcile_health(holder: dict, state: dict, cloud: Cloud) -> None:
    """Increment 5/6: upload retained transitions/checkpoints and acknowledge them PER SERVER
    DISPOSITION. Splits by LAYER — camera video (+ checkpoints) go to wl_reconcile_health;
    recording/storage go to the parallel wl_reconcile_recording_storage — so each RPC gets its own
    single-epoch batch. RPC success does NOT mean every row was accepted: BOTH RPCs return per-id
    accepted/duplicate/rejected (and wl_reconcile_health also per-checkpoint), so only ledger-durable
    (accepted+duplicate) ids are marked uploaded; rejected ids are quarantined (structural) or
    bounded-retried (transient). The two subsets are acknowledged INDEPENDENTLY — one RPC failing
    leaves only its subset pending. Idempotent on the server; on RPC failure the whole subset stays
    pending and retries next cycle. Never raises."""
    store = holder.get("store")
    if store is None:
        return
    try:
        batch = store.export_batch(RECONCILE_BATCH)
    except Exception:                                   # noqa: BLE001
        return
    txs, cps = batch.get("transitions", []), batch.get("checkpoints", [])
    if not txs and not cps:
        return
    import nvr_health
    cam_tx = [t for t in txs if t.get("layer") == "camera"]
    # Only the two layers wl_reconcile_recording_storage actually accepts. NVR recording is a
    # server-derived rollup (never a persisted primary transition), so it is NOT routed here — a
    # row it can't map would be rejected wrong_layer, which is exactly what we must not manufacture.
    rs_tx = [t for t in txs if t.get("layer") in ("camera_recording", "nvr_storage")]
    uploaded = []

    if cam_tx or cps:
        try:
            res = cloud.call("wl_reconcile_health", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_transitions=cam_tx, p_checkpoints=cps)
        except Exception as e:                          # noqa: BLE001 — whole subset stays pending
            log(f"reconcile(health) deferred: {type(e).__name__}: {nvr_health.redact(str(e))}")
        else:
            # PARITY with rec/storage: RPC success != every row accepted. 0046 can reject individual
            # transitions AND checkpoints, so acknowledge ONLY server-confirmed ids and park the rest.
            uploaded += _ack_transition_dispositions(store, {t["id"] for t in cam_tx}, res)
            _ack_checkpoint_dispositions(store, cps, res)
            log(f"reconciled health: acc={len(res.get('accepted_ids') or [])} "
                f"dup={len(res.get('duplicate_ids') or [])} "
                f"ckpt_acc={len(res.get('checkpoints_accepted_ids') or [])} "
                f"ckpt_rej={len(res.get('checkpoints_rejected_ids') or [])}")

    if rs_tx:
        try:
            res = cloud.call("wl_reconcile_recording_storage", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_transitions=rs_tx)
        except Exception as e:                          # noqa: BLE001 — whole subset stays pending
            log(f"reconcile(rec/storage) deferred: {type(e).__name__}: {nvr_health.redact(str(e))}")
        else:
            uploaded += _ack_transition_dispositions(store, {t["id"] for t in rs_tx}, res)
            log(f"reconciled rec/storage: acc={len(res.get('accepted_ids') or [])} "
                f"dup={len(res.get('duplicate_ids') or [])} rej={len(res.get('rejected') or [])}")

    if uploaded:
        store.mark_transitions_uploaded(uploaded)


def health_cycle(cloud: Cloud, state: dict, cfg: Config, holder: dict) -> None:
    """One combined recorder assessment feeding BOTH reports:
      * NVR connectivity/auth + channel inventory (increment 3), and
      * the camera hybrid-health cycle (increment 4),
    sharing a single driver connection and a single authenticated recorder assessment.

    Best-effort: it classifies an unreachable/auth-failed recorder rather than throwing, uses
    only vendor-authenticated APIs (never event activity), sends no secrets, and never raises —
    a stall or crash here can never disturb event ingestion or the heartbeat.
    """
    import camera_health
    import nvr_health
    driver = None
    collector_recent = False
    try:
        # On Hikvision the live collector already owns an authenticated recorder
        # session. Do not open a competing Digest session merely to prove the same
        # thing again; the field DS-7608NI-Q1 started rejecting those extra sessions.
        live_at = float(holder.get("recorder_live_at") or 0.0)
        collector_recent = (
            cfg.nvr_driver == "hikvision-isapi"
            and live_at > 0.0
            and time.monotonic() - live_at < 150.0
        )
        if collector_recent:
            reported = []
            for row in (getattr(cfg, "camera_profiles", None) or []):
                ch = str(row.get("channel") or "").strip()
                if ch:
                    reported.append({
                        "channel": ch,
                        "name": row.get("name") or f"Camera {ch}",
                        "enabled": bool(row.get("monitored", True)),
                    })
            if not reported:
                mon0 = holder.get("monitor")
                reported = [
                    {"channel": str(ch), "name": f"Camera {ch}", "enabled": True}
                    for ch in (getattr(mon0, "channels", None) or [])
                ]
            assessment = {
                "nvr": {
                    "reachable": True,
                    "auth_ok": True,
                    "state": "ok",
                    "reason": "ok",
                    "vendor": holder.get("recorder_vendor") or "Hikvision",
                    "model": holder.get("recorder_model") or None,
                },
                "channels": {
                    "enumerated": bool(reported),
                    "reported": reported,
                    "current_faults": {"supported": False},
                },
            }
        else:
            try:
                if cfg.nvr_driver in ("auto", ""):
                    driver, _ = autodetect(cfg.nvr_url, cfg.nvr_username,
                                           cfg.nvr_password, log=lambda *a, **k: None)
                else:
                    driver = build(cfg.nvr_driver, cfg.nvr_url, cfg.nvr_username, cfg.nvr_password)
                assessment = nvr_health.assess_nvr_health(driver)
            except DriverError as e:
                assessment = nvr_health.assess_from_error(e)
                driver = None

        # --- NVR connectivity/auth + inventory (increment 3) ---
        try:
            res = cloud.call("wl_report_health", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_report=assessment)
            log(f"health reported: nvr={assessment['nvr'].get('state')} "
                f"present={res.get('present')} missing={res.get('missing')} "
                f"disabled={res.get('disabled')} unknown={res.get('unknown')}")
        except Exception as e:                          # noqa: BLE001
            log(f"health report skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")

        # --- camera hybrid health (increment 4), reusing THIS assessment + driver ---
        chans = [str(c["channel"]) for c in assessment.get("channels", {}).get("reported", [])
                 if c.get("channel")]
        mon = holder.get("monitor")
        if mon is None and chans:
            mon = camera_health.CameraHealthMonitor(
                chans, batch_size=cfg.health_batch, concurrency=cfg.health_concurrency)
            holder["monitor"] = mon
        if mon is not None:
            if driver is not None:
                probe = camera_health.make_probe_fn(driver)
            elif collector_recent:
                def probe(channel):
                    seen = float((holder.get("snapshot_ok") or {}).get(str(channel)) or 0.0)
                    fresh = seen > 0.0 and time.monotonic() - seen < 900.0
                    return camera_health.ProbeResult(
                        ok=fresh,
                        upper=None,
                        reason=("ok" if fresh else "probe_timeout"),
                    )
            else:
                probe = lambda _c: camera_health.ProbeResult(ok=False, upper="nvr_unreachable")
            cam = mon.run_cycle(lambda: assessment, probe)   # one assessment, bounded probing
            # increment 5: persist transitions + checkpoint LOCALLY first — survives an outage.
            persist_health(holder, state, cfg, cam, assessment)
            try:
                cr = cloud.call("wl_report_camera_health", p_agent_id=state["agent_id"],
                                p_agent_key=state["agent_key"], p_report=cam)
                log(f"camera health: op={cr.get('operational')} deg={cr.get('degraded')} "
                    f"off={cr.get('offline')} unk={cr.get('unknown')}")
            except Exception as e:                      # noqa: BLE001
                log(f"camera health report skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")

        # --- NVR recording + storage health (increment 6), reusing THIS driver + assessment ---
        # No live current-state RPC: EVERY recording/storage change flows through the local store and
        # is applied by wl_reconcile_recording_storage, which solely owns the ledger AND the durable
        # ordering watermark. Nothing here can bypass that watermark and regress current state.
        try:
            import recording_health
            nvr_state = (assessment.get("nvr") or {}).get("state", "unknown")
            # inventory (present/disabled) from the reported channels — a disabled channel has no
            # meaningful recording state (UNKNOWN, never NOT_RECORDING).
            inv = {str(c.get("channel")): ("disabled" if not c.get("enabled", True) else "present")
                   for c in assessment.get("channels", {}).get("reported", []) if c.get("channel")}
            rs = recording_health.assess_recording_storage(driver, chans, nvr_state, inventory=inv)
            persist_recording_storage(holder, state, rs)     # record transitions on change (durable)
        except Exception as e:                          # noqa: BLE001
            log(f"recording/storage assess skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")

        # reconcile ALL retained transitions/checkpoints (camera video + recording/storage), split by
        # layer to the right RPC, per-subset acknowledged. Idempotent; bounded to the cycle cadence.
        reconcile_health(holder, state, cloud)
    except Exception as e:                              # noqa: BLE001 — must never break the loop
        log(f"health cycle skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:                          # noqa: BLE001
                pass


def health_worker(cfg: Config, state: dict, cloud: Cloud, holder: dict,
                  stop: threading.Event, resume_evt: "threading.Event | None" = None) -> None:
    """Run the health cycle on its OWN thread so a probe stall can never delay heartbeat or
    event upload. Jittered interval so a fleet does not probe in lockstep. When the main loop
    signals a resume (site PC woke from sleep), reconcile IMMEDIATELY instead of waiting a full
    interval — so a camera that failed while the PC was asleep is caught right away (the H2
    recorder-state reconciliation runs inside health_cycle)."""
    stop.wait(min(10, cfg.health_seconds))              # let enrollment/sync settle first
    while not stop.is_set():
        health_cycle(cloud, state, cfg, holder)
        jitter = random.uniform(0, max(1.0, cfg.health_seconds * 0.2))
        if resume_evt is not None:
            if resume_evt.wait(cfg.health_seconds + jitter):
                resume_evt.clear()                      # woke early for resume reconciliation
        else:
            stop.wait(cfg.health_seconds + jitter)


def command_worker(cfg: Config, state: dict, cloud: Cloud, stop: threading.Event) -> None:
    """Site Control (H6): poll for a queued READ command, run it against the recorder via the
    LOCAL driver, and return the structured result. OFF unless cfg.site_control_enabled — a new
    capability is never auto-enabled on a live site. Strictly read-only: site_control.execute_read
    implements only read actions. Outbound-only and agent-authenticated; the recorder credential
    never leaves this process, and a failure here can never disturb events/heartbeat/health."""
    if not cfg.site_control_enabled:
        return
    import site_control
    stop.wait(min(8, cfg.site_control_seconds))         # let enrollment/sync settle first
    while not stop.is_set():
        busy = False
        try:
            claimed = cloud.call("wl_agent_claim_command",
                                 p_agent_id=state["agent_id"], p_agent_key=state["agent_key"])
            cmd = (claimed or {}).get("command")
            if cmd:
                busy = True
                action = cmd.get("action")
                is_write = action in site_control.WRITE_ACTIONS
                driver = build(cfg.nvr_driver, cfg.nvr_url, cfg.nvr_username, cfg.nvr_password)
                try:
                    res = (site_control.execute_write(driver, action, cmd.get("params"))
                           if is_write else
                           site_control.execute_read(driver, action, cmd.get("params")))

                    # A bounded clip probe is an explicit field proof. Promote clip
                    # capability only after real bytes came back from THIS recorder.
                    if (not is_write and action == "probe_clip_export"
                            and res.get("ok")
                            and (res.get("data") or {}).get("obtained")):
                        try:
                            import connector_capabilities
                            import connector_rediscovery
                            info = driver.probe()
                            connector_rediscovery.save_identity(
                                cfg, info, getattr(driver, "base_url", cfg.nvr_url)
                            )
                            connector_capabilities.mark_proof(
                                cfg,
                                "operations_evidence_clip",
                                {
                                    "driver": getattr(driver, "name", cfg.nvr_driver),
                                    "bytes": int((res.get("data") or {}).get("bytes") or 0),
                                },
                            )
                        except Exception:  # noqa: BLE001
                            pass
                finally:
                    try:
                        driver.close()
                    except Exception:                    # noqa: BLE001
                        pass
                # Writes carry before/after/verified (transactional audit); reads carry 'data'.
                cloud.call("wl_agent_complete_command",
                           p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                           p_command_id=cmd["id"],
                           p_status=("succeeded" if res.get("ok") else "failed"),
                           p_result=(res if is_write else res.get("data")),
                           p_error=res.get("error"))
        except Exception as e:                           # noqa: BLE001 — Site Control never disturbs the agent
            log(f"site control: {type(e).__name__}: {nvr_health.redact(str(e))}")
        if not busy:
            stop.wait(cfg.site_control_seconds)          # idle poll; drain promptly when busy


# --- commands ----------------------------------------------------------


def cmd_connector_selftest() -> int:
    """Offline packaging check for the connectivity-first Site Connector.

    This intentionally performs no LAN/cloud I/O. It proves the frozen build
    contains the three recorder connection strategies plus the archive/control
    modules needed by the connector. Hardware validation remains a separate
    field test and is never implied by this check.
    """
    problems = []
    required_drivers = ("dahua-cgi", "hikvision-isapi", "onvif")
    for name in required_drivers:
        if name not in DRIVERS:
            problems.append(f"missing recorder driver: {name}")

    try:
        import dahua_archive as _archive
        import hikvision_archive as _hik_archive
        import recorder_probe as _probe
        import site_control as _site_control
        from drivers import hikvision as _hikvision
        if not callable(getattr(discover, "sweep", None)):
            problems.append("LAN sweep unavailable")
        if not callable(getattr(wsdiscovery, "discover", None)):
            problems.append("WS-Discovery unavailable")
        if not callable(getattr(_archive, "find_recordings", None)):
            problems.append("archive search unavailable")
        if not callable(getattr(_archive, "get_clip", None)):
            problems.append("clip extraction unavailable")
        if not callable(getattr(_probe, "candidates", None)):
            problems.append("recorder port fallback unavailable")
        if not hasattr(_site_control, "execute_read"):
            problems.append("site-control read plane unavailable")
        if getattr(_hikvision, "HIKVISION_STREAM_SLICE_SECONDS", None) != 30:
            problems.append("Hikvision bounded visual sampling unavailable")
        if not callable(getattr(_hik_archive, "search_recordings", None)):
            problems.append("Hikvision archive search unavailable")
        if not callable(getattr(_hik_archive, "get_clip", None)):
            problems.append("Hikvision clip extraction unavailable")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"connector module load failed: {type(exc).__name__}")

    print(f"watchlog-site-connector {AGENT_VERSION}")
    print("drivers: " + ", ".join(required_drivers))
    print("discovery: WS-Discovery + multi-NIC TCP/vendor fingerprint fallback")
    print("local AI: disabled in production connector; server-side vision is authoritative")
    if problems:
        for problem in problems:
            print("FAIL: " + problem)
        return 1
    print("RESULT: PASS")
    return 0

def cmd_selftest() -> int:
    """
    Prove the on-site AI false-alarm filter is packaged and working in THIS
    build. Used by tools/verify_agent_ai.py against the frozen exe, so that
    "the filter ships" is verified by running it, not by scanning strings.

    Exit codes (read by the verifier):
      0  PASS       — ONNX runtime + model loaded, inference ran, a junk
                      frame with no objects was discarded as a false alarm.
      3  FAIL-OPEN  — the runtime or model is not packaged; every event is
                      kept unfiltered (correct, safe behaviour — but this is
                      NOT the shippable AI build).
      2  INCONCLUSIVE / 1 error.

    Optional: WATCHLOG_SELFTEST_MODEL points at a model when running from
    source (the frozen exe finds the bundled model automatically);
    WATCHLOG_SELFTEST_IMAGE points at a real image to additionally prove a
    person/car/motorcycle is retained.
    """
    import io as _io
    import os as _os
    print(f"watchlog-agent {AGENT_VERSION} — AI filter self-test")

    class _Cfg:
        detect = True
        detect_model = _os.environ.get("WATCHLOG_SELFTEST_MODEL")
        detect_confidence = vision.DEFAULT_CONFIDENCE
        detect_classes = None

    det = vision.build(_Cfg(), log=print)
    if det is None:
        print("RESULT: NO-FILTER (build returned no detector)")
        return 3

    print(f"backend: {type(det).__name__}   model: {getattr(det, 'model_name', '?')}")

    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (640, 480), (120, 120, 120)).save(buf, "JPEG")
    keep, dets = det.classify_event(buf.getvalue())

    if not det.available:
        print(f"vision unavailable: {det._unavailable}")
        print("RESULT: FAIL-OPEN (AI runtime/model NOT packaged; every event kept)")
        return 3

    print(f"synthetic gray frame -> keep={keep} detections={dets}")

    retained = None
    real = _os.environ.get("WATCHLOG_SELFTEST_IMAGE")
    if real and _os.path.exists(real):
        with open(real, "rb") as f:
            k2, d2 = det.classify_event(f.read())
        labels = sorted({x.label for x in (d2 or [])})
        print(f"real image -> keep={k2} detections={labels}")
        retained = bool(k2 and d2)

    print(det.summary())
    if det.available and keep is False:
        extra = "" if retained is None else f"; real-object retained={retained}"
        print(f"RESULT: PASS (ONNX runtime + model loaded, inference ran, "
              f"junk frame discarded{extra})")
        return 0
    print("RESULT: INCONCLUSIVE (detector loaded but junk frame not discarded)")
    return 2


def cmd_configure_push(cfg: Config, *, _state=None, _cloud_factory=None,
                       _open_driver=None) -> int:
    """Point the RECORDER at WatchLog so the site reports with no PC running.

    RUNS AS ITS OWN PROCESS, deliberately. The setup wizard used to do this inline, and
    in 0.4.11 it took the whole installer down with a native crash the moment the feature
    was first enabled on real hardware. Recorder-push is a resilience BONUS layered on a
    working agent install -- it must never be able to kill the thing that installs it. As
    a separate process, any failure here (Python exception, native crash, hang, a recorder
    that wedges mid-request) is contained: the parent sees an exit code and moves on.

    It also means the recorder still gets configured even when the wizard dies, because
    the background agent can run this on its own schedule with no installer present.

    Prints a single machine-readable PUSH_JSON line. Exit 0 = the recorder confirmed it.
    """
    result = {"configured": False, "verified": False, "detail": ""}
    try:
        base = (cfg.push_bridge_url or "").strip().rstrip("/")
        if not base:
            result["detail"] = "no push bridge configured in this build"
            print("PUSH_JSON " + json.dumps(result), flush=True)
            return 2

        state = _state if _state is not None else load_state(cfg.state_path)
        if not state or not state.get("agent_id") or not state.get("agent_key"):
            result["detail"] = "this site is not enrolled yet"
            print("PUSH_JSON " + json.dumps(result), flush=True)
            return 2

        cloud = (_cloud_factory or (lambda: Cloud(cfg.supabase_url, cfg.publishable_key)))()
        issued = cloud.call("wl_agent_issue_push_token",
                            p_agent_id=state["agent_id"], p_agent_key=state["agent_key"])
        token = (issued or {}).get("token") if isinstance(issued, dict) else None
        if not token:
            result["detail"] = "WatchLog did not issue a push token"
            print("PUSH_JSON " + json.dumps(result), flush=True)
            return 2

        opened = (_open_driver or open_driver)(cfg)
        # Production open_driver() returns (driver, DeviceInfo). Test injectors may
        # return a bare driver. Treat both shapes correctly; the old tuple mistake made
        # every real recorder look like it had no configure_push method.
        driver = opened[0] if isinstance(opened, tuple) else opened
        try:
            configure = getattr(driver, "configure_push", None)
            if configure is None:
                result["detail"] = "this recorder model does not support recorder-push"
                print("PUSH_JSON " + json.dumps(result), flush=True)
                return 2

            out = configure(f"{base}/push/{token}") or {}
            result = {"configured": bool(out.get("applied")),
                      "verified": bool(out.get("verified")),
                      "detail": str(out.get("detail") or "")}
            print("PUSH_JSON " + json.dumps(result), flush=True)
            log(f"recorder push: configured={result['configured']} "
                f"verified={result['verified']} {result['detail']}")
            return 0 if result["verified"] else 2
        finally:
            try:
                driver.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001 - a bonus layer never fails loudly
        result["detail"] = f"could not configure recorder push ({type(exc).__name__})"
        try:
            print("PUSH_JSON " + json.dumps(result), flush=True)
        except Exception:  # noqa: BLE001
            pass
        return 2


def cmd_accept(cfg: Config, *, _state=None, _open_driver=None, _cloud_factory=None,
               _heartbeat=None, _spool_factory=None, _archive=None, _detector=None,
               _clip_probe=None, _ini_text=None, live_seconds: int | None = None) -> int:
    """0.4.4 §10 — post-install acceptance self-test.

    Exercises the REAL runtime chain on THIS site — configuration, local identity, cloud auth,
    recorder reachability, camera enumeration, retrievable archive (so the outage-recovery
    promise is real), the live-event path and a healthy local spool — and prints an honest
    ACCEPTED / BLOCKED report plus a machine-readable ``ACCEPTANCE_JSON`` line for the installer.

    Read-only: it changes no recorder setting and enables no runtime feature (operations/site
    control stay OFF). Exit 0 = accepted, 2 = blocked. Dependencies are injectable so the whole
    flow is testable with no cloud, recorder or spool.
    """
    import acceptance
    import dahua_archive
    import hikvision_archive
    from types import SimpleNamespace

    open_driver_fn = _open_driver or open_driver
    heartbeat_fn = _heartbeat or heartbeat
    cloud_factory = _cloud_factory or (lambda: Cloud(cfg.supabase_url, cfg.publishable_key))
    if _spool_factory is None:
        from spool import Spool
        spool_factory = lambda: Spool(cfg.spool_path, cfg.spool_max_rows)   # noqa: E731
    else:
        spool_factory = _spool_factory
    archive_fn = _archive
    live_seconds = int(live_seconds if live_seconds is not None
                       else (os.environ.get("WATCHLOG_ACCEPT_LIVE_SECONDS") or 10))
    state = _state if _state is not None else load_state(cfg.state_path)

    print(f"watchlog-agent {AGENT_VERSION} — post-install acceptance self-test\n")
    holder: dict = {}

    def _config():
        missing = [name for name, value in (("recorder address", cfg.nvr_url),
                                            ("WatchLog URL", cfg.supabase_url),
                                            ("WatchLog key", cfg.publishable_key)) if not value]
        return ("blocked", "missing " + ", ".join(missing)) if missing else ("pass", cfg.nvr_url)

    def _identity():
        if not state or not state.get("agent_id") or not state.get("agent_key"):
            return "blocked", "this site is not enrolled yet"
        return "pass", f"agent {state['agent_id']}"

    def _cloud():
        if not state:
            return "blocked", "no local identity to authenticate"
        device = SimpleNamespace(vendor=None, model=None, driver=cfg.nvr_driver)
        heartbeat_fn(cloud_factory(), state, device)
        return "pass", "cloud authenticated this agent"

    def _recorder():
        driver, info = open_driver_fn(cfg)
        holder["driver"], holder["info"] = driver, info
        return "pass", (f"{info.vendor} {info.model or ''}".strip() or "recorder reachable")

    def _cameras():
        driver = holder.get("driver")
        if driver is None:
            return "blocked", "recorder was not reachable"
        chans = driver.list_channels()
        holder["channels"] = chans
        return ("pass", f"{len(chans)} camera(s)") if chans else ("blocked", "no camera channels found")

    def _archive_check():
        driver = holder.get("driver")
        chans = holder.get("channels") or []
        if driver is None or not chans:
            return "warn", "recorder/cameras unavailable to check the archive"
        try:
            dahua_archive.install()
            hikvision_archive.install()
        except Exception:  # noqa: BLE001 — unsupported firmware reports honestly below
            pass
        channel = chans[0].get("channel") if isinstance(chans[0], dict) else getattr(chans[0], "channel", None)
        if archive_fn is not None:
            proof = archive_fn(driver, channel)
        elif getattr(driver, "name", "") == "hikvision-isapi":
            proof = hikvision_archive.prove_recorder_archive(driver, channel)
        else:
            proof = dahua_archive.prove_recorder_archive(driver, channel)
        status, _passed = acceptance.map_archive_status((proof or {}).get("status"))
        return status, (proof or {}).get("detail")

    def _clip_check():
        """Connector release gate: prove actual bounded media bytes, not just archive search."""
        driver = holder.get("driver")
        chans = holder.get("channels") or []
        if driver is None or not chans:
            return "blocked", "recorder/cameras unavailable to prove evidence export"
        channel = chans[0].get("channel") if isinstance(chans[0], dict) else getattr(chans[0], "channel", None)
        if not channel:
            return "blocked", "no camera channel available for evidence proof"

        try:
            dahua_archive.install()
        except Exception:  # noqa: BLE001
            pass

        if _clip_probe is not None:
            data = _clip_probe(driver, channel)
        else:
            seconds = max(5, min(60, int(getattr(cfg, "connector_accept_clip_seconds", 10))))
            end = now_utc() - timedelta(seconds=5)  # let the recorder flush the newest segment
            start = end - timedelta(seconds=seconds)
            data = driver.get_clip(str(channel), start, end)

        if not data:
            return "blocked", "recorder did not return evidence video bytes"

        try:
            import connector_capabilities
            import connector_rediscovery
            info = holder.get("info")
            if info is not None:
                connector_rediscovery.save_identity(
                    cfg, info, getattr(driver, "base_url", cfg.nvr_url)
                )
            connector_capabilities.mark_proof(
                cfg,
                "operations_evidence_clip",
                {"driver": getattr(driver, "name", cfg.nvr_driver), "bytes": len(data)},
            )
        except Exception:  # noqa: BLE001
            pass
        return "pass", f"evidence video export proven ({len(data)} bytes)"

    def _live():
        driver = holder.get("driver")
        if driver is None:
            return "blocked", "recorder was not reachable"
        stop = threading.Event()
        timer = threading.Timer(live_seconds, stop.set)
        timer.start()
        seen = 0
        try:
            for _ev in driver.stream_events(stop):
                seen += 1
                break
        finally:
            stop.set()
            timer.cancel()
        if seen:
            return "pass", f"live events flowing (seen within {live_seconds}s)"
        return "warn", f"no live events during a {live_seconds}s check (a quiet site is normal)"

    def _spool():
        sp = spool_factory()
        try:
            queued = sp.count()
        finally:
            try:
                sp.close()
            except Exception:  # noqa: BLE001
                pass
        return "pass", f"local spool healthy ({queued} queued)"

    def _ai():
        # Same packaged AI as live/recovery: prove the false-alarm filter runs and discards a blank
        # frame. Fail-open by design, so a missing runtime/model WARNS (not blocks) — but a
        # production build should pass.
        det = _detector if _detector is not None else (vision.build(cfg, log=lambda *a: None)
                                                       if getattr(cfg, "detect", True) else None)
        if det is None or not getattr(det, "available", True):
            return "warn", "AI false-alarm filter not packaged (every event will be kept)"
        try:
            import io as _io
            from PIL import Image
            buf = _io.BytesIO()
            Image.new("RGB", (320, 240), (120, 120, 120)).save(buf, "JPEG")
            keep, _dets = det.classify_event(buf.getvalue())
        except Exception as exc:  # noqa: BLE001
            return "warn", f"AI self-test could not run ({type(exc).__name__})"
        return ("pass", "AI packaged; blank frame discarded") if keep is False else \
               ("warn", "AI loaded but did not discard a blank frame")

    def _runtime():
        import wl_version
        meta = wl_version.build_metadata()
        if not meta.get("build_sha"):
            return "warn", f"agent {meta.get('version')} (build SHA not stamped — not a release build)"
        return "pass", f"agent {meta.get('version_string')}"

    def _security():
        text = _ini_text
        if text is None:
            try:
                p = base_dir() / "watchlog.ini"
                text = p.read_text(encoding="utf-8-sig", errors="replace") if p.exists() else ""
            except Exception:  # noqa: BLE001
                text = ""
        import re as _re
        m = _re.search(r"(?im)^\s*nvr_password\s*=\s*(.+?)\s*$", text or "")
        val = (m.group(1).strip() if m else "")
        if val and val.upper() != "REPLACE_ME":
            return "blocked", "a plaintext recorder password is present in watchlog.ini (must live only in the encrypted store)"
        return "pass", "no plaintext recorder password on disk"

    # ORDER MATTERS (0.4.6). Every REQUIRED check runs first, so the Ready/Blocked verdict is
    # decided from fast local+cloud probes in a few seconds. The three slow probes are all soft --
    # they can only ever add a warning, never block -- so they must not stand between the customer
    # and their answer. Dependencies are preserved: archive and live still run after the recorder
    # is open and cameras are enumerated.
    #
    # Every check carries its own "budget" in seconds. A wedged probe is the thing that left 0.4.5
    # spinning on "Running final acceptance checks", so no probe is allowed to run unbounded:
    # a hard check over budget is BLOCKED (fail closed), a soft one only warns.
    connector_mode = bool(getattr(cfg, "connector_mode", False))
    checks = [
        {"key": "config", "label": "Configuration present", "hard": True, "run": _config,
         "budget": 10},
        {"key": "identity", "label": "Site enrolled (local identity)", "hard": True,
         "run": _identity, "budget": 10},
        {"key": "cloud", "label": "WatchLog cloud authenticates this connector", "hard": True,
         "run": _cloud, "budget": 25},
        {"key": "recorder", "label": "Recorder reachable", "hard": True, "run": _recorder,
         "budget": 25},
        {"key": "cameras", "label": "Cameras enumerated", "hard": True, "run": _cameras,
         "budget": 30},
        {"key": "spool", "label": "Local spool healthy", "hard": True, "run": _spool,
         "budget": 20},
        {"key": "security", "label": "No plaintext recorder password on disk", "hard": True,
         "run": _security, "budget": 10},
    ]
    if connector_mode and getattr(cfg, "connector_require_clip_acceptance", False):
        checks.append(
            {"key": "clip", "label": "Evidence video export proven", "hard": True,
             "run": _clip_check, "budget": 75}
        )

    # Informational/soft checks: a quiet event stream or unstamped dev build must not
    # block a functioning connector. Local WatchLog AI is intentionally absent in
    # connector mode and is therefore not tested here.
    checks.extend([
        {"key": "runtime", "label": "Runtime version + build identity", "hard": False,
         "run": _runtime, "budget": 15},
        {"key": "archive", "label": "Recorded archive searchable (outage recovery)",
         "hard": False, "run": _archive_check, "budget": 25},
        {"key": "live", "label": "Recorder event stream", "hard": False, "run": _live,
         "budget": live_seconds + 10},
    ])
    if not connector_mode:
        checks.append(
            {"key": "ai", "label": "On-site AI false-alarm filter", "hard": False,
             "run": _ai, "budget": 30}
        )

    report = acceptance.run_checks(checks, log=print)
    stats = report["summary"]
    print()
    if report["ready"]:
        print(f"RESULT: ACCEPTED ({stats['passed']} passed, {stats['warned']} warning(s))")
    else:
        print(f"RESULT: BLOCKED ({stats['hard_failures']} required check(s) not passing)")
    # Single machine-readable line for the installer status panel — contains no secrets.
    print("ACCEPTANCE_JSON " + json.dumps(report, separators=(",", ":")))

    driver = holder.get("driver")
    if driver is not None:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass
    return 0 if report["ready"] else 2


def cmd_support_bundle(cfg: Config, *, dest_dir=None, _section=None, _state=None,
                       _setup_log=None, _spool_count=None) -> int:
    """0.4.4 §18 — export a NON-SECRET support bundle (.zip) for WatchLog support.

    Contains build identity, redacted operational config, non-secret identity (UUIDs), local
    counts and a redacted setup.log tail. NEVER contains the recorder password/username, agent
    key, enrollment code or any decrypted secret — support_bundle assembles config by allowlist
    and screens every line. Exit 0 on success.
    """
    import configparser as _cp
    import support_bundle
    import wl_version

    # Read the raw config section fresh so the allowlist sees EVERY key actually on disk
    # (including any secret-shaped ones) and drops all but the safe operational settings.
    if _section is not None:
        section = _section
    else:
        section = {}
        ini_path = base_dir() / "watchlog.ini"
        if ini_path.exists():
            ini = _cp.ConfigParser()
            try:
                ini.read(ini_path, encoding="utf-8-sig")
                if ini.has_section("watchlog"):
                    section = dict(ini.items("watchlog"))
            except _cp.Error:
                section = {}

    state = _state if _state is not None else (load_state(cfg.state_path) or {})

    if _setup_log is not None:
        setup_log = _setup_log
    else:
        setup_log = ""
        try:
            from setup_backend import programdata_dir
            log_path = programdata_dir() / "setup.log"
            if log_path.exists():
                setup_log = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            setup_log = ""

    if _spool_count is not None:
        spool_count = _spool_count
    else:
        spool_count = None
        try:
            from spool import Spool
            if cfg.spool_path.exists():
                sp = Spool(cfg.spool_path, cfg.spool_max_rows)
                try:
                    spool_count = sp.count()
                finally:
                    sp.close()
        except Exception:  # noqa: BLE001
            spool_count = None

    files = support_bundle.collect(section, state=state, setup_log=setup_log,
                                   build_meta=wl_version.build_metadata(), spool_count=spool_count)
    dest = Path(dest_dir) if dest_dir else Path.cwd()
    path = support_bundle.write_zip(dest, files)
    print(f"support bundle written: {path}")
    print("contents (no secrets): " + ", ".join(files.keys()))
    return 0


def cmd_check_update(cfg: Config, *, _fetch=None) -> int:
    """0.4.4 §13/§36 — check for updates (READ-ONLY).

    Fetches the signed release manifest for THIS site's channel over HTTPS, verifies its Ed25519
    signature, and reports whether an update is available. It NEVER downloads or installs anything
    — the transactional apply (via wl-upgrade.ps1) is a separate, deliberate step. Exit 0 =
    up-to-date or update-available, 2 = blocked (untrusted / too-old / unknown-channel), 1 = error.
    """
    import json as _json
    import updater
    import wl_version

    current = wl_version.version_string()
    print(f"watchlog-agent {AGENT_VERSION} — check for updates (channel: {cfg.update_channel})")
    if not cfg.update_url:
        print("update channel not configured (set update_url in watchlog.ini)")
        return 1
    if not cfg.update_url.lower().startswith("https://"):
        print("refusing to fetch the update manifest over a non-HTTPS URL")
        return 1

    fetch = _fetch or (lambda url: requests.get(url, timeout=20).text)
    try:
        manifest = updater.parse_manifest(fetch(cfg.update_url))
    except Exception as exc:  # noqa: BLE001 — network/parse failure is an honest error, not a crash
        print(f"could not fetch or parse the update manifest: {type(exc).__name__}")
        return 1

    signature_state = updater.verify_manifest_signature(manifest, cfg.update_public_key)
    plan = updater.plan_update(manifest, current, cfg.update_channel,
                              signature_state=signature_state,
                              require_signature=cfg.update_require_signature)
    action = plan.get("action")
    if action == "up-to-date":
        print(f"up to date ({current} on {cfg.update_channel})")
    elif action == "update":
        print(f"update available: {current} -> {plan['target']} on {cfg.update_channel}")
        if plan.get("notes"):
            print(f"  notes: {plan['notes']}")
    else:
        print(f"update blocked: {plan.get('reason')} (channel {cfg.update_channel})")
    print("UPDATE_JSON " + _json.dumps(plan, separators=(",", ":")))
    return 0 if action in ("up-to-date", "update") else 2


def cmd_update(cfg: Config, *, _fetch=None, _apply=None) -> int:
    """0.4.4 §13/§36 — APPLY an update transactionally (auto-rollback).

    Fetches + verifies the signed manifest; if a newer signed release exists for this channel,
    downloads the package, verifies its SHA-256 + size, and hands it to the transactional
    wl-upgrade sequence (preflight -> stage -> verify-version -> register -> commit), rolling back
    on ANY failure. Identity/config/secrets are preserved (only the binary is swapped). Exit 0 =
    updated or already up-to-date, 2 = blocked/refused/rolled-back, 1 = error.
    """
    import json as _json
    import platform as _platform
    import shutil
    import subprocess
    import tempfile
    import updater
    import wl_version

    current = wl_version.version_string()
    print(f"watchlog-agent {AGENT_VERSION} — update (channel: {cfg.update_channel})")
    if not cfg.update_url or not cfg.update_url.lower().startswith("https://"):
        print("update channel not configured over HTTPS (set update_url in watchlog.ini)")
        return 1

    fetch = _fetch or (lambda url: requests.get(url, timeout=20).text)
    try:
        manifest = updater.parse_manifest(fetch(cfg.update_url))
    except Exception as exc:  # noqa: BLE001
        print(f"could not fetch or parse the update manifest: {type(exc).__name__}")
        return 1

    signature_state = updater.verify_manifest_signature(manifest, cfg.update_public_key)
    plan = updater.plan_update(manifest, current, cfg.update_channel,
                              signature_state=signature_state,
                              require_signature=cfg.update_require_signature)
    if plan.get("action") == "up-to-date":
        print(f"already up to date ({current})")
        return 0
    if plan.get("action") != "update":
        print(f"update blocked: {plan.get('reason')}")
        return 2
    print(f"update available: {current} -> {plan['target']}; applying transactionally…")

    apply_fn = _apply or updater.apply_update
    install_dir = str(base_dir())
    if _apply is None and _platform.system() != "Windows":
        # The dangerous binary swap only runs on the installed Windows appliance.
        print("apply is only available on the installed Windows appliance; use --check-update here")
        return 2

    def _download(url):
        resp = requests.get(url, timeout=180, stream=True)
        resp.raise_for_status()
        fd, path = tempfile.mkstemp(suffix=".pkg")
        total = 0
        cap = 512 * 1024 * 1024                       # bounded: never stream an unbounded package
        with os.fdopen(fd, "wb") as out:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                total += len(chunk)
                if total > cap:
                    raise RuntimeError("update package exceeds the size cap")
                out.write(chunk)
        return path

    def _run_stage(stage, expected_version=None):
        script = base_dir() / "wl-upgrade.ps1"
        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-Stage", stage, "-InstallDir", install_dir]
        if expected_version:
            args += ["-ExpectedVersion", expected_version]
        return subprocess.run(args, capture_output=True).returncode

    def _stage_binary(pkg, dest_dir):
        shutil.copy2(pkg, str(Path(dest_dir) / "watchlog-agent.exe"))
        return True

    def _register():
        script = base_dir() / "register-service.ps1"
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(script)], capture_output=True).returncode == 0

    result = apply_fn(plan["target"], plan["url"], plan["sha256"], install_dir=install_dir,
                      size=plan.get("size"), download=_download, run_stage=_run_stage,
                      stage_binary=_stage_binary, register=_register, log=print)
    print("UPDATE_APPLY_JSON " + _json.dumps(result, separators=(",", ":")))
    if result.get("ok"):
        return 0
    return 2 if result.get("rolled_back") else 1


def cmd_status_json(cfg: Config, *, _state=None, _open_driver=None, _cloud_factory=None,
                    _heartbeat=None, _spool_factory=None, _archive=None, _retention=None,
                    _now=None) -> int:
    """0.4.4 P1 — emit the WatchLog Site Status document (STATUS_JSON) for the status panel.

    Local appliance view: agent identity/version/spool, recorder reachability + archive capability,
    cameras (with the monitored-vs-unused classification from local config), an archive/recovery
    verdict, and honest 'not available' storage. Read-only; every probe is guarded and injected, so
    the whole thing is testable with no recorder/cloud. Exit 0 always (a status read never fails).
    """
    import json as _json
    import site_status as ss
    import wl_version
    from types import SimpleNamespace

    now = _now or now_utc()
    state = _state if _state is not None else (load_state(cfg.state_path) or {})

    # --- agent ---
    spool_backlog = 0
    try:
        if _spool_factory is not None:
            sp = _spool_factory()
        else:
            from spool import Spool
            sp = Spool(cfg.spool_path, cfg.spool_max_rows)
        try:
            spool_backlog = sp.count()
        finally:
            try:
                sp.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        spool_backlog = 0

    cloud_ok = None
    if state.get("agent_id"):
        try:
            cloud = (_cloud_factory or (lambda: Cloud(cfg.supabase_url, cfg.publishable_key)))()
            (_heartbeat or heartbeat)(cloud, state, SimpleNamespace(vendor=None, model=None,
                                                                    driver=cfg.nvr_driver))
            cloud_ok = True
        except Exception:  # noqa: BLE001
            cloud_ok = False

    agent = ss.agent_view(build_meta=wl_version.build_metadata(), channel=cfg.update_channel,
                          state=state, running=None, last_heartbeat=None, cloud_ok=cloud_ok,
                          spool_backlog=spool_backlog, recovery_backlog=0)

    # --- recorder + cameras + archive (one driver open, all guarded) ---
    driver = info = None
    try:
        driver, info = (_open_driver or open_driver)(cfg)
    except Exception:  # noqa: BLE001 — recorder unreachable is an honest state, not a crash
        driver = info = None

    capability = None
    channels = []
    if driver is not None:
        try:
            capability = driver.historical_capability() if hasattr(driver, "historical_capability") else None
        except Exception:  # noqa: BLE001
            capability = None
        try:
            channels = [{"channel": str(c.channel), "name": c.name} for c in driver.list_channels()]
        except Exception:  # noqa: BLE001
            channels = []

    recorder = ss.recorder_view(
        reachable=driver is not None, auth_ok=(driver is not None or None),
        info={"vendor": getattr(info, "vendor", None), "model": getattr(info, "model", None),
              "driver": getattr(driver, "name", None)} if info is not None else None,
        capability=capability)

    # monitored-vs-unused classification from local config (falls back to all-monitored)
    configured = {}
    names = {}
    for prof in (cfg.camera_profiles or []):
        ch = str(prof.get("channel"))
        if not ch:
            continue
        names[ch] = prof.get("name") or None
        configured[ch] = bool(prof.get("monitored", prof.get("analytics_enabled", True)))
    merged = [{"channel": c["channel"], "name": names.get(c["channel"]) or c["name"]} for c in channels] \
        or [{"channel": ch, "name": names.get(ch) or f"Camera {ch}"} for ch in configured]
    camera = ss.camera_view(merged, configured=configured or None, health=None)

    archive_status = None
    if driver is not None and merged:
        try:
            import dahua_archive
            try:
                dahua_archive.install()
            except Exception:  # noqa: BLE001
                pass
            proof = (_archive or dahua_archive.prove_recorder_archive)(driver, merged[0]["channel"])
            archive_status = (proof or {}).get("status")
        except Exception:  # noqa: BLE001
            archive_status = None
    archive = ss.archive_view(proof_status=archive_status, last_proof_at=iso(now),
                              recovery_backlog=0)

    recording = ss.recording_view(camera["cameras"], recording=None)

    # Retention depth (P7): bounded, best-effort. Off by default so the panel refresh stays fast;
    # WATCHLOG_STATUS_RETENTION=1 (or a dedicated deep recheck) enables the archive-boundary probe.
    ret = _retention
    if ret is None and driver is not None and merged and \
            os.environ.get("WATCHLOG_STATUS_RETENTION", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            import retention as _retmod
            ret = _retmod.estimate_retention(driver, merged[0]["channel"], now=now)
        except Exception:  # noqa: BLE001
            ret = None
    if ret and ret.get("status") in ("measured", "at_least"):
        storage = ss.storage_view({"retention_days": ret.get("retention_days"),
                                   "oldest_recording": ret.get("oldest_recording")})
    else:
        storage = ss.storage_view(None)              # honest 'Not available on this recorder'

    if driver is not None:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass

    snap = ss.build_snapshot(agent=agent, recorder=recorder, camera=camera, recording=recording,
                             archive=archive, storage=storage, generated_at=iso(now))
    print("STATUS_JSON " + _json.dumps(snap, separators=(",", ":")))
    return 0


def cmd_recheck_archive_json(cfg: Config, *, _open_driver=None, _archive=None, _inspect=None,
                             _now=None) -> int:
    """0.4.4 P1.5 — run a FRESH archive proof NOW (never cached) and classify honestly:
    ARCHIVE VERIFIED / ARCHIVE AVAILABLE — FRAME DECODE UNVERIFIED / ARCHIVE EMPTY /
    ARCHIVE UNSUPPORTED / ARCHIVE FAILED. Emits ARCHIVE_JSON with safe media diagnostics
    (size + magic + decoder + result — never image contents or secrets). Exit 0 always.
    """
    import json as _json
    import dahua_archive
    import recovery_ai

    now = _now or now_utc()
    out = {"schema": "watchlog.archive_recheck.v1", "state": "ARCHIVE FAILED",
           "checked_at": iso(now), "channel": None, "frame_decoded": None, "diagnostics": {}, "detail": ""}
    driver = None
    try:
        driver, _info = (_open_driver or open_driver)(cfg)
    except Exception:  # noqa: BLE001
        driver = None
    if driver is None:
        out["detail"] = "recorder not reachable"
        print("ARCHIVE_JSON " + _json.dumps(out, separators=(",", ":")))
        return 0
    try:
        dahua_archive.install()
    except Exception:  # noqa: BLE001
        pass

    channel = "1"
    for prof in (getattr(cfg, "camera_profiles", None) or []):
        if prof.get("monitored", True) and prof.get("channel"):
            channel = str(prof["channel"])
            break
    out["channel"] = channel

    try:
        proof = (_archive or dahua_archive.prove_recorder_archive)(driver, channel) or {}
        status = proof.get("status")
    except Exception:  # noqa: BLE001
        proof, status = {}, "error"

    if status == "verified":
        sample = proof.get("sample") or []
        ts = (sample[0].get("start") if sample and isinstance(sample[0], dict) else None) or iso(now)
        try:
            frame, diag = (_inspect or recovery_ai.inspect_and_decode)(driver, channel, ts)
        except Exception:  # noqa: BLE001
            frame, diag = None, {}
        out["diagnostics"] = diag or {}
        out["frame_decoded"] = bool(frame)
        out["state"] = "ARCHIVE VERIFIED" if frame else "ARCHIVE AVAILABLE — FRAME DECODE UNVERIFIED"
    elif status == "empty":
        out["state"] = "ARCHIVE EMPTY"
    elif status == "unsupported":
        out["state"] = "ARCHIVE UNSUPPORTED"
    else:
        out["state"] = "ARCHIVE FAILED"

    try:
        driver.close()
    except Exception:  # noqa: BLE001
        pass
    print("ARCHIVE_JSON " + _json.dumps(out, separators=(",", ":")))
    return 0


def cmd_probe(cfg: Config) -> None:
    """Identify the recorder. Touches no cloud service — pure diagnosis."""
    log(f"probing {cfg.nvr_url}")
    try:
        driver, info = open_driver(cfg)
    except DriverError as e:
        # Whoever runs --probe is standing in front of the recorder with a
        # laptop. A Python traceback tells them nothing they can act on.
        print(f"\n  Could not identify a recorder at {cfg.nvr_url}\n")
        # autodetect() puts a header on line 1 and one line per driver
        # after it; a named driver raises a single line. Show the detail
        # either way — "HTTP 401" is the whole answer for a bad password.
        lines = str(e).splitlines()
        for line in (lines[1:] if lines[0].startswith("no driver recognised")
                     else lines):
            if line.strip():
                print("   ", line.strip()[:200])

        # Do not stop at "it did not work". Find out what IS there: a
        # closed port, a web interface moved to 8080, an unsupported
        # protocol and a wrong IP all look identical above, and they have
        # four different fixes.
        host = discover.host_of(cfg.nvr_url)
        discover.report(host, discover.scan(cfg.nvr_url, log=print), log=print)
        raise SystemExit(1) from None
    print()
    print(f"  driver     {driver.name}"
          + ("" if driver.verified_against_hardware
             else "   (NOT verified against hardware)"))
    print(f"  vendor     {info.vendor}")
    print(f"  model      {info.model or '-'}")
    print(f"  firmware   {info.firmware or '-'}")
    print(f"  serial     {info.serial or '-'}")
    print(f"  channels   {info.channel_count if info.channel_count is not None else '-'}")
    try:
        chans = driver.list_channels()
        print(f"\n  {len(chans)} channel(s):")
        for c in chans:
            print(f"    {c.channel:>4}  {c.name or '-'}")
    except DriverError as e:
        print(f"  channel list failed: {e}")

    # Read-only: what analytics this recorder supports, and what is already
    # on. We never change a setting here.
    try:
        caps = driver.capabilities()
        if caps.get("channels"):
            print("\n  analytics available on this recorder:")
            for ch in caps["channels"]:
                on = [a["label"] for a in ch["analytics"] if a.get("active")]
                avail = [a["label"] for a in ch["analytics"]
                         if a.get("supported") and not a.get("active")]
                print(f"    ch{ch['channel']} {ch.get('name') or '':<14} "
                      f"on: {', '.join(on) or 'none'}")
                if avail:
                    print(f"        available to enable: {', '.join(avail)}")
            print("  (line/zone analytics need the line or zone drawn on the "
                  "scene before they fire.)")
    except Exception as e:                       # noqa: BLE001
        print(f"  (capability probe skipped: {type(e).__name__})")

    print("\n  listening 20s for live events...")
    stop = threading.Event()
    threading.Timer(20, stop.set).start()
    seen = 0
    try:
        for ev in driver.stream_events(stop):
            seen += 1
            print(f"    {iso(ev.device_ts)}  ch{ev.channel:<4} {ev.event_type}")
            if seen >= 20:
                break
    except DriverError as e:
        print(f"  event stream failed: {e}")
    finally:
        stop.set()
        driver.close()
    if seen == 0:
        print("    (nothing fired — normal on a quiet site; walk past a camera)")
    print()


PUSH_INITIAL_DELAY_SECONDS = 45
PUSH_STATUS_POLL_SECONDS = 300
PUSH_RECONFIGURE_SECONDS = 6 * 60 * 60
PUSH_CONFIGURE_TIMEOUT_SECONDS = 45


def recorder_push_worker(cfg: Config, state: dict, cloud: Cloud,
                         stop: threading.Event) -> None:
    """Best-effort PC-off resilience, never on the live/install critical path.

    Configuration read-back proves only that a setting stuck. PC-off coverage is
    verified only by a NEW NVR-originated POST after this runtime started/configured
    the recorder; stale push history can never satisfy the gate.
    """
    if not (cfg.push_bridge_url or "").strip():
        return
    if stop.wait(PUSH_INITIAL_DELAY_SECONDS):
        return

    next_configure = 0.0
    configured_once = False
    backend_missing_logged = False
    verification_after = now_utc()

    while not stop.is_set():
        now = time.monotonic()

        # Configure first. The previous implementation queried historical push status
        # first, so an old last_push_at could make a new/broken recorder look verified.
        if now >= next_configure:
            try:
                import proc_util
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, "--configure-push"]
                else:
                    cmd = [sys.executable, str(Path(__file__).resolve()), "--configure-push"]
                code, out = proc_util.run_bounded(cmd, PUSH_CONFIGURE_TIMEOUT_SECONDS)
                report = None
                for line in (out or "").splitlines():
                    if line.startswith("PUSH_JSON "):
                        try:
                            report = json.loads(line[len("PUSH_JSON "):])
                        except Exception:  # noqa: BLE001
                            report = None
                        break
                if report and report.get("configured"):
                    configured_once = True
                    verification_after = now_utc()
                    if report.get("verified"):
                        log("recorder push: NVR config read-back verified; awaiting a fresh direct POST")
                    else:
                        log("recorder push: NVR config written; awaiting a fresh direct POST")
                elif report:
                    log("recorder push: no safe generic PC-off configuration for this recorder")
                elif code != 0:
                    log(f"recorder push: configuration helper exited {code}; live agent remains authoritative")
            except Exception as exc:  # noqa: BLE001
                log(f"recorder push: background configuration deferred ({type(exc).__name__})")
            next_configure = time.monotonic() + PUSH_RECONFIGURE_SECONDS

        try:
            status = cloud.call(
                "wl_agent_push_status",
                p_agent_id=state["agent_id"],
                p_agent_key=state["agent_key"],
            ) or {}
            backend_missing_logged = False
            last_raw = status.get("last_push_at")
            last_push = None
            if last_raw:
                try:
                    last_push = datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                    if last_push.tzinfo is None:
                        last_push = last_push.replace(tzinfo=timezone.utc)
                    last_push = last_push.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    last_push = None

            # A current recorder POST is the only end-to-end proof. Five seconds of
            # skew tolerance covers recorder/bridge/server timestamp boundaries without
            # accepting an old deployment's liveness.
            if last_push and last_push >= (verification_after - timedelta(seconds=5)):
                log("recorder push: fresh end-to-end PC-off delivery verified")
                return
        except Exception as exc:  # noqa: BLE001
            low = str(exc).lower()
            if ("wl_agent_push_status" in low or "schema cache" in low or "404" in low):
                if not backend_missing_logged:
                    log("recorder push: status RPC unavailable; will retry after backend migration")
                    backend_missing_logged = True
            elif not configured_once:
                log(f"recorder push: status check deferred ({type(exc).__name__})")

        stop.wait(PUSH_STATUS_POLL_SECONDS)


def recovery_worker(cfg: Config, state: dict, cloud: Cloud, stop: threading.Event,
                    spool, channels=None, holder=None) -> None:
    """Automatic NVR outage recovery (0.4.4 §1/§2/§5). On start, the persisted last-live vs now
    yields the missed interval, reported as a PENDING recovery interval. Then it claims pending
    intervals and backfills each from the recorder archive in bounded, resumable, idempotent
    chunks (read-only; recovered events carry recorder_archive provenance). LIVE monitoring always
    has priority (yields when the live spool has a backlog) and it is throttled. OFF only if
    recovery_enabled=false. A failure here can never disturb events/heartbeat/health."""
    if not cfg.recovery_enabled:
        return
    import recovery as rec
    stop.wait(min(20, cfg.recovery_seconds))            # let enrollment / live settle first
    def _channel_id(item):
        if isinstance(item, dict):
            return item.get("channel")
        return getattr(item, "channel", None)

    cams = [str(ch) for ch in (_channel_id(c) for c in (channels or []))
            if ch is not None] or None
    holder = holder if holder is not None else {}

    # Build the on-site detector ONCE (same packaged AI as the live path) so deep recovery can run
    # WatchLog analysis over recovered footage. A missing runtime/model just means recorder-native
    # event replay only — never a crash, never fabricated intelligence.
    detector = None
    if cfg.recovery_ai_enabled:
        try:
            detector = vision.build(cfg, log)
        except Exception as e:                           # noqa: BLE001
            log(f"recovery: detector unavailable ({type(e).__name__}); event-replay only")

    def recorder_is_live() -> bool:
        drv = holder.get("live_driver")
        activity = float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0)
        if activity and time.monotonic() - activity < 150.0:
            return True
        seen = float(holder.get("recorder_live_at") or 0.0)
        return bool(seen and time.monotonic() - seen < 150.0)

    while not stop.is_set():
        # A very long Internet outage can fill the bounded local spool. trim() records
        # exactly which local-observation interval had to be evicted; convert that durable
        # marker into the same recorder-archive recovery pipeline once the NVR is live.
        try:
            overflow_gap = spool.pending_recovery_gap()
            if overflow_gap and recorder_is_live():
                cloud.call("wl_open_recovery_interval",
                           p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
                           p_started_at=overflow_gap[0], p_ended_at=overflow_gap[1],
                           p_cameras=cams or [])
                if spool.clear_recovery_gap(*overflow_gap):
                    log(f"recovery: spool overflow {overflow_gap[0]}..{overflow_gap[1]}; "
                        "opened recorder-archive reconciliation")
        except Exception as e:                           # noqa: BLE001
            log(f"recovery: spool-overflow reconciliation deferred: {type(e).__name__}")

        # Detect BOTH restart gaps and in-process recorder/network gaps. Only open the
        # interval after the recorder is live again; while it is still down there is
        # nothing to backfill and no reason to hammer it.
        try:
            if recorder_is_live():
                last_live = rec.read_last_live(cfg.last_live_path)
                now = now_utc()
                outage = rec.detect_outage(last_live, now, cfg.recovery_threshold_seconds)
                if outage:
                    cloud.call("wl_open_recovery_interval", p_agent_id=state["agent_id"],
                               p_agent_key=state["agent_key"], p_started_at=iso(outage[0]),
                               p_ended_at=iso(outage[1]), p_cameras=cams or [])
                    rec.persist_last_live(cfg.last_live_path, now)
                    log(f"recovery: detected recorder gap {iso(outage[0])}..{iso(outage[1])}; "
                        "opened resumable archive recovery")
        except Exception as e:                           # noqa: BLE001
            log(f"recovery: gap detector skipped: {type(e).__name__}")

        try:
            driver, _info = open_driver(cfg)
            try:
                import dahua_archive
                import hikvision_archive
                dahua_archive.install()
                hikvision_archive.install()             # both vendors expose bounded archive reads
            except Exception:                           # noqa: BLE001
                pass
            try:
                runner = rec.RecoveryRunner(
                    cloud, state["agent_id"], state["agent_key"], driver,
                    lambda ev: spool.add(ev),
                    chunk_seconds=cfg.recovery_chunk_seconds,
                    throttle_seconds=cfg.recovery_throttle_seconds,
                    live_pending=lambda: spool.count() > cfg.recovery_live_backlog,
                    detector=detector, ai_max_frames=cfg.recovery_ai_max_frames,
                    log=log)
                runner.run_once(limit=1)
            finally:
                try:
                    driver.close()
                except Exception:                        # noqa: BLE001
                    pass
        except Exception as e:                           # noqa: BLE001 — recovery never disturbs the agent
            log(f"recovery: {type(e).__name__}: {nvr_health.redact(str(e))}")
        stop.wait(cfg.recovery_seconds)


def cmd_run(cfg: Config, state: dict, cloud: Cloud, once: bool,
            device=None, channels=None) -> None:
    from spool import Spool
    import camera_health

    spool = Spool(cfg.spool_path, cfg.spool_max_rows)
    log(f"spool: {cfg.spool_path} ({spool.count()} queued)")

    # Shared holder so the collector (native faults) and the health worker (probes) drive the
    # SAME per-camera machines. Seeded from the channels found at startup; (re)built lazily by
    # the health cycle once enumeration succeeds if we started with none.
    mon_channels = [str(c.get("channel")) for c in (channels or []) if c.get("channel")]
    monitor = (camera_health.CameraHealthMonitor(
        mon_channels, batch_size=cfg.health_batch, concurrency=cfg.health_concurrency)
        if mon_channels else None)
    holder = {"monitor": monitor}

    if once:
        # Collect for a short window first, otherwise --once on a fresh
        # install drains an empty spool and looks like nothing works.
        stop = threading.Event()
        worker = threading.Thread(target=collector, args=(cfg, spool, stop, holder),
                                  daemon=True, name="collector")
        worker.start()
        log(f"collecting for {ONCE_COLLECT_SECONDS}s...")
        stop.wait(ONCE_COLLECT_SECONDS)
        stop.set()
        worker.join(timeout=5)
        try:
            upload_once(cloud, state, spool)
        except RuntimeError as e:
            log(f"ERROR: upload failed: {e}")
        heartbeat(cloud, state, device)
        health_cycle(cloud, state, cfg, holder)
        spool.close()
        return

    stop = threading.Event()
    worker = threading.Thread(target=collector, args=(cfg, spool, stop, holder),
                              daemon=True, name="collector")
    worker.start()
    # Health probing runs on its OWN thread so a stalled probe can never delay heartbeat/upload.
    import monitoring_coverage as coverage   # local module; NOT the PyPI 'coverage' tool
    resume_evt = threading.Event()
    cov = coverage.CoverageMonitor(loop_period=1.0)
    health = threading.Thread(target=health_worker,
                              args=(cfg, state, cloud, holder, stop, resume_evt),
                              daemon=True, name="health")
    health.start()
    # Site Control read plane (H6). Thread exits immediately unless enabled in the ini.
    sitectl = threading.Thread(target=command_worker, args=(cfg, state, cloud, stop),
                               daemon=True, name="sitecontrol")
    sitectl.start()
    # Automatic NVR outage recovery (§1/§2). Read-only; yields to live; OFF only if disabled in ini.
    recov = threading.Thread(target=recovery_worker,
                             args=(cfg, state, cloud, stop, spool, channels, holder),
                             daemon=True, name="recovery")
    recov.start()

    # PC-off resilience is deliberately asynchronous. It never delays setup or live
    # monitoring and becomes "verified" only after an actual NVR -> bridge POST.
    push_direct = threading.Thread(
        target=recorder_push_worker, args=(cfg, state, cloud, stop),
        daemon=True, name="recorder-push")
    push_direct.start()

    log(f"running: upload every {cfg.upload_seconds}s, heartbeat every "
        f"{cfg.heartbeat_seconds}s, health every ~{cfg.health_seconds}s, outbound only. "
        f"Ctrl-C to stop.")

    next_up = next_beat = 0.0
    last_wall = time.time()
    try:
        while True:
            clock = time.monotonic()
            now_wall = time.time()
            # Suspend/resume detection: a big wall-clock jump across the ~1 s loop means the
            # site PC was asleep/hibernated/stalled and WatchLog was NOT observing the site.
            gap = cov.tick(last_wall, now_wall)
            last_wall = now_wall
            if gap is not None:
                log(f"resume: site not observed for ~{int(gap.ended_at - gap.started_at)}s "
                    f"(site PC sleep/suspend); reconciling recorder health now")
                resume_evt.set()                        # immediate health reconciliation
            cov.report_pending(cloud, state)            # best-effort; retries while cloud down
            if clock >= next_up:
                next_up = clock + cfg.upload_seconds
                try:
                    upload_once(cloud, state, spool)
                except (RuntimeError, requests.RequestException) as e:
                    log(f"ERROR: upload failed, will retry: "
                        f"{str(e).splitlines()[0][:200]}")
            if clock >= next_beat:
                next_beat = clock + cfg.heartbeat_seconds
                try:
                    heartbeat(cloud, state, device)
                except (RuntimeError, requests.RequestException) as e:
                    log(f"ERROR: heartbeat failed, will retry: "
                        f"{str(e).splitlines()[0][:200]}")
                # Persist RECORDER observation, not merely PC/cloud liveness. If the
                # recorder goes away while the agent still heartbeats, this clock intentionally
                # stops so recovery can open the missing interval when recorder contact returns.
                if cfg.recovery_enabled:
                    try:
                        drv = holder.get("live_driver")
                        activity = float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0)
                        fresh = activity and time.monotonic() - activity < 150.0
                        if fresh:
                            import recovery as _rec
                            _rec.persist_last_live(cfg.last_live_path, now_utc())
                    except Exception:                    # noqa: BLE001
                        pass
            time.sleep(1)
    except KeyboardInterrupt:
        log("stopping...")
        stop.set()
        resume_evt.set()                                # wake the health thread so it can exit
        worker.join(timeout=5)
        health.join(timeout=5)
        sitectl.join(timeout=5)
        recov.join(timeout=5)
        push_direct.join(timeout=5)
        spool.close()
        if holder.get("store"):
            try:
                holder["store"].close()
            except Exception:                          # noqa: BLE001
                pass
        log("stopped")


# --- main --------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="WatchLog site agent")
    ap.add_argument("--probe", action="store_true",
                    help="identify the NVR and watch for events; no cloud calls")
    ap.add_argument("--once", action="store_true",
                    help="one spool drain + heartbeat, then exit")
    ap.add_argument("--enroll-only", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="delete local identity and spool, then exit")
    ap.add_argument("--list-drivers", action="store_true")
    ap.add_argument("--setup", action="store_true",
                    help="run the setup wizard: find the recorder, ask for "
                         "its login, test it, and write watchlog.ini")
    ap.add_argument("--find", nargs="?", const="", metavar="SUBNET",
                    help="sweep this PC's local network for recorders and "
                         "report their addresses; needs no config")
    ap.add_argument("--scan", metavar="IP",
                    help="scan an address for a recorder and report what "
                         "answers; needs no config at all")
    ap.add_argument("--connector-selftest", action="store_true",
                    help="offline packaging check for recorder discovery/control/evidence; "
                         "needs no config or network")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the on-site AI false-alarm filter is packaged "
                         "and working in this build; needs no config")
    ap.add_argument("--configure-push", action="store_true",
                    help="point the recorder at the WatchLog push bridge (PC-free reporting)")
    ap.add_argument("--accept", action="store_true",
                    help="run the post-install acceptance self-test (identity, cloud, "
                         "recorder, cameras, archive, live events, spool) and exit")
    ap.add_argument("--support-bundle", action="store_true",
                    help="export a non-secret diagnostic support bundle (.zip) and exit")
    ap.add_argument("--check-update", action="store_true",
                    help="check the signed release manifest for a newer version (read-only) and exit")
    ap.add_argument("--update", action="store_true",
                    help="apply an available signed update transactionally (auto-rollback) and exit")
    ap.add_argument("--status-json", action="store_true",
                    help="print the machine-readable Site Status document (for the status panel) and exit")
    ap.add_argument("--recheck-archive-json", action="store_true",
                    help="run a fresh archive proof now (with media-decode diagnostics) and exit")
    ap.add_argument("--version", action="store_true",
                    help="print the runtime version and exit (no config, no cloud) — used by "
                         "the installer to verify the actually-installed/running agent")
    args = ap.parse_args()

    if args.version:
        # bare, machine-parseable single line so the installer can compare it to the expected
        # release version. Runs before ANY config/enrollment so a not-yet-configured or upgraded
        # binary still answers truthfully.
        print(AGENT_VERSION)
        return

    if args.connector_selftest:
        raise SystemExit(cmd_connector_selftest())

    if args.selftest:
        raise SystemExit(cmd_selftest())

    # These need no configuration at all - they are the tools you reach
    # for precisely when the configuration is wrong.
    if args.find is not None:
        print()
        # Ask the network first, then fall back to sweeping it.
        found = wsdiscovery.discover(log=print)
        wsdiscovery.report(found, log=print)
        print()
        discover.sweep_report(discover.sweep(args.find or None, log=print),
                              log=print)
        return

    if args.scan:
        host = discover.host_of(args.scan)
        discover.report(host, discover.scan(args.scan, log=print), log=print)
        return

    if args.list_drivers:
        for name, cls in DRIVERS.items():
            mark = "verified" if cls.verified_against_hardware else "UNVERIFIED"
            print(f"  {name:18} {mark}")
        return

    cfg = Config()

    # Post-install acceptance runs against the config as-is and must never launch the
    # setup wizard — an unconfigured site should report a 'blocked' config check, not
    # be walked through setup.
    if args.configure_push:
        raise SystemExit(cmd_configure_push(cfg))

    if args.accept:
        raise SystemExit(cmd_accept(cfg))

    if args.support_bundle:
        raise SystemExit(cmd_support_bundle(cfg))

    if args.check_update:
        raise SystemExit(cmd_check_update(cfg))

    if args.update:
        raise SystemExit(cmd_update(cfg))

    if args.status_json:
        raise SystemExit(cmd_status_json(cfg))

    if args.recheck_archive_json:
        raise SystemExit(cmd_recheck_archive_json(cfg))

    # The wizard runs on request, and automatically when no recorder is
    # configured yet. Someone who double-clicks the exe for the first time
    # should be walked through setup, not shown an error about a missing
    # nvr_url they have never heard of.
    if args.setup or (not cfg.nvr_url and not args.status and not args.reset):
        ini_path = base_dir() / "watchlog.ini"
        values = setup_wizard.run(ini_path, cfg.supabase_url,
                                  cfg.publishable_key, cfg.enrollment_code)
        if not values:
            setup_wizard.pause()
            return
        setup_wizard.write_config(ini_path, values)
        cfg = Config()          # re-read what we just wrote
        print()

    log(f"watchlog-agent {AGENT_VERSION} on {platform.node()} "
        f"({platform.system()} {platform.release()})")
    log(f"state file: {cfg.state_path}")

    if args.reset:
        for p in (cfg.state_path, cfg.spool_path):
            if p.exists():
                p.unlink()
                log(f"deleted {p}")
        log("next run will re-enroll")
        return

    state = load_state(cfg.state_path)

    if args.status:
        if not state:
            log("not enrolled")
        else:
            log(f"enrolled  agent_id={state['agent_id']}")
            log(f"          site_id={state['site_id']} tenant_id={state['tenant_id']}")
            log("          key=(stored, encrypted)")
            log(f"          enrolled_at={state.get('enrolled_at')}")
        if cfg.spool_path.exists():
            from spool import Spool
            sp = Spool(cfg.spool_path, cfg.spool_max_rows)
            log(f"spool     {sp.count()} events queued at {cfg.spool_path}")
            sp.close()
        return

    if args.probe:
        cmd_probe(cfg)
        return

    cfg.require_cloud()
    log(f"supabase: {cfg.supabase_url}  publishable key "
        f"{mask(cfg.publishable_key)}")
    cloud = Cloud(cfg.supabase_url, cfg.publishable_key)

    # Identify the recorder ONCE and reuse the answer: enrollment, camera sync
    # and the first heartbeat need only identity + channel inventory.
    #
    # Do NOT call driver.capabilities() here. On Hikvision that fans out into
    # multiple ISAPI calls per channel and Build 41 could spend minutes doing
    # optional enrichment before the live collector/heartbeat loop even started.
    # Connectivity and event collection must start first; recorder capability
    # enrichment is deliberately deferred out of this startup critical path.
    device, channels = None, []
    try:
        driver, device = open_driver(cfg)
        try:
            channels = [{"channel": c.channel, "name": c.name}
                        for c in driver.list_channels()]
        finally:
            driver.close()
    except (DriverError, SystemExit) as e:
        for line in str(e).splitlines():
            if line.strip():
                log(f"WARNING: NVR not identified: {line.strip()[:200]}")
        # Run the scan automatically, once, at startup. Telling someone to
        # "go and run --probe" assumes they will read the log, be at that
        # machine, and try again. They usually just run it the same way
        # again, and we learn nothing. Fifteen seconds spent here answers
        # the question the first time.
        if cfg.nvr_url:
            try:
                host = discover.host_of(cfg.nvr_url)
                discover.report(host,
                                discover.scan(cfg.nvr_url, log=log),
                                log=log)
            except Exception as se:                    # noqa: BLE001
                log(f"scan failed: {type(se).__name__}: {se}")

    if state:
        log(f"already enrolled as {state['agent_id']} - skipping enrollment")
    else:
        state = enroll(cfg, cloud, device)

    if args.enroll_only:
        return

    if channels:
        try:
            mapping = cloud.call("wl_sync_cameras", p_agent_id=state["agent_id"],
                                 p_agent_key=state["agent_key"],
                                 p_cameras=channels)
            log(f"cameras synced: {len(mapping or [])} channels")
        # BOOT SAFETY: catch the TRANSPORT failure too, not just CloudError(RuntimeError).
        # The -AtStartup trigger fires before the network stack is ready. The NVR is on the
        # same LAN so the recorder probe above SUCCEEDS, then this first cloud call raises
        # requests.ConnectionError (an OSError, NOT a RuntimeError) and used to escape
        # uncaught -- killing the agent before it ever reached its resilient run loop, and
        # taking the launcher's restart loop down with it. The run loop below retries
        # forever, so a startup sync failure must only WARN.
        except (RuntimeError, requests.RequestException, OSError) as e:
            log(f"WARNING: camera sync failed: {str(e).splitlines()[0][:160]}")

    cmd_run(cfg, state, cloud, once=args.once, device=device, channels=channels)


if __name__ == "__main__":
    main()

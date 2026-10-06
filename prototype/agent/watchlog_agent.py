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
import hashlib
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
import nvr_health   # module scope: every worker except-handler redacts through it
import recorder_probe
import setup_wizard
import vision
import wsdiscovery
from drivers import DRIVERS, DriverError, autodetect, build
from drivers.base import RecorderIdentityMismatch

import credential_store
import recorder_runtime
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


def worker_fault(worker: str, error: BaseException) -> None:
    """Last-resort log for a background worker loop. It never raises: anything escaping a
    worker's handler ends that thread for the life of the process, and nothing restarts it."""
    try:
        log(f"{worker}: {type(error).__name__}: {nvr_health.redact(str(error))}")
    except BaseException:                              # noqa: BLE001
        pass


_RUNTIME_HEALTH_LOCK = threading.Lock()


def runtime_health_path() -> Path:
    # Integrity-sensitive installer proof: keep it under the existing
    # SYSTEM+Administrators-only Secrets ACL so a standard local user cannot
    # forge a healthy-version marker and trick Repair/Upgrade into committing.
    return default_state_dir() / "Secrets" / "runtime-health.json"


def update_runtime_health(**fields) -> None:
    """Atomically publish non-secret local proof that the runtime is actually healthy.

    The repair upgrader reads this as SYSTEM after swapping binaries. It is deliberately
    local/non-secret: version, timestamps, agent/site ids and recorder identity only.
    """
    path = runtime_health_path()
    try:
        with _RUNTIME_HEALTH_LOCK:
            current = {}
            if path.exists():
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    current = {}
            current.update({
                "schema": "watchlog.runtime_health.v1",
                "agent_version": AGENT_VERSION,
                "updated_at": iso(now_utc()),
            })
            current.update({k: v for k, v in fields.items() if v is not None})
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(current, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
    except Exception:
        # Health proof is an installer aid; failure to write it must never kill monitoring.
        pass


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
    def __init__(self, config_path: Path | None = None, *, read_only_credentials: bool = False) -> None:
        ini = configparser.ConfigParser()
        ini_path = Path(config_path) if config_path else (base_dir() / "watchlog.ini")

        # Public build defaults are a read-only fallback. Existing site-specific values
        # in watchlog.ini always win. This lets a Repair/Upgrade deliver new public update
        # metadata without rewriting recorder/enrollment configuration.
        section: dict[str, str] = {}
        safe_public = {
            "supabase_url", "supabase_publishable_key", "push_bridge_url",
            "update_url", "update_public_key", "update_require_signature",
            "update_channel",
        }
        defaults_path = base_dir() / "watchlog.defaults.ini"
        if defaults_path.exists():
            try:
                defaults = configparser.ConfigParser()
                defaults.read(defaults_path, encoding="utf-8-sig")
                if defaults.has_section("watchlog"):
                    for key, value in defaults.items("watchlog"):
                        if key in safe_public and str(value or "").strip():
                            section[key] = value
            except configparser.Error:
                pass

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
            current = dict(ini.items("watchlog"))
            # Existing site-specific values always win. For PUBLIC build metadata,
            # however, an old explicitly-empty key must not suppress the newly
            # shipped signed-update defaults; empty means "not configured yet".
            for key, value in current.items():
                if key in safe_public and not str(value or "").strip() and section.get(key):
                    continue
                section[key] = value
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
        # Production: the recorder credential lives in the encrypted split store.
        # Staged repair validation MUST be read-only: it proves the existing DPAPI
        # blob decrypts without migrating/deleting/changing any site file.
        if read_only_credentials and os.name == "nt":
            try:
                cred = credential_store.load_nvr_credential_readonly()
            except credential_store.SecretError as exc:
                raise SystemExit(
                    "FATAL: the recorder credential could not be read by this candidate. "
                    f"The installed WatchLog has not been changed.\n  {exc}") from exc
            if cred:
                self.nvr_username = cred.get("username") or self.nvr_username
                self.nvr_password = cred.get("password") or ""
        else:
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
        # Restore one historical visual checkpoint per camera every N seconds
        # across a missed interval. 300s gives useful coverage without hammering
        # the recorder; recovery remains bounded by recovery_ai_max_frames/chunk.
        self.recovery_snapshot_seconds = int(get("recovery_snapshot_seconds") or 300)
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
        — never a plaintext fallback.

        On a site whose recorders.json configures recorders, every recorder logs in with
        its own credential and this legacy one is only the continuity recorder's mirror.
        Its failure then leaves this Config's login empty and names it in
        ``legacy_credential_error`` (open_driver refuses such a Config) instead of
        stopping every recorder."""
        if os.name != "nt":
            return  # dev/lean builds use the env/ini values already set
        try:
            cred = credential_store.load_nvr_credential(self._ini_path)
        except credential_store.SecretError as exc:
            if not _registry_configures_recorders():
                raise SystemExit(
                    "FATAL: the recorder credential could not be read (corrupt, or a "
                    f"blob copied from another machine). Repair WatchLog.\n  {exc}")
            self.nvr_password = ""
            self.legacy_credential_error = type(exc).__name__
            log("WARNING: the legacy recorder credential on this PC could not be read "
                f"({type(exc).__name__}); each configured recorder uses its own login, so "
                "only a recorder whose own login is unreadable stays unverified. "
                "Repair WatchLog to restore it.")
            return
        self.legacy_credential_error = None
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


def _fingerprint_serial(identity_fingerprint) -> str | None:
    text = str(identity_fingerprint or "").strip()
    if not text.lower().startswith("serial:"):
        return None
    return text[len("serial:"):].strip().upper() or None


def require_recorder_identity(cfg, info) -> None:
    """Refuse a device whose serial differs from the one saved for this recorder.

    The recorder's address is operational configuration, not its identity (contract
    sections 3, 8, 12): after an address swap another recorder that accepts the same login
    can answer there. Unknown stays unknown: with no saved or no reported serial nothing
    is refused."""
    expected = _fingerprint_serial(getattr(cfg, "recorder_identity_fingerprint", None))
    observed = str(getattr(info, "serial", "") or "").strip().upper() or None
    if expected and observed and expected != observed:
        raise RecorderIdentityMismatch(
            "the device at this recorder's address reports a different serial number than "
            "the saved recorder; it is not monitored until Setup confirms the recorder")


def open_driver(cfg: Config):
    driver, info = _open_driver_unverified(cfg)
    try:
        require_recorder_identity(cfg, info)
    except RecorderIdentityMismatch:
        try:
            driver.close()
        except Exception:                                       # noqa: BLE001
            pass
        raise
    return driver, info


def _open_driver_unverified(cfg: Config):
    cfg.require_nvr()
    if getattr(cfg, "legacy_credential_error", None) and not getattr(cfg, "recorder_local_id", None):
        # A registry site's legacy login could not be read (Config.load_recorder_credential):
        # signing in with an empty login would only be a false "wrong password".
        raise DriverError("the legacy recorder login on this PC cannot be read; "
                          "Repair WatchLog to restore it")
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


# ONVIF profile names of the form MediaProfile_Channel<N> are read as the recorder's own channel
# number for that video source (the label 0117 already uses for physical_channel). ASSUMPTION,
# IMPLEMENTED_UNVERIFIED: that ONVIF Channel<N> is the same input as native CGI/ISAPI channel N
# has not been checked on recorder hardware. The ONVIF camera channel itself is only the order in
# which GetProfiles listed the sources.
_ONVIF_CHANNEL_LABEL = r"mediaprofile[_ -]*channel(\d+)"

_ARCHIVE_CHANNEL_UNVERIFIED = ("WatchLog could not confirm which recorder input this camera uses, "
                               "so recorded footage was not retrieved.")


def _onvif_channel_labels(onvif) -> dict:
    """{ONVIF camera channel: recorder channel labels on its profiles}. Read-only.

    The camera channels and their SourceTokens come from the driver's own list_channels(), so
    they are exactly the ids live events and the cloud use. A profile with no label adds "".
    """
    import re
    channels = [str(c.channel) for c in onvif.list_channels()]
    source_to_channel = dict(getattr(onvif, "_source_to_channel", None) or {})
    media = getattr(onvif, "media_service", None)
    if not media or not source_to_channel:
        return {}
    labels = {channel: set() for channel in channels}
    root = onvif._call(media, "<trt:GetProfiles/>")
    for prof in root.findall(".//Profiles"):
        channel = source_to_channel.get(
            (prof.findtext(".//VideoSourceConfiguration/SourceToken") or "").strip())
        if channel not in labels:
            continue
        match = re.search(_ONVIF_CHANNEL_LABEL, prof.findtext("Name") or "", re.I)
        labels[channel].add(str(int(match.group(1))) if match else "")
    return labels


def _consistent_native_channel_map(onvif, native) -> dict:
    """ONVIF camera channel -> native recorder channel, only where the profile labels agree.

    A camera is mapped only when every profile of its video source carries the same
    MediaProfile_Channel<N> label, no other camera carries N on any of its profiles, and the
    native transport lists channel N. A recorder that labels a channel 0 does not number channels
    the way the native side does, so nothing is mapped. Everything else stays unmapped: refused,
    never guessed. The map is label-consistent, not hardware-verified: reading Channel<N> as
    native channel N is an assumption, IMPLEMENTED_UNVERIFIED (see _ONVIF_CHANNEL_LABEL).
    """
    labels = _onvif_channel_labels(onvif)
    if any("0" in found for found in labels.values()):
        return {}
    single = {channel: next(iter(found)) for channel, found in labels.items()
              if len(found) == 1 and "" not in found}
    # Count every label of every camera, so a camera with mixed or conflicting labels still
    # makes its N ambiguous for the others.
    claimed = [label for found in labels.values() for label in found if label]
    native_ids = {}
    for row in native.list_channels():
        raw = str(getattr(row, "channel", "") or "")
        if raw.isdigit():
            native_ids[str(int(raw))] = raw
    return {channel: native_ids[label] for channel, label in single.items()
            if claimed.count(label) == 1 and label in native_ids}


class _MappedArchiveDriver:
    """Vendor-native archive reader addressed by the ONVIF camera channels WatchLog uses.

    Exposes only the read-only archive interface. Every call translates the camera channel
    through the label-consistent ONVIF-to-native map; a camera without one is refused, never
    guessed.
    """

    def __init__(self, native, channel_map: dict):
        self._native = native
        self.name = native.name
        self.channel_map = dict(channel_map)

    def native_channel(self, channel) -> str:
        native = self.channel_map.get(str(channel))
        if native is None:
            raise DriverError(_ARCHIVE_CHANNEL_UNVERIFIED)
        return native

    def historical_capability(self) -> dict:
        return self._native.historical_capability()

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit: int = 500) -> dict:
        native = self.channel_map.get(str(channel))
        if native is None:
            return {"status": "unknown", "events": [], "next_cursor": None}
        page = self._native.enumerate_historical_events(native, start, end,
                                                        cursor=cursor, limit=limit) or {}
        events = [dict(ev, channel=str(channel)) if isinstance(ev, dict) and "channel" in ev else ev
                  for ev in (page.get("events") or [])]
        return {**page, "events": events}

    def get_clip(self, channel, start, end, **kw):
        # kw carries dahua-cgi's clock argument through (incident_evidence._get_clip).
        return self._native.get_clip(self.native_channel(channel), start, end, **kw)

    def get_recorded_segment(self, channel, start, end) -> dict:
        native = self.channel_map.get(str(channel))
        if native is None:
            return {"status": "unknown", "bytes": None}
        return self._native.get_recorded_segment(native, start, end)

    def close(self) -> None:
        self._native.close()


# The archive is opened every recovery cycle and for every footage request or archive-scan
# camera. A vendor-native CGI/ISAPI that rejects the on-site credential must not be probed again
# on each open (the drivers retry a 401 with Basic: two failed logins per probe). Confirmed auth
# failures back off per recorder like the live collector (5 -> 15 -> 30 min); a credential change
# in Setup clears the breaker at once. The recovery thread's primary logins use the same breaker
# under their own key (_recovery_login). It is also kept beside the Agent state
# (_auth_breaker_path): --status-json, --accept and --recheck-archive-json are fresh processes
# and would otherwise probe the vendor-native login again on every run.
_NATIVE_ARCHIVE_AUTH: dict = {}
_NATIVE_ARCHIVE_AUTH_LOCK = threading.Lock()


def _native_archive_key(cfg: Config, native_name: str) -> tuple:
    return (native_name, str(cfg.nvr_url or ""), str(cfg.nvr_username or ""))


def _continuity_recorder_at(cfg) -> str | None:
    """The registry's continuity recorder when ``cfg`` is main()'s base Config pointed at its
    address, else None. That Config logs in with the legacy singleton credential, which on a
    registry site is the continuity recorder's own login mirrored (replace_recorder_credential
    with mirror_legacy). Another recorder's login is never assumed; no or an unreadable
    registry keeps the 5.0.x singleton."""
    try:
        import recorder_registry
        url = str(getattr(cfg, "nvr_url", "") or "").rstrip("/")
        rows = [row for row in recorder_registry.recorders()
                if row.get("continuity_owner") and row.get("is_configured")
                and str(row.get("url") or "").strip().rstrip("/") == url]
    except Exception:  # noqa: BLE001 — unknown registry: the legacy token, as before
        return None
    return rows[0]["local_id"] if url and len(rows) == 1 else None


def _registry_configures_recorders() -> bool:
    """True when recorders.json configures at least one recorder; an absent or unreadable
    registry is not a configured one (an unreadable registry holds in enhanced_cmd_run)."""
    try:
        import recorder_registry
        return any(row.get("is_configured") for row in recorder_registry.recorders())
    except Exception:  # noqa: BLE001
        return False


def _boot_probe_config(cfg):
    """The Config main() identifies the recorder with before the run loop, or None to skip.

    With no configured registry: ``cfg`` itself, the 5.0.x singleton probe and camera sync.
    With one configured recorder: that recorder's own registry Config (its address, its own
    login and its saved identity), never the legacy ini recorder and login. With several, or
    a registry that cannot be read: None. Each recorder is then identified and synced only
    by its own recorder check (multi_recorder_orchestrator), so the legacy probe neither
    contacts a recorder without an identity check nor sends a site-level camera sync."""
    try:
        import recorder_registry
        rows = [row for row in recorder_registry.recorders() if row.get("is_configured")]
    except Exception:  # noqa: BLE001 — the runtime holds for Setup; nothing to probe here
        return None
    if not rows:
        return cfg
    if len(rows) > 1:
        return None
    try:
        (ctx,) = recorder_runtime.load_contexts(cfg, degrade_credential_errors=True)
    except Exception:  # noqa: BLE001 — the recorder check reports the registry fault
        return None
    return None if ctx.credential_error else ctx.config


def _credential_generation(cfg):
    """The change token of the credential this recorder logs in with: its own DPAPI blob for a
    registry recorder, the legacy singleton credential otherwise. The base Config of
    --status-json/--accept/--recheck-archive-json pointed at the continuity recorder resolves
    to that recorder's own token, the one its running Agent persisted (RV-AF2-01): a refusal
    one of them saw then holds back the other instead of being deleted as stale."""
    try:
        if not getattr(cfg, "recorder_local_id", None):
            local_id = _continuity_recorder_at(cfg)
            if local_id:
                return credential_store.recorder_credential_generation(local_id)
        return _credential_generation_for_cfg(cfg)
    except Exception:  # noqa: BLE001 — an unreadable token only means "no change seen"
        return None


def _auth_breaker_path(cfg) -> Path | None:
    """Where the auth breaker is kept beside the Agent state, so that short-lived processes
    (--status-json from the Site Status panel, --accept, --recheck-archive-json) and the running
    Agent all respect a refusal any of them saw. None without an Agent state path."""
    state_path = getattr(cfg, "state_path", None)
    return Path(state_path).parent / "recorder_auth_backoff.json" if state_path else None


def _auth_breaker_id(key: tuple) -> str:
    return hashlib.sha256("|".join(str(k) for k in key).encode("utf-8")).hexdigest()[:24]


def _read_auth_breaker(path: Path) -> dict:
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("entries")
    except Exception:  # noqa: BLE001 — absent or unreadable: nothing persisted
        return {}
    return entries if isinstance(entries, dict) else {}


def _write_auth_breaker(path: Path, entries: dict) -> None:
    try:
        if not path.parent.is_dir():
            return
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"entries": entries}), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — the in-memory breaker still holds for this process
        pass


def _native_archive_backoff(key: tuple, cfg, path: Path | None = None) -> float:
    """Seconds before a refused native archive login may be tried again (0 = probe now).
    With ``path`` a refusal persisted by another process counts too (wall clock, capped at the
    longest back-off so a clock step cannot hold the recorder back for longer)."""
    with _NATIVE_ARCHIVE_AUTH_LOCK:
        wait = 0.0
        entry = _NATIVE_ARCHIVE_AUTH.get(key)
        if entry is not None:
            if entry["generation"] != _credential_generation(cfg):
                _NATIVE_ARCHIVE_AUTH.pop(key, None)
            else:
                wait = entry["retry_at"] - time.monotonic()
        if path is not None:
            entries = _read_auth_breaker(path)
            saved = entries.get(_auth_breaker_id(key))
            if isinstance(saved, dict):
                if saved.get("generation") != _credential_generation(cfg):
                    entries.pop(_auth_breaker_id(key), None)
                    _write_auth_breaker(path, entries)
                else:
                    remaining = float(saved.get("retry_at") or 0) - time.time()
                    wait = max(wait, min(remaining, float(max(_AUTH_BACKOFF_SECONDS))))
        return max(0.0, wait)


def _note_native_archive_probe(key: tuple, error: Exception | None, cfg,
                               path: Path | None = None) -> None:
    """Record a native probe outcome: success clears the breaker, a rejected login escalates it.
    With ``path`` the outcome is also persisted for the Agent's other processes."""
    from drivers.base import NvrAuthFailed
    with _NATIVE_ARCHIVE_AUTH_LOCK:
        entries = _read_auth_breaker(path) if path is not None else {}
        ident = _auth_breaker_id(key)
        if error is None:
            _NATIVE_ARCHIVE_AUTH.pop(key, None)
            if entries.pop(ident, None) is not None:
                _write_auth_breaker(path, entries)
            return
        if not (isinstance(error, NvrAuthFailed) or _is_auth_failure(error)):
            return
        generation = _credential_generation(cfg)
        prior = 0
        entry = _NATIVE_ARCHIVE_AUTH.get(key)
        if entry and entry["generation"] == generation:
            prior = entry["failures"]
        saved = entries.get(ident)
        if isinstance(saved, dict) and saved.get("generation") == generation:
            prior = max(prior, int(saved.get("failures") or 0))
        failures = prior + 1
        wait = _AUTH_BACKOFF_SECONDS[min(failures - 1, len(_AUTH_BACKOFF_SECONDS) - 1)]
        _NATIVE_ARCHIVE_AUTH[key] = {"failures": failures, "generation": generation,
                                     "retry_at": time.monotonic() + wait}
        if path is not None:
            entries[ident] = {"failures": failures, "generation": generation,
                              "retry_at": time.time() + wait}
            _write_auth_breaker(path, entries)


def open_archive_driver(cfg: Config, *, live=None):
    """Open the best read-only recorder transport for archive/evidence work.

    Live monitoring may legitimately use ONVIF when that was the proven enrollment
    path. Recorded-media APIs are vendor-specific, however. When an ONVIF probe
    identifies the recorder vendor, make one bounded attempt to open the matching
    native HTTP driver with the SAME on-site credential/address. The ONVIF camera
    channel is only an enumeration ordinal, so the native reader is returned only
    for cameras with a label-consistent ONVIF-to-native channel map (the label-to-
    native equivalence is IMPLEMENTED_UNVERIFIED on hardware). Failure, or no
    mapped camera, falls back to the already-open ONVIF driver and therefore
    remains honestly unsupported.

    ``live`` is an already-open live ``(driver, info)`` (acceptance, status) used instead of a
    second recorder login. It is returned as is when it is the archive transport and is never
    closed here: the caller still owns it.
    """
    driver, info = live if live is not None else open_driver(cfg)
    try:
        import dahua_archive
        import hikvision_archive
        dahua_archive.install()
        hikvision_archive.install()
    except Exception:  # noqa: BLE001 — capability remains honest if optional wiring fails
        pass

    if getattr(driver, "name", "") != "onvif":
        return driver, info

    vendor = str(getattr(info, "vendor", "") or "").strip().lower()
    native_name = None
    if "dahua" in vendor:
        native_name = "dahua-cgi"
    elif "hikvision" in vendor:
        native_name = "hikvision-isapi"
    if not native_name:
        return driver, info

    key = _native_archive_key(cfg, native_name)
    breaker = _auth_breaker_path(cfg)
    wait = _native_archive_backoff(key, cfg, breaker)
    if wait:
        log(f"archive: vendor-native {native_name} rejected the recorder login; not retrying "
            f"for {max(1, round(wait / 60))} min (keeping {driver.name})")
        return driver, info

    candidate = None
    try:
        candidate = build(native_name, cfg.nvr_url, cfg.nvr_username, cfg.nvr_password)
        native_info = candidate.probe()
        _note_native_archive_probe(key, None, cfg, breaker)
        channel_map = _consistent_native_channel_map(driver, candidate)
    except Exception as error:  # noqa: BLE001 — live ONVIF path stays untouched
        _note_native_archive_probe(key, error, cfg, breaker)
        if candidate is not None:
            try:
                candidate.close()
            except Exception:
                pass
        log(f"archive: vendor-native {native_name} unavailable; "
            f"keeping {driver.name} ({type(error).__name__})")
        return driver, info

    if not channel_map:
        try:
            candidate.close()
        except Exception:
            pass
        log(f"archive: no camera has a consistent ONVIF-to-{native_name} channel label; "
            f"keeping {driver.name} (recorded media is not guessed)")
        return driver, info
    if live is None:
        driver.close()
    log(f"archive: using vendor-native {native_name} transport for recorded media "
        f"({len(channel_map)} camera channel(s) mapped by profile label)")
    return _MappedArchiveDriver(candidate, channel_map), native_info


def _archive_transport(driver) -> dict | None:
    """Which transport recorded media is read through, as reported by accept/status/recheck.
    channel_map is the label-consistent ONVIF-to-native map (IMPLEMENTED_UNVERIFIED on hardware,
    see _ONVIF_CHANNEL_LABEL), or None when channels are the driver's own."""
    if driver is None:
        return None
    mapped = isinstance(driver, _MappedArchiveDriver)
    return {"driver": getattr(driver, "name", None) or None,
            "channel_map": dict(driver.channel_map) if mapped else None}


def prove_recorder_archive(driver, channel) -> dict:
    """Run the matching vendor archive proof without guessing capabilities."""
    name = str(getattr(driver, "name", "") or "")
    mapped = driver if isinstance(driver, _MappedArchiveDriver) else None
    if mapped is not None and str(channel) not in mapped.channel_map:
        return {"status": "unknown", "channel": str(channel), "detail": _ARCHIVE_CHANNEL_UNVERIFIED}
    try:
        if name == "dahua-cgi":
            import dahua_archive
            dahua_archive.install()
            return dahua_archive.prove_recorder_archive(driver, channel)
        if name == "hikvision-isapi":
            import hikvision_archive
            hikvision_archive.install()
            if mapped is not None:
                # The Hikvision proof calls the ISAPI module directly, so give it the native
                # driver and channel and keep the camera channel in the result.
                proof = hikvision_archive.prove_recorder_archive(mapped._native,
                                                                 mapped.native_channel(channel))
                return {**proof, "channel": str(channel)}
            return hikvision_archive.prove_recorder_archive(driver, channel)
    except Exception as error:  # noqa: BLE001
        return {"status": "unknown", "detail": f"archive proof failed: {type(error).__name__}"}
    return {
        "status": "unsupported",
        "detail": "Recorded-media access is not validated through this recorder transport.",
    }


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


def _credential_generation_for_cfg(cfg) -> str:
    local_id = getattr(cfg, "recorder_local_id", None)
    if local_id:
        return credential_store.recorder_credential_generation(local_id)
    return credential_store.credential_generation()


def _reload_credential_for_cfg(cfg) -> None:
    local_id = getattr(cfg, "recorder_local_id", None)
    if local_id:
        cred = credential_store.load_recorder_credential(local_id)
        cfg.nvr_username = cred.get("username") or ""
        cfg.nvr_password = cred.get("password") or ""
        return
    cfg.load_recorder_credential()


def _reload_credential_if_changed(cfg) -> bool:
    """Reload this recorder's login if Setup rewrote it since this config last loaded it.

    The health and recovery workers call this every cycle, so a repaired password reaches
    them without an Agent restart (MNVR-012). Only a changed credential file is decrypted
    again; an unreadable new one keeps the current login and is retried next cycle."""
    try:
        generation = _credential_generation_for_cfg(cfg)
    except Exception:                                   # noqa: BLE001
        return False
    seen = getattr(cfg, "credential_generation_seen", None)
    if seen is None:
        cfg.credential_generation_seen = generation     # baseline: the login loaded at start
        return False
    if generation == seen:
        return False
    try:
        _reload_credential_for_cfg(cfg)
    except (Exception, SystemExit) as e:                # noqa: BLE001 — keep the current login
        # (a legacy config's load_recorder_credential exits on an unreadable store)
        log(f"recorder credential changed in Setup but could not be read yet: {type(e).__name__}")
        return False
    cfg.credential_generation_seen = generation
    log("recorder credential changed in Setup; health and recovery now use it")
    return True


def _reconnect_wait(stop: threading.Event, cfg: "Config", auth_failures: int,
                    last_gen: str, seconds: float | None = None) -> tuple[str, str]:
    """Interruptible backoff between driver reconnects. Returns (outcome, gen).

    Confirmed auth failures escalate 5->15->30 min so a wrong password never
    hammers the recorder (lockout risk); everything else uses the short
    DRIVER_RETRY_SECONDS, or ``seconds`` when given (the packaged collector's
    jittered delay before reopening a dropped event stream on the same driver).
    A credential change (Setup rewriting the DPAPI blob) wakes the wait
    immediately, reloads the credential and lets the caller retry now — never
    wait out 30 minutes after the operator fixes the password."""
    if auth_failures > 0:
        total = float(_AUTH_BACKOFF_SECONDS[min(auth_failures - 1, len(_AUTH_BACKOFF_SECONDS) - 1)])
        log(f"recorder authentication is failing; backing off {int(total) // 60} min "
            f"(will retry immediately if the credential is updated in Setup)")
    else:
        total = float(DRIVER_RETRY_SECONDS if seconds is None else max(0.0, seconds))
    waited, step = 0.0, 5.0
    unreadable_gen = None
    while waited < total:
        if stop.wait(min(step, total - waited)):
            return "stop", last_gen
        waited += step
        try:
            gen = _credential_generation_for_cfg(cfg)
        except Exception:                               # noqa: BLE001 — unknown: poll again
            continue
        if gen != last_gen:
            try:
                _reload_credential_for_cfg(cfg)
            except (Exception, SystemExit) as e:        # noqa: BLE001 — keep the current login
                # A missing, mid-replace or unreadable blob must not end the collector thread
                # (nothing restarts it). last_gen is kept, so the next poll tries again.
                if gen != unreadable_gen:
                    log("recorder credential changed in Setup but could not be read yet: "
                        f"{type(e).__name__}; keeping the current login")
                    unreadable_gen = gen
                continue
            log("recorder credential changed in Setup; reloading and retrying now")
            return "reload", gen
    return "timeout", last_gen


def collector(cfg: Config, spool, stop: threading.Event, holder: dict = None) -> None:
    """Driver thread. Never dies: on error it backs off and re-opens."""
    # Built once, outside the reconnect loop: loading the weights costs
    # seconds, and a flapping NVR must not re-pay that on every retry.
    detector = vision.build(cfg, log)
    auth_failures = 0
    last_gen = _credential_generation_for_cfg(cfg)
    while not stop.is_set():
        driver = None
        auth_error = False
        try:
            driver, info = open_driver(cfg)
            if holder is not None:
                holder["live_driver"] = driver
                holder["recorder_live_at"] = time.monotonic()
                holder["recorder_live_wall"] = now_utc()
                holder["recorder_vendor"] = info.vendor
                holder["recorder_model"] = info.model
            log(f"driver {driver.name}: {info.vendor} {info.model or ''} "
                f"fw={info.firmware or '?'}".rstrip())
            if not driver.verified_against_hardware:
                log(f"NOTE: driver '{driver.name}' has not been verified against "
                    f"real hardware. Treat its output as unproven.")

            last_shot: dict[str, float] = {}

            for ev in driver.stream_events(stop):
                if stop.is_set():
                    break
                recorder_id = getattr(cfg, "recorder_cloud_id", None)
                if recorder_id:
                    ev = ev.with_recorder_id(recorder_id)
                if holder is not None:
                    holder["recorder_live_at"] = time.monotonic()
                    holder["recorder_live_wall"] = now_utc()

                # The image is best-effort and strictly secondary. A
                # camera that hangs, refuses auth or returns junk must
                # cost us the picture, never the incident record.
                raw = None
                # A recorder-scoped or channel-less event (channel None) has no camera
                # to take a still from.
                if (cfg.snapshots and ev.channel is not None
                        and ev.event_type not in NO_SNAPSHOT_EVENTS):
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
                if (holder is not None and ev.channel is not None
                        and ev.event_type in NATIVE_FAULT_TYPES):
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


def _event_stream_health(stream: dict | None) -> dict | None:
    """The recorder's event-stream state for the local health proof:
    {connected, connected_at, last_frame_at, last_error}. connected is None when the
    driver cannot report its stream. The error text is redacted (no URL, credential or
    recorder address); this file stays non-secret. An ONVIF stream adds dropped_unmapped
    (events whose camera token matched no camera) and last_clock_skew_s (recorder clock
    minus this PC's, in seconds) when it has measured them: integers only."""
    if not stream:
        return None
    import nvr_health
    error = stream.get("last_error")
    out = {"connected": stream.get("connected"),
           "connected_at": stream.get("connected_at"),
           "last_frame_at": stream.get("last_frame_at"),
           "last_error": nvr_health.redact(error) if error else None}
    for key in ("dropped_unmapped", "last_clock_skew_s"):
        value = stream.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    return out


def heartbeat(cloud: Cloud, state: dict, device, *, recorder_live: bool | None = None,
              event_stream: dict | None = None) -> None:
    cloud.call("wl_heartbeat", p_agent_id=state["agent_id"],
               p_agent_key=state["agent_key"], p_agent_version=AGENT_VERSION,
               p_device_vendor=device.vendor if device else None,
               p_device_model=device.model if device else None,
               p_device_driver=device.driver if device else None)
    stamp = iso(now_utc())
    # The device object is startup identity and may remain populated long after a
    # recorder disconnects. Repair/Upgrade health proof must advance recorder_seen_at
    # only from CURRENT recorder transport activity.
    recorder_is_live = bool(device) if recorder_live is None else bool(recorder_live)
    update_runtime_health(
        heartbeat_at=stamp,
        agent_id=state.get("agent_id"),
        site_id=state.get("site_id"),
        tenant_id=state.get("tenant_id"),
        recorder_seen_at=(stamp if recorder_is_live else None),
        recorder_vendor=(device.vendor if device else None),
        recorder_model=(device.model if device else None),
        recorder_driver=(device.driver if device else None),
        event_stream=_event_stream_health(event_stream),
    )
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
        recorder_id = holder.get("recorder_cloud_id") or getattr(cfg, "recorder_cloud_id", None)
        for c in cam.get("cameras", []):
            ch = c.get("channel")
            if ch is None:
                continue
            store.observe("camera", str(ch), c.get("health", "unknown"),
                          c.get("reason", "unknown"), c.get("source", "probe"), device_ts,
                          recorder_id=recorder_id)
        # checkpoint: proof local monitoring continued this cycle (no raw probe/image data)
        store.checkpoint(device_ts,
                         nvr_state=(assessment.get("nvr") or {}).get("state", "unknown"),
                         cameras_observed=len(cam.get("cameras", [])), cycle_ok=True,
                         recorder_id=recorder_id)
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
        recorder_id = holder.get("recorder_cloud_id")
        for c in (rs.get("recording") or {}).get("channels", []):
            ch = c.get("channel")
            if ch is not None:
                store.observe("camera_recording", str(ch), c.get("state", "unknown"),
                              c.get("reason", "unknown"), "probe", device_ts,
                              recorder_id=recorder_id)
        st = rs.get("storage") or {}
        store.observe("nvr_storage", str(state["agent_id"]), st.get("state", "unknown"),
                      st.get("reason", "unknown"), "probe", device_ts,
                      recorder_id=recorder_id)
    except Exception as e:                              # noqa: BLE001
        log(f"recording/storage persist skipped: {type(e).__name__}")


# Server reconcile rejection categories (mirror wl_reconcile_recording_storage / reconcile_model).
# STRUCTURAL poison can never become valid on a resend, so it is quarantined AT ONCE (observable);
# anything else — notably unmapped_channel, where the camera may simply not be enrolled YET — is
# retried under a bounded policy (health_store.defer_transitions), then quarantined if it never maps.
_PERMANENT_REJECTIONS = frozenset({
    "missing_id", "wrong_layer", "missing_state",
    "invalid_timestamp", "invalid_sequence",
    "invalid_recorder_id", "missing_recorder",
})


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


def _retry_recorder_cloud_inventory(cloud: Cloud, state: dict, cfg: Config,
                                    holder: dict, assessment: dict, driver) -> None:
    """Bind camera inventory/capabilities after a recorder recovers post-preflight.

    Preflight may legitimately see an unreachable recorder. Multi-recorder runtime
    must not require an Agent restart when it later comes back. This helper retries
    only after authenticated channel enumeration succeeds and caches the channel
    signature so steady-state health cycles do not churn the DB.

    Failure is recorder-local and best-effort; callers still report recorder health.
    """
    recorder_id = str(getattr(cfg, "recorder_cloud_id", "") or "").strip()
    channels_block = assessment.get("channels") or {}
    if not recorder_id or driver is None or not channels_block.get("enumerated"):
        return

    reported = [
        row for row in (channels_block.get("reported") or [])
        if isinstance(row, dict) and row.get("channel") is not None
    ]
    channels = sorted({str(row["channel"]) for row in reported})
    if not channels:
        return

    signature = tuple(channels)
    mapping = holder.get("camera_mapping")
    if holder.get("camera_sync_signature") == signature and isinstance(mapping, dict) and mapping:
        return

    payload = [
        {
            "channel": ch,
            "name": next(
                (
                    str(row.get("name"))
                    for row in reported
                    if str(row.get("channel")) == ch and row.get("name")
                ),
                f"Camera {ch}",
            ),
        }
        for ch in channels
    ]
    mapping = cloud.call(
        "wl_sync_recorder_cameras",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_recorder_id=recorder_id,
        p_cameras=payload,
    )
    if not isinstance(mapping, dict):
        raise RuntimeError("recorder camera retry returned no mapping")
    if not set(channels).issubset({str(k) for k in mapping}):
        raise RuntimeError("recorder camera retry mapping is incomplete")
    # Pin what WatchLog accepted, as main() does (_synced_inventory): this recorder's ONVIF
    # cameras then keep their synced channels while the Agent runs.
    pin = getattr(driver, "pin_inventory", None)
    if callable(pin):
        pin()

    holder["camera_mapping"] = {str(k): str(v) for k, v in mapping.items()}
    holder["camera_sync_signature"] = signature
    holder["synced_channels"] = [
        {
            "channel": ch,
            "camera_id": str(mapping[ch]),
        }
        for ch in channels
        if ch in mapping
    ]

    try:
        capabilities = driver.capabilities()
    except Exception:
        capabilities = None
    if isinstance(capabilities, dict):
        cloud.call(
            "wl_sync_recorder_capabilities",
            p_agent_id=state["agent_id"],
            p_agent_key=state["agent_key"],
            p_recorder_id=recorder_id,
            p_capabilities=capabilities,
        )


# Field Build 69 (Hikvision DS-7608NI-Q1): while the live collector's stream is proving the
# recorder, the health cycle must not open a competing session merely to prove it again. It
# reports connectivity from the live stream, reuses the last REAL enumeration (never a
# synthetic channel list, which the server would turn into "missing" cameras), judges cameras
# by their fresh stills from that stream, and leaves recording/storage unobserved. A real,
# serialized assessment (including recording/storage) still runs at least this often.
LIVE_STREAM_HEALTH_MAX_AGE_SECONDS = 900.0
# A camera whose last still from the live stream is older than this is not proven healthy.
LIVE_STREAM_STILL_FRESH_SECONDS = 900.0
# The live stream proves the recorder only while it showed activity this recently.
LIVE_STREAM_FRESH_SECONDS = 150.0


class _LiveStreamProven(Exception):
    """Control flow inside health_cycle: this cycle is proven by the live stream."""


def _live_stream_health(cfg: Config, holder: dict) -> dict | None:
    """The health assessment proven by the live stream, or None when a real one is needed."""
    live = holder.get("live_driver")
    if live is None or not getattr(live, "samples_in_stream", False):
        return None
    try:
        import periodic_stills
        if not periodic_stills.load_settings(cfg)["enabled"]:
            return None      # no stills from the stream: cameras need a real probe
    except Exception:                                   # noqa: BLE001
        return None
    stream = getattr(live, "event_stream", None) or {}
    activity = float(getattr(live, "last_activity_monotonic", 0.0) or 0.0)
    now = time.monotonic()
    if stream.get("connected") is not True or not activity or now - activity >= LIVE_STREAM_FRESH_SECONDS:
        return None
    real = holder.get("real_assessment") or {}
    if not real or now - float(real.get("at") or 0.0) >= LIVE_STREAM_HEALTH_MAX_AGE_SECONDS:
        return None
    channels = real.get("channels") or {}
    if not channels.get("enumerated"):
        return None
    nvr = {k: v for k, v in (real.get("nvr") or {}).items()
           if k in ("vendor", "model", "firmware", "channel_count")}
    nvr.update(reachable=True, auth_ok=True, state="ok", reason="ok")
    return {"nvr": nvr,
            "channels": {"enumerated": True,
                         "reported": [dict(row) for row in channels.get("reported") or []],
                         # Present-tense faults need a recorder read; not re-observed here.
                         "current_faults": {"supported": False}}}


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
    try:
        _reload_credential_if_changed(cfg)
        from_stream = _live_stream_health(cfg, holder)
        try:
            if from_stream is not None:
                assessment = from_stream
                raise _LiveStreamProven()
            if cfg.nvr_driver in ("auto", ""):
                driver, _ = autodetect(cfg.nvr_url, cfg.nvr_username,
                                       cfg.nvr_password, log=lambda *a, **k: None)
            else:
                driver = build(cfg.nvr_driver, cfg.nvr_url, cfg.nvr_username, cfg.nvr_password)
            refused = []

            def _verify(info):
                try:
                    require_recorder_identity(cfg, info)
                except RecorderIdentityMismatch:
                    refused.append(True)
                    raise
            assessment = nvr_health.assess_nvr_health(driver, verify=_verify)
            if refused:
                # Another recorder answered at this address: none of its cameras, recording
                # state or channels are this recorder's. Its cameras stay UNKNOWN.
                log("recorder identity mismatch: the device at this recorder's address is a "
                    "different recorder; not monitoring it until Setup confirms the recorder")
                try:
                    driver.close()
                except Exception:                      # noqa: BLE001
                    pass
                driver = None
        except _LiveStreamProven:
            pass
        except DriverError as e:
            assessment = nvr_health.assess_from_error(e)   # still report the classified state
            driver = None
        if driver is not None and (assessment.get("channels") or {}).get("enumerated"):
            holder["real_assessment"] = {
                "at": time.monotonic(),
                "nvr": dict(assessment.get("nvr") or {}),
                "channels": {"enumerated": True,
                             "reported": [dict(r) for r in assessment["channels"].get("reported") or []]},
            }

        # --- NVR connectivity/auth + inventory (increment 3) ---
        try:
            recorder_id = getattr(cfg, "recorder_cloud_id", None)
            health_rpc = "wl_report_recorder_health" if recorder_id else "wl_report_health"
            health_args = dict(
                p_agent_id=state["agent_id"],
                p_agent_key=state["agent_key"],
                p_report=assessment,
            )
            if recorder_id:
                health_args["p_recorder_id"] = recorder_id
            res = cloud.call(health_rpc, **health_args)
            log(f"health reported: nvr={assessment['nvr'].get('state')} "
                f"present={res.get('present')} missing={res.get('missing')} "
                f"disabled={res.get('disabled')} unknown={res.get('unknown')}")
        except Exception as e:                          # noqa: BLE001
            log(f"health report skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")

        # --- camera hybrid health (increment 4), reusing THIS assessment + driver ---
        chans = [str(c["channel"]) for c in assessment.get("channels", {}).get("reported", [])
                 if c.get("channel")]

        # A recorder may have been unreachable during startup preflight. Once
        # authenticated enumeration succeeds, bind its cameras/capabilities here
        # so live events, health and recovery no longer require an Agent restart.
        if recorder_id:
            try:
                _retry_recorder_cloud_inventory(
                    cloud, state, cfg, holder, assessment, driver
                )
            except Exception as e:                      # noqa: BLE001
                log(
                    "recorder inventory sync deferred: "
                    f"{type(e).__name__}: {nvr_health.redact(str(e))}"
                )

        mon = holder.get("monitor")
        if mon is None and chans:
            mon = camera_health.CameraHealthMonitor(
                chans, batch_size=cfg.health_batch, concurrency=cfg.health_concurrency)
            holder["monitor"] = mon
        if mon is not None:
            if driver is not None:
                probe = camera_health.make_probe_fn(driver)
            elif from_stream is not None:
                # Judged by the still each camera last gave on the live stream: no session.
                def probe(channel):
                    seen = float((holder.get("snapshot_ok") or {}).get(str(channel)) or 0.0)
                    fresh = seen > 0.0 and time.monotonic() - seen < LIVE_STREAM_STILL_FRESH_SECONDS
                    return camera_health.ProbeResult(ok=fresh, upper=None,
                                                     reason="ok" if fresh else "probe_timeout")
            else:
                probe = lambda _c: camera_health.ProbeResult(ok=False, upper="nvr_unreachable")
            cam = mon.run_cycle(lambda: assessment, probe)   # one assessment, bounded probing
            # increment 5: persist transitions + checkpoint LOCALLY first — survives an outage.
            persist_health(holder, state, cfg, cam, assessment)
            try:
                camera_health_rpc = (
                    "wl_report_recorder_camera_health"
                    if recorder_id else "wl_report_camera_health"
                )
                camera_health_args = dict(
                    p_agent_id=state["agent_id"],
                    p_agent_key=state["agent_key"],
                    p_report=cam,
                )
                if recorder_id:
                    camera_health_args["p_recorder_id"] = recorder_id
                cr = cloud.call(camera_health_rpc, **camera_health_args)
                log(f"camera health: op={cr.get('operational')} deg={cr.get('degraded')} "
                    f"off={cr.get('offline')} unk={cr.get('unknown')}")
            except Exception as e:                      # noqa: BLE001
                log(f"camera health report skipped: {type(e).__name__}: {nvr_health.redact(str(e))}")

        # --- NVR recording + storage health (increment 6), reusing THIS driver + assessment ---
        # No live current-state RPC: EVERY recording/storage change flows through the local store and
        # is applied by wl_reconcile_recording_storage, which solely owns the ledger AND the durable
        # ordering watermark. Nothing here can bypass that watermark and regress current state.
        try:
            if from_stream is not None:
                # Not observed this cycle: liveness never proves recording or storage, and
                # writing UNKNOWN here would flap a state the last real read established.
                raise _LiveStreamProven()
            import recording_health
            nvr_state = (assessment.get("nvr") or {}).get("state", "unknown")
            # inventory (present/disabled) from the reported channels — a disabled channel has no
            # meaningful recording state (UNKNOWN, never NOT_RECORDING).
            inv = {str(c.get("channel")): ("disabled" if not c.get("enabled", True) else "present")
                   for c in assessment.get("channels", {}).get("reported", []) if c.get("channel")}
            rs = recording_health.assess_recording_storage(driver, chans, nvr_state, inventory=inv)
            persist_recording_storage(holder, state, rs)     # record transitions on change (durable)
        except _LiveStreamProven:
            pass
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
    recorder-state reconciliation runs inside health_cycle). Like the recovery and Site Control
    threads it outlives any fault: the fan-out runs one per recorder, and nothing restarts a
    thread that has ended."""
    stop.wait(min(10, cfg.health_seconds))              # let enrollment/sync settle first
    while not stop.is_set():
        try:
            health_cycle(cloud, state, cfg, holder)
        except BaseException as e:                       # noqa: BLE001 — last resort; the thread must outlive any fault
            worker_fault("health", e)
        jitter = random.uniform(0, max(1.0, cfg.health_seconds * 0.2))
        if resume_evt is not None:
            if resume_evt.wait(cfg.health_seconds + jitter):
                resume_evt.clear()                      # woke early for resume reconciliation
        else:
            stop.wait(cfg.health_seconds + jitter)


def _site_control_driver(cfg: Config):
    """The recorder driver a Site Control command runs against. 'auto' (the Config default, and
    what older installers wrote) is not a registered driver name, so resolve it the way the health
    cycle does instead of letting build() raise KeyError after the command was claimed.

    The device must still be the recorder the command names: after an address swap another
    recorder that accepts the same login can answer there (contract section 13). Where a serial
    is saved for this recorder, the device's reported serial is checked before any command
    runs (a RecorderIdentityMismatch then fails the claimed command); with none saved nothing
    extra is probed and nothing is refused."""
    if cfg.nvr_driver in ("auto", ""):
        driver, info = autodetect(cfg.nvr_url, cfg.nvr_username, cfg.nvr_password,
                                  log=lambda *a, **k: None)
    else:
        driver = build(cfg.nvr_driver, cfg.nvr_url, cfg.nvr_username, cfg.nvr_password)
        info = None
    try:
        if _fingerprint_serial(getattr(cfg, "recorder_identity_fingerprint", None)):
            require_recorder_identity(cfg, info if info is not None else driver.probe())
    except BaseException:
        try:
            driver.close()
        except Exception:                                       # noqa: BLE001
            pass
        raise
    return driver


def _run_claimed_command(cfg: Config, state: dict, cloud: Cloud, cmd: dict, site_control) -> None:
    """Execute one claimed Site Control command and ALWAYS complete it.

    The command is already 'claimed' in the cloud. A recorder-routing, driver or
    executor failure therefore completes it as failed (same sanitised error shape as
    site_control: a redacted DriverError line, otherwise only the exception type)
    instead of escaping and leaving it claimed forever."""
    action = cmd.get("action")
    is_write = action in site_control.WRITE_ACTIONS
    try:
        job_cfg = recorder_runtime.config_for_cloud_recorder(
            cfg, cmd.get("recorder_id")
        )
        driver = _site_control_driver(job_cfg)
        try:
            res = (site_control.execute_write(driver, action, cmd.get("params"))
                   if is_write else
                   site_control.execute_read(driver, action, cmd.get("params")))
        finally:
            try:
                driver.close()
            except Exception:                    # noqa: BLE001
                pass
        status = "succeeded" if res.get("ok") else "failed"
        # Writes carry before/after/verified (transactional audit); reads carry 'data'.
        result, error = (res if is_write else res.get("data")), res.get("error")
    except Exception as e:                       # noqa: BLE001 — complete it, never strand it
        log(f"site control: command {str(cmd.get('id'))[:8]} failed: "
            f"{type(e).__name__}: {nvr_health.redact(str(e))}")
        status, result = "failed", None
        error = ((nvr_health.redact(str(e)) if isinstance(e, DriverError) else "")
                 or type(e).__name__)
    cloud.call("wl_agent_complete_command",
               p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
               p_command_id=cmd["id"], p_status=status, p_result=result, p_error=error)


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
            # Runtime capability truth: advertise Site Control only after this
            # worker has actually reached the claim RPC recently.
            cfg.site_control_last_poll_monotonic = time.monotonic()
            update_runtime_health(site_control_poll_at=iso(now_utc()))
            cmd = (claimed or {}).get("command")
            if cmd:
                busy = True
                _run_claimed_command(cfg, state, cloud, cmd, site_control)
        except BaseException as e:                       # noqa: BLE001 — Site Control never disturbs the agent
            worker_fault("site control", e)
        if not busy:
            stop.wait(cfg.site_control_seconds)          # idle poll; drain promptly when busy


# --- commands ----------------------------------------------------------

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
    if not (det.available and keep is False):
        print("RESULT: INCONCLUSIVE (detector loaded but junk frame not discarded)")
        return 2

    # Recovery is part of the production contract: prove the frozen executable
    # also contains a working FFmpeg capable of turning historical recorder
    # footage into a JPEG checkpoint. This catches "recovery code exists but
    # codec was not packaged" before an installer can ship.
    try:
        import recovery_ai
        dec = recovery_ai.decoder_selftest()
    except Exception as exc:  # noqa: BLE001
        dec = {"ok": False, "reason": type(exc).__name__}
    print(f"archive recovery decoder -> ok={dec.get('ok')} reason={dec.get('reason') or '-'}")
    if not dec.get("ok"):
        print("RESULT: FAIL (historical footage decoder NOT packaged/working)")
        return 2

    extra = "" if retained is None else f"; real-object retained={retained}"
    print(f"RESULT: PASS (ONNX runtime + model + archive FFmpeg decoder packaged; "
          f"junk frame discarded{extra})")
    return 0


def cmd_existing_site_preflight(cfg: Config, *, result_path: str | None = None,
                                mode: str = "full") -> int:
    """Read-only staged validation for an already-installed WatchLog site.

    This command is designed to run as SYSTEM from the Repair/Upgrade package
    before any installed payload is replaced. mode="passive" performs checks that
    cannot compete with the live recorder session while the current Agent is still
    running. mode="recorder" adds recorder identity/channel validation after the
    current Agent has been safely paused. It must never enroll, write recorder
    settings, mutate credentials, heartbeat a staged version, or consume queued work.
    """
    result = {
        "schema": "watchlog.existing_site_preflight.v1",
        "agent_version": AGENT_VERSION,
        "mode": mode,
        "ok": False,
        "checks": {},
    }
    driver = None

    def mark(name: str, ok: bool, detail: str = "") -> None:
        result["checks"][name] = {"ok": bool(ok), "detail": str(detail or "")[:300]}

    try:
        config_ok = bool(cfg.supabase_url and cfg.publishable_key and cfg.nvr_url)
        mark("config", config_ok, "existing site config + staged public defaults loaded")
        if not config_ok:
            raise RuntimeError("existing WatchLog configuration is incomplete")

        # Read-only DPAPI proof. Config(read_only_credentials=True) never migrates
        # or removes legacy files; a missing authoritative blob blocks this repair path.
        cred = credential_store.load_nvr_credential_readonly() if os.name == "nt" else {
            "username": cfg.nvr_username, "password": cfg.nvr_password,
        }
        credential_ok = bool(cred and cred.get("password"))
        mark("recorder_credential", credential_ok,
             "machine credential decrypts" if credential_ok else "authoritative DPAPI recorder credential missing")
        if not credential_ok:
            raise RuntimeError("existing recorder credential is not repair-upgrade ready")

        state = load_state(cfg.state_path)
        identity_ok = bool(state and state.get("agent_id") and state.get("agent_key")
                           and state.get("site_id") and state.get("tenant_id"))
        mark("identity", identity_ok, f"agent={state.get('agent_id') if state else '-'}")
        if not identity_ok:
            raise RuntimeError("existing WatchLog enrollment identity is incomplete")

        try:
            import recovery_ai
            decoder = recovery_ai.decoder_selftest()
        except Exception as exc:  # noqa: BLE001
            decoder = {"ok": False, "reason": type(exc).__name__}
        mark("archive_decoder", bool(decoder.get("ok")), decoder.get("reason") or "bundled FFmpeg OK")
        if not decoder.get("ok"):
            raise RuntimeError("historical footage decoder is not working")

        if mode not in ("passive", "recorder", "full"):
            raise RuntimeError(f"unknown preflight mode {mode!r}")

        if mode in ("recorder", "full"):
            cfg.require_nvr()
            driver, device = open_driver(cfg)
            chans = driver.list_channels()
            channel_count = len(chans or [])
            recorder_ok = bool(device and channel_count > 0)
            detail = f"{getattr(device, 'vendor', '')} {getattr(device, 'model', '')}; {channel_count} channel(s)".strip()
            mark("recorder", recorder_ok, detail)
            if not recorder_ok:
                raise RuntimeError("candidate could not identify the existing recorder/cameras")
        else:
            mark("recorder", True, "deferred until current Agent is safely paused")

        cfg.require_cloud()
        cloud = Cloud(cfg.supabase_url, cfg.publishable_key)
        auth = cloud.call("wl_agent_preflight_auth",
                          p_agent_id=state["agent_id"], p_agent_key=state["agent_key"]) or {}
        cloud_ok = (str(auth.get("agent_id") or "") == str(state["agent_id"])
                    and str(auth.get("site_id") or "") == str(state["site_id"]))
        mark("cloud_identity", cloud_ok,
             f"site={auth.get('site_id') or '-'} tenant={auth.get('tenant_id') or '-'}")
        if not cloud_ok:
            raise RuntimeError("cloud did not authenticate this existing site identity")

        update_ok = (str(cfg.update_url or "").lower().startswith("https://")
                     and bool(cfg.update_public_key)
                     and bool(cfg.update_require_signature))
        mark("signed_remote_update", update_ok,
             "HTTPS manifest + Ed25519 public key + signature-required"
             if update_ok else "signed remote-update public configuration is incomplete")
        if not update_ok:
            raise RuntimeError("candidate is not configured for signed online updates")

        result["ok"] = True
    except BaseException as exc:  # includes SystemExit from strict config/driver checks
        result["error"] = f"{type(exc).__name__}: {str(exc)}"[:500]
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    raw = json.dumps(result, separators=(",", ":"))
    print("PREFLIGHT_JSON " + raw, flush=True)
    if result_path:
        try:
            path = Path(result_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            print(f"preflight result write failed: {type(exc).__name__}", flush=True)
            return 2
    return 0 if result["ok"] else 2


# The database's own 42501 text for a site-level push token on a multi-recorder site
# (0146 wl_push_recorder_for_site). Shown verbatim when WatchLog offers no recorder-
# scoped push, so the local refusal and the database refusal read the same.
MULTI_RECORDER_PUSH_REFUSED = (
    "recorder push is not available for a site with more than one recorder")


def _recorder_push_absent(error: Exception) -> bool:
    """True only when the database definitively has no recorder-scoped push token RPC
    (wl_agent_issue_push_token(agent, key, recorder) does not exist there)."""
    return (isinstance(error, CloudError)
            and getattr(error, "fn", "") == "wl_agent_issue_push_token"
            and (getattr(error, "code", None) == "PGRST202"
                 or getattr(error, "status", None) == 404))


def _push_refusal(error: Exception) -> str | None:
    """The database's refusal text for a 42501 from the push token RPC, else None."""
    if isinstance(error, CloudError) and getattr(error, "code", None) == "42501":
        return str(getattr(error, "message", "") or "")[:200] or None
    return None


def _issue_push_token(cloud, state: dict, recorder_id: str | None) -> str | None:
    """One push token: recorder-scoped when ``recorder_id`` is given (the answer must
    name that recorder), otherwise the 5.0.x site-level form."""
    params = {"p_agent_id": state["agent_id"], "p_agent_key": state["agent_key"]}
    if recorder_id:
        params["p_recorder_id"] = recorder_id
    issued = cloud.call("wl_agent_issue_push_token", **params)
    if not isinstance(issued, dict):
        return None
    if recorder_id and str(issued.get("recorder_id") or "") != str(recorder_id):
        return None                     # never point a recorder at another one's token
    return issued.get("token") or None


def _configure_recorder_push(driver_cfg, open_fn, url: str) -> dict:
    """Point one recorder (opened from its own config) at its push URL."""
    driver = open_fn(driver_cfg)
    try:
        configure = getattr(driver, "configure_push", None)
        if configure is None:
            return {"configured": False, "verified": False,
                    "detail": "this recorder model does not support recorder-push"}
        out = configure(url) or {}
        return {"configured": bool(out.get("applied")),
                "verified": bool(out.get("verified")),
                "detail": str(out.get("detail") or "")}
    finally:
        try:
            close = getattr(driver, "close", None)
            if close is not None:
                close()
        except Exception:  # noqa: BLE001
            pass


def _recorder_push_token(cloud, state: dict, ctx, multi: bool) -> tuple:
    """(token or None, refusal detail) for one RecorderContext.

    Raises _NoRecorderScopedPush on a multi-recorder registry when the database has no
    recorder-scoped form; any other unexpected failure propagates."""
    def legacy():
        try:
            return _issue_push_token(cloud, state, None), ""
        except CloudError as exc:
            if _push_refusal(exc) is None:
                raise
            return None, _push_refusal(exc)

    if not ctx.cloud_recorder_id:
        if multi:
            return None, "this recorder is not linked to WatchLog yet"
        return legacy()                # one recorder WatchLog has no identity for
    try:
        return _issue_push_token(cloud, state, ctx.cloud_recorder_id), ""
    except CloudError as exc:
        if _recorder_push_absent(exc):
            if multi:
                raise _NoRecorderScopedPush() from exc
            return legacy()
        if _push_refusal(exc) is not None:
            return None, _push_refusal(exc)
        raise


class _NoRecorderScopedPush(Exception):
    """WatchLog offers no recorder-scoped push token on a multi-recorder site."""


def cmd_configure_push(cfg: Config, *, _state=None, _cloud_factory=None,
                       _open_driver=None, _contexts=None) -> int:
    """Point the RECORDER at WatchLog so the site reports with no PC running.

    RUNS AS ITS OWN PROCESS, deliberately. The setup wizard used to do this inline, and
    in 0.4.11 it took the whole installer down with a native crash the moment the feature
    was first enabled on real hardware. Recorder-push is a resilience BONUS layered on a
    working agent install -- it must never be able to kill the thing that installs it. As
    a separate process, any failure here (Python exception, native crash, hang, a recorder
    that wedges mid-request) is contained: the parent sees an exit code and moves on.

    It also means the recorder still gets configured even when the wizard dies, because
    the background agent can run this on its own schedule with no installer present.

    Push identity is per RECORDER (MNVR-011): the token names the recorder, and WatchLog
    resolves the recorder from it before any channel. With a recorder registry every
    configured recorder runs from its own RecorderContext, with its own token from
    wl_agent_issue_push_token(agent, key, p_recorder_id), its own driver and its own push
    URL. On a multi-recorder registry it refuses unless WatchLog offers recorder-scoped
    push, and never falls back to a site-level token there. A single recorder on a
    database without the recorder form, and the 5.0.x singleton (no registry), keep the
    site-level form, which the database itself refuses on a multi-recorder site.

    Prints a single machine-readable PUSH_JSON line (never a token). Exit 0 = every
    recorder confirmed it.
    """
    result = {"configured": False, "verified": False, "detail": ""}

    def report(res: dict, code: int) -> int:
        print("PUSH_JSON " + json.dumps(res), flush=True)
        return code

    try:
        base = (cfg.push_bridge_url or "").strip().rstrip("/")
        if not base:
            result["detail"] = "no push bridge configured in this build"
            return report(result, 2)

        state = _state if _state is not None else load_state(cfg.state_path)
        if not state or not state.get("agent_id") or not state.get("agent_key"):
            result["detail"] = "this site is not enrolled yet"
            return report(result, 2)

        contexts = list((_contexts or recorder_runtime.load_contexts)(cfg) or [])
        cloud = (_cloud_factory or (lambda: Cloud(cfg.supabase_url, cfg.publishable_key)))()
        open_fn = _open_driver or open_driver

        if not contexts:
            # 5.0.x singleton runtime: the site-level form, guarded by the database.
            try:
                token = _issue_push_token(cloud, state, None)
            except CloudError as exc:
                if _push_refusal(exc) is None:
                    raise
                result["detail"] = _push_refusal(exc)
                return report(result, 2)
            if not token:
                result["detail"] = "WatchLog did not issue a push token"
                return report(result, 2)
            result = _configure_recorder_push(cfg, open_fn, f"{base}/push/{token}")
            log(f"recorder push: configured={result['configured']} "
                f"verified={result['verified']} {result['detail']}")
            return report(result, 0 if result["verified"] else 2)

        # Every token is issued before any recorder is touched, so a refusal for the
        # whole site leaves every recorder exactly as it was.
        multi = len(contexts) > 1
        planned = []
        try:
            for ctx in contexts:
                token, detail = _recorder_push_token(cloud, state, ctx, multi)
                if token is None and not detail:
                    detail = "WatchLog did not issue a push token"
                planned.append((ctx, token, detail))
        except _NoRecorderScopedPush:
            result["detail"] = MULTI_RECORDER_PUSH_REFUSED
            return report(result, 2)

        rows = []
        for ctx, token, detail in planned:
            row = {"recorder": ctx.display_name, "configured": False,
                   "verified": False, "detail": detail}
            if token:
                try:
                    row.update(_configure_recorder_push(ctx.config, open_fn,
                                                        f"{base}/push/{token}"))
                except Exception as exc:  # noqa: BLE001 - one recorder never sinks the rest
                    row["detail"] = f"could not configure recorder push ({type(exc).__name__})"
                log(f"recorder push [{ctx.display_name}]: configured={row['configured']} "
                    f"verified={row['verified']} {row['detail']}")
            rows.append(row)

        result = {"configured": all(r["configured"] for r in rows),
                  "verified": all(r["verified"] for r in rows),
                  "detail": (rows[0]["detail"] if not multi else
                             "; ".join(f"{r['recorder']}: {r['detail'] or 'ok'}"
                                       for r in rows)),
                  "recorders": rows}
        return report(result, 0 if result["verified"] else 2)
    except Exception as exc:  # noqa: BLE001 - a bonus layer never fails loudly
        result = {"configured": False, "verified": False,
                  "detail": f"could not configure recorder push ({type(exc).__name__})"}
        try:
            print("PUSH_JSON " + json.dumps(result), flush=True)
        except Exception:  # noqa: BLE001
            pass
        return 2


def cmd_accept(cfg: Config, *, _state=None, _open_driver=None, _cloud_factory=None,
               _heartbeat=None, _spool_factory=None, _archive=None, _detector=None,
               _ini_text=None, live_seconds: int | None = None) -> int:
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
    from types import SimpleNamespace

    open_driver_fn = _open_driver or open_driver
    heartbeat_fn = _heartbeat or heartbeat
    cloud_factory = _cloud_factory or (lambda: Cloud(cfg.supabase_url, cfg.publishable_key))
    if _spool_factory is None:
        from spool import Spool
        spool_factory = lambda: Spool(cfg.spool_path, cfg.spool_max_rows)   # noqa: E731
    else:
        spool_factory = _spool_factory
    archive_fn = _archive or prove_recorder_archive
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
        live = holder.get("driver")
        chans = holder.get("channels") or []
        if live is None or not chans:
            return "warn", "recorder/cameras unavailable to check the archive"
        channel = chans[0].get("channel") if isinstance(chans[0], dict) else getattr(chans[0], "channel", None)
        # Recorded media is read through open_archive_driver at runtime (incident footage,
        # recovery), which on an ONVIF site can be the vendor-native reader. Prove THAT transport
        # (MNVR-063), reusing the open live driver instead of logging in again.
        try:
            driver, _info = open_archive_driver(cfg, live=(live, holder.get("info")))
        except Exception:  # noqa: BLE001 — soft check: an unopened archive only warns
            return "warn", "the recorder archive could not be opened for this check"
        if driver is not live:
            holder["archive_driver"] = driver
        holder["archive_transport"] = _archive_transport(driver)
        proof = archive_fn(driver, channel)
        status, _passed = acceptance.map_archive_status((proof or {}).get("status"))
        return status, (proof or {}).get("detail")

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
    checks = [
        {"key": "config", "label": "Configuration present", "hard": True, "run": _config,
         "budget": 10},
        {"key": "identity", "label": "Site enrolled (local identity)", "hard": True,
         "run": _identity, "budget": 10},
        {"key": "cloud", "label": "WatchLog cloud authenticates this agent", "hard": True,
         "run": _cloud, "budget": 25},
        {"key": "recorder", "label": "Recorder reachable", "hard": True, "run": _recorder,
         "budget": 25},
        {"key": "cameras", "label": "Cameras enumerated", "hard": True, "run": _cameras,
         "budget": 30},
        {"key": "spool", "label": "Local spool healthy", "hard": True, "run": _spool,
         "budget": 20},
        {"key": "security", "label": "No plaintext recorder password on disk", "hard": True,
         "run": _security, "budget": 10},
        # --- soft from here: informational only, can never block Ready ---
        {"key": "runtime", "label": "Runtime version + build identity", "hard": False,
         "run": _runtime, "budget": 15},
        {"key": "archive", "label": "Recorded footage retrievable (outage recovery)",
         "hard": False, "run": _archive_check, "budget": 25},
        {"key": "live", "label": "Live events flowing", "hard": False, "run": _live,
         "budget": live_seconds + 10},
        {"key": "ai", "label": "On-site AI false-alarm filter", "hard": False, "run": _ai,
         "budget": 30},
    ]

    report = acceptance.run_checks(checks, log=print)
    report["archive_transport"] = holder.get("archive_transport")
    stats = report["summary"]
    print()
    if report["ready"]:
        print(f"RESULT: ACCEPTED ({stats['passed']} passed, {stats['warned']} warning(s))")
    else:
        print(f"RESULT: BLOCKED ({stats['hard_failures']} required check(s) not passing)")
    # Single machine-readable line for the installer status panel — contains no secrets.
    print("ACCEPTANCE_JSON " + json.dumps(report, separators=(",", ":")))

    for key in ("driver", "archive_driver"):
        driver = holder.get(key)
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

    # --- recorder + cameras + archive (all guarded) ---
    driver = info = None
    try:
        driver, info = (_open_driver or open_driver)(cfg)
    except Exception:  # noqa: BLE001 — recorder unreachable is an honest state, not a crash
        driver = info = None

    # Recorded media is read through open_archive_driver at runtime (incident footage, recovery),
    # which on an ONVIF site can be the vendor-native reader: report and prove THAT transport,
    # while recorder/cameras stay on the live driver (MNVR-063). The open live driver is reused,
    # so a site whose live driver is already the archive transport is not logged in to twice.
    archive_driver = None
    if driver is not None:
        try:
            archive_driver, _archive_info = open_archive_driver(cfg, live=(driver, info))
        except Exception:  # noqa: BLE001
            archive_driver = None

    capability = None
    channels = []
    if driver is not None:
        try:
            capability = archive_driver.historical_capability() \
                if hasattr(archive_driver, "historical_capability") else None
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
    if archive_driver is not None and merged:
        try:
            # The same proof acceptance and Recheck Archive run, on the runtime archive transport.
            proof = (_archive or prove_recorder_archive)(archive_driver, merged[0]["channel"])
            archive_status = (proof or {}).get("status")
        except Exception:  # noqa: BLE001
            archive_status = None
    archive = ss.archive_view(proof_status=archive_status, last_proof_at=iso(now),
                              recovery_backlog=0)
    archive["transport"] = _archive_transport(archive_driver)

    recording = ss.recording_view(camera["cameras"], recording=None)

    # Retention depth (P7): bounded, best-effort. Off by default so the panel refresh stays fast;
    # WATCHLOG_STATUS_RETENTION=1 (or a dedicated deep recheck) enables the archive-boundary probe.
    ret = _retention
    if ret is None and archive_driver is not None and merged and \
            os.environ.get("WATCHLOG_STATUS_RETENTION", "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            import retention as _retmod
            ret = _retmod.estimate_retention(archive_driver, merged[0]["channel"], now=now)
        except Exception:  # noqa: BLE001
            ret = None
    if ret and ret.get("status") in ("measured", "at_least"):
        storage = ss.storage_view({"retention_days": ret.get("retention_days"),
                                   "oldest_recording": ret.get("oldest_recording")})
    else:
        storage = ss.storage_view(None)              # honest 'Not available on this recorder'

    for opened in (driver, archive_driver if archive_driver is not driver else None):
        if opened is not None:
            try:
                opened.close()
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
    import recovery_ai

    now = _now or now_utc()
    out = {"schema": "watchlog.archive_recheck.v1", "state": "ARCHIVE FAILED",
           "checked_at": iso(now), "channel": None, "frame_decoded": None, "diagnostics": {}, "detail": "",
           "transport": None}
    driver = None
    try:
        driver, _info = (_open_driver or open_archive_driver)(cfg)
    except Exception:  # noqa: BLE001
        driver = None
    if driver is None:
        out["detail"] = "recorder not reachable"
        print("ARCHIVE_JSON " + _json.dumps(out, separators=(",", ":")))
        return 0
    out["transport"] = _archive_transport(driver)
    channel = "1"
    for prof in (getattr(cfg, "camera_profiles", None) or []):
        if prof.get("monitored", True) and prof.get("channel"):
            channel = str(prof["channel"])
            break
    out["channel"] = channel

    try:
        proof = (_archive or prove_recorder_archive)(driver, channel) or {}
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
            # A recorder-scoped or channel-less event has channel None: no camera column.
            channel = "-" if ev.channel is None else ev.channel
            print(f"    {iso(ev.device_ts)}  ch{channel:<4} {ev.event_type}")
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


def _open_recovery_interval_for_cfg(cloud: Cloud, state: dict, cfg: Config,
                                    started_at, ended_at, targets) -> dict:
    """Open a recovery interval on the contract this recorder context speaks.

    A bound recorder names its archive channels (wl_open_recorder_recovery_interval takes
    p_channels). The recorder-less 5.0.x contract names cameras by cloud camera UUID
    (wl_open_recovery_interval takes p_cameras uuid[]), never by recorder channel number."""
    recorder_id = getattr(cfg, "recorder_cloud_id", None)
    args = dict(
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
        p_started_at=started_at,
        p_ended_at=ended_at,
    )
    if recorder_id:
        args["p_recorder_id"] = recorder_id
        args["p_channels"] = list(targets or [])
        return cloud.call("wl_open_recorder_recovery_interval", **args)
    args["p_cameras"] = list(targets or [])
    return cloud.call("wl_open_recovery_interval", **args)


# holder flag: recovery_worker has read last_live.json while the recorder was live and holds no
# gap the cloud has not accepted. A heartbeat that refreshes last_live must wait for it, or a
# restart gap is overwritten before it is ever detected (or lost if the Agent restarts while held).
LAST_LIVE_CHECKED = "last_live_checked"


def _recovery_login(cfg: Config, opener):
    """Run one of the recovery thread's recorder logins behind a confirmed-auth back-off.

    The live collector backs off a refused login 5 -> 15 -> 30 min; this thread used to retry
    every recovery cycle (12 an hour while no startup inventory exists, or an interval is
    claimed), which can keep a recorder's failed-login lock engaged after Setup fixed the
    password. It shares the breaker the native archive probe uses, keyed on the primary login;
    a credential change in Setup clears it at once. Raises NvrAuthFailed while backed off."""
    from drivers.base import NvrAuthFailed
    key = _native_archive_key(cfg, "recorder-login")
    breaker = _auth_breaker_path(cfg)
    wait = _native_archive_backoff(key, cfg, breaker)
    if wait:
        raise NvrAuthFailed(f"recorder login refused earlier; not retried for "
                            f"{max(1, round(wait / 60))} min")
    try:
        result = opener()
    except Exception as error:  # noqa: BLE001 — recorded, then raised to the caller as before
        _note_native_archive_probe(key, error, cfg, breaker)
        raise
    _note_native_archive_probe(key, None, cfg, breaker)
    return result


def _synced_inventory(driver) -> list:
    """The recorder's cameras, listed for wl_sync_cameras. The numbering sent is pinned as the
    recorder's camera identity for this process (ONVIF numbers cameras by GetProfiles position,
    which a camera removed on the recorder would shift while the Agent runs)."""
    chans = list(driver.list_channels())
    pin = getattr(driver, "pin_inventory", None)
    if callable(pin):
        pin()
    return chans


def _recovery_camera_ids(cfg: Config, state: dict, cloud: Cloud, channels) -> dict:
    """{recorder channel: cloud camera UUID} for opening and reading recovery intervals.

    wl_open_recovery_interval takes camera UUIDs (uuid[]), never recorder channel numbers. They come
    from wl_sync_cameras with the same startup payload main() sends, which is idempotent, so a boot
    sync that failed (network not ready) is simply retried here. With no startup inventory (recorder
    offline at boot) the recorder is enumerated again first. Returns {} while no mapping exists, so
    the caller defers rather than guessing."""
    payload = []
    for c in channels or []:
        ch = c.get("channel") if isinstance(c, dict) else getattr(c, "channel", None)
        if ch is not None:
            name = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
            payload.append({"channel": str(ch), "name": name})
    if not payload:
        driver, _info = _recovery_login(cfg, lambda: open_driver(cfg))
        try:
            payload = [{"channel": str(c.channel), "name": c.name}
                       for c in _synced_inventory(driver)]
        finally:
            driver.close()
    if not payload:
        return {}
    mapping = cloud.call(
        "wl_sync_cameras", p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
        p_cameras=payload) or {}
    return {c["channel"]: str(mapping[c["channel"]]) for c in payload if mapping.get(c["channel"])}


def recovery_worker(cfg: Config, state: dict, cloud: Cloud, stop: threading.Event,
                    spool, channels=None, holder=None) -> None:
    """Automatic NVR outage recovery (0.4.4 §1/§2/§5). On start, the persisted last-live vs now
    yields the missed interval, reported as a PENDING recovery interval. Then it claims pending
    intervals and backfills each from the recorder archive in bounded, resumable, idempotent
    chunks (read-only; recovered events carry recorder_archive provenance). LIVE monitoring always
    has priority (yields when the live spool has a backlog) and it is throttled. OFF only if
    recovery_enabled=false. A failure here can never disturb events/heartbeat/health.

    The recorder-less contract names cameras by cloud camera UUID. Until the channel->camera
    mapping exists nothing is opened or claimed: a detected gap is held, never sent with recorder
    channel numbers or with an empty camera list. A bound recorder names its explicitly synced
    archive channels instead, and waits for that inventory rather than guessing a channel.

    Time this worker saw the recorder live in is never reopened: one cycle is longer than the
    outage threshold, so a gap between two of its checks is only known from a last_live a
    heartbeat kept while the recorder was live. "Saw live" is the recorder's last activity, not
    the check's own clock: the recorder counts as live for a grace after its stream died, and a
    stamp from inside that grace would hide the outage that follows."""
    if not cfg.recovery_enabled:
        return
    import recovery as rec
    stop.wait(min(20, cfg.recovery_seconds))            # let enrollment / live settle first
    camera_ids = {}                                     # {channel: camera UUID}; {} until synced
    pending_gaps = []                                   # detected gaps the cloud has not accepted yet
    seen_live_at = None                                 # this worker's last check with the recorder live
    holder = holder if holder is not None else {}

    def _channel_id(item):
        if isinstance(item, dict):
            return item.get("channel")
        return getattr(item, "channel", None)

    def _bound_inventory():
        """(archive channels, {camera UUID: channel}) of the bound recorder's synced cameras.
        (Not the module-level _synced_inventory(driver), which enumerates and pins a driver.)"""
        source = channels() if callable(channels) else channels
        chans, cams = [], {}
        for c in source or []:
            ch = _channel_id(c)
            if ch is None:
                continue
            chans.append(str(ch))
            cam = c.get("camera_id") if isinstance(c, dict) else getattr(c, "camera_id", None)
            if cam:
                cams[str(cam)] = str(ch)
        return chans, cams

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

    def recorder_live_until(now):
        """Wall time of the latest recorder activity recorder_is_live() counted (at most now)."""
        drv = holder.get("live_driver")
        # Same presence test as recorder_is_live(): any non-zero stamp counts. Clamping with
        # max(..., 0.0) dropped a stamp taken shortly after boot (monotonic near zero) and the
        # outage was then dated to this cycle instead of the last activity.
        stamps = [s for s in (float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0),
                              float(holder.get("recorder_live_at") or 0.0)) if s]
        if not stamps:
            return now
        latest = max(stamps)
        return now - timedelta(seconds=max(0.0, time.monotonic() - latest))

    def heartbeat_keeps_last_live() -> bool:
        # A driver that reports its event stream has last_live kept at its stream activity by
        # the heartbeat (analytics_agent._persist_stream_last_live); one that cannot report it
        # (connected None, or no stream state) has nothing but this worker's own checks.
        return (holder.get("event_stream") or {}).get("connected") is not None

    while not stop.is_set():
        try:
            _reload_credential_if_changed(cfg)
            recorder_id = getattr(cfg, "recorder_cloud_id", None)
            if recorder_id:
                cams, camera_channels = _bound_inventory()
                if not cams:
                    # Never guess a recorder channel. A recorder that was unreachable at
                    # startup waits until health enumeration has explicitly synced its
                    # camera inventory, then this same worker begins recovery automatically.
                    holder["recovery_waiting_for_inventory"] = True
                    stop.wait(min(max(5, cfg.recovery_seconds), 30))
                    continue
                holder.pop("recovery_waiting_for_inventory", None)
            else:
                if not camera_ids:
                    try:
                        camera_ids = _recovery_camera_ids(cfg, state, cloud, channels)
                    except Exception as e:               # noqa: BLE001
                        log(f"recovery: camera inventory not synced yet; recovery deferred: "
                            f"{type(e).__name__}")
                cams = list(camera_ids.values())
                camera_channels = {cam: ch for ch, cam in camera_ids.items()}

            # A very long Internet outage can fill the bounded local spool. trim() records
            # exactly which local-observation interval had to be evicted; convert that durable
            # marker into the same recorder-archive recovery pipeline once the NVR is live.
            try:
                overflow_gap = spool.pending_recovery_gap()
                if overflow_gap and cams and recorder_is_live():
                    _open_recovery_interval_for_cfg(
                        cloud, state, cfg, overflow_gap[0], overflow_gap[1], cams
                    )
                    if spool.clear_recovery_gap(*overflow_gap):
                        log(f"recovery: spool overflow {overflow_gap[0]}..{overflow_gap[1]}; "
                            "opened recorder-archive reconciliation")
            except Exception as e:                       # noqa: BLE001
                log(f"recovery: spool-overflow reconciliation deferred: {type(e).__name__}")

            # Detect BOTH restart gaps and in-process recorder/network gaps. Only open the
            # interval after the recorder is live again; while it is still down there is
            # nothing to backfill and no reason to hammer it. A detected gap is held until the
            # cloud accepts it, so a deferred open neither loses the gap nor stretches it over
            # the live time that follows.
            try:
                if recorder_is_live():
                    last_live = rec.read_last_live(cfg.last_live_path)
                    now = now_utc()
                    live_until = recorder_live_until(now)
                    if (seen_live_at is not None and not heartbeat_keeps_last_live()
                            and (last_live is None or last_live <= seen_live_at)):
                        last_live = None                # nothing later than this worker's own check
                    # With a heartbeat keeping last_live, a value it did not move past this
                    # worker's last check is the recorder's last activity before an outage.
                    outage = rec.detect_outage(last_live, now, cfg.recovery_threshold_seconds)
                    seen_live_at = live_until
                    if outage and not any(abs((g[0] - outage[0]).total_seconds()) < 5
                                          for g in pending_gaps):
                        pending_gaps = (pending_gaps + [outage])[-32:]
                    # While a gap is held only in memory, last_live must keep its start for a
                    # restart to find it again: the heartbeat may not move it on.
                    holder[LAST_LIVE_CHECKED] = not pending_gaps
                    while pending_gaps and cams:
                        gap = pending_gaps[0]
                        _open_recovery_interval_for_cfg(
                            cloud, state, cfg, iso(gap[0]), iso(gap[1]), cams
                        )
                        pending_gaps.pop(0)
                        log(f"recovery: detected recorder gap {iso(gap[0])}..{iso(gap[1])}; "
                            "opened resumable archive recovery")
                    if not pending_gaps:
                        rec.persist_last_live(cfg.last_live_path, live_until)
                        holder[LAST_LIVE_CHECKED] = True
            except Exception as e:                       # noqa: BLE001
                log(f"recovery: gap detector skipped: {type(e).__name__}")

            # Claimed intervals name camera UUIDs (or a bound recorder's channels); without the
            # inventory they cannot be read. The recorder archive is opened (and closed) by the
            # runner only for a claimed interval that needs reading, so an idle cycle never logs
            # in to the recorder. An archive that cannot be opened hands the claim back as
            # pending, its retry budgets untouched.
            if cams:
                try:
                    runner = rec.RecoveryRunner(
                        cloud, state["agent_id"], state["agent_key"], None,
                        lambda ev: spool.add(ev),
                        recorder_id=recorder_id,
                        chunk_seconds=cfg.recovery_chunk_seconds,
                        throttle_seconds=cfg.recovery_throttle_seconds,
                        live_pending=lambda: spool.count() > cfg.recovery_live_backlog,
                        detector=detector, ai_max_frames=cfg.recovery_ai_max_frames,
                        snapshot_interval_seconds=cfg.recovery_snapshot_seconds,
                        camera_channels=camera_channels,
                        driver_factory=lambda: _recovery_login(
                            cfg, lambda: open_archive_driver(cfg))[0],
                        log=log)
                    runner.run_once(limit=1)
                except Exception as e:                   # noqa: BLE001 — recovery never disturbs the agent
                    log(f"recovery: {type(e).__name__}: {nvr_health.redact(str(e))}")
        except BaseException as e:                       # noqa: BLE001 — last resort; the thread must outlive any fault
            worker_fault("recovery", e)
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
                # recorder is unreachable while the agent still heartbeats, this clock
                # intentionally stops so the missing interval is recovered when contact returns.
                if cfg.recovery_enabled:
                    try:
                        drv = holder.get("live_driver")
                        activity = float(getattr(drv, "last_activity_monotonic", 0.0) or 0.0)
                        seen = float(holder.get("recorder_live_at") or 0.0)
                        fresh = ((activity and time.monotonic() - activity < 150.0)
                                 or (seen and time.monotonic() - seen < 150.0))
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
    ap.add_argument("--config", metavar="PATH",
                    help="explicit watchlog.ini path (used by staged repair validation)")
    ap.add_argument("--preflight-existing-site", action="store_true",
                    help="read-only staged compatibility validation for an existing enrolled site")
    ap.add_argument("--preflight-json", metavar="PATH",
                    help="write existing-site preflight result JSON to this path")
    ap.add_argument("--preflight-mode", choices=("passive", "recorder", "full"), default="full",
                    help="existing-site preflight phase; Repair/Upgrade uses passive before pause "
                         "and recorder after pause")
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

    cfg = Config(Path(args.config) if args.config else None,
                 read_only_credentials=bool(args.preflight_existing_site))

    if args.preflight_existing_site:
        raise SystemExit(cmd_existing_site_preflight(
            cfg, result_path=args.preflight_json, mode=args.preflight_mode))

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

    # Identify the recorder ONCE and reuse the answer: enrollment, the
    # camera sync and the heartbeat all want it, and probing four times
    # on every start is noise on the wire and in the log.
    device, channels = None, []
    boot_cfg = _boot_probe_config(cfg)
    if boot_cfg is None:
        log("recorder: the configured recorders are identified by their own recorder "
            "check; skipping the legacy startup probe")
    else:
        try:
            driver, device = open_driver(boot_cfg)
            try:
                channels = [{"channel": c.channel, "name": c.name}
                            for c in _synced_inventory(driver)]
                # Do NOT read recorder capabilities here (field Build 41/69): on Hikvision
                # it fans out into several ISAPI calls per channel and delayed the live
                # collector and heartbeat by minutes. capability_sync sends them once
                # monitoring has started (deferred below).
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
            if boot_cfg.nvr_url:
                try:
                    host = discover.host_of(boot_cfg.nvr_url)
                    discover.report(host,
                                    discover.scan(boot_cfg.nvr_url, log=log),
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

    # Report what analytics the recorder supports, so the portal can show
    # them. Captured above while the driver was open; a failure to upload
    # must not stop the agent doing its actual job.
    if device is not None and boot_cfg is not None:
        import capability_sync
        capability_sync.defer(boot_cfg, state, cloud, open_driver, log=log)
    cmd_run(cfg, state, cloud, once=args.once, device=device, channels=channels)


if __name__ == "__main__":
    main()

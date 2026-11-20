"""Lightweight core shared by the WatchLog Agent and WatchLog Setup.

Configuration (``Config``), the cloud RPC client (``Cloud``/``CloudError``), local identity
state (``load_state``/``save_state``), the recorder open path (``open_driver``), the
heartbeat, the one-shot spool upload and the runtime-health proof live here. Setup
(``setup_backend``, ``setup_gui``) needs exactly these and nothing else of the Agent runtime,
so it imports this module instead of ``watchlog_agent``.

``watchlog_agent`` re-exports every name below, so ``watchlog_agent.Config``,
``core.heartbeat`` and the rest keep working for the runtime, the analytics entrypoint and
the tests. The definitions themselves now live here: a test that replaces one of these
collaborators to change what another function in this module does must patch it on
``agent_core``.

Packaging rule (enforced by prototype/tests/test_setup_import_graph.py): this module and
everything it imports must stay free of the Agent runtime's heavy stack: no vision,
onnxruntime, numpy, Pillow, imageio_ffmpeg/FFmpeg, recovery/recovery_ai, analytics,
updater/cryptography. That is what keeps those out of watchlog-setup-ui.exe.

Moved verbatim from watchlog_agent.py (5.1.0); behaviour is unchanged.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

import recorder_probe
from drivers import DriverError, autodetect, build
from drivers.base import RecorderIdentityMismatch

import credential_store
from wl_version import VERSION as AGENT_VERSION  # single source of truth

# --- server address helpers (5.1.6) ---------------------------------------

def server_host(url: str | None) -> str:
    """Lower-case host of a server URL, whatever form it was written in (with or
    without scheme, trailing slash, path or port). '' when there is none."""
    u = str(url or "").strip().strip('"').strip("'").lower()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        return urlsplit(u).hostname or ""
    except ValueError:
        return ""


def retired_hosts(value: str | None) -> set[str]:
    """Hosts listed in retired_supabase_urls (comma separated, any URL form)."""
    return {h for h in (server_host(p) for p in str(value or "").split(",")) if h}


# An sslip.io / nip.io name carries its own address (supa.x.1.2.3.4.sslip.io -> 1.2.3.4).
# Resolve it locally so a site whose DNS filter or resolver refuses these names can still
# reach the WatchLog server. TLS (SNI + certificate check) still uses the real name.
_EMBEDDED_IP_HOST = re.compile(
    r"(?:^|[.-])(\d{1,3})[.-](\d{1,3})[.-](\d{1,3})[.-](\d{1,3})\.(?:sslip\.io|nip\.io)\.?$")
_system_getaddrinfo = socket.getaddrinfo


def embedded_ip(host) -> str | None:
    if not isinstance(host, str):
        return None
    m = _EMBEDDED_IP_HOST.search(host.lower())
    if not m or not all(0 <= int(part) <= 255 for part in m.groups()):
        return None
    return ".".join(str(int(part)) for part in m.groups())


def _getaddrinfo_embedded_ip(host, *args, **kwargs):
    ip = embedded_ip(host)
    if ip:
        try:
            return _system_getaddrinfo(ip, *args, **kwargs)
        except OSError:
            pass
    return _system_getaddrinfo(host, *args, **kwargs)


if getattr(socket.getaddrinfo, "__name__", "") != "_getaddrinfo_embedded_ip":
    socket.getaddrinfo = _getaddrinfo_embedded_ip


HEARTBEAT_SECONDS = 60
HEALTH_SECONDS = 300          # recorder assessment + camera health cycle, every 5 min (jittered)
HEALTH_BATCH = 4              # cameras probed per cycle (fair round-robin over cycles)
HEALTH_CONCURRENCY = 2        # strict cap on simultaneous snapshot probes — never the whole wall
UPLOAD_SECONDS = 15
UPLOAD_BATCH = 200
HTTP_TIMEOUT = 30
RECORDER_PROBE_MAX_ATTEMPTS = 3   # bounded alternate-port attempts on a non-auth connect failure

# Incident stills. One per camera at most every SNAPSHOT_MIN_INTERVAL
# seconds: a busy gate can fire every few seconds, and an image per event
# would flood both the site uplink and the storage budget for no extra
# information.
SNAPSHOT_MIN_INTERVAL = 60
# Uploads are capped by BYTES as well as count - 200 events carrying
# stills would be a ~40 MB request.
UPLOAD_MAX_BYTES = 4_000_000

# Wall times of this process's last cloud-accepted event upload and heartbeat, reported to
# the cloud as runtime status (5.1.2, migration 0162). None until one succeeds in this run.
CLOUD_PROOF: dict = {"upload_ok_at": None, "heartbeat_ok_at": None}


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


_RUNTIME_HEALTH_LOCK = threading.Lock()


def runtime_health_path() -> Path:
    # Integrity-sensitive installer proof: keep it under the existing
    # SYSTEM+Administrators-only Secrets ACL so a standard local user cannot
    # forge a healthy-version marker and trick Repair/Upgrade into committing.
    return default_state_dir() / "Secrets" / "runtime-health.json"


def _runtime_build_sha() -> str:
    try:
        import wl_version
        return str(wl_version.BUILD_SHA or "")
    except Exception:
        return ""


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
                # The build, so a remote update can prove the exact released build runs.
                "build_sha": _runtime_build_sha(),
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
        retired_value = ""
        if defaults_path.exists():
            try:
                defaults = configparser.ConfigParser()
                defaults.read(defaults_path, encoding="utf-8-sig")
                if defaults.has_section("watchlog"):
                    retired_value = defaults["watchlog"].get("retired_supabase_urls", "") or ""
                    for key, value in defaults.items("watchlog"):
                        if key in safe_public and str(value or "").strip():
                            section[key] = value
            except configparser.Error:
                pass
        build_url = section.get("supabase_url", "")
        build_key = section.get("supabase_publishable_key", "")

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

        # Server move (5.1.6): a site still pointing at a backend this build retires uses
        # the build's server instead -- from watchlog.ini, from an old environment
        # override, in any URL form. Applied here, in memory, so the Agent, the
        # Repair/Upgrade preflight and Setup all agree even if no file was rewritten.
        ignore_env: set[str] = set()
        retired = retired_hosts(retired_value)
        if retired and build_url and build_key and server_host(build_url) not in retired:
            if server_host(os.environ.get("WATCHLOG_SUPABASE_URL")) in retired:
                ignore_env |= {"supabase_url", "supabase_publishable_key"}
                log("config: ignoring WATCHLOG_SUPABASE_URL (retired server)")
            if server_host(section.get("supabase_url")) in retired:
                log(f"config: {section.get('supabase_url')} is retired; using {build_url}")
                section["supabase_url"] = build_url
                section["supabase_publishable_key"] = build_key

        def get(key: str, default: str | None = None) -> str | None:
            env = None if key in ignore_env else os.environ.get("WATCHLOG_" + key.upper())
            return env or section.get(key) or default

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
        # Site Control command plane (H6), read-only executor. ON by default (5.1.3): the
        # owner's per-site cloud switch (sites.site_control_enabled, off by default) is the
        # gate, checked by wl_agent_claim_command on every poll, so nothing runs until the
        # owner turns it on, and no site visit is needed to make a site remotely testable.
        # `site_control = false` in the ini still opts a PC out locally.
        self.site_control_enabled = str(get("site_control") or "true").strip().lower() != "false"
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


def _stale_connection(exc: BaseException) -> bool:
    """True when the connection was closed by the far end before any reply: the request did not
    reach the server (a reused idle keep-alive socket), so one resend is safe."""
    text = repr(exc)
    return any(mark in text for mark in ("Connection aborted", "RemoteDisconnected",
                                         "ConnectionResetError", "10054", "BrokenPipe"))


class Cloud:
    """
    The entire cloud surface: four SECURITY DEFINER functions.

    No table is ever addressed directly. The publishable key on its own
    grants nothing — RLS is on with no policies — so identity is the
    agent's own secret, passed to each call.
    """

    def __init__(self, url: str, key: str) -> None:
        self.rpc = url + "/rest/v1/rpc"
        # Default existing cloud call behavior; heartbeat owns a dedicated bounded client.
        self.request_timeout = HTTP_TIMEOUT
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        })

    def call(self, fn: str, **params):
        try:
            r = self.s.post(f"{self.rpc}/{fn}", json=params, timeout=self.request_timeout)
        except requests.ConnectionError as exc:
            # Field (Al-Khalid, 5.1.3, 2026-10-08): a pooled keep-alive connection the server
            # had closed while idle (a 3-minute acceptance test) was reused, and the POST died
            # with "Connection aborted ... 10054" before any byte of a reply. That request never
            # reached the database, so it is sent once more on a fresh connection. Anything else
            # (a timeout, a reply) is never retried here: the caller decides.
            if not _stale_connection(exc):
                raise
            self.s.close()
            r = self.s.post(f"{self.rpc}/{fn}", json=params, timeout=self.request_timeout)
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
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(public, indent=2))
        handle.flush()
        os.fsync(handle.fileno())   # a power cut never publishes an empty identity
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


def _open_driver_verified(cfg: Config):
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


def open_driver(cfg: Config):
    """Open this recorder; find it again when DHCP moved it (5.1.14).

    The configured URL stays authoritative while it works. After repeated non-auth
    connection failures (including another device answering at the old address), the
    conservative same-recorder rediscovery runs, at most once per REDISCOVERY_MIN_SECONDS
    per recorder. An auth failure never triggers it: the address is right, the login is
    not, and trying other hosts with it would be credential spraying."""
    _apply_adopted_url(cfg)
    try:
        driver, info = _open_driver_verified(cfg)
    except (DriverError, OSError) as exc:
        if _is_auth_failure(exc):
            _note_auth_failure(cfg)
            raise
        moved = _maybe_rediscover(cfg)
        if moved is not None:
            return moved
        raise
    _note_connected(cfg, info)
    return driver, info


# --- same-recorder rediscovery after an address change (5.1.14) -------------
#
# Field (17-20 Nov 2026): after a power cut the recorder's DHCP address changes. Before
# 5.1.14 the running Agent only retried other web ports on the SAME host, so Al-Khalid had
# to reinstall three times and Chai Wala stayed offline. The rediscovery itself
# (connector_rediscovery: vendor-filtered candidates, at most MAX_AUTH_CANDIDATES logins,
# accepted only on the saved serial or a sole vendor+model match) existed but was never
# called by the running Agent.

_REDISCOVERY_LOCK = threading.Lock()
_REDISCOVERY: dict[str, dict] = {}       # recorder key -> failures / last attempt / running
_ADOPTED_URLS: dict[str, tuple[str, str]] = {}   # recorder key -> (old url, proven new url)
_IDENTITY_CHECKED: set[str] = set()      # recorder keys whose identity file is known present
_rediscovery_clock = None                # injectable monotonic clock (tests)


def _monotonic() -> float:
    import time as _time
    return (_rediscovery_clock or _time.monotonic)()


def _reset_rediscovery_state() -> None:
    """Forget counters, adopted addresses and identity checks (tests)."""
    with _REDISCOVERY_LOCK:
        _REDISCOVERY.clear()
        _ADOPTED_URLS.clear()
        _IDENTITY_CHECKED.clear()


def _recorder_key(cfg) -> str:
    local_id = getattr(cfg, "recorder_local_id", None)
    return f"recorder:{local_id}" if local_id else "legacy"


def _recorder_label(cfg) -> str:
    return str(getattr(cfg, "recorder_display_name", None) or "recorder")


def redact_url(url) -> str:
    """scheme://host[:port] only: no login, path or query ever reaches the log."""
    host, port, scheme = _parse_host_port_scheme(url)
    if not host:
        return "<no address>"
    shown = f"[{host}]" if ":" in host else host
    default = (scheme == "http" and port in (None, 80)) or (scheme == "https" and port in (None, 443))
    return f"{scheme}://{shown}" if default else f"{scheme}://{shown}:{port}"


def _same_endpoint(a, b) -> bool:
    ha, pa, sa = _parse_host_port_scheme(a)
    hb, pb, sb = _parse_host_port_scheme(b)
    return (bool(ha) and ha.lower() == hb.lower()
            and _effective_scheme_port(sa, pa) == _effective_scheme_port(sb, pb))


def _apply_adopted_url(cfg) -> None:
    """Another worker of this recorder already proved it at a new address: use that."""
    with _REDISCOVERY_LOCK:
        adopted = _ADOPTED_URLS.get(_recorder_key(cfg))
    if adopted and _same_endpoint(getattr(cfg, "nvr_url", ""), adopted[0]):
        cfg.nvr_url = adopted[1]


def _note_auth_failure(cfg) -> None:
    with _REDISCOVERY_LOCK:
        state = _REDISCOVERY.get(_recorder_key(cfg))
        if state is not None:
            state["failures"] = 0


def _note_connected(cfg, info) -> None:
    with _REDISCOVERY_LOCK:
        state = _REDISCOVERY.get(_recorder_key(cfg))
        if state is not None:
            state["failures"] = 0
    _ensure_identity_saved(cfg, info)


def _ensure_identity_saved(cfg, info) -> None:
    """Sites installed before identity saving existed have no recorder_identity.json, so
    rediscovery could never prove their recorder. Save it after the first good connect."""
    key = _recorder_key(cfg)
    if key in _IDENTITY_CHECKED:
        return
    try:
        import connector_rediscovery as cr
        if not (str(getattr(info, "serial", "") or "").strip()
                or str(getattr(info, "model", "") or "").strip()):
            return                       # nothing that could prove this recorder later
        path = cr.identity_path(cfg)
        if not path.exists():
            cr.save_identity(cfg, info, cfg.nvr_url)
            if not path.exists():
                return                   # write failed; try again on the next connect
            log(f"recorder identity saved for {_recorder_label(cfg)} "
                "(used to find it again if its address changes)")
        _IDENTITY_CHECKED.add(key)
    except Exception as exc:             # noqa: BLE001 — resilience metadata never fails a connect
        log(f"recorder identity save skipped ({type(exc).__name__})")


def _maybe_rediscover(cfg):
    """Count a non-auth failure; run rediscovery when due. Returns (driver, info) or None."""
    import connector_rediscovery as cr
    key = _recorder_key(cfg)
    now = _monotonic()
    with _REDISCOVERY_LOCK:
        state = _REDISCOVERY.setdefault(key, {"failures": 0, "last_attempt": None,
                                              "running": False})
        state["failures"] += 1
        if state["failures"] < cr.REDISCOVERY_AFTER_FAILURES or state["running"]:
            return None
        last = state["last_attempt"]
        if last is not None and now - last < cr.REDISCOVERY_MIN_SECONDS:
            return None
        if (getattr(cfg, "credential_error", None) or getattr(cfg, "legacy_credential_error", None)
                or not (getattr(cfg, "nvr_username", "") or "")):
            return None                  # no usable login: a search could only fail logins
        state["running"] = True
        state["last_attempt"] = now
        failures = state["failures"]
    result = None
    try:
        result = _rediscover(cfg, failures)
    except Exception as exc:             # noqa: BLE001 — rediscovery never masks the real error
        log(f"recorder rediscovery failed ({type(exc).__name__})")
        result = None
    finally:
        with _REDISCOVERY_LOCK:
            state["running"] = False
            if result is not None:
                state["failures"] = 0
    return result


def _expected_identity(cfg) -> dict:
    """The identity rediscovery must prove: this recorder's own saved identity, checked
    against (and completed from) its registry row. A site-level identity is used for a
    registry recorder only when it was saved for this recorder's serial or address."""
    import connector_rediscovery as cr
    expected = cr.load_identity(cfg)
    local_id = getattr(cfg, "recorder_local_id", None)
    if not local_id:
        return expected
    try:
        import recorder_registry
        row = recorder_registry.recorder(local_id) or {}
    except Exception:                    # noqa: BLE001
        row = {}
    row_serial = _fingerprint_serial(row.get("identity_fingerprint")
                                     or getattr(cfg, "recorder_identity_fingerprint", None))
    if not expected:
        site = cr.load_site_identity(cfg)
        site_serial = str(site.get("serial") or "").strip().upper()
        if site and ((row_serial and site_serial == row_serial)
                     or (not row_serial and _same_endpoint(site.get("url"), cfg.nvr_url))):
            expected = site
    if expected:
        serial = str(expected.get("serial") or "").strip().upper()
        if row_serial and serial and serial != row_serial:
            expected = {}                # the registry's proven serial wins
    if not expected and (row_serial or row.get("model")) and row.get("vendor"):
        expected = {"vendor": row.get("vendor") or "", "model": row.get("model") or "",
                    "serial": row_serial or "", "driver": row.get("driver") or "",
                    "url": row.get("url") or ""}
    if expected and row_serial and not expected.get("serial"):
        expected = {**expected, "serial": row_serial}
    if expected and not str(expected.get("serial") or "").strip() and _configured_count() > 1:
        # Two recorders of one model are common on one site: a model-only match could be
        # this recorder's sibling. With several recorders only a serial proves identity.
        return {}
    return expected


def _configured_count() -> int:
    try:
        import recorder_registry
        return sum(1 for row in recorder_registry.recorders() if row.get("is_configured"))
    except Exception:                    # noqa: BLE001
        return 0


def _serial_of_another_recorder(cfg, info) -> bool:
    serial = str(getattr(info, "serial", "") or "").strip().upper()
    local_id = getattr(cfg, "recorder_local_id", None)
    if not serial or not local_id:
        return False
    try:
        import recorder_registry
        rows = recorder_registry.recorders()
    except Exception:                    # noqa: BLE001
        return True                      # cannot prove it is not a sibling: refuse
    return any(row["local_id"] != str(local_id)
               and _fingerprint_serial(row.get("identity_fingerprint")) == serial
               for row in rows)


def _rediscover(cfg, failures: int):
    import connector_rediscovery as cr
    label = _recorder_label(cfg)
    old_url = str(cfg.nvr_url or "").rstrip("/")
    expected = _expected_identity(cfg)
    if not expected:
        log(f"recorder rediscovery skipped for {label}: no saved recorder identity "
            "(a site with several recorders needs the recorder's serial)")
        return None
    log(f"recorder rediscovery: {label} unreachable at {redact_url(old_url)} after "
        f"{failures} attempts; looking for the same recorder on this network")
    found = cr.find_same_recorder(cfg, expected, log)
    if found is None:
        return None
    new_url, driver, info = found
    new_url = str(new_url).rstrip("/")
    try:
        require_recorder_identity(cfg, info)
    except RecorderIdentityMismatch:
        try:
            driver.close()
        except Exception:                # noqa: BLE001
            pass
        log(f"recorder rediscovery refused for {label}: serial differs from the saved recorder")
        return None
    if _serial_of_another_recorder(cfg, info):
        try:
            driver.close()
        except Exception:                # noqa: BLE001
            pass
        log(f"recorder rediscovery refused for {label}: the device found is another "
            "recorder of this site")
        return None
    if _same_endpoint(new_url, old_url):
        log(f"recorder rediscovery: {label} answers again at {redact_url(new_url)}")
        return driver, info
    cfg.nvr_url = new_url
    with _REDISCOVERY_LOCK:
        _ADOPTED_URLS[_recorder_key(cfg)] = (old_url, new_url)
    log(f"recorder rediscovery: same recorder proven at {redact_url(new_url)} "
        f"({label}, was {redact_url(old_url)})")
    _persist_moved_recorder(cfg, old_url, new_url, info)
    return driver, info


def _persist_moved_recorder(cfg, old_url: str, new_url: str, info) -> None:
    """Make restarts use the proven address: the recorder's registry row (registry sites),
    watchlog.ini where it still names the old address (legacy singleton, or the continuity
    recorder's mirror), and the recorder's identity file. A failed write is logged; this
    run keeps the proven address in memory."""
    import connector_rediscovery as cr
    label = _recorder_label(cfg)
    local_id = getattr(cfg, "recorder_local_id", None)
    if local_id:
        try:
            import recorder_registry
            serial = str(getattr(info, "serial", "") or "").strip()
            recorder_registry.update_recorder_address(
                local_id, url=new_url,
                identity_fingerprint=(f"serial:{serial}" if serial else None))
            log(f"recorder rediscovery: {label} address saved in the recorder list")
        except Exception as exc:         # noqa: BLE001
            log(f"recorder rediscovery: could not save {label}'s new address "
                f"({type(exc).__name__}); using it for this run only")
    try:
        if cr.replace_ini_url(getattr(cfg, "_ini_path", None), old_url, new_url):
            log("recorder rediscovery: watchlog.ini now names the new recorder address")
    except Exception as exc:             # noqa: BLE001
        log(f"recorder rediscovery: could not update watchlog.ini ({type(exc).__name__})")
    cr.save_identity(cfg, info, new_url)
    with _REDISCOVERY_LOCK:
        _IDENTITY_CHECKED.add(_recorder_key(cfg))


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


def _registry_configures_recorders() -> bool:
    """True when recorders.json configures at least one recorder; an absent or unreadable
    registry is not a configured one (an unreadable registry holds in enhanced_cmd_run)."""
    try:
        import recorder_registry
        return any(row.get("is_configured") for row in recorder_registry.recorders())
    except Exception:  # noqa: BLE001
        return False


def _is_auth_failure(err: Exception) -> bool:
    s = str(err).lower()
    return ("rejected the username or password" in s or "http 401" in s
            or "http 403" in s or "invalid username or password" in s
            or "sender not authorized" in s)


# --- upload + heartbeat (also used by Setup) ---------------------------

# wl_ingest_events rejections that belong to particular rows, not to this Agent: a data error
# (SQLSTATE class 22/23) or a row naming a recorder that is not configured for this site
# (0146). Identity (28000), privilege for the whole Agent, server and transport errors are not
# row rejections: the batch is kept and retried.
_ROW_REJECTION_CLASSES = ("22", "23")
_ROW_REJECTION_42501 = "event recorder not configured for this agent site"


def _row_rejection(error: "CloudError") -> bool:
    code = str(getattr(error, "code", "") or "")
    if code[:2] in _ROW_REJECTION_CLASSES:
        return True
    return code == "42501" and _ROW_REJECTION_42501 in str(getattr(error, "message", "") or "")


def _set_aside(spool, row_id: int, event: dict, error: "CloudError") -> None:
    """Keep a rejected row next to its queue (never deleted, never retried), then ack it."""
    path = Path(str(spool.path) + ".rejected.jsonl")
    record = {"rejected_at": iso(now_utc()), "code": str(error.code or ""),
              "reason": str(error.message or "")[:300], "event": event}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    spool.ack([row_id])


def _upload_isolating(cloud: Cloud, state: dict, spool, ids: list, events: list) -> int:
    """Deliver a batch the server rejected because of particular rows: halve it until each
    rejected row stands alone, set those aside, deliver every other row."""
    inserted = 0
    stack = [(list(ids), list(events))]
    while stack:
        part_ids, part_events = stack.pop()
        try:
            res = cloud.call("wl_ingest_events", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"], p_events=part_events)
        except CloudError as error:
            if not _row_rejection(error):
                raise
            if len(part_ids) == 1:
                _set_aside(spool, part_ids[0], part_events[0], error)
                log(f"WARNING: one queued event was rejected ({error.code}: "
                    f"{str(error.message)[:120]}); kept aside, not retried")
                continue
            half = len(part_ids) // 2
            stack.append((part_ids[half:], part_events[half:]))
            stack.append((part_ids[:half], part_events[:half]))
            continue
        spool.ack(part_ids)
        inserted += int((res or {}).get("inserted") or 0)
    log(f"uploaded around rejected events: {inserted} new; {spool.count()} left in spool")
    return inserted


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

    try:
        res = cloud.call("wl_ingest_events", p_agent_id=state["agent_id"],
                         p_agent_key=state["agent_key"], p_events=events)
    except CloudError as error:
        if not _row_rejection(error):
            raise                       # identity, server or transport: retry the batch later
        # One undeliverable row must not hold every event behind it forever: isolate the
        # rejected rows, keep them aside locally, deliver the rest.
        inserted = _upload_isolating(cloud, state, spool, ids, events)
        CLOUD_PROOF["upload_ok_at"] = now_utc()
        return inserted
    # Only acknowledge after the server has committed.
    spool.ack(ids)
    CLOUD_PROOF["upload_ok_at"] = now_utc()
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
    CLOUD_PROOF["heartbeat_ok_at"] = now_utc()
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
        # THIS process proved the recorder's identity at startup (field Build 41/69
        # readiness): fresh only for the current run, so a fresh-install check compares it
        # with the task start and never accepts an earlier run's identity.
        recorder_identified_at=(stamp if device else None),
        recorder_vendor=(device.vendor if device else None),
        recorder_model=(device.model if device else None),
        recorder_driver=(device.driver if device else None),
        event_stream=_event_stream_health(event_stream),
    )
    log("heartbeat ok")

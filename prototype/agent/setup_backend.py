"""Non-interactive backend used by the branded WatchLog Windows setup UI.

This module deliberately reuses the production recorder drivers, discovery and
cloud RPCs. UI code owns presentation; this module owns real work and returns
structured results with no raw secrets in messages.
"""
from __future__ import annotations

import configparser
import json
import os
import platform
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import dahua_archive
import discover
import watchlog_agent as core
import wsdiscovery
from drivers import DriverError, build
import credential_store
from windows_secret import SecretError

from wl_version import VERSION as SETUP_AGENT_VERSION  # single source of truth

SITE_TYPES = [
    ("retail", "Retail / QSR"),
    ("warehouse_logistics", "Warehouse / Logistics"),
    ("manufacturing", "Manufacturing / Factory"),
    ("office_commercial", "Office / Commercial"),
    ("school_campus", "School / Campus"),
    ("parking_yard", "Parking / Yard"),
    ("residential_community", "Residential Community"),
    ("custom", "Other / Custom"),
]

PURPOSES = [
    ("entrance_exit", "Entrance / Exit"),
    ("main_gate", "Main Gate"),
    ("reception", "Reception"),
    ("checkout_till", "Checkout / Till"),
    ("loading_bay", "Loading Bay"),
    ("warehouse_floor", "Warehouse Floor"),
    ("perimeter", "Perimeter"),
    ("restricted_area", "Restricted Area"),
    ("parking", "Parking"),
    ("office_floor", "Office Floor"),
    ("school_gate", "School Gate"),
    ("corridor", "Corridor"),
    ("custom", "Other / Configure Later"),
]

DEFAULTS_BY_SITE = {
    "retail": "entrance_exit",
    "warehouse_logistics": "warehouse_floor",
    "manufacturing": "warehouse_floor",
    "office_commercial": "office_floor",
    "school_campus": "school_gate",
    "parking_yard": "parking",
    "residential_community": "main_gate",
    "custom": "custom",
}


def programdata_dir() -> Path:
    return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"


def read_public_defaults(config_path: Path) -> dict:
    ini = configparser.ConfigParser()
    section = {}
    if config_path.exists():
        ini.read(config_path, encoding="utf-8-sig")
        if ini.has_section("watchlog"):
            section = dict(ini.items("watchlog"))
    return {
        "supabase_url": os.environ.get("WATCHLOG_SUPABASE_URL") or section.get("supabase_url", ""),
        "supabase_publishable_key": os.environ.get("WATCHLOG_SUPABASE_PUBLISHABLE_KEY") or section.get("supabase_publishable_key", ""),
        "enrollment_code": section.get("enrollment_code", ""),
        "nvr_url": section.get("nvr_url", ""),
        # Username lives (atomically with the password) in the encrypted store;
        # pre-fill from there on a re-run, else the conventional default.
        "nvr_username": (credential_store.stored_nvr_username()
                         or section.get("nvr_username", "") or "admin"),
        "site_type": section.get("site_type", "custom"),
        # PC-free reporting: where the recorder should POST its own alarms.
        # Absent -> provisioning skips quietly and the agent reports as usual.
        "push_bridge_url": (os.environ.get("WATCHLOG_PUSH_BRIDGE_URL")
                            or section.get("push_bridge_url", "")),
    }


def migrate_legacy_credentials(config_path: Path) -> bool:
    """Migrate any legacy recorder credential (0.3.3 plaintext watchlog.env,
    old plaintext-INI nvr_password, or the 0.2-0.3.2 nvr_password.dpapi blob)
    into the encrypted split store. Idempotent, fail-closed, crash-recoverable —
    the actual work lives in credential_store so the agent and the --migrate-only
    installer path share one authoritative implementation. Returns True if a
    credential was migrated."""
    migrated = credential_store.migrate_legacy_if_needed(config_path)
    merge_public_defaults(config_path)
    return migrated


def merge_public_defaults(config_path: Path, defaults_path: Path | None = None) -> list:
    """Add public keys the build knows about but an EXISTING watchlog.ini predates.

    NSIS writes watchlog.defaults.ini only when there is no watchlog.ini
    (watchlog.nsi:124-125), which correctly preserves a site's recorder settings on
    upgrade -- but also means an already-installed site can NEVER receive a new public
    key. push_bridge_url is the live example: baking it into the build fixes new installs
    and does nothing whatsoever for the existing fleet, which is the fleet that matters.

    Merge semantics are deliberately narrow and safe: a key is copied ONLY when it is
    present in the shipped defaults AND absent or empty in the existing ini. Nothing the
    operator or setup has already written is ever overwritten. Returns the keys added.
    """
    added: list = []
    try:
        src = Path(defaults_path) if defaults_path else (config_path.parent / "watchlog.defaults.ini")
        if not src.exists() or not config_path.exists():
            return added
        defaults = configparser.ConfigParser()
        defaults.read(src, encoding="utf-8-sig")
        current = configparser.ConfigParser()
        current.read(config_path, encoding="utf-8-sig")
        if not defaults.has_section("watchlog"):
            return added
        if not current.has_section("watchlog"):
            current.add_section("watchlog")
        for key, value in defaults.items("watchlog"):
            # Never resurrect a consumed one-time code, and never touch recorder settings.
            if key in ("enrollment_code", "nvr_url", "nvr_username", "nvr_driver",
                       "nvr_password_protected"):
                continue
            if str(value or "").strip() and not str(current["watchlog"].get(key, "") or "").strip():
                current["watchlog"][key] = value
                added.append(key)
        if added:
            _write_ini(config_path, current)
    except Exception:  # noqa: BLE001 - a config merge may never fail an upgrade
        return added
    return added


def _write_ini(path: Path, ini: configparser.ConfigParser) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        ini.write(handle)
    tmp.replace(path)


def discover_recorders(progress: Callable[[str], None] | None = None) -> list[dict]:
    progress = progress or (lambda _message: None)
    results: dict[str, dict] = {}
    progress("Looking for compatible CCTV devices…")
    try:
        for item in wsdiscovery.discover(log=lambda _m: None):
            label = " ".join(x for x in (getattr(item, "name", ""), getattr(item, "hardware", "")) if x)
            results[item.ip] = {"ip": item.ip, "label": label or "Compatible recorder", "source": "ONVIF"}
    except Exception:
        pass

    progress("Checking the local network for CCTV recorders…")
    try:
        # Keep this guard derived from discover.SWEEP_PORTS. It used to be a second
        # hardcoded list that had drifted out of sync (it accepted 81/88/443/8081 that
        # the sweep never probed), which hid HTTPS-only and alt-web-port recorders.
        candidate_ports = set(discover.SWEEP_PORTS)
        for ip, ports in discover.sweep(None, log=lambda _m: None):
            ports = sorted(ports)
            if not any(port in candidate_ports for port in ports):
                continue
            hint = "Recorder candidate"
            if any(p in ports for p in _DAHUA_SDK_PORTS):
                hint = "Dahua-family recorder candidate"
            elif 8000 in ports:
                hint = "Hikvision-family recorder candidate"
            elif 34567 in ports:
                hint = "Unsupported Xiongmai-family device"
            results.setdefault(ip, {"ip": ip, "label": hint, "source": "Network scan"})
            results[ip]["ports"] = ports
            results[ip]["vendor_hint"] = _vendor_hint_from_ports(ports)
    except Exception:
        pass
    return sorted(results.values(), key=lambda row: row["ip"])


# --- Step 04 recorder login: fast, deterministic, port/vendor-aware ---------
#
# Discovery (Step 03) already learns the open ports and the recorder family.
# The login test reuses that instead of blind-probing every driver against
# every candidate URL. A native vendor port identifies the family (37777 ->
# Dahua, 8000 -> Hikvision, 34567 -> unsupported Xiongmai); the test then tries
# the RIGHT driver on the recorder's real web port first, with a short per-probe
# timeout and a hard overall deadline so the UI can never appear to hang for
# minutes. Capabilities discovery is deliberately deferred to the background
# agent so Step 04 only proves identity + credentials + channels.

POST_CONNECT_BUDGET_SECONDS = 120   # total for ALL optional post-connection work
RECORDER_PROBE_TIMEOUT = 5          # seconds per driver probe
RECORDER_DEADLINE = 18             # seconds hard cap for the whole login test

_WEB_PORTS = (80, 8000, 8080, 81, 88, 8081, 443, 8443)
_DAHUA_SDK_PORTS = (37777, 37778)
_XIONGMAI_PORTS = (34567, 9000)

_DRIVERS_BY_VENDOR = {
    "dahua": ["dahua-cgi", "onvif"],
    "hikvision": ["hikvision-isapi", "onvif"],
}

_CUSTOMER_ERROR = {
    "wrong_credentials": "The recorder rejected that username or password.",
    "web_unreachable": "WatchLog found the recorder, but its web service is not reachable. "
                       "Check that the recorder's HTTP or HTTPS service is enabled.",
    "unsupported": "WatchLog found this recorder, but this model is not yet supported.",
    "network": "WatchLog cannot reach the recorder from this PC. Confirm this PC and the "
               "recorder are on the same local network.",
    "timeout": "The recorder did not respond in time. WatchLog found the device but could "
               "not complete the login check.",
    "connect": "WatchLog could not connect to that recorder. Check that this PC is on the "
               "same network, the recorder's web service is enabled, and the address is correct.",
}


def _vendor_hint_from_ports(ports) -> str | None:
    ps = set(ports or [])
    if ps & set(_DAHUA_SDK_PORTS):
        return "dahua"
    if 8000 in ps:
        return "hikvision"
    if ps & set(_XIONGMAI_PORTS):
        return "xiongmai"
    return None


def _ordered_drivers(vendor_hint: str | None) -> list[str]:
    if vendor_hint in _DRIVERS_BY_VENDOR:
        return list(_DRIVERS_BY_VENDOR[vendor_hint])
    return ["hikvision-isapi", "dahua-cgi", "onvif"]


def _web_target_urls(host: str, open_ports) -> list[str]:
    urls = []
    for port in _WEB_PORTS:
        if port in open_ports:
            scheme = "https" if port in (443, 8443) else "http"
            urls.append(f"{scheme}://{host}" if port in (80, 443)
                        else f"{scheme}://{host}:{port}")
    return urls


def plan_recorder_probes(host: str, open_ports, vendor_hint: str | None):
    """Return (attempts, hard_error).

    attempts is an ordered, bounded list of (driver_name, url). hard_error is a
    customer-error key when the port evidence already rules the login test out:
    an unsupported family, a recorder whose web service is not reachable (e.g.
    Dahua SDK 37777 open but no HTTP), or nothing on the network at all.
    """
    open_ports = set(open_ports or [])
    hint = vendor_hint or _vendor_hint_from_ports(open_ports)
    web_ports = [p for p in _WEB_PORTS if p in open_ports]
    has_dahua_sdk = bool(open_ports & set(_DAHUA_SDK_PORTS))
    has_xiongmai = bool(open_ports & set(_XIONGMAI_PORTS))

    if not open_ports:
        return [], "network"
    if not web_ports:
        if has_xiongmai and not has_dahua_sdk:
            return [], "unsupported"
        return [], "web_unreachable"           # Dahua SDK-only, or web disabled
    if has_xiongmai and hint == "xiongmai" and not has_dahua_sdk:
        return [], "unsupported"

    targets = _web_target_urls(host, web_ports)
    drivers = _ordered_drivers(hint)
    attempts: list[tuple[str, str]] = []
    for url in targets[:2]:                     # best web port, then one fallback
        for driver_name in drivers:
            pair = (driver_name, url)
            if pair not in attempts:
                attempts.append(pair)
    return attempts[:5], None                   # bounded: never minutes of probing


def _probe_web_ports(host: str, timeout: float | None = None, deadline: float = 6.0) -> list[int]:
    """Targeted rescue for the flaky 0.4s subnet sweep, which frequently finds a Dahua's SDK port
    (37777) but misses its slower embedded HTTP port (80). Re-probe the standard web ports on THIS
    host with the generous per-host timeout, stopping at the first that answers — one reachable web
    port is enough to proceed. Returns [] when none respond (a genuine, fail-closed web_unreachable)."""
    import socket
    budget = discover.CONNECT_TIMEOUT if timeout is None else timeout
    start = time.monotonic()
    for port in _WEB_PORTS:
        if time.monotonic() - start > deadline:
            break
        try:
            with socket.create_connection((host, port), timeout=budget):
                return [port]
        except OSError:
            continue
    return []


def _classify_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "401" in text or "unauthor" in text or "403" in text:
        return "wrong_credentials"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if ("refused" in text or "no route" in text or "unreachable" in text
            or "getaddrinfo" in text or "failed to establish" in text
            or "connection aborted" in text):
        return "web_unreachable"
    if ("no driver" in text or "not recognis" in text or "unparseable" in text
            or "http 404" in text or "returned nothing" in text):
        return "unsupported"
    return "connect"


def _redact(text: str, password: str) -> str:
    out = (text or "").splitlines()
    out = out[-1] if out else ""
    out = out[:200]
    if password:
        out = out.replace(password, "***")
    return out


def _setup_log(message: str) -> None:
    """Append one diagnostic line to setup.log. Never called with secrets."""
    try:
        path = programdata_dir() / "setup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[recorder-test] {message}\n")
    except Exception:
        pass


def test_recorder(address: str, username: str, password: str,
                  progress: Callable[[str], None] | None = None,
                  hint: dict | None = None, _scan=None, _build=None, _probe=None) -> dict:
    """Prove recorder identity + credentials + channel list — fast and bounded.

    `hint` may carry discovery metadata: {"ports": [...], "vendor_hint": "dahua"}.
    When present the network scan is skipped entirely. Capabilities discovery is
    NOT done here; it is deferred to the background agent.
    """
    progress = progress or (lambda _message: None)
    scan_fn = _scan or (lambda h: discover.scan(h, log=lambda _m: None))
    build_fn = _build or build
    probe_fn = _probe or _probe_web_ports
    if not username.strip() or not password:
        raise ValueError("Enter the recorder username and password.")

    host = discover.host_of(address)
    is_url = address.strip().lower().startswith(("http://", "https://"))
    started = time.monotonic()
    progress(f"Checking recorder at {host}…")

    hint = hint or {}
    vendor_hint = hint.get("vendor_hint")
    open_ports = list(hint.get("ports") or [])

    if is_url:
        drivers = _ordered_drivers(vendor_hint or _vendor_hint_from_ports(open_ports))
        attempts = [(driver_name, address.strip().rstrip("/")) for driver_name in drivers][:3]
        hard_error = None
    else:
        if not open_ports:
            try:
                results = scan_fn(host)
                open_ports = [r.port for r in results if getattr(r, "open", False)]
                if not vendor_hint:
                    for r in results:
                        guess = (getattr(r, "vendor_guess", "") or "").lower()
                        if "dahua" in guess or "cp plus" in guess:
                            vendor_hint = "dahua"; break
                        if "hikvision" in guess or "hilook" in guess:
                            vendor_hint = "hikvision"; break
                        if "xiongmai" in guess:
                            vendor_hint = "xiongmai"; break
            except Exception as exc:
                _setup_log(f"scan failed host={host}: {_redact(str(exc), password)}")
                open_ports = []
        # Targeted web-port rescue: a recorder was found but no web port registered. The fast 0.4s
        # subnet sweep (or a stale discovery hint) commonly misses a Dahua's slower embedded HTTP
        # port (80) while catching its SDK port (37777). Re-probe the standard web ports on THIS host
        # with the generous per-host timeout before declaring the web service unreachable. This is a
        # rescue for a false negative, NOT a weakening: if HTTP is genuinely absent it still fails closed.
        if open_ports and not any(p in open_ports for p in _WEB_PORTS):
            rescued = probe_fn(host)
            if rescued:
                _setup_log(f"web-port rescue host={host} added={rescued}")
                open_ports = sorted(set(open_ports) | set(rescued))
        attempts, hard_error = plan_recorder_probes(host, open_ports, vendor_hint)

    fam = vendor_hint or _vendor_hint_from_ports(open_ports)
    _setup_log(f"host={host} ports={sorted(open_ports)} vendor_hint={fam} "
               f"attempts={[(d, u.split('://')[-1]) for d, u in attempts]} hard={hard_error}")

    if hard_error:
        raise ValueError(_CUSTOMER_ERROR[hard_error])

    if fam == "dahua":
        progress("Detected Dahua-compatible recorder.")
    elif fam == "hikvision":
        progress("Detected Hikvision-compatible recorder.")
    else:
        progress("Detected a compatible recorder.")

    last_class = "connect"
    for driver_name, url in attempts:
        if time.monotonic() - started > RECORDER_DEADLINE:
            last_class = "timeout"
            break
        progress("Signing in to the recorder…")
        driver = None
        attempt_started = time.monotonic()
        try:
            driver = build_fn(driver_name, url, username.strip(), password,
                              RECORDER_PROBE_TIMEOUT)
            info = driver.probe()
            progress("Reading camera channels…")
            channels = driver.list_channels()
            _setup_log(f"OK driver={driver_name} identity=1 channels={len(channels)} "
                       f"elapsed={time.monotonic() - attempt_started:.1f}s")
            return {
                "url": url,
                "vendor": info.vendor or "Recorder",
                "model": info.model or "Unknown model",
                "firmware": info.firmware or "",
                "driver": driver.name,
                "verified_against_hardware": bool(driver.verified_against_hardware),
                "channels": [{"channel": str(row.channel),
                              "name": row.name or f"Camera {row.channel}"}
                             for row in channels],
                "capabilities": None,           # deferred to the background agent
            }
        except Exception as exc:   # noqa: BLE001 — classify, do not mask
            cls = _classify_exception(exc)
            _setup_log(f"fail driver={driver_name} class={cls} "
                       f"elapsed={time.monotonic() - attempt_started:.1f}s "
                       f"detail={_redact(str(exc), password)}")
            if cls == "wrong_credentials":
                raise ValueError(_CUSTOMER_ERROR["wrong_credentials"]) from None
            last_class = cls
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass

    raise ValueError(_CUSTOMER_ERROR.get(last_class, _CUSTOMER_ERROR["connect"]))


def verify_recorder_archive(url: str, driver_name: str, username: str, password: str,
                            channels, *, now=None, window_seconds: int = dahua_archive.ARCHIVE_PROOF_WINDOW,
                            _build=None, _install=None) -> dict:
    """0.4.4 §6 — setup-time archive PROOF wrapper.

    ``test_recorder`` already proved identity + credentials + channels; this reuses the proven
    ``url``/``driver_name`` to build ONE driver, installs the validated archive implementation,
    and proves retrievable recorded footage on the first channel via
    :func:`dahua_archive.prove_recorder_archive`. Bounded, read-only, and NEVER raises — an
    archive check must never block or crash a setup that otherwise succeeded.
    """
    build_fn = _build or build
    install_fn = _install or dahua_archive.install
    channel = None
    for cam in (channels or []):
        channel = cam.get("channel") if isinstance(cam, dict) else getattr(cam, "channel", None)
        if channel:
            break
    if not channel:
        return {"status": "unknown", "channel": None, "segments_found": 0, "sample": [],
                "window_seconds": int(window_seconds),
                "detail": "No camera channel was available to check the archive."}

    try:
        install_fn()                 # idempotent: patches the driver class with the archive impl
    except Exception:  # noqa: BLE001 — a driver without this impl degrades to 'unsupported' below
        pass

    driver = None
    try:
        driver = build_fn(driver_name, url, username.strip(), password, RECORDER_PROBE_TIMEOUT)
        return dahua_archive.prove_recorder_archive(driver, channel, now=now,
                                                    window_seconds=window_seconds)
    except Exception:  # noqa: BLE001 — never let the archive proof crash setup
        return {"status": "unknown", "channel": str(channel), "segments_found": 0, "sample": [],
                "window_seconds": int(window_seconds),
                "detail": "The recorder archive could not be checked during setup."}
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001
                pass


def suggest_purpose(camera_name: str, site_type: str) -> str:
    name = (camera_name or "").lower()
    heuristics = [
        (("load", "dock", "bay"), "loading_bay"),
        (("till", "checkout", "cash", "counter"), "checkout_till"),
        (("reception", "lobby"), "reception"),
        (("parking", "car park"), "parking"),
        (("perimeter", "boundary", "fence"), "perimeter"),
        (("corridor", "hall"), "corridor"),
        (("gate",), "main_gate"),
        (("entry", "entrance", "door"), "entrance_exit"),
    ]
    for words, purpose in heuristics:
        if any(word in name for word in words):
            return purpose
    return DEFAULTS_BY_SITE.get(site_type, "custom")


def _write_proven_config(config_path: Path, public: dict, enrollment_code: str,
                         recorder: dict, username: str, site_type: str,
                         profiles: list[dict]) -> None:
    ini = configparser.ConfigParser()
    ini.add_section("watchlog")
    section = ini["watchlog"]
    section["supabase_url"] = public["supabase_url"].rstrip("/")
    section["supabase_publishable_key"] = public["supabase_publishable_key"]
    section["enrollment_code"] = enrollment_code.strip()
    section["nvr_url"] = recorder["url"]
    # Persist the driver that was just PROVEN against this exact recorder, not "auto".
    # Writing "auto" threw that away and made every later probe (the agent at boot, the
    # acceptance suite) re-walk the vendor list -- trying Hikvision paths against a Dahua
    # box, costing time and producing confusing failures on a recorder we had identified.
    section["nvr_driver"] = recorder.get("driver") or "auto"
    # The recorder credential (username + password) is stored atomically in the
    # encrypted Secrets store, never in this INI.
    section["nvr_password_protected"] = "dpapi-secrets"
    section["site_type"] = site_type
    if public.get("push_bridge_url"):
        section["push_bridge_url"] = str(public["push_bridge_url"]).rstrip("/")
    section["camera_profiles_json"] = json.dumps(profiles, separators=(",", ":"))
    _write_ini(config_path, ini)


def _clear_consumed_code(config_path: Path) -> bool:
    """Blank the consumed enrollment code. MUST NOT be able to fail the install.

    0.4.9: the background agent is now started BEFORE this runs, and it holds
    watchlog.ini open. On Windows os.replace() onto a file another process has open
    raises PermissionError, so the atomic write used here could turn a perfectly good,
    already-connected install into a failure. A stale code in the ini is harmless -- it
    is single-use and the server has already consumed it -- so this is best-effort:
    retry briefly, fall back to an in-place rewrite, and give up quietly rather than
    take down a working site.
    """
    try:
        ini = configparser.ConfigParser()
        ini.read(config_path, encoding="utf-8-sig")
        if not ini.has_section("watchlog"):
            return True
        ini["watchlog"]["enrollment_code"] = ""
        for attempt in range(3):
            try:
                _write_ini(config_path, ini)
                return True
            except OSError:
                time.sleep(0.5 * (attempt + 1))
        # Last resort: rewrite in place (no rename), which does not need the
        # destination to be unopened by other processes.
        with config_path.open("w", encoding="utf-8", newline=chr(10)) as handle:
            ini.write(handle)
        return True
    except Exception:  # noqa: BLE001 — a cosmetic tidy-up may never fail an install
        return False


class AgentSyncError(ValueError):
    """A classified, customer-safe setup failure. `category` is a stable internal
    code (CAMERA_SYNC_AUTH_FAILED, CAMERA_SYNC_CONSTRAINT_FAILED, ENROLL_REQUIRED, …);
    the message is safe to show the operator. Subclasses ValueError so existing
    `except ValueError` handling and the setup worker still surface it."""
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _load_existing_identity(state_path: Path) -> dict | None:
    """Load a local agent identity if present AND usable, else None. Unlike the agent
    runtime, setup must never hard-FATAL on a corrupt/absent key: an unusable local
    identity just means 'not enrolled here yet', and setup recovers by (re)enrolling
    with the operator's site code."""
    try:
        return core.load_state(state_path)
    except SystemExit:
        _setup_log("agent_state present but its key is unreadable; treating as not enrolled")
        return None
    except Exception as exc:                       # noqa: BLE001 — stale state must never break setup
        _setup_log(f"agent_state unreadable ({type(exc).__name__}); treating as not enrolled")
        return None


def _enroll(cloud, enrollment_code: str, device) -> dict | None:
    """Claim the one-time code -> a fresh, correctly-bound identity, or None if the code
    is unknown / already-used / expired (22023). wl_enroll returns the code's OWN site,
    so honouring the code also transparently rebinds a PC set up for a different site
    than any stale local identity. Network/other errors raise ENROLL_NETWORK."""
    try:
        r = cloud.call(
            "wl_enroll", p_code=enrollment_code.strip(), p_hostname=platform.node(),
            p_platform=f"{platform.system()} {platform.release()}", p_agent_version=SETUP_AGENT_VERSION,
            p_device_vendor=device.vendor, p_device_model=device.model, p_device_driver=device.driver)
    except core.CloudError as exc:
        if exc.code == "22023" or (exc.status == 400 and "code" in str(exc.message).lower()):
            return None
        raise AgentSyncError("ENROLL_NETWORK",
            "WatchLog could not verify this site code. Check the internet connection and try again.") from exc
    except Exception as exc:                       # noqa: BLE001 — transport/timeout
        raise AgentSyncError("ENROLL_NETWORK",
            "WatchLog could not reach the cloud to verify this site code. Check the internet connection and try again.") from exc
    return {"agent_id": r["agent_id"], "agent_key": r["agent_key"], "tenant_id": r["tenant_id"],
            "site_id": r["site_id"], "enrolled_at": core.iso(core.now_utc()),
            "agent_version": SETUP_AGENT_VERSION}


def establish_identity(cloud, state_path: Path, enrollment_code: str, device,
                       progress: Callable[[str], None] | None = None) -> dict:
    """Return a cloud-VALID agent identity for this PC.

    Fixes the field failure where a stale local identity made setup skip enrollment
    and then sync cameras against a defunct agent:
      1. Honour the supplied site code first. An UNUSED code enrolls -> a new identity
         bound to THAT code's site (also correct when the PC is moved to a different
         site than a stale local identity). Adopting it overwrites agent_state.json +
         Secrets/agent_key.dpapi (via save_state); Secrets/nvr_credential.dpapi is kept.
      2. If the code is spent/invalid, reuse the existing local identity ONLY if it still
         authenticates in the cloud (heartbeat) — never reuse a defunct agent. Preserves
         the legitimate retry (enroll already consumed the code; camera sync failed).
      3. Otherwise raise an actionable ENROLL_REQUIRED error.
    """
    progress = progress or (lambda _m: None)
    existing = _load_existing_identity(state_path)

    fresh = _enroll(cloud, enrollment_code, device)
    if fresh is not None:
        core.save_state(state_path, fresh)         # overwrite stale identity+key; nvr credential preserved
        _setup_log(f"enrolled new agent for site={fresh['site_id']} (code consumed)"
                   + ("; retired stale local identity" if existing and existing.get("agent_id") != fresh["agent_id"] else ""))
        return fresh

    if existing:
        try:
            core.heartbeat(cloud, existing, device)     # wl_heartbeat: 28000 if the agent is gone
            _setup_log(f"reusing existing agent for site={existing.get('site_id')} (code already used; retry)")
            return existing
        except core.CloudError as exc:
            if exc.code == "28000" or exc.status in (401, 403):
                _setup_log("local identity is defunct AND the site code is already used/expired")
            else:
                raise AgentSyncError("ENROLL_NETWORK",
                    "WatchLog could not confirm this PC's identity. Check the internet connection and try again.") from exc
        except Exception as exc:                   # noqa: BLE001
            raise AgentSyncError("ENROLL_NETWORK",
                "WatchLog could not reach the cloud to confirm this PC's identity. Check the internet connection and try again.") from exc

    raise AgentSyncError("ENROLL_REQUIRED",
        "This site code could not be used (unknown, already used, or expired) and there is no active WatchLog "
        "identity on this PC. Get a current site code from the WatchLog portal, then run setup again.")


def _classify_camera_sync(exc) -> AgentSyncError:
    code, status = getattr(exc, "code", None), getattr(exc, "status", 0)
    if code == "28000" or status in (401, 403):
        return AgentSyncError("CAMERA_SYNC_AUTH_FAILED",   # the PC's WatchLog identity, NOT the recorder password
            "WatchLog did not accept this PC's identity while adding cameras. Run WatchLog Setup again to re-link this site.")
    if code == "23503":
        return AgentSyncError("CAMERA_SYNC_CONSTRAINT_FAILED",
            "This WatchLog site is no longer available. Run WatchLog Setup again with a current site code.")
    if code == "23505":
        return AgentSyncError("CAMERA_SYNC_CONSTRAINT_FAILED",
            "WatchLog hit a conflict while adding cameras. Run WatchLog Setup again.")
    if code == "PGRST202" or status == 404:
        return AgentSyncError("CAMERA_SYNC_SCHEMA_MISMATCH",
            "This WatchLog account needs an update before cameras can be added. Contact WatchLog support.")
    return AgentSyncError("CAMERA_SYNC_FAILED",
        "WatchLog linked this site but could not add the cameras. Please try again; if it persists, contact WatchLog support.")


def merge_camera_config(channels: list, profiles: list) -> list:
    """Merge the operator's Monitor/Ignore + name choices (0.4.4 P4) into the wl_sync_cameras
    payload: is_configured = the channel's monitored flag. A discovered channel with no explicit
    profile stays monitored (the operator saw it in discovery); a channel the operator marked Ignore
    becomes is_configured=false and never generates a false health warning. Pure/testable."""
    by_ch = {str(p.get("channel", "")).strip(): p for p in (profiles or [])
             if str(p.get("channel", "")).strip()}
    out = []
    for c in (channels or []):
        ch = str(c.get("channel", "")).strip()
        if not ch:
            continue
        prof = by_ch.get(ch, {})
        out.append({"channel": ch,
                    "name": (prof.get("name") or c.get("name") or ""),
                    "is_configured": bool(prof.get("monitored", True))})
    return out


def sync_cameras(cloud, identity: dict, channels: list, progress: Callable[[str], None] | None = None) -> dict:
    """Idempotently reconcile the discovered channels into WatchLog (server-side
    ON CONFLICT (site_id, channel) DO UPDATE). Never reports success on failure; every
    failure is classified. Camera creation NEVER blames the recorder credential."""
    progress = progress or (lambda _m: None)
    if not channels:
        raise AgentSyncError("CAMERA_ENUMERATION_FAILED",
            "WatchLog could not read any camera channels from the recorder. Check the recorder is online and try again.")
    try:
        mapping = cloud.call("wl_sync_cameras", p_agent_id=identity["agent_id"],
                             p_agent_key=identity["agent_key"], p_cameras=channels)
    except core.CloudError as exc:
        err = _classify_camera_sync(exc)
        _setup_log(f"camera sync {err.category} site={identity.get('site_id')} channels={len(channels)} "
                   f"http={getattr(exc, 'status', 0)} code={getattr(exc, 'code', None)}")
        raise err from exc
    except Exception as exc:                       # noqa: BLE001 — transport/timeout
        cat = "CAMERA_SYNC_TIMEOUT" if "timeout" in type(exc).__name__.lower() else "CAMERA_SYNC_FAILED"
        _setup_log(f"camera sync {cat} site={identity.get('site_id')} channels={len(channels)} ({type(exc).__name__})")
        raise AgentSyncError(cat,
            "WatchLog could not reach the cloud to add the cameras. Check the internet connection and try again.") from exc
    mapping = mapping or {}
    wanted = {str(c.get("channel", "")).strip() for c in channels if str(c.get("channel", "")).strip()}
    present = {str(k) for k in mapping.keys()}
    missing = wanted - present
    if missing:
        # Never report success when the cloud did not confirm every discovered channel.
        _setup_log(f"camera sync CAMERA_SYNC_PARTIAL site={identity.get('site_id')} "
                   f"created={len(wanted & present)} of {len(wanted)} missing={sorted(missing)}")
        raise AgentSyncError("CAMERA_SYNC_PARTIAL",
            "WatchLog added some but not all of the recorder's cameras. Please try again; "
            "if it persists, contact WatchLog support.")
    _setup_log(f"camera sync ok site={identity.get('site_id')} cameras={len(wanted)} (site total {len(mapping)})")
    return mapping


def ensure_background_agent(install_dir: Path | None = None, timeout: int = 120,
                            _run=None) -> dict:
    """Register and START the background agent as soon as the site is genuinely connected.

    WHY THIS EXISTS (0.4.7). The NSIS installer runs the setup wizard under ExecWait and
    only registers the background task AFTERWARDS. So anything that stops the wizard from
    exiting -- a wedged probe, a customer closing the window, a crash -- means
    register-service.ps1 never runs, the scheduled task is never created, and the site
    enrols, heartbeats exactly once from setup, and is then offline forever. That is
    precisely what the field showed: agents at 0.4.1/0.4.5/0.4.6 each last seen 3-20
    seconds after enrolling, while 0.4.3 -- whose wizard completed -- ran for three days.

    Connectivity must not depend on a later, slower, failure-prone verification step. Once
    enrollment and the recorder credential exist, the site can and should start reporting.
    Acceptance is a REPORT, not a gate on whether the agent runs.

    Safe to call twice: register-service.ps1 uses Register-ScheduledTask -Force, and the
    installer still runs it again afterwards.

    Fail-open and never raises -- returns {"started": bool, "detail": str}.
    """
    if os.name != "nt":
        return {"started": False, "detail": "background registration is Windows-only"}
    base = Path(install_dir) if install_dir else Path(sys.executable).resolve().parent
    script = base / "register-service.ps1"
    if not script.exists():
        return {"started": False, "detail": f"register-service.ps1 not found beside {base}"}

    runner = _run
    if runner is None:
        # SHARED hardened runner. The obvious subprocess.run(capture_output=True,
        # timeout=...) does NOT bound anything when the child leaves a survivor holding
        # the pipe -- that is what hung the 0.4.7 wizard on step 06 from right here.
        import proc_util

        def runner(cmd, timeout):
            return proc_util.run_bounded(cmd, timeout)

    powershell = (Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
                  / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    cmd = [str(powershell),
           "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
           "-File", str(script), "-InstallDir", str(base)]
    try:
        code, out = runner(cmd, timeout)
    except Exception as exc:  # noqa: BLE001 — never block a connected site
        return {"started": False, "detail": f"could not start background agent ({type(exc).__name__})"}
    if code == 0:
        return {"started": True, "detail": "background agent registered and started"}
    return {"started": False,
            "detail": f"background registration exited {code}: {(out or '').strip()[:160]}"}


def confirm_background_agent(timeout: float = 20.0, since_offset: int | None = None,
                             log_path: Path | None = None, _sleep=None) -> dict:
    """Best-effort: has the background agent written a heartbeat since we started it?

    NOT ON THE CRITICAL PATH, deliberately. 0.4.8 blocked setup for 75s waiting on this
    and then reported a HEALTHY agent as failed, because run-agent.ps1 captured the agent
    through a PowerShell redirection that does not reach disk promptly. The agent was
    heartbeating to the cloud the whole time; only the local file was stale.

    0.4.9 fixes that redirection so the log streams, which makes this signal meaningful
    again -- but it stays advisory. Whether a site reports is proven by the scheduled task
    running, and ultimately by the cloud, never by the presence of a local log line.
    """
    path = Path(log_path) if log_path else (programdata_dir() / "agent.log")
    sleep = _sleep or time.sleep
    start = since_offset if since_offset is not None else _log_size(path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(start)
                    if "heartbeat ok" in handle.read():
                        return {"confirmed": True,
                                "detail": "background agent is reporting to WatchLog"}
        except OSError:
            pass
        sleep(2)
    return {"confirmed": False,
            "detail": f"no background heartbeat seen locally within {int(timeout)}s "
                      "(not conclusive -- the agent may still be reporting)"}


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def provision_recorder_push(cloud, state: dict, recorder: dict, public: dict,
                            username: str, password: str,
                            progress: Callable[[str], None] | None = None,
                            _build=None) -> dict:
    """Point the RECORDER itself at WatchLog, so the site keeps reporting with no PC.

    This is the 0013 "PC-free" path, wired into setup. The wizard is the only thing
    that is ever on the recorder's LAN holding recorder credentials, so it is the
    right place to do this. Afterwards the recorder POSTs its own alarms to the push
    bridge and the site survives this PC being shut down, uninstalled or rebuilt.

    FAIL-OPEN BY DESIGN. Push is a resilience bonus layered on top of a working
    agent install; a recorder that cannot do it is normal and common. Nothing here
    may raise, and nothing here may make an otherwise-good install look failed.

    Returns {"configured": bool, "verified": bool, "detail": str}. ``verified`` is
    only true when the recorder CONFIRMED the config on read-back -- a site that
    believes it is covered and is not would be worse than no push at all.
    """
    progress = progress or (lambda _message: None)
    base = (public.get("push_bridge_url")
            or os.environ.get("WATCHLOG_PUSH_BRIDGE_URL") or "").strip().rstrip("/")
    if not base:
        return {"configured": False, "verified": False,
                "detail": "no push bridge configured in this build"}

    try:
        progress("Setting up PC-free reporting on the recorder…")
        issued = cloud.call("wl_agent_issue_push_token",
                            p_agent_id=state["agent_id"], p_agent_key=state["agent_key"])
        token = (issued or {}).get("token") if isinstance(issued, dict) else None
        if not token:
            return {"configured": False, "verified": False,
                    "detail": "WatchLog did not issue a push token"}

        build_fn = _build or build
        driver = build_fn(recorder["driver"], recorder["url"], username.strip(), password,
                          RECORDER_PROBE_TIMEOUT)
        configure = getattr(driver, "configure_push", None)
        if configure is None:
            return {"configured": False, "verified": False,
                    "detail": f"{recorder.get('vendor') or 'this recorder'} does not support "
                              "recorder-push; the site agent will report instead"}

        out = configure(f"{base}/push/{token}") or {}
        return {"configured": bool(out.get("applied")),
                "verified": bool(out.get("verified")),
                "detail": str(out.get("detail") or "")}
    except Exception as exc:  # noqa: BLE001 — resilience bonus, never a setup failure
        return {"configured": False, "verified": False,
                "detail": f"could not configure recorder push ({type(exc).__name__})"}


def finalize_install(config_path: Path, public: dict, enrollment_code: str,
                     address: str, username: str, password: str, site_type: str,
                     profiles: list[dict], progress: Callable[[str], None] | None = None,
                     hint: dict | None = None) -> dict:
    """Prove local recorder + WatchLog enrollment and persist only protected secrets."""
    progress = progress or (lambda _message: None)
    if not public.get("supabase_url") or not public.get("supabase_publishable_key"):
        raise ValueError("This installer is missing its WatchLog public connection settings.")
    if not enrollment_code.strip():
        raise ValueError("Enter the WatchLog site code from the portal.")

    progress("Verifying the recorder one more time…")
    recorder = test_recorder(address, username, password, progress=progress, hint=hint)

    progress("Encrypting recorder credentials on this PC…")
    try:
        credential_store.save_nvr_credential(username.strip(), password)
    except SecretError as exc:
        raise ValueError("Windows could not securely store the recorder credential on this PC.") from exc
    _write_proven_config(config_path, public, enrollment_code, recorder, username, site_type, profiles)

    progress("Connecting this site to WatchLog…")
    cloud = core.Cloud(public["supabase_url"].rstrip("/"), public["supabase_publishable_key"])
    state_path = programdata_dir() / "agent_state.json"
    device = SimpleNamespace(vendor=recorder["vendor"], model=recorder["model"],
                             driver=recorder["driver"])
    # Honour the supplied site code first; only reuse a local identity that still
    # authenticates. Never skip enrollment just because a stale agent_state.json exists.
    state = establish_identity(cloud, state_path, enrollment_code, device, progress)

    progress("Adding cameras to this WatchLog site…")
    # Honor the operator's Monitor/Ignore + name choices so monitored cameras are configured
    # immediately and ignored channels never raise a false health warning (0.4.4 P4).
    mapping = sync_cameras(cloud, state, merge_camera_config(recorder["channels"], profiles), progress)

    capabilities = recorder.get("capabilities")
    if capabilities and capabilities.get("channels"):
        progress("Confirming camera capabilities…")
        try:
            cloud.call("wl_sync_capabilities", p_agent_id=state["agent_id"],
                       p_agent_key=state["agent_key"], p_capabilities=capabilities)
        except Exception:
            pass

    if site_type or profiles:
        progress("Applying camera purposes…")
        try:
            cloud.call("wl_agent_bootstrap_analytics", p_agent_id=state["agent_id"],
                       p_agent_key=state["agent_key"], p_site_type=site_type or "custom",
                       p_camera_profiles=profiles)
        except Exception:
            # The background agent will retry this bootstrap from the local
            # config after production schema alignment. It is enrichment, not
            # a reason to lie that recorder/enrollment failed.
            pass

    progress("Confirming the WatchLog connection…")
    try:
        core.heartbeat(cloud, state, device)
    except Exception as exc:
        raise ValueError("WatchLog linked the site but could not confirm the final connection. Try again.") from exc

    # =================================================================
    # THE SITE IS NOW CONNECTED: enrolled, credential stored, heartbeat proven.
    # EVERYTHING BELOW IS OPTIONAL and runs under ONE hard deadline.
    #
    # Four separate hangs shipped in this stretch of code (0.4.5 acceptance, 0.4.7
    # pipe deadlock, 0.4.7 GUI thread, 0.4.9 ini lock). Fixing them one at a time
    # was not working, because the real defect is the SHAPE: optional post-connection
    # work was able to pin the wizard forever. So the budget is now structural --
    # whatever is unfinished when it expires is simply reported as unfinished, and
    # setup always reaches a final screen.
    # =================================================================
    optional_deadline = time.monotonic() + POST_CONNECT_BUDGET_SECONDS

    def _remaining(cap: float) -> float:
        return max(0.0, min(cap, optional_deadline - time.monotonic()))

    progress("Starting WatchLog in the background…")
    # NOT optional work, and NOT drawn from the optional budget. 0.4.9 gave registration
    # whatever was LEFT of the 120s, so a slow recorder probe could hand it a fraction of a
    # second and it was taskkill'd mid-registration -- the one step that makes the site
    # survive a reboot. It gets its own guaranteed floor.
    agent_start = ensure_background_agent(timeout=max(60.0, _remaining(90)))
    core.log(f"background agent start: {agent_start.get('detail')}")
    connected = bool(agent_start.get("started"))

    push = {"configured": False, "verified": False, "detail": "skipped (time budget)"}
    if _remaining(1) > 0:
        push = provision_recorder_push(cloud, state, recorder, public, username, password,
                                       progress=progress)

    # The field outcome of PC-free reporting was computed and then thrown away -- never
    # logged, never shown. That is the second reason nobody noticed the bridge was dead.
    core.log(f"recorder push: configured={push.get('configured')} "
             f"verified={push.get('verified')} {push.get('detail')}")

    cleared = _clear_consumed_code(config_path)
    core.log(f"post-connect phase done in "
             f"{POST_CONNECT_BUDGET_SECONDS - max(0.0, optional_deadline - time.monotonic()):.0f}s "
             f"(agent_started={connected} code_cleared={cleared})")
    return {
        "site_id": state["site_id"],
        "recorder_push": push,
        "agent_start": agent_start,
        "connected": connected,
        "camera_count": len(mapping or recorder["channels"]),
        "vendor": recorder["vendor"],
        "model": recorder["model"],
        "verified_against_hardware": recorder["verified_against_hardware"],
    }

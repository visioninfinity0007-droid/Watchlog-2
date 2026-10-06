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
import recorder_registry
import recorder_runtime
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
            vendor_hint = _vendor_hint_from_text(label)
            row = {
                "ip": item.ip,
                "label": label or "Compatible recorder",
                "source": "ONVIF",
                "vendor_hint": vendor_hint,
            }
            if getattr(item, "port", None) in _WEB_PORTS:
                row["preferred_web_port"] = int(item.port)
            results[item.ip] = row
    except Exception:
        pass

    progress("Checking the local network for CCTV recorders…")
    try:
        # Keep this guard derived from discover.SWEEP_PORTS. It used to be a second
        # hardcoded list that had drifted out of sync (it accepted 81/88/443/8081 that
        # the sweep never probed), which hid HTTPS-only and alt-web-port recorders.
        candidate_ports = set(discover.SWEEP_PORTS)
        for ip, ports in discover.sweep(None, log=lambda _m: None, progress=progress):
            ports = sorted(ports)
            if not any(port in candidate_ports for port in ports):
                continue

            # ONVIF may be disabled. Fingerprint only the ports we already know
            # are open, using read-only HTTP/HTTPS banners/auth realms.
            try:
                fp = discover.fingerprint(ip, ports)
            except Exception:
                fp = {"vendor_guess": None, "rtsp": 554 in ports, "web": []}

            vendor_hint = (_vendor_hint_from_ports(ports)
                           or _vendor_hint_from_text(fp.get("vendor_guess")))
            integration_state = None
            integration_port = None
            if not vendor_hint and any(p in ports for p in _WEB_PORTS):
                deep = discover.probe_hikvision_isapi(ip, ports)
                if deep.get("vendor_hint") == "hikvision":
                    vendor_hint = "hikvision"
                    integration_state = deep.get("state")
                    integration_port = deep.get("port")
            hint = "Recorder candidate"
            if vendor_hint == "dahua":
                hint = "Dahua-family recorder candidate"
            elif vendor_hint == "hikvision":
                hint = "Hikvision-family recorder candidate"
            elif vendor_hint == "uniview":
                hint = "Uniview recorder candidate"
            elif vendor_hint == "tiandy":
                hint = "Tiandy recorder candidate"
            elif vendor_hint == "xiongmai":
                hint = "Unsupported Xiongmai-family device"
            elif fp.get("rtsp"):
                hint = "RTSP CCTV device / recorder candidate"

            row = results.setdefault(ip, {"ip": ip, "label": hint, "source": "Network scan"})
            # Prefer the stronger non-ONVIF fingerprint when it identifies the box.
            if vendor_hint:
                row["label"] = hint
                row["source"] = "Network fingerprint"
            row["ports"] = ports
            row["vendor_hint"] = vendor_hint
            row["rtsp"] = bool(fp.get("rtsp"))

            # Preserve the web endpoint that actually identified/answered as the first
            # login target. Build 69 blindly preferred port 80 from a numeric list even
            # when fingerprinting had already proven HTTPS/another port, wasting a full
            # native-auth timeout before trying the useful endpoint.
            web_rows = list(fp.get("web") or [])
            preferred = None
            if vendor_hint:
                for web_row in web_rows:
                    guess = _vendor_hint_from_text(web_row.get("vendor_guess"))
                    if guess == vendor_hint and web_row.get("status") is not None:
                        preferred = web_row.get("port")
                        break
            if preferred is None:
                for web_row in web_rows:
                    if web_row.get("status") is not None:
                        preferred = web_row.get("port")
                        break
            if preferred in _WEB_PORTS:
                row["preferred_web_port"] = int(preferred)

            if integration_state:
                row["integration_state"] = integration_state
                row["source"] = "Hikvision ISAPI probe"
            if vendor_hint == "hikvision" and integration_port in _WEB_PORTS:
                row["preferred_web_port"] = int(integration_port)
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

BACKGROUND_START_TIMEOUT_SECONDS = 40  # registration + task start (no readiness wait)
# Fresh install: registration + start + up to 60 s for the background Agent to prove it
# reached WatchLog and identified the recorder (register-service -RequireRecorderReadiness).
BACKGROUND_READY_TIMEOUT_SECONDS = 100
RECORDER_PROBE_TIMEOUT = 5           # seconds per driver probe
RECORDER_DEADLINE = 18              # backend target; GUI has a 30s hard UX watchdog

_WEB_PORTS = (80, 8080, 81, 82, 88, 8081, 8888, 443, 8443)
_HIKVISION_SDK_PORTS = (8000,)
_DAHUA_SDK_PORTS = (37777, 37778)
_XIONGMAI_PORTS = (34567, 9000)

_DRIVERS_BY_VENDOR = {
    "dahua": ["dahua-cgi", "onvif"],
    "hikvision": ["hikvision-isapi", "onvif"],
}

_CUSTOMER_ERROR = {
    "wrong_credentials": "The recorder rejected that username or password.",
    "hikvision_integration_auth":
        "The Hikvision recorder answered, but its integration API rejected this login. "
        "If the same login works in the normal browser page, enable ISAPI and set HTTP "
        "Authentication to Digest (or Digest/Basic) in Hikvision System Service, then retry.",
    "hikvision_integration_unavailable":
        "This looks like a Hikvision recorder, but WatchLog could not reach an enabled ISAPI/ONVIF "
        "integration service. Enable ISAPI in Hikvision System Service (or ONVIF Integration "
        "Protocol) and restart the recorder if its settings require it, then retry.",
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
    if ps & set(_HIKVISION_SDK_PORTS):
        return "hikvision"
    if ps & set(_XIONGMAI_PORTS):
        return "xiongmai"
    return None


def _vendor_hint_from_text(value: str | None) -> str | None:
    text = (value or "").lower()
    if any(token in text for token in ("dahua", "cp plus", "imou")):
        return "dahua"
    if any(token in text for token in ("hikvision", "hilook", "ds-")):
        return "hikvision"
    if any(token in text for token in ("xiongmai", "xmeye", "netsurveillance")):
        return "xiongmai"
    if any(token in text for token in ("uniview", "unv")):
        return "uniview"
    if "tiandy" in text:
        return "tiandy"
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


def plan_recorder_probes(host: str, open_ports, vendor_hint: str | None,
                         preferred_port: int | None = None):
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
        return [], "web_unreachable"           # vendor SDK/RTSP found, but HTTP(S) control is disabled
    if has_xiongmai and hint == "xiongmai" and not has_dahua_sdk:
        return [], "unsupported"

    targets = _web_target_urls(host, web_ports)
    if preferred_port in web_ports:
        preferred_url = _web_target_urls(host, [preferred_port])[0]
        targets = [preferred_url] + [url for url in targets if url != preferred_url]

    drivers = _ordered_drivers(hint)
    attempts: list[tuple[str, str]] = []

    # Build 62 changed this to exhaust the native driver on HTTP *and* HTTPS before
    # trying ONVIF. On field Dahua firmware a dead/slow secondary web endpoint consumes
    # another full timeout; Build 69 could sometimes finish just under 30 seconds, while
    # the 24s/22s watchdogs in Builds 70/71 cut the same valid login off every time.
    #
    # Keep the native API first (so a healthy Dahua/Hikvision never downgrades), but
    # fall back on the SAME proven web endpoint before spending time on a second port.
    # Only then try the alternate endpoint. This preserves native preference without
    # serially stacking avoidable timeouts.
    for url in targets[:2]:
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


# test_recorder's display placeholders for a vendor or model the recorder did not report.
# They are for the Setup screens only: unknown stays unknown in recorders.json and in the
# descriptors synced to WatchLog.
VENDOR_PLACEHOLDER = "Recorder"
MODEL_PLACEHOLDER = "Unknown model"


def _observed(recorder: dict, key: str) -> str | None:
    """The recorder's reported vendor or model, or None when it reported none."""
    value = str((recorder or {}).get(key) or "").strip()
    placeholder = VENDOR_PLACEHOLDER if key == "vendor" else MODEL_PLACEHOLDER
    return value if value and value != placeholder else None


def test_recorder(address: str, username: str, password: str,
                  progress: Callable[[str], None] | None = None,
                  hint: dict | None = None, _scan=None, _build=None, _probe=None,
                  _hik_probe=None) -> dict:
    """Prove recorder identity + credentials + channel list — fast and bounded.

    `hint` may carry discovery metadata: {"ports": [...], "vendor_hint": "dahua"}.
    When present the network scan is skipped entirely. Capabilities discovery is
    NOT done here; it is deferred to the background agent.
    """
    progress = progress or (lambda _message: None)
    scan_fn = _scan or (lambda h: discover.scan(h, log=lambda _m: None))
    build_fn = _build or build
    probe_fn = _probe or _probe_web_ports
    hik_probe_fn = _hik_probe or discover.probe_hikvision_isapi
    if not username.strip() or not password:
        raise ValueError("Enter the recorder username and password.")

    host = discover.host_of(address)
    is_url = address.strip().lower().startswith(("http://", "https://"))
    started = time.monotonic()
    progress(f"Checking recorder at {host}…")

    hint = hint or {}
    vendor_hint = hint.get("vendor_hint")
    integration_state = hint.get("integration_state")
    preferred_web_port = hint.get("preferred_web_port")
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
                        parsed = _vendor_hint_from_text(guess)
                        if parsed:
                            vendor_hint = parsed
                            break
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

        # Salman field path: discovery may initially see only RTSP, then the targeted
        # rescue finds the web port. Re-identify AFTER rescue so Hikvision gets the
        # Hikvision/ONVIF route and actionable integration diagnostics instead of the
        # generic vendor loop.
        if not vendor_hint and any(p in open_ports for p in _WEB_PORTS):
            deep = hik_probe_fn(host, open_ports) or {}
            if deep.get("vendor_hint") == "hikvision":
                vendor_hint = "hikvision"
                integration_state = deep.get("state") or integration_state

        attempts, hard_error = plan_recorder_probes(
            host, open_ports, vendor_hint, preferred_port=preferred_web_port)

    fam = vendor_hint or _vendor_hint_from_ports(open_ports)
    _setup_log(f"host={host} ports={sorted(open_ports)} vendor_hint={fam} "
               f"preferred_web_port={preferred_web_port} "
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
    hikvision_auth_rejected = False
    hikvision_api_unavailable = (vendor_hint == "hikvision" and integration_state == "unavailable")
    for driver_name, url in attempts:
        # If ISAPI itself issued an authentication rejection, this host is now a
        # strong Hikvision candidate. Do not let a generic Dahua attempt overwrite
        # that diagnosis with another 401 before we try the standards fallback.
        if hikvision_auth_rejected and driver_name not in ("hikvision-isapi", "onvif"):
            continue
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

            # Setup login is an AUTHENTICATION check, not a full inventory crawl.
            # Field Hikvision DS-7608NI-Q1 and Dahua embedded web stacks can accept
            # Digest auth/deviceInfo quickly, then stall on extra channel/config reads.
            # Once the native identity call succeeds, trust the recorder's reported
            # physical input count and let the background agent enrich names later.
            # This keeps a correct password from being turned into a false 30s timeout.
            if driver_name in ("hikvision-isapi", "dahua-cgi") and info.channel_count:
                progress("Recorder login verified.")
                channels = [
                    SimpleNamespace(channel=str(i), name=f"Camera {i}")
                    for i in range(1, int(info.channel_count) + 1)
                ]
                _setup_log(
                    f"setup-fast-path driver={driver_name} "
                    f"reported_channels={len(channels)}")
            else:
                progress("Reading camera channels…")
                channels = driver.list_channels()

            _setup_log(f"OK driver={driver_name} identity=1 channels={len(channels)} "
                       f"elapsed={time.monotonic() - attempt_started:.1f}s")
            return {
                "url": url,
                "vendor": info.vendor or VENDOR_PLACEHOLDER,
                "model": info.model or MODEL_PLACEHOLDER,
                "firmware": info.firmware or "",
                "serial": info.serial or "",
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
                if driver_name == "hikvision-isapi":
                    # A Hikvision browser login and its integration service are not the
                    # same proof. Do not falsely tell the technician the password is wrong
                    # after only the ISAPI attempt; try ONVIF too, then explain the exact
                    # integration setting if neither API accepts the account.
                    hikvision_auth_rejected = True
                    last_class = cls
                    continue
                if driver_name == "onvif" and (vendor_hint == "hikvision" or hikvision_auth_rejected):
                    last_class = cls
                    continue
                raise ValueError(_CUSTOMER_ERROR["wrong_credentials"]) from None
            if driver_name == "hikvision-isapi" and cls == "unsupported" and vendor_hint == "hikvision":
                hikvision_api_unavailable = True
            last_class = cls
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass

    if vendor_hint == "hikvision" or hikvision_auth_rejected:
        if hikvision_auth_rejected:
            raise ValueError(_CUSTOMER_ERROR["hikvision_integration_auth"])
        if hikvision_api_unavailable:
            raise ValueError(_CUSTOMER_ERROR["hikvision_integration_unavailable"])
    raise ValueError(_CUSTOMER_ERROR.get(last_class, _CUSTOMER_ERROR["connect"]))



def _public_recorder_row(row: dict, *, credential_state: str = "unknown") -> dict:
    """Customer/support-safe local recorder metadata. Never returns credentials."""
    return {
        "local_id": row["local_id"],
        "cloud_recorder_id": row.get("cloud_recorder_id"),
        "display_name": row["display_name"],
        "url": row.get("url") or "",
        "driver": row.get("driver") or "auto",
        "vendor": row.get("vendor"),
        "model": row.get("model"),
        "firmware": row.get("firmware"),
        "is_primary": bool(row.get("is_primary")),
        "continuity_owner": bool(row.get("continuity_owner")),
        "is_configured": bool(row.get("is_configured")),
        "cloud_linked": bool(row.get("cloud_recorder_id")),
        "credential_state": credential_state,
    }


def _retained_event_count(local_id: str) -> int | None:
    """Events a disabled recorder still holds on this PC, or None when unknown."""
    path = recorder_runtime.recorder_state_dir(programdata_dir(), local_id) / "spool.sqlite"
    if not path.exists():
        return 0
    try:
        from spool import Spool
        spool = Spool(path)
        try:
            return int(spool.count())
        finally:
            spool.close()
    except Exception as exc:  # noqa: BLE001 — a count is display-only; unknown stays unknown
        _setup_log(f"retained event count unavailable ({type(exc).__name__})")
        return None


def list_managed_recorders(config_path: Path) -> list[dict]:
    """List the local recorder registry without exposing credentials."""
    recorder_registry.migrate_legacy_singleton(config_path)
    out = []
    for row in recorder_registry.recorders():
        state = "available"
        try:
            credential_store.load_recorder_credential(row["local_id"])
        except SecretError:
            state = "needs_attention"
        public = _public_recorder_row(row, credential_state=state)
        if not row.get("is_configured"):
            public["retained_events"] = _retained_event_count(row["local_id"])
        out.append(public)
    return out


def _events_kept(count: int) -> str:
    return (f"{count} recorded event{'s' if count != 1 else ''} from this recorder "
            f"{'are' if count != 1 else 'is'} kept on this PC")


def managed_recorder_state(row: dict) -> str:
    """Manage Recorders 'State' column text for one list_managed_recorders row."""
    if row.get("credential_state") != "available":
        return "Needs attention"
    if row.get("is_configured"):
        return "Available"
    retained = row.get("retained_events")
    if isinstance(retained, int) and retained > 0:
        return f"Disabled ({retained} event{'s' if retained != 1 else ''} kept on this PC)"
    return "Disabled"


def disabled_recorder_message(result: dict | None) -> str:
    """What Manage Recorders says after disable_managed_recorder succeeded."""
    message = "Recorder disabled. Historical evidence was preserved."
    retained = (result or {}).get("retained_events")
    if isinstance(retained, int) and retained > 0:
        message += (f" {_events_kept(retained)} because WatchLog could not take "
                    + ("it yet. It uploads" if retained == 1 else "them yet. They upload")
                    + " if the recorder is re-enabled.")
    return message


def _activate_managed_registry_change(
    config_path: Path,
    before_registry: dict,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Restart the installed Agent after a lifecycle change.

    If the background task could not be started at all, restore the exact prior
    registry and restart the previous configuration. Once a new process starts,
    confirmation is advisory: it may already have synchronized the cloud registry,
    so rolling back merely because the local log is late would create divergence.
    """
    progress = progress or (lambda _message: None)
    progress("Restarting WatchLog with the updated recorder settings…")
    log_path = programdata_dir() / "agent.log"
    offset = _log_size(log_path)
    started = ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)
    if not started.get("started"):
        recorder_registry.save_registry(before_registry)
        restore = ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)
        raise ValueError(
            "WatchLog could not activate the recorder change. "
            "The previous recorder settings were restored."
            if restore.get("started") else
            "WatchLog could not activate the recorder change or restart the "
            "previous background connection. Export a support bundle."
        )

    confirmed = confirm_background_agent(
        timeout=45.0, since_offset=offset, log_path=log_path
    )
    return {"agent_start": started, "background": confirmed}


def rename_managed_recorder(
    config_path: Path,
    local_id: str,
    display_name: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    before = recorder_registry.load_registry()
    row = recorder_registry.rename_recorder(local_id, display_name)
    activation = _activate_managed_registry_change(
        config_path, before, progress=progress
    )
    out = _public_recorder_row(row, credential_state="available")
    out["activation"] = activation
    return out


def make_managed_recorder_primary(
    config_path: Path,
    local_id: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Move preferred-primary designation only.

    Legacy continuity ownership never moves. The registry layer also requires
    all configured recorder identities to be cloud-bound before this operation.
    """
    before = recorder_registry.load_registry()
    row = recorder_registry.make_primary(local_id)
    activation = _activate_managed_registry_change(
        config_path, before, progress=progress
    )
    out = _public_recorder_row(row, credential_state="available")
    out["activation"] = activation
    return out


def _assert_recorder_can_be_disabled(local_id: str) -> None:
    """5.1 release boundary: immutable continuity ownership cannot be retired.

    Moving preferred-primary is safe; retiring the recorder that owns the legacy
    singleton spool/health namespace needs a separately designed quiesce+drain
    workflow. Until then, fail closed rather than risk stranding pre-cutover data.
    """
    row = recorder_registry.recorder(local_id)
    if row is None:
        raise ValueError("Recorder not found.")
    if row.get("continuity_owner"):
        raise ValueError(
            "The original WatchLog recorder cannot be disabled in this release. "
            "You can make another recorder primary, but keep this recorder enabled "
            "to preserve monitoring-history continuity."
        )


def _lifecycle_cloud(config_path: Path) -> tuple:
    """The enrolled Agent identity + cloud client Setup uses for lifecycle changes."""
    public = read_public_defaults(config_path)
    if not public.get("supabase_url") or not public.get("supabase_publishable_key"):
        raise ValueError("WatchLog connection settings are unavailable on this PC.")
    state = _load_existing_identity(programdata_dir() / "agent_state.json")
    if not state:
        raise ValueError(
            "This PC is not linked to a WatchLog site. Run normal WatchLog Setup first."
        )
    cloud = core.Cloud(
        public["supabase_url"].rstrip("/"),
        public["supabase_publishable_key"],
    )
    return cloud, state


def _drain_recorder_queue(cloud, state: dict, local_id: str, *, max_batches: int = 25) -> int:
    """Upload what a recorder still has queued while WatchLog still accepts it.

    Once WatchLog marks the recorder disabled it rejects that recorder's events,
    so this runs first. Whatever cannot be sent now stays on this PC (it is never
    deleted) and uploads if the recorder is re-enabled. Returns the number of
    queued events retained locally.
    """
    path = recorder_runtime.recorder_state_dir(programdata_dir(), local_id) / "spool.sqlite"
    if not path.exists():
        return 0
    from spool import Spool
    spool = Spool(path)
    try:
        for _ in range(max_batches):
            if not spool.count():
                break
            try:
                core.upload_once(cloud, state, spool)
            except Exception as exc:  # noqa: BLE001 — retained and reported, never lost
                _setup_log(f"recorder queue upload stopped ({type(exc).__name__})")
                break
        return spool.count()
    finally:
        spool.close()


def _sync_recorder_lifecycle(cloud, state: dict, planned: dict, local_id: str) -> None:
    """Send the planned lifecycle state to WatchLog and require an exact echo.

    WatchLog's recorder sync is a desired-state sync: a non-empty payload must
    name exactly one configured primary. So every cloud-bound row of the planned
    registry is sent, as the Agent's startup sync does: the primary, the recorder
    being changed and any other bound recorder. Unbound rows are left out: Setup
    never creates a cloud recorder (the background Agent owns first binding).
    """
    bound = {
        row["local_id"]: str(row["cloud_recorder_id"])
        for row in planned["recorders"] if row.get("cloud_recorder_id")
    }
    payload = [
        row for row in recorder_registry.registry_cloud_descriptors(planned)
        if row["local_key"] in bound
    ]
    if str(local_id) not in bound or not any(row["is_primary"] for row in payload):
        raise ValueError(
            "WatchLog has not finished connecting this site's recorders yet. "
            "Nothing was changed on this PC."
        )
    try:
        mapping = cloud.call(
            "wl_sync_recorders",
            p_agent_id=state["agent_id"],
            p_agent_key=state["agent_key"],
            p_recorders=payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "WatchLog could not confirm the recorder change. Nothing was changed on this PC."
        ) from exc
    if (not isinstance(mapping, dict)
            or {str(k): str(v) for k, v in mapping.items()} != bound):
        raise ValueError(
            "WatchLog did not confirm the recorder change. Nothing was changed on this PC."
        )


def disable_managed_recorder(
    config_path: Path,
    local_id: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Disable a secondary recorder: cloud first, then this PC, then restart.

    The order matters. With the recorder still configured in WatchLog, its queued
    events are uploaded first. WatchLog is then told it is disabled, so a site
    left with one recorder never has its uploads rejected as ambiguous. Only
    after WatchLog confirms is the change committed locally and the Agent
    restarted with recorder-aware ingest for the remaining recorder(s).
    """
    progress = progress or (lambda _message: None)
    before = recorder_registry.load_registry()
    _assert_recorder_can_be_disabled(local_id)
    planned = recorder_registry.planned_disable(local_id)
    cloud, state = _lifecycle_cloud(config_path)
    _require_multi_recorder_setup_contract(cloud, state)

    progress("Sending this recorder's remaining activity to WatchLog…")
    retained = _drain_recorder_queue(cloud, state, local_id)
    if retained:
        _setup_log(f"recorder {str(local_id)[:8]} disabled with {retained} queued "
                   "event(s) kept on this PC")

    progress("Updating the recorder on WatchLog…")
    _sync_recorder_lifecycle(cloud, state, planned, local_id)

    row = recorder_registry.disable_recorder(local_id)
    activation = _activate_managed_registry_change(
        config_path, before, progress=progress
    )
    out = _public_recorder_row(row, credential_state="available")
    out["activation"] = activation
    out["retained_events"] = retained
    return out


def enable_managed_recorder(
    config_path: Path,
    local_id: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    before = recorder_registry.load_registry()
    # Never fall back to another recorder or the legacy singleton credential.
    credential_store.load_recorder_credential(local_id)
    row = recorder_registry.enable_recorder(local_id)
    activation = _activate_managed_registry_change(
        config_path, before, progress=progress
    )
    out = _public_recorder_row(row, credential_state="available")
    out["activation"] = activation
    return out


def repair_managed_recorder_credential(
    local_id: str,
    username: str,
    password: str,
    *,
    progress: Callable[[str], None] | None = None,
    hint: dict | None = None,
    verified_recorder: dict | None = None,
) -> dict:
    """Verify against this exact recorder before replacing its DPAPI credential."""
    row = recorder_registry.recorder(local_id)
    if row is None:
        raise ValueError("Recorder not found.")
    if not username.strip() or not password:
        raise ValueError("Enter the recorder username and password.")

    progress = progress or (lambda _message: None)
    if verified_recorder:
        proven = dict(verified_recorder)
        required = ("url", "vendor", "model", "driver", "channels")
        if any(key not in proven for key in required):
            raise ValueError("WatchLog lost the recorder verification. Test it again.")
        if discover.host_of(str(proven.get("url") or "")) != discover.host_of(
            row.get("url") or ""
        ):
            raise ValueError("Recorder selection changed. Test this recorder again.")
    else:
        proven = test_recorder(
            row.get("url") or "",
            username,
            password,
            progress=progress,
            hint=hint,
        )

    # Only after successful hardware authentication may the protected secret move.
    # The continuity recorder is also the legacy singleton, whose credential file
    # is still read; both move together until those files are retired.
    credential_store.replace_recorder_credential(
        local_id, username.strip(), password,
        mirror_legacy=bool(row.get("continuity_owner")),
    )
    updated = recorder_registry.update_observed_identity(
        local_id,
        vendor=_observed(proven, "vendor"),
        model=_observed(proven, "model"),
        firmware=proven.get("firmware"),
        driver=proven.get("driver") or row.get("driver") or "auto",
        identity_fingerprint=(
            f"serial:{proven.get('serial')}" if proven.get("serial")
            else row.get("identity_fingerprint")
        ),
        verified_by_setup=True,
    )
    out = _public_recorder_row(updated, credential_state="available")
    out["channels"] = list(proven.get("channels") or [])
    out["verified_against_hardware"] = bool(proven.get("verified_against_hardware"))
    out["activation"] = _restart_for_credential_change(progress)
    return out


def _restart_for_credential_change(progress: Callable[[str], None]) -> dict:
    """Restart the installed Agent so every recorder thread uses the new login.

    Collector, health, recovery and job threads each hold the credential they
    loaded; only a restart reaches all of them. The verified login is already
    saved, so a failed restart is reported truthfully rather than rolled back to
    a login the recorder now rejects."""
    progress("Restarting WatchLog with the updated recorder login…")
    log_path = programdata_dir() / "agent.log"
    offset = _log_size(log_path)
    started = ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)
    if not started.get("started"):
        raise ValueError(
            "The recorder login was verified and saved, but WatchLog could not "
            "restart to use it yet. Restart this PC, or export a support bundle."
        )
    confirmed = confirm_background_agent(
        timeout=45.0, since_offset=offset, log_path=log_path
    )
    return {"agent_start": started, "background": confirmed}


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


def _seed_recorder_identity(config_path: Path, recorder: dict) -> None:
    """Persist the proven recorder's NON-SECRET identity before the background task starts.

    The production connector can then safely rediscover the same recorder after DHCP/IP
    movement even if its very first background connection fails. Build 41 could otherwise
    have a perfectly proven foreground setup but no recorder_identity.json, leaving the
    background runtime unable to authenticate any rediscovery candidate.
    """
    try:
        import connector_rediscovery
        cfg = SimpleNamespace(
            state_path=programdata_dir() / "agent_state.json",
            nvr_driver=recorder.get("driver") or "auto",
            nvr_url=recorder.get("url") or "",
            _ini_path=config_path,
        )
        info = SimpleNamespace(
            vendor=recorder.get("vendor") or "",
            model=recorder.get("model") or "",
            serial=recorder.get("serial") or "",
            driver=recorder.get("driver") or "",
        )
        connector_rediscovery.save_identity(cfg, info, recorder.get("url") or "")
    except Exception as exc:  # noqa: BLE001 — resilience metadata may never fail setup
        _setup_log(f"recorder identity seed skipped ({type(exc).__name__})")


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
                            _run=None, *, require_readiness: bool = False) -> dict:
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
    if require_readiness:
        # Field Build 41/69: a running task is not proof the background Agent reached
        # WatchLog and the recorder; register-service.ps1 waits for that proof (exit 3 =
        # running but not proven). The Agent keeps running either way.
        cmd.append("-RequireRecorderReadiness")
    try:
        code, out = runner(cmd, timeout)
    except Exception as exc:  # noqa: BLE001 — never block a connected site
        return {"started": False, "detail": f"could not start background agent ({type(exc).__name__})"}
    if code == 0:
        return {"started": True, "proven": bool(require_readiness),
                "detail": ("background agent reached WatchLog and identified the recorder"
                           if require_readiness else "background agent registered and started")}
    if code == 3 and require_readiness:
        return {"started": True, "proven": False,
                "detail": "background agent is running but has not yet confirmed the recorder"}
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
                            timeout: float = 20.0, _run=None) -> dict:
    """Point the RECORDER at WatchLog, so the site keeps reporting with no PC running.

    RUNS OUT OF PROCESS (5.0). 0.4.11 did this inline and the very first time the feature
    was enabled on real hardware it took the whole installer down with a native crash --
    "WatchLog Setup has stopped working" -- mid-way through configuring the recorder. A
    resilience BONUS must never be able to kill the thing that installs it, and a
    try/except cannot catch a native crash. Delegating to `watchlog-agent.exe
    --configure-push` makes that structural: a crash, a hang, or a recorder that wedges
    mid-request is contained in a child process and the wizard just reads an exit code.

    It is also why the recorder still gets configured when the wizard dies: the same
    command runs from the background agent with no installer present.

    Never raises. Returns {"configured", "verified", "detail"}.
    """
    progress = progress or (lambda _message: None)
    base = (public.get("push_bridge_url")
            or os.environ.get("WATCHLOG_PUSH_BRIDGE_URL") or "").strip().rstrip("/")
    if not base:
        return {"configured": False, "verified": False,
                "detail": "no push bridge configured in this build"}

    progress("Finishing optional recorder integration (up to 10 seconds)…")
    try:
        if _run is not None:
            code, out = _run()
        else:
            import proc_util
            exe = Path(sys.executable).resolve().parent / "watchlog-agent.exe"
            cmd = ([str(exe)] if exe.exists()
                   else [sys.executable, str(Path(__file__).resolve().parent / "watchlog_agent.py")])
            code, out = proc_util.run_bounded(cmd + ["--configure-push"], timeout)
    except Exception as exc:  # noqa: BLE001 - the launcher itself must not fail setup
        return {"configured": False, "verified": False,
                "detail": f"could not run recorder push setup ({type(exc).__name__})"}

    for line in (out or "").splitlines():
        if line.startswith("PUSH_JSON "):
            try:
                parsed = json.loads(line[len("PUSH_JSON "):])
                return {"configured": bool(parsed.get("configured")),
                        "verified": bool(parsed.get("verified")),
                        "detail": str(parsed.get("detail") or "")}
            except Exception:  # noqa: BLE001
                break
    # No report: the child crashed, was killed at the deadline, or printed nothing. That
    # is a failed BONUS, never a failed install.
    return {"configured": False, "verified": False,
            "detail": f"recorder push setup did not report back (exit {code})"}


MULTI_RECORDER_SETUP_CONTRACT_VERSION = 4
MULTI_RECORDER_SETUP_FEATURES = frozenset({
    "recorders",
    "recorder_cameras",
    "recorder_events",
    "recorder_health",
    "recorder_recovery",
    "recorder_reconciliation",
    "recorder_capabilities",
    "recorder_job_routing",
    "recorder_analytics",
    "recorder_continuity",
})


def list_local_recorders(config_path: Path) -> list[dict]:
    """Return non-secret local recorder configuration for post-install setup."""
    try:
        # Existing singleton installations are staged copy-only on first manage
        # open. The old INI/credential remains intact.
        recorder_registry.migrate_legacy_singleton(config_path)
    except Exception:
        # If there is no legacy recorder yet, an empty registry is legitimate.
        if not config_path.exists() and not recorder_registry.registry_path().exists():
            return []
        raise

    rows = []
    for row in recorder_registry.recorders():
        rows.append({
            "local_id": row["local_id"],
            "cloud_recorder_id": row.get("cloud_recorder_id"),
            "display_name": row["display_name"],
            "url": row.get("url") or "",
            "driver": row.get("driver") or "auto",
            "vendor": row.get("vendor"),
            "model": row.get("model"),
            "firmware": row.get("firmware"),
            "is_primary": bool(row.get("is_primary")),
            "is_configured": bool(row.get("is_configured")),
        })
    return rows


def _require_multi_recorder_setup_contract(cloud, state: dict) -> dict:
    contract = cloud.call(
        "wl_multi_recorder_agent_contract",
        p_agent_id=state["agent_id"],
        p_agent_key=state["agent_key"],
    )
    if not isinstance(contract, dict) or not contract.get("ok"):
        raise ValueError(
            "This WatchLog site is not ready to add another recorder yet."
        )
    version = int(contract.get("version") or 0)
    features = set(contract.get("features") or [])
    if version != MULTI_RECORDER_SETUP_CONTRACT_VERSION:
        raise ValueError(
            "This WatchLog site needs the multi-recorder backend update before "
            "another recorder can be added."
        )
    missing = MULTI_RECORDER_SETUP_FEATURES - features
    if missing:
        raise ValueError(
            "This WatchLog site is missing required multi-recorder capabilities."
        )
    return contract


def add_existing_site_recorder(
    config_path: Path,
    public: dict,
    address: str,
    username: str,
    password: str,
    progress: Callable[[str], None] | None = None,
    hint: dict | None = None,
    verified_recorder: dict | None = None,
) -> dict:
    """Add one recorder to an already-enrolled WatchLog site.

    Cutover order is intentionally local-first:
      1. prove backend contract v3 using the existing Agent identity;
      2. verify the new recorder locally;
      3. stage the legacy primary if needed;
      4. write the secondary registry row + per-recorder DPAPI secret;
      5. force-restart the background Agent.

    The background Agent owns cloud recorder creation/binding. Setup never creates
    the secondary cloud recorder while the singleton runtime is still running.
    """
    progress = progress or (lambda _message: None)
    public = dict(public or read_public_defaults(config_path))
    if not public.get("supabase_url") or not public.get("supabase_publishable_key"):
        raise ValueError("WatchLog connection settings are unavailable on this PC.")

    state_path = programdata_dir() / "agent_state.json"
    state = _load_existing_identity(state_path)
    if not state:
        raise ValueError(
            "This PC is not linked to a WatchLog site. Run normal WatchLog Setup first."
        )

    cloud = core.Cloud(
        public["supabase_url"].rstrip("/"),
        public["supabase_publishable_key"],
    )
    progress("Checking multi-recorder readiness…")
    _require_multi_recorder_setup_contract(cloud, state)

    if verified_recorder:
        recorder = dict(verified_recorder)
        required = ("url", "vendor", "model", "driver", "channels")
        if any(key not in recorder for key in required):
            raise ValueError(
                "WatchLog lost the recorder verification. Test the recorder again."
            )
        if discover.host_of(str(recorder["url"])) != discover.host_of(address):
            raise ValueError(
                "The recorder selection changed after login. Test the recorder again."
            )
    else:
        progress("Verifying the additional recorder…")
        recorder = test_recorder(
            address, username, password, progress=progress, hint=hint
        )

    progress("Preparing the existing recorder identity…")
    try:
        recorder_registry.migrate_legacy_singleton(config_path)
    except Exception as exc:
        raise ValueError(
            "WatchLog could not prepare the existing recorder identity safely."
        ) from exc

    display = " ".join(
        value for value in (_observed(recorder, "vendor"), _observed(recorder, "model"))
        if value
    ) or "Additional Recorder"

    progress("Encrypting the additional recorder credential on this PC…")
    try:
        added = recorder_registry.add_recorder(
            display_name=display,
            url=str(recorder.get("url") or address).rstrip("/"),
            driver=str(recorder.get("driver") or "auto"),
            username=username.strip(),
            password=password,
            is_primary=False,
            vendor=_observed(recorder, "vendor"),
            model=_observed(recorder, "model"),
            firmware=recorder.get("firmware"),
            identity_fingerprint=(
                f"serial:{recorder.get('serial')}"
                if recorder.get("serial") else None
            ),
        )
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(
            "Windows could not securely store the additional recorder."
        ) from exc

    # Force-stop the singleton process and start the exact installed build. The
    # new process performs two-phase cloud binding before any multi-recorder
    # worker starts.
    log_path = programdata_dir() / "agent.log"
    log_offset = _log_size(log_path)
    progress("Restarting WatchLog with all configured recorders…")
    agent_start = ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)

    if not agent_start.get("started"):
        # No new runtime was started, so the recorder cannot have acquired cloud
        # identity. Roll back locally and restore the old singleton task.
        try:
            recorder_registry.remove_unbound_recorder(added["local_id"])
        except Exception:
            pass
        restore = ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)
        raise ValueError(
            "WatchLog could not activate the additional recorder. "
            "The previous recorder configuration was restored."
            if restore.get("started") else
            "WatchLog could not activate the additional recorder or restart the "
            "previous background connection. Export a support bundle."
        )

    progress("Confirming the restarted WatchLog connection…")
    confirmed = confirm_background_agent(
        timeout=45.0, since_offset=log_offset, log_path=log_path
    )
    current = next(
        (row for row in recorder_registry.recorders()
         if row["local_id"] == added["local_id"]),
        added,
    )

    # If the restarted process bound the recorder to cloud, deletion would break
    # lineage; keep it and report verification truthfully. If it never bound and
    # never heartbeated, roll back safely to the previous singleton config.
    if not confirmed.get("confirmed") and not current.get("cloud_recorder_id"):
        try:
            recorder_registry.remove_unbound_recorder(added["local_id"])
            ensure_background_agent(timeout=BACKGROUND_START_TIMEOUT_SECONDS)
        except Exception:
            pass
        raise ValueError(
            "WatchLog could not verify the additional recorder startup. "
            "The unbound recorder was rolled back."
        )

    rows = list_local_recorders(config_path)
    return {
        "ok": True,
        "connected": bool(confirmed.get("confirmed")),
        "background": confirmed,
        "agent_start": agent_start,
        "recorder": next(
            (row for row in rows if row["local_id"] == added["local_id"]),
            current,
        ),
        "recorder_count": len([row for row in rows if row.get("is_configured")]),
        "camera_count": len(recorder.get("channels") or []),
        "vendor": recorder.get("vendor"),
        "model": recorder.get("model"),
    }


# --- first install with more than one recorder --------------------------------
#
# The first recorder is set up exactly as a single-recorder install (legacy
# singleton store + staged continuity row). Every further recorder gets its own
# registry row and DPAPI credential, and Setup binds them all to WatchLog before
# the background Agent starts, so each recorder's cameras are created on that
# recorder with the technician's names and Monitor/Ignore choices.

RECORDER_CAMERA_PROFILES_NAME = "camera_profiles.json"
RECORDER_CAMERA_PROFILES_SCHEMA = "watchlog.recorder_camera_profiles.v1"
ANALYTICS_BOOTSTRAP_MARKER_NAME = "analytics_bootstrap_sent.json"


def default_recorder_name(index: int) -> str:
    """Prefilled recorder name in Setup. The first matches the registry default."""
    return "Primary Recorder" if int(index) == 0 else f"Recorder {int(index) + 1}"


def unused_recorder_name(names) -> str:
    """The prefilled name for the next recorder: the next default name that no
    recorder already chosen uses (a technician may have typed "Recorder 2")."""
    taken = {str(name or "").strip().casefold() for name in names or []}
    index = len(taken)
    while default_recorder_name(index).casefold() in taken:
        index += 1
    return default_recorder_name(index)


def install_recorders_problem(entries: list[dict]) -> str | None:
    """What finalize_install would refuse about this recorder set, as the message
    Setup shows on the camera step before Connect; None when it is acceptable."""
    if not entries:
        return None
    first = entries[0]
    try:
        _validate_install_recorders(
            {"address": first.get("address"), "verified_recorder": first.get("verified_recorder"),
             "display_name": first.get("display_name")},
            list(entries[1:]))
    except ValueError as exc:
        return str(exc)
    return None


def default_camera_profiles(recorder: dict | None) -> list[dict]:
    """Connectivity-first camera choices for one verified recorder: every
    discovered channel monitored, purpose left for the portal."""
    return [{
        "channel": str(camera["channel"]),
        "name": camera.get("name") or f"Camera {camera['channel']}",
        "purpose": "custom",
        "monitored": True,
        "analytics_enabled": True,
    } for camera in ((recorder or {}).get("channels") or [])]


def camera_rows_by_recorder(entries: list[dict]) -> list[tuple[str, str, str]]:
    """(recorder name, channel, camera name) rows, grouped by recorder in setup
    order. Channel numbers repeat across recorders; each row says whose it is."""
    rows = []
    for index, entry in enumerate(entries or []):
        name = str(entry.get("display_name") or "").strip() or default_recorder_name(index)
        for camera in (entry.get("verified_recorder") or {}).get("channels") or []:
            rows.append((name, str(camera["channel"]),
                         camera.get("name") or f"Camera {camera['channel']}"))
    return rows


def _entry_url(entry: dict) -> str:
    verified = entry.get("verified_recorder") or {}
    return str(verified.get("url") or entry.get("address") or "").strip()


def _fingerprint(recorder: dict | None) -> str | None:
    serial = str((recorder or {}).get("serial") or "").strip()
    return f"serial:{serial}" if serial else None


def find_install_duplicate(entries: list[dict], candidate: dict) -> int | None:
    """Index of an already chosen recorder that is the same physical recorder as
    ``candidate`` (same normalised address, or the same serial where both are known)."""
    rows = [{
        "local_id": str(index),
        "url": _entry_url(entry),
        "identity_fingerprint": _fingerprint(entry.get("verified_recorder")),
        "is_configured": True,
    } for index, entry in enumerate(entries or [])]
    # The registry's own duplicate rule, so Setup and the registry agree.
    same = recorder_registry._duplicate_of(
        rows, _entry_url(candidate), _fingerprint(candidate.get("verified_recorder")))
    return int(same["local_id"]) if same is not None else None


def _validate_install_recorders(primary: dict, additional: list[dict]) -> list[dict]:
    """Check the whole recorder set before anything is written. Returns the
    additional entries with their display names settled."""
    chosen = [dict(primary)]
    out = []
    names = {str(primary.get("display_name") or default_recorder_name(0)).strip().casefold()}
    for index, raw in enumerate(additional, start=1):
        entry = dict(raw or {})
        if not str(entry.get("address") or "").strip():
            raise ValueError("Each additional recorder needs its local address.")
        if not str(entry.get("username") or "").strip() or not entry.get("password"):
            raise ValueError("Each additional recorder needs its own username and password.")
        if find_install_duplicate(chosen, entry) is not None:
            raise ValueError(
                "The same recorder was added twice. Remove the duplicate and try again.")
        entry["display_name"] = (str(entry.get("display_name") or "").strip()
                                 or default_recorder_name(index))
        key = entry["display_name"].casefold()
        if key in names:
            raise ValueError("Give each recorder a different name.")
        names.add(key)
        chosen.append(entry)
        out.append(entry)
    return out


def _verified_install_recorder(entry: dict, progress: Callable[[str], None]) -> dict:
    """Reuse the recorder login proven on its own Login step (or prove it now)."""
    address = str(entry["address"]).strip()
    verified = entry.get("verified_recorder")
    if verified:
        recorder = dict(verified)
        if any(key not in recorder for key in ("url", "vendor", "model", "driver", "channels")):
            raise ValueError("WatchLog lost a recorder verification. Please test that recorder again.")
        if discover.host_of(str(recorder["url"])) != discover.host_of(address):
            raise ValueError("A recorder selection changed after login. Please test that recorder again.")
        return recorder
    progress(f"Verifying {entry.get('display_name') or 'the next recorder'}…")
    return test_recorder(address, str(entry["username"]).strip(), entry["password"],
                         progress=progress, hint=entry.get("hint"))


def _stage_additional_recorder(entry: dict, recorder: dict) -> tuple[dict, bool]:
    """Write one additional recorder's registry row and DPAPI credential.

    Returns (row, added_now). A recorder this same site already has (a Retry
    after binding, or Setup run again) is re-pointed at the new login in place,
    keeping its local and WatchLog identity, instead of becoming a duplicate."""
    url = str(recorder.get("url") or entry["address"]).rstrip("/")
    fingerprint = _fingerprint(recorder)
    username = str(entry["username"]).strip()
    existing = recorder_registry._duplicate_of(recorder_registry.recorders(), url, fingerprint)
    if existing is not None:
        if existing.get("continuity_owner") or not existing.get("is_configured"):
            # The first recorder twice, or a recorder disabled in Manage Recorders.
            recorder_registry._reject_duplicate([existing], url, fingerprint)
        row = recorder_registry.update_recorder_connection(
            existing["local_id"], url=url, driver=recorder.get("driver") or "auto",
            username=username, password=entry["password"],
            vendor=_observed(recorder, "vendor"), model=_observed(recorder, "model"),
            firmware=recorder.get("firmware"), identity_fingerprint=fingerprint)
        if row["display_name"] != entry["display_name"]:
            row = recorder_registry.rename_recorder(row["local_id"], entry["display_name"])
        return row, False
    row = recorder_registry.add_recorder(
        display_name=entry["display_name"], url=url,
        driver=str(recorder.get("driver") or "auto"),
        username=username, password=entry["password"], is_primary=False,
        vendor=_observed(recorder, "vendor"), model=_observed(recorder, "model"),
        firmware=recorder.get("firmware"), identity_fingerprint=fingerprint)
    return row, True


def _discard_unbound(local_ids: list[str]) -> None:
    """Best-effort rollback of recorders this run added and WatchLog never bound."""
    for local_id in local_ids:
        try:
            row = recorder_registry.recorder(local_id)
            if row is not None and not row.get("cloud_recorder_id"):
                recorder_registry.remove_unbound_recorder(local_id)
        except Exception as exc:  # noqa: BLE001 — report, never mask the first failure
            _setup_log(f"unbound recorder rollback skipped ({type(exc).__name__})")


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _save_recorder_camera_profiles(local_id: str, display_name: str,
                                   profiles: list[dict]) -> None:
    """Persist one recorder's camera choices, keyed by recorder (non-secret).

    watchlog.ini camera_profiles_json stays the first recorder's channel-keyed
    list for the singleton runtime; it cannot describe two recorders that both
    have a channel 1."""
    _write_json_file(
        recorder_runtime.recorder_state_dir(programdata_dir(), local_id)
        / RECORDER_CAMERA_PROFILES_NAME,
        {"schema": RECORDER_CAMERA_PROFILES_SCHEMA, "local_id": str(local_id),
         "display_name": display_name, "profiles": list(profiles or [])})


class RecorderBindingUnknown(ValueError):
    """wl_sync_recorders was sent with the further recorders, but whether WatchLog
    committed it is unknown (no answer, a server or gateway error, or an answer that
    names other recorders). Those recorders' local rows must be kept: WatchLog may
    already hold a recorder for each local key, and a Retry re-sends the same keys."""


def _binding_definitely_refused(exc: Exception) -> bool:
    """True only for an answer saying WatchLog rolled the call back: a 4xx from the
    RPC (a refused or failed call), not a request timeout (408)."""
    status = getattr(exc, "status", None)
    return (isinstance(exc, core.CloudError) and isinstance(status, int)
            and 400 <= status < 500 and status != 408)


def _bind_install_recorders(cloud, state: dict) -> dict:
    """Bind every local recorder to its WatchLog recorder, as the Agent's startup
    binding does: the continuity recorder alone first (it adopts the recorder the
    first camera sync created), then the whole registry. Fail closed unless the
    answer names exactly the local recorders. When the whole-registry call's outcome
    is unknown it raises RecorderBindingUnknown, so the caller keeps the rows."""
    def sync(payload: list[dict], unknown: type) -> dict:
        try:
            mapping = cloud.call("wl_sync_recorders", p_agent_id=state["agent_id"],
                                 p_agent_key=state["agent_key"], p_recorders=payload)
        except Exception as exc:  # noqa: BLE001
            error = ValueError if _binding_definitely_refused(exc) else unknown
            raise error(
                "WatchLog could not link this site's recorders. Please try again.") from exc
        if not isinstance(mapping, dict) or \
                {str(k) for k in mapping} != {row["local_key"] for row in payload}:
            raise unknown("WatchLog did not confirm this site's recorders. Please try again.")
        recorder_registry.apply_cloud_mapping(mapping)
        return mapping

    continuity = recorder_registry.continuity_recorder()
    if continuity is not None and not continuity.get("cloud_recorder_id"):
        # The further recorders are not in this call, so rolling them back stays safe.
        sync([row for row in recorder_registry.registry_cloud_descriptors()
              if row["local_key"] == continuity["local_id"]], ValueError)
    return sync(recorder_registry.registry_cloud_descriptors(), RecorderBindingUnknown)


def _sync_install_recorder_cameras(cloud, state: dict, row: dict, recorder: dict,
                                   profiles: list[dict]) -> dict:
    """Create one recorder's cameras on that recorder, with its own choices."""
    channels = merge_camera_config(recorder.get("channels") or [], profiles)
    if not channels:
        raise AgentSyncError("CAMERA_ENUMERATION_FAILED",
            f"WatchLog could not read any camera channels from {row['display_name']}. "
            "Check the recorder is online and try again.")
    try:
        mapping = cloud.call("wl_sync_recorder_cameras", p_agent_id=state["agent_id"],
                             p_agent_key=state["agent_key"],
                             p_recorder_id=row["cloud_recorder_id"], p_cameras=channels)
    except core.CloudError as exc:
        raise _classify_camera_sync(exc) from exc
    except Exception as exc:  # noqa: BLE001 — transport/timeout
        raise AgentSyncError("CAMERA_SYNC_FAILED",
            "WatchLog could not reach the cloud to add the cameras. "
            "Check the internet connection and try again.") from exc
    wanted = {c["channel"] for c in channels}
    missing = wanted - {str(k) for k in (mapping or {})}
    if missing:
        _setup_log(f"camera sync CAMERA_SYNC_PARTIAL recorder={row['local_id'][:8]} "
                   f"created={len(wanted) - len(missing)} of {len(wanted)}")
        raise AgentSyncError("CAMERA_SYNC_PARTIAL",
            f"WatchLog added some but not all of {row['display_name']}'s cameras. "
            "Please try again; if it persists, contact WatchLog support.")
    capabilities = recorder.get("capabilities")
    if capabilities and capabilities.get("channels"):
        try:
            cloud.call("wl_sync_recorder_capabilities", p_agent_id=state["agent_id"],
                       p_agent_key=state["agent_key"],
                       p_recorder_id=row["cloud_recorder_id"], p_capabilities=capabilities)
        except Exception:  # noqa: BLE001 — enrichment; the Agent re-syncs it
            pass
    return mapping


def _stage_recorder_registry(config_path: Path, recorder: dict, username: str,
                             password: str, prior_identity: dict | None,
                             state: dict) -> None:
    """Keep the recorder registry authoritative for the recorder Setup just proved.

    * No registry: stage the legacy singleton copy-only, as before.
    * A registry of this same enrolled site: re-point its continuity recorder
      (the legacy singleton) at the newly proven address and login, keeping its
      local and cloud identity, so the registry and watchlog.ini agree.
    * A registry left by an earlier installation (uninstall removes the identity
      but keeps recorders.json), by another site, or unreadable: quarantine it
      (moved aside, never deleted) and stage fresh, instead of blocking every
      reinstall. After an uninstall the site is unknown, so the fresh row keeps
      the old continuity recorder's local id: on the same site the new Agent then
      re-attaches to that WatchLog recorder instead of creating another one.
    """
    fingerprint = f"serial:{recorder.get('serial')}" if recorder.get("serial") else None
    reuse_local_id = None
    if recorder_registry.registry_path().exists():
        same_site = bool(
            prior_identity and prior_identity.get("site_id")
            and prior_identity.get("site_id") == (state or {}).get("site_id")
        )
        continuity = None
        if same_site:
            try:
                continuity = recorder_registry.continuity_recorder()
            except ValueError:
                continuity = None
        if continuity is not None:
            recorder_registry.update_recorder_connection(
                continuity["local_id"],
                url=str(recorder.get("url") or "").rstrip("/"),
                driver=recorder.get("driver") or "auto",
                username=username,
                password=password,
                vendor=_observed(recorder, "vendor"),
                model=_observed(recorder, "model"),
                firmware=recorder.get("firmware"),
                identity_fingerprint=fingerprint,
            )
            return
        if not prior_identity:
            reuse_local_id = recorder_registry.reusable_continuity_id(
                recorder.get("url"), fingerprint)
        moved = recorder_registry.quarantine_registry()
        _setup_log(
            "recorder registry quarantined ("
            + ("unreadable" if same_site else "earlier installation or another site")
            + "): " + ", ".join(path.name for path in moved)
        )
    recorder_registry.migrate_legacy_singleton(config_path, local_id=reuse_local_id)


def finalize_install(config_path: Path, public: dict, enrollment_code: str,
                     address: str, username: str, password: str, site_type: str,
                     profiles: list[dict], progress: Callable[[str], None] | None = None,
                     hint: dict | None = None,
                     verified_recorder: dict | None = None,
                     additional_recorders: list[dict] | None = None,
                     primary_display_name: str | None = None) -> dict:
    """Prove local recorder + WatchLog enrollment and persist only protected secrets.

    ``additional_recorders`` (5.1) connects more recorders in the same first
    install: each item is {address, username, password, display_name,
    verified_recorder, profiles[, hint]}. Without it the install is the
    single-recorder install, unchanged. ``primary_display_name`` renames the
    first recorder when the technician gave it a name."""
    progress = progress or (lambda _message: None)
    if not public.get("supabase_url") or not public.get("supabase_publishable_key"):
        raise ValueError("This installer is missing its WatchLog public connection settings.")
    if not enrollment_code.strip():
        raise ValueError("Enter the WatchLog site code from the portal.")
    primary_name = str(primary_display_name or "").strip() or None
    extras = _validate_install_recorders(
        {"address": address, "verified_recorder": verified_recorder,
         "display_name": primary_name or default_recorder_name(0)},
        list(additional_recorders or []),
    )
    multi = bool(extras)

    # Step 04 already authenticated the recorder. Repeating that full hardware
    # transaction in Step 06 was both redundant and a field source of false hangs:
    # embedded Digest/ISAPI/CGI stacks can answer once and then stall on the immediate
    # duplicate session. Reuse the exact successful proof when supplied by the UI.
    if verified_recorder:
        recorder = dict(verified_recorder)
        required = ("url", "vendor", "model", "driver", "channels")
        if any(key not in recorder for key in required):
            raise ValueError("WatchLog lost the recorder verification. Please run setup again.")
        try:
            if discover.host_of(str(recorder["url"])) != discover.host_of(address):
                raise ValueError("WatchLog recorder selection changed after login. Please test the recorder again.")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("WatchLog could not reuse the recorder verification. Please test the recorder again.") from exc
        progress("Recorder login already verified.")
    else:
        progress("Verifying the recorder…")
        recorder = test_recorder(address, username, password, progress=progress, hint=hint)
    extra_recorders = []
    chosen = [{"address": address, "verified_recorder": recorder}]
    for entry in extras:
        proven = _verified_install_recorder(entry, progress)
        candidate = {"address": entry["address"], "verified_recorder": proven}
        if find_install_duplicate(chosen, candidate) is not None:   # e.g. the same serial
            raise ValueError(
                "The same recorder was added twice. Remove the duplicate and try again.")
        chosen.append(candidate)
        extra_recorders.append(proven)

    # The legacy singleton store (watchlog.ini, nvr_credential.dpapi and the
    # rediscovery identity) is written before Setup can know whether an existing
    # registry belongs to this site. Until the registry names the same recorder,
    # any failure or refusal puts those files back as they were, so the two
    # stores never point at different recorders.
    state_path = programdata_dir() / "agent_state.json"
    legacy_store = credential_store.snapshot_secret_files([
        config_path, credential_store.nvr_credential_path(),
        state_path.parent / "recorder_identity.json",
    ])
    try:
        progress("Encrypting recorder credentials on this PC…")
        try:
            credential_store.save_nvr_credential(username.strip(), password)
        except SecretError as exc:
            raise ValueError("Windows could not securely store the recorder credential on this PC.") from exc
        _write_proven_config(config_path, public, enrollment_code, recorder, username, site_type, profiles)
        _seed_recorder_identity(config_path, recorder)

        progress("Connecting this site to WatchLog…")
        cloud = core.Cloud(public["supabase_url"].rstrip("/"), public["supabase_publishable_key"])
        device = SimpleNamespace(vendor=recorder["vendor"], model=recorder["model"],
                                 driver=recorder["driver"])
        # The identity this PC had before this run decides whether an existing
        # recorder registry still belongs here (see _stage_recorder_registry).
        prior_identity = _load_existing_identity(state_path)
        # Label any older Agent's unstamped queue with the site that wrote it BEFORE this
        # enrollment, so the new Agent sets it aside if this run moves the PC to another site.
        try:
            import site_runtime
            site_runtime.stamp_prior_site(state_path.parent, prior_identity)
        except Exception as exc:  # noqa: BLE001 - never block setup; the Agent adopts unstamped data
            _setup_log(f"site runtime stamp skipped ({type(exc).__name__})")
        # Honour the supplied site code first; only reuse a local identity that still
        # authenticates. Never skip enrollment just because a stale agent_state.json exists.
        state = establish_identity(cloud, state_path, enrollment_code, device, progress)

        if multi:
            # More than one recorder needs the complete multi-recorder backend; on
            # anything less the Agent would stop monitoring, so refuse up front.
            progress("Checking multi-recorder readiness…")
            try:
                _require_multi_recorder_setup_contract(cloud, state)
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    "WatchLog could not confirm this site is ready for more than one "
                    "recorder. Check the internet connection and try again.") from exc

        # Establish the recorder registry (stable local recorder UUID + independent
        # DPAPI credential) before the background process starts. The legacy
        # singleton files are retained so the proven 5.0.27 path still boots.
        try:
            _stage_recorder_registry(config_path, recorder, username.strip(), password,
                                     prior_identity, state)
            continuity = recorder_registry.continuity_recorder()
            if primary_name and continuity and continuity["display_name"] != primary_name:
                recorder_registry.rename_recorder(continuity["local_id"], primary_name)
        except recorder_registry.DuplicateRecorder:
            raise                               # customer-safe: says which action to take
        except Exception as exc:
            raise ValueError(
                "Windows could not prepare this recorder for WatchLog multi-recorder storage."
            ) from exc

        # Every further recorder: its own registry row and DPAPI credential.
        added: list[str] = []
        extra_rows: list[dict] = []
        for entry, extra in zip(extras, extra_recorders):
            progress(f"Encrypting the login for {entry['display_name']} on this PC…")
            try:
                row, added_now = _stage_additional_recorder(entry, extra)
            except recorder_registry.DuplicateRecorder:
                _discard_unbound(added)
                raise
            except Exception as exc:
                _discard_unbound(added)
                raise ValueError(
                    f"Windows could not prepare {entry['display_name']} for WatchLog."
                ) from exc
            if added_now:
                added.append(row["local_id"])
            extra_rows.append(row)
    except BaseException:
        credential_store.restore_secret_files(legacy_store)
        raise

    # A first recorder that WatchLog already knows (Setup run again on a site whose
    # recorders are bound) can no longer use the single-recorder camera calls.
    continuity = recorder_registry.continuity_recorder() if multi else None
    rebound = bool(continuity and continuity.get("cloud_recorder_id"))
    purposes_applied = False
    mapping = bootstrap = None
    try:
        if not rebound:
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
                    bootstrap = cloud.call(
                        "wl_agent_bootstrap_analytics", p_agent_id=state["agent_id"],
                        p_agent_key=state["agent_key"], p_site_type=site_type or "custom",
                        p_camera_profiles=profiles)
                    purposes_applied = True
                except Exception:
                    # The background agent will retry this bootstrap from the local
                    # config after production schema alignment. It is enrichment, not
                    # a reason to lie that recorder/enrollment failed.
                    pass

        if multi:
            # wl_agent_bootstrap_analytics matches cameras by channel across the whole
            # site. It ran above while only the first recorder's cameras existed; the
            # Agent must not send it again once a second recorder shares channel 1.
            _write_json_file(programdata_dir() / ANALYTICS_BOOTSTRAP_MARKER_NAME, (
                {"site_id": state.get("site_id"), "sent_at": core.iso(core.now_utc()),
                 "version": (bootstrap or {}).get("version"),
                 "updated_cameras": (bootstrap or {}).get("updated_cameras", 0),
                 "recorder_local_id": continuity["local_id"]}
                if purposes_applied else
                {"site_id": state.get("site_id"), "skipped": True,
                 "reason": "multi-recorder site: channel-keyed purposes are ambiguous"}))

            # Recorder-scoped calls are accepted only from the site's current WatchLog
            # connection (the most recently seen Agent), so this PC reports in first.
            try:
                core.heartbeat(cloud, state, device)
            except Exception as exc:
                raise ValueError(
                    "WatchLog linked the site but could not confirm the connection. Try again."
                ) from exc
            progress("Linking each recorder to this WatchLog site…")
            _bind_install_recorders(cloud, state)
            bound = {row["local_id"]: row for row in recorder_registry.recorders()}
            mappings = {}
            if rebound:
                progress("Adding cameras to this WatchLog site…")
                mappings[continuity["local_id"]] = _sync_install_recorder_cameras(
                    cloud, state, bound[continuity["local_id"]], recorder, profiles)
            else:
                mappings[continuity["local_id"]] = mapping
            for entry, extra, row in zip(extras, extra_recorders, extra_rows):
                progress(f"Adding the cameras of {entry['display_name']}…")
                mappings[row["local_id"]] = _sync_install_recorder_cameras(
                    cloud, state, bound[row["local_id"]], extra,
                    entry.get("profiles") or default_camera_profiles(extra))
            _save_recorder_camera_profiles(continuity["local_id"],
                                           bound[continuity["local_id"]]["display_name"],
                                           profiles)
            for entry, extra, row in zip(extras, extra_recorders, extra_rows):
                _save_recorder_camera_profiles(
                    row["local_id"], entry["display_name"],
                    entry.get("profiles") or default_camera_profiles(extra))
    except BaseException as exc:
        # Roll back this run's further recorders only when WatchLog definitely does
        # not hold them. After an unknown binding outcome they stay, with their
        # credentials, so Retry re-sends the same local keys (no second recorder).
        if multi and not isinstance(exc, RecorderBindingUnknown):
            _discard_unbound(added)
        raise

    progress("Confirming the WatchLog connection…")
    try:
        core.heartbeat(cloud, state, device)
    except Exception as exc:
        raise ValueError("WatchLog linked the site but could not confirm the final connection. Try again.") from exc

    # =================================================================
    # CORE CONNECTION IS PROVEN ABOVE. From here the ONLY installer-critical
    # operation is starting the real background connector and proving that the
    # SYSTEM-launched agent itself reaches WatchLog.
    #
    # Build 41 field evidence proved that recorder-push, although labelled
    # "optional", was still executed synchronously here and could strand Step 06
    # after the site/cameras were already connected. Optional recorder-side
    # integration is therefore NEVER run by first-run setup.
    # =================================================================
    progress("Starting WatchLog in the background…")
    agent_start = ensure_background_agent(timeout=BACKGROUND_READY_TIMEOUT_SECONDS,
                                          require_readiness=True)
    core.log(f"background agent start: {agent_start.get('detail')}")
    connected = bool(agent_start.get("started"))

    # Recorder-side push remains an explicit support/diagnostic command only.
    # It is intentionally absent from the installer critical path until it has
    # been field-verified across supported recorder firmware.
    push = {
        "configured": False,
        "verified": False,
        "detail": "not run during installation; background Site Connector is authoritative",
    }

    cleared = _clear_consumed_code(config_path)
    core.log(f"post-connect phase done "
             f"(agent_started={connected} code_cleared={cleared})")
    result = {
        "site_id": state["site_id"],
        "recorder_push": push,
        "agent_start": agent_start,
        "connected": connected,
        "camera_count": len(mapping or recorder["channels"]),
        "vendor": recorder["vendor"],
        "model": recorder["model"],
        "verified_against_hardware": recorder["verified_against_hardware"],
    }
    if multi:
        rows = {row["local_id"]: row for row in recorder_registry.recorders()}
        installed = [continuity["local_id"]] + [row["local_id"] for row in extra_rows]
        result["camera_count"] = sum(len(mappings[local_id]) for local_id in installed)
        result["recorder_count"] = len([r for r in rows.values() if r.get("is_configured")])
        # Purposes reach WatchLog only through the channel-keyed bootstrap, which is
        # safe for the first recorder alone. The others keep theirs on this PC until
        # WatchLog can take a purpose for one recorder's camera.
        result["recorders"] = [{
            "local_id": local_id,
            "display_name": rows[local_id]["display_name"],
            "is_primary": bool(rows[local_id].get("is_primary")),
            "cloud_linked": bool(rows[local_id].get("cloud_recorder_id")),
            "camera_count": len(mappings[local_id]),
            "purposes_applied": purposes_applied and local_id == continuity["local_id"],
        } for local_id in installed]
        _setup_log(f"multi-recorder install recorders={len(installed)} "
                   f"cameras={result['camera_count']} agent_started={connected}")
    return result

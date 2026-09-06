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
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import discover
import watchlog_agent as core
import wsdiscovery
from drivers import DriverError, build
from windows_secret import NVR_PASSWORD_ENV_KEY, SecretError, write_env_file

SETUP_AGENT_VERSION = "0.3.3"

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


def credential_path() -> Path:
    """ACL-restricted plaintext env file holding the recorder password."""
    return programdata_dir() / "watchlog.env"


def legacy_secret_path() -> Path:
    """Old machine-scoped DPAPI blob (0.2–0.3.2). Removed on migration."""
    return programdata_dir() / "nvr_password.dpapi"


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
        "nvr_username": section.get("nvr_username", "admin"),
        "site_type": section.get("site_type", "custom"),
    }


def migrate_legacy_credentials(config_path: Path) -> bool:
    """Move an old plaintext nvr_password from watchlog.ini into the
    ACL-restricted env credential file.

    Only the plaintext-INI shape is migrated. Older DPAPI-blob installs are
    intentionally *not* decrypted here (the credential store no longer
    decrypts anything); the installer falls back to re-running setup so the
    password is re-entered once and stored in the env file. Returns True when
    a credential was migrated.
    """
    if not config_path.exists():
        return False
    ini = configparser.ConfigParser()
    ini.read(config_path, encoding="utf-8-sig")
    if not ini.has_section("watchlog"):
        return False
    section = ini["watchlog"]
    password = section.get("nvr_password", "")
    if not password:
        return False
    write_env_file(credential_path(), {NVR_PASSWORD_ENV_KEY: password})
    section.pop("nvr_password", None)
    section["nvr_password_protected"] = "env-file"
    _write_ini(config_path, ini)
    # The legacy DPAPI blob, if any, is now superseded by the env file.
    try:
        legacy_secret_path().unlink()
    except OSError:
        pass
    return True


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
        for ip, ports in discover.sweep(None, log=lambda _m: None):
            ports = sorted(ports)
            if not any(port in ports for port in (80, 81, 88, 443, 554, 8000, 8080, 8081, 37777, 34567)):
                continue
            hint = "Recorder candidate"
            if 37777 in ports:
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
                  hint: dict | None = None, _scan=None, _build=None) -> dict:
    """Prove recorder identity + credentials + channel list — fast and bounded.

    `hint` may carry discovery metadata: {"ports": [...], "vendor_hint": "dahua"}.
    When present the network scan is skipped entirely. Capabilities discovery is
    NOT done here; it is deferred to the background agent.
    """
    progress = progress or (lambda _message: None)
    scan_fn = _scan or (lambda h: discover.scan(h, log=lambda _m: None))
    build_fn = _build or build
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
    section["nvr_username"] = username.strip()
    section["nvr_driver"] = "auto"
    section["nvr_password_protected"] = "env-file"
    section["site_type"] = site_type
    section["camera_profiles_json"] = json.dumps(profiles, separators=(",", ":"))
    _write_ini(config_path, ini)


def _clear_consumed_code(config_path: Path) -> None:
    ini = configparser.ConfigParser()
    ini.read(config_path, encoding="utf-8-sig")
    if ini.has_section("watchlog"):
        ini["watchlog"]["enrollment_code"] = ""
        _write_ini(config_path, ini)


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

    progress("Saving recorder credentials on this PC…")
    try:
        write_env_file(credential_path(), {NVR_PASSWORD_ENV_KEY: password})
    except SecretError as exc:
        raise ValueError("Windows could not save the recorder credential on this PC.") from exc
    _write_proven_config(config_path, public, enrollment_code, recorder, username, site_type, profiles)

    progress("Connecting this site to WatchLog…")
    cloud = core.Cloud(public["supabase_url"].rstrip("/"), public["supabase_publishable_key"])
    state_path = programdata_dir() / "agent_state.json"
    state = core.load_state(state_path)
    device = SimpleNamespace(vendor=recorder["vendor"], model=recorder["model"],
                             driver=recorder["driver"])
    if not state:
        try:
            response = cloud.call(
                "wl_enroll",
                p_code=enrollment_code.strip(),
                p_hostname=platform.node(),
                p_platform=f"{platform.system()} {platform.release()}",
                p_agent_version=SETUP_AGENT_VERSION,
                p_device_vendor=device.vendor,
                p_device_model=device.model,
                p_device_driver=device.driver,
            )
        except Exception as exc:
            raise ValueError(
                "WatchLog could not verify this site code. Check the internet connection and make sure "
                "the code is current, then try again.") from exc
        state = {
            "agent_id": response["agent_id"], "agent_key": response["agent_key"],
            "tenant_id": response["tenant_id"], "site_id": response["site_id"],
            "enrolled_at": core.iso(core.now_utc()), "agent_version": SETUP_AGENT_VERSION,
        }
        core.save_state(state_path, state)

    progress("Adding cameras to this WatchLog site…")
    try:
        mapping = cloud.call(
            "wl_sync_cameras", p_agent_id=state["agent_id"], p_agent_key=state["agent_key"],
            p_cameras=recorder["channels"])
    except Exception as exc:
        raise ValueError("The site linked to WatchLog, but its cameras could not be added. Try again.") from exc

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

    _clear_consumed_code(config_path)
    return {
        "site_id": state["site_id"],
        "camera_count": len(mapping or recorder["channels"]),
        "vendor": recorder["vendor"],
        "model": recorder["model"],
        "verified_against_hardware": recorder["verified_against_hardware"],
    }

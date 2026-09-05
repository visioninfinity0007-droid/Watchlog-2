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
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import discover
import watchlog_agent as core
import wsdiscovery
from drivers import DriverError, autodetect
from windows_secret import SecretError, write_secret

SETUP_AGENT_VERSION = "0.3.0"

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


def secret_path() -> Path:
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
    """Move an old plaintext nvr_password entry into machine-scoped DPAPI."""
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
    write_secret(secret_path(), password)
    section.pop("nvr_password", None)
    section["nvr_password_protected"] = "dpapi-local-machine"
    _write_ini(config_path, ini)
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
    except Exception:
        pass
    return sorted(results.values(), key=lambda row: row["ip"])


def _candidate_urls(address: str) -> list[str]:
    value = address.strip().rstrip("/")
    if not value:
        return []
    if value.startswith("http://") or value.startswith("https://"):
        return [value]
    ports = [80, 443, 8000, 8080, 81, 88, 8081]
    try:
        found = [row.port for row in discover.scan(value, log=lambda _m: None)
                 if row.open and row.kind in ("http", "https")]
        ports = found + [port for port in ports if port not in found]
    except Exception:
        pass
    urls = []
    for port in ports:
        scheme = "https" if port == 443 else "http"
        urls.append(f"{scheme}://{value}" if port in (80, 443) else f"{scheme}://{value}:{port}")
    return urls


def test_recorder(address: str, username: str, password: str,
                  progress: Callable[[str], None] | None = None) -> dict:
    progress = progress or (lambda _message: None)
    if not username.strip() or not password:
        raise ValueError("Enter the recorder username and password.")
    last_errors = []
    for url in _candidate_urls(address):
        progress("Checking the recorder connection…")
        driver = None
        try:
            driver, info = autodetect(url, username.strip(), password, timeout=7,
                                      log=lambda _m: None)
            channels = driver.list_channels()
            try:
                capabilities = driver.capabilities()
            except Exception:
                capabilities = None
            return {
                "url": url,
                "vendor": info.vendor or "Recorder",
                "model": info.model or "Unknown model",
                "firmware": info.firmware or "",
                "driver": driver.name,
                "verified_against_hardware": bool(driver.verified_against_hardware),
                "channels": [{"channel": str(row.channel), "name": row.name or f"Camera {row.channel}"}
                             for row in channels],
                "capabilities": capabilities,
            }
        except DriverError as exc:
            text = str(exc).lower()
            if "401" in text or "unauthor" in text:
                raise ValueError("The recorder rejected that username or password.") from None
            last_errors.append(str(exc).splitlines()[-1][:160] if str(exc) else "no response")
        finally:
            if driver:
                try:
                    driver.close()
                except Exception:
                    pass
    raise ValueError(
        "WatchLog could not connect to that recorder. Check that this PC is on the same network, "
        "the recorder web service is enabled, and the address is correct.")


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
    section["nvr_password_protected"] = "dpapi-local-machine"
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
                     profiles: list[dict], progress: Callable[[str], None] | None = None) -> dict:
    """Prove local recorder + WatchLog enrollment and persist only protected secrets."""
    progress = progress or (lambda _message: None)
    if not public.get("supabase_url") or not public.get("supabase_publishable_key"):
        raise ValueError("This installer is missing its WatchLog public connection settings.")
    if not enrollment_code.strip():
        raise ValueError("Enter the WatchLog site code from the portal.")

    progress("Verifying the recorder one more time…")
    recorder = test_recorder(address, username, password, progress)

    progress("Protecting recorder credentials on this PC…")
    try:
        write_secret(secret_path(), password)
    except SecretError as exc:
        raise ValueError("Windows could not protect the recorder credential on this PC.") from exc
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

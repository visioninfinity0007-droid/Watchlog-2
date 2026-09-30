"""Conservative same-recorder rediscovery for the production Site Connector.

The configured recorder URL is authoritative while it works. Rediscovery is used only
after repeated non-auth connection failures and only when a previously proven recorder
identity is available. It never sprays credentials across arbitrary LAN hosts: candidates
must first match vendor-native network evidence, and an address change is accepted only
when the authenticated device matches the saved serial number (preferred) or is the sole
vendor+model match.
"""
from __future__ import annotations

import configparser
import json
import os
import time
from pathlib import Path

import discover
from drivers import DriverError, build


REDISCOVERY_AFTER_FAILURES = 3
REDISCOVERY_MIN_SECONDS = 300
MAX_AUTH_CANDIDATES = 4


def identity_path(cfg) -> Path:
    return cfg.state_path.parent / "recorder_identity.json"


def load_identity(cfg) -> dict:
    try:
        data = json.loads(identity_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_identity(cfg, info, base_url: str) -> None:
    """Persist non-secret recorder identity for later DHCP recovery."""
    data = {
        "vendor": str(getattr(info, "vendor", "") or ""),
        "model": str(getattr(info, "model", "") or ""),
        "serial": str(getattr(info, "serial", "") or ""),
        "driver": str(getattr(info, "driver", "") or cfg.nvr_driver or ""),
        "url": str(base_url or cfg.nvr_url or ""),
        "updated_at": int(time.time()),
    }
    path = identity_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def same_device(expected: dict, info) -> bool:
    """Strong identity match. Serial wins; model fallback is intentionally strict."""
    ev = str(expected.get("vendor") or "").strip().lower()
    em = str(expected.get("model") or "").strip().lower()
    es = str(expected.get("serial") or "").strip().lower()
    av = str(getattr(info, "vendor", "") or "").strip().lower()
    am = str(getattr(info, "model", "") or "").strip().lower()
    ass = str(getattr(info, "serial", "") or "").strip().lower()

    if ev and av and ev != av:
        return False
    if es:
        return bool(ass and es == ass)
    return bool(em and am and em == am)


def _vendor_candidate(expected: dict, ip: str, ports) -> bool:
    vendor = str(expected.get("vendor") or "").lower()
    ps = {int(p) for p in (ports or [])}
    if "dahua" in vendor:
        if ps & {37777, 37778}:
            return True
    elif "hikvision" in vendor or "hilook" in vendor:
        if 8000 in ps:
            return True
    else:
        return False

    # Native-port evidence may be disabled on unusual firmware. A read-only web
    # fingerprint is acceptable as the secondary candidate filter.
    try:
        fp = discover.fingerprint(ip, ps)
        guess = str(fp.get("vendor_guess") or "").lower()
        if "dahua" in vendor:
            return any(x in guess for x in ("dahua", "cp plus", "imou"))
        return any(x in guess for x in ("hikvision", "hilook"))
    except Exception:  # noqa: BLE001
        return False


def _web_urls(ip: str, ports) -> list[str]:
    urls = []
    for port in (80, 443, 8443, 8080, 81, 82, 88, 8081, 8888):
        if port not in set(ports or []):
            continue
        scheme = "https" if port in (443, 8443) else "http"
        default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        urls.append(f"{scheme}://{ip}" if default else f"{scheme}://{ip}:{port}")
    return urls


def _persist_public_url(cfg, url: str) -> None:
    """Persist only the public recorder URL; recorder credentials remain in DPAPI."""
    cfg.nvr_url = url.rstrip("/")
    path = getattr(cfg, "_ini_path", None)
    if not path:
        return
    path = Path(path)
    try:
        ini = configparser.ConfigParser()
        if path.exists():
            ini.read(path, encoding="utf-8-sig")
        if not ini.has_section("watchlog"):
            ini.add_section("watchlog")
        ini["watchlog"]["nvr_url"] = cfg.nvr_url
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            ini.write(handle)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        # In-memory recovery still restores service; persistence can be repaired later.
        pass


def rediscover_same_recorder(cfg, log=lambda _m: None):
    """Return (driver, info) for a proven moved recorder, else None.

    Authentication is attempted only against a small vendor-filtered candidate set. If
    serial is unavailable, exactly one matching vendor+model device must exist; multiple
    matches are ambiguous and are rejected.
    """
    expected = load_identity(cfg)
    if not expected:
        log("recorder rediscovery skipped: no previously proven recorder identity")
        return None

    hits = discover.sweep(None, log=lambda _m: None)
    candidates = [(ip, ports) for ip, ports in hits if _vendor_candidate(expected, ip, ports)]
    if not candidates:
        log("recorder rediscovery: no same-vendor candidate found")
        return None

    driver_name = str(expected.get("driver") or cfg.nvr_driver or "auto").strip().lower()
    if driver_name in ("", "auto"):
        vendor = str(expected.get("vendor") or "").lower()
        driver_name = "dahua-cgi" if "dahua" in vendor else "hikvision-isapi"

    matches = []
    attempts = 0
    for ip, ports in candidates:
        for url in _web_urls(ip, ports)[:2]:
            if attempts >= MAX_AUTH_CANDIDATES:
                break
            attempts += 1
            driver = None
            try:
                driver = build(driver_name, url, cfg.nvr_username, cfg.nvr_password, 5)
                info = driver.probe()
                if same_device(expected, info):
                    matches.append((url, driver, info))
                    driver = None  # ownership moves to matches
                    break
            except DriverError:
                pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:  # noqa: BLE001
                        pass
        if attempts >= MAX_AUTH_CANDIDATES:
            break

    if len(matches) != 1:
        for _url, driver, _info in matches:
            try:
                driver.close()
            except Exception:  # noqa: BLE001
                pass
        if len(matches) > 1:
            log("recorder rediscovery refused: multiple devices match saved identity")
        else:
            log("recorder rediscovery: candidate(s) found but identity did not match")
        return None

    url, driver, info = matches[0]
    _persist_public_url(cfg, url)
    save_identity(cfg, info, url)
    log(f"recorder rediscovery: same recorder proven at {url}")
    return driver, info

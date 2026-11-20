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


IDENTITY_FILE = "recorder_identity.json"


def site_identity_path(cfg) -> Path:
    """The site-level identity Setup seeds for the recorder it connected (and the only one
    a legacy singleton site has)."""
    return Path(cfg.state_path).parent / IDENTITY_FILE


def identity_path(cfg) -> Path:
    """Where THIS recorder's identity lives.

    A registry recorder (multi-recorder sites, 5.1.0+) has its own file in its recorder
    state directory, so one recorder's identity is never used to look for another. A legacy
    singleton keeps the site-level file."""
    state_dir = getattr(cfg, "recorder_state_dir", None)
    if state_dir and getattr(cfg, "recorder_local_id", None):
        return Path(state_dir) / IDENTITY_FILE
    return site_identity_path(cfg)


def _read_identity(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def load_identity(cfg) -> dict:
    return _read_identity(identity_path(cfg))


def load_site_identity(cfg) -> dict:
    return _read_identity(site_identity_path(cfg))


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


def find_same_recorder(cfg, expected: dict, log=lambda _m: None):
    """Look for the recorder ``expected`` describes; return (url, driver, info) or None.

    Authentication is attempted only against a small vendor-filtered candidate set
    (MAX_AUTH_CANDIDATES). If serial is unavailable, exactly one matching vendor+model
    device must exist; multiple matches are ambiguous and are rejected. Nothing is
    persisted here."""
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
    return matches[0]


def _endpoint(url):
    from urllib.parse import urlsplit
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    return (parts.scheme.lower(), host, port) if host else None


def replace_ini_url(ini_path, old_url: str, new_url: str) -> bool:
    """Point watchlog.ini's nvr_url at ``new_url`` only where it still names ``old_url``.

    A registry site's watchlog.ini mirrors the continuity recorder; another recorder's move
    must never overwrite it. Only the public URL is written; the login stays in DPAPI."""
    if not ini_path:
        return False
    path = Path(ini_path)
    if not path.exists():
        return False
    ini = configparser.ConfigParser()
    ini.read(path, encoding="utf-8-sig")
    if not ini.has_section("watchlog"):
        return False
    current = ini["watchlog"].get("nvr_url", "")
    if not current or _endpoint(current) != _endpoint(old_url):
        return False
    ini["watchlog"]["nvr_url"] = str(new_url).rstrip("/")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        ini.write(handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return True


def rediscover_same_recorder(cfg, log=lambda _m: None):
    """Return (driver, info) for a proven moved recorder, else None.

    Legacy singleton entry point: looks for the recorder saved in this config's identity
    file and, when proven, persists the new public URL to watchlog.ini and the identity.
    The running Agent uses ``agent_core.open_driver``, which also covers registry
    recorders."""
    found = find_same_recorder(cfg, load_identity(cfg), log)
    if found is None:
        return None
    url, driver, info = found
    _persist_public_url(cfg, url)
    save_identity(cfg, info, url)
    log(f"recorder rediscovery: same recorder proven at {url}")
    return driver, info

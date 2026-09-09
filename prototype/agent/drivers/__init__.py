"""
WatchLog driver registry and auto-detection.

Vendor APIs are preferred over ONVIF because they expose richer recorder-side
events and capabilities. The registered Dahua/Hikvision wrappers preserve the
existing transport logic while adding explicit recorder-native AI provenance;
Dahua also exposes a bounded, on-demand incident-footage pilot path.

Known gap: Xiongmai/Hisilicon devices on proprietary port 34567 are not
supported and must not be treated as ONVIF-compatible by assumption.
"""

from __future__ import annotations

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver
from .mock import MockDriver
from .native_recorder import NativeDahuaDriver, NativeHikvisionDriver
from .onvif_driver import OnvifDriver

DRIVERS: dict[str, type[NvrDriver]] = {
    NativeHikvisionDriver.name: NativeHikvisionDriver,
    NativeDahuaDriver.name:     NativeDahuaDriver,
    OnvifDriver.name:           OnvifDriver,
    MockDriver.name:            MockDriver,
}

# Probed in order of specificity. Vendor-native APIs first, then ONVIF.
DETECT_ORDER = [NativeHikvisionDriver, NativeDahuaDriver, OnvifDriver, MockDriver]

__all__ = ["Channel", "DeviceInfo", "DriverError", "Event", "NvrDriver",
           "DRIVERS", "DETECT_ORDER", "build", "autodetect"]


def build(name: str, base_url: str, username: str = "", password: str = "",
          timeout: int = 15) -> NvrDriver:
    """Instantiate a named driver. Raises KeyError on an unknown name."""
    key = name.strip().lower()
    if key not in DRIVERS:
        raise KeyError(f"unknown driver '{name}'. "
                       f"Known: {', '.join(sorted(DRIVERS))}, or 'auto'.")
    return DRIVERS[key](base_url, username, password, timeout)


def autodetect(base_url: str, username: str = "", password: str = "",
               timeout: int = 8,
               log=lambda m: None) -> tuple[NvrDriver, DeviceInfo]:
    """Try each driver until one identifies the device."""
    failures: list[str] = []
    for cls in DETECT_ORDER:
        driver = cls(base_url, username, password, timeout)
        try:
            info = driver.probe()
            log(f"detected {cls.name}: {info.vendor} {info.model or ''}".rstrip())
            return driver, info
        except (DriverError, Exception) as e:   # noqa: BLE001 — report, do not mask
            failures.append(f"{cls.name}: {str(e).splitlines()[0][:120]}")
            driver.close()

    joined = "\n  ".join(failures)
    # A recorder that answers its vendor CGI with 401/403 is reachable and the
    # right vendor — the username/password (or an empty password from a
    # mis-launched agent) is wrong. Say so, instead of the useless and
    # misleading "no driver recognised the device".
    if any(_looks_like_auth(f) for f in failures):
        raise DriverError(
            "the recorder rejected the username or password at " + base_url
            + " (HTTP 401). Check nvr_username / nvr_password.\n  " + joined)
    raise DriverError(
        "no driver recognised the device at " + base_url + "\n  " + joined)


def _looks_like_auth(failure: str) -> bool:
    f = failure.lower()
    return ("http 401" in f or "http 403" in f or "unauthor" in f
            or "invalid username or password" in f
            or "sender not authorized" in f)

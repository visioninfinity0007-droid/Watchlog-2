"""
WatchLog driver registry and auto-detection.

Which drivers, and why these three (plus the test mock):

    hikvision-isapi   Hikvision and HiLook. HiLook is Hikvision's own
                      budget line and shares the ISAPI stack, so one
                      driver covers both.

    dahua-cgi         Dahua, Imou (Dahua's consumer brand), and the
                      Dahua-OEM units sold under other badges. CP Plus
                      matters most here: it has a real dealer network in
                      Pakistan and its recorders answer Dahua CGI.

    onvif             Everything else that is ONVIF-conformant — Uniview,
                      Tiandy, and the long tail of rebadged recorders.
                      This is what turns "supports two brands" into
                      "supports most of the market".

Known gap, stated plainly: the cheapest no-name recorders on Pakistani
dealer shelves are often Xiongmai/Hisilicon boards that speak a
proprietary binary protocol on port 34567 and implement ONVIF badly or
not at all. They are not covered. Adding them is a separate driver and a
separate decision — do not assume the ONVIF fallback catches them.

Auto-detection probes in order of specificity: the vendor APIs first,
because they give richer events than ONVIF on the same hardware, then
ONVIF, then the mock. First driver whose probe() succeeds wins.
"""

from __future__ import annotations

from .base import Channel, DeviceInfo, DriverError, Event, NvrDriver
from .dahua import DahuaDriver
from .hikvision import HikvisionDriver
from .mock import MockDriver
from .onvif_driver import OnvifDriver

DRIVERS: dict[str, type[NvrDriver]] = {
    HikvisionDriver.name: HikvisionDriver,
    DahuaDriver.name:     DahuaDriver,
    OnvifDriver.name:     OnvifDriver,
    MockDriver.name:      MockDriver,
}

# Probed in this order by autodetect().
DETECT_ORDER = [HikvisionDriver, DahuaDriver, OnvifDriver, MockDriver]

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
    """
    Try each driver until one identifies the device.

    Returns (driver, device_info). Raises DriverError with every failure
    listed if nothing matched — a device that answers none of these is a
    real finding, not a bug to paper over.
    """
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

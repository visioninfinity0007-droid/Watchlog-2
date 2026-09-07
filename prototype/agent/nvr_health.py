#!/usr/bin/env python3
"""Phase A — increment 3: agent-side recorder health assessment.

assess_nvr_health() drives a vendor-authenticated driver to answer three questions the
running agent is uniquely placed to answer, and NOTHING more:

  * is the recorder reachable?           (probe connects at all)
  * are our credentials accepted?        (probe is not 401/403)
  * what channels does it report, and which are disabled?   (list_channels)

It deliberately never touches the event stream or snapshots — health is determined from
active, authenticated recorder APIs, never inferred from whether events happened to fire.

Telemetry safety: the returned dict is what goes to the cloud, so it carries NO credential,
NO base_url/RTSP URL, and NO raw driver error string (those embed the recorder address).
Only sanitized state + reason codes and non-sensitive identity (vendor/model/firmware/count)
leave the site. The cloud (wl_report_health) turns the reported channel set into
PRESENT/MISSING/DISABLED/UNKNOWN against its own cameras — see prototype/server/inventory_model.py.
"""
from __future__ import annotations

import re

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable

_URL_RE = re.compile(r"\w+://\S+")
_CRED_RE = re.compile(r"[\w.-]+:[^/\s@]+@\S+")
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def redact(text) -> str:
    """Strip URLs, user:pass@host credentials, and bare IPv4 recorder addresses from arbitrary
    text before it is logged.

    A driver/requests/cloud error can embed the recorder address (and, in a bad config, an
    inline credential); log output must never carry either. Order matters: URLs first (a
    credentialed URL is removed whole), then bare credentials, then any remaining bare IP.
    Returns the sanitized first line, capped. Used by the agent's health logging so a broad
    `except` can't leak a secret."""
    if not text:
        return ""
    text = _URL_RE.sub("[url]", str(text))
    text = _CRED_RE.sub("[redacted]", text)
    text = _IP_RE.sub("[ip]", text)
    lines = text.splitlines()
    return (lines[0] if lines else "")[:160]


def _classify_probe_error(e: Exception):
    """(reachable, auth_ok, state, reason) from a probe failure. Structured driver errors
    are authoritative; a bare DriverError is classified by message as a fallback."""
    if isinstance(e, NvrAuthFailed):
        return True, False, "auth_failed", "nvr_auth_failed"
    if isinstance(e, NvrUnreachable):
        return False, None, "unreachable", "nvr_unreachable"
    msg = str(e).lower()
    if "401" in msg or "403" in msg or "unauthor" in msg:
        return True, False, "auth_failed", "nvr_auth_failed"
    if any(k in msg for k in ("timed out", "timeout", "refused", "unreachable",
                              "no route", "resolve", "getaddrinfo")):
        return False, None, "unreachable", "nvr_unreachable"
    if "http " in msg:                 # recorder answered but errored — reachable, undetermined
        return True, None, "unknown", "unknown"
    return False, None, "unreachable", "nvr_unreachable"


def assess_from_error(e: Exception) -> dict:
    """Build a report from a probe/connect failure we hit before we even had a driver
    (e.g. autodetect could not reach the recorder). Same shape as assess_nvr_health, with
    channels not enumerated. No secrets — only the classified state/reason."""
    reachable, auth_ok, state, reason = _classify_probe_error(e)
    return {"nvr": {"reachable": reachable, "auth_ok": auth_ok, "state": state, "reason": reason},
            "channels": {"enumerated": False}}


def assess_nvr_health(driver) -> dict:
    """Probe the recorder and return the cloud health report (no secrets)."""
    report = {"nvr": {}, "channels": {"enumerated": False}}

    try:
        info = driver.probe()
    except DriverError as e:
        reachable, auth_ok, state, reason = _classify_probe_error(e)
        report["nvr"] = {"reachable": reachable, "auth_ok": auth_ok,
                         "state": state, "reason": reason}
        # upper layer down / undetermined -> we cannot enumerate; leave channels not enumerated
        return report

    report["nvr"] = {
        "reachable": True, "auth_ok": True, "state": "ok", "reason": "ok",
        "vendor": getattr(info, "vendor", None),
        "model": getattr(info, "model", None),
        "firmware": getattr(info, "firmware", None),
        "channel_count": getattr(info, "channel_count", None),
    }

    try:
        chans = driver.list_channels()
    except DriverError:
        # recorder is up but we could not list channels; do NOT forward the raw error (it
        # embeds the recorder URL). A sanitized reason is enough for the cloud.
        report["channels"] = {"enumerated": False, "reason": "enumeration_failed"}
        return report

    report["channels"] = {
        "enumerated": True,
        "reported": [{"channel": str(c.channel),
                      "name": getattr(c, "name", None),
                      "enabled": bool(getattr(c, "enabled", True))}
                     for c in chans],
    }
    return report


__all__ = ["assess_nvr_health", "assess_from_error", "redact"]

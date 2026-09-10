#!/usr/bin/env python3
"""Recorder configuration drift engine.

WatchLog remediates a recorder to a known-good state (e.g. Al-Khalid: indoor SMD
Human-only with Vehicle OFF, NTP on, DST off, meaningful channel titles). Nothing
stops a site technician re-opening the recorder's own web UI later and undoing it —
turning indoor Vehicle back on (the exact false-positive this deployment removed),
disabling NTP so the clock drifts again, or renaming a channel. Drift detection reads
the recorder's CURRENT config, compares it to WatchLog's DESIRED baseline, and surfaces
each divergence with a severity — WITHOUT writing anything. Re-applying is a separate,
approved Site Control action, never automatic.

Two honest states are kept distinct:
  * DRIFT      — desired and observed are both known and disagree.
  * UNREADABLE — the recorder did not return this setting; we make NO claim either way
                 (never silently reported as "in sync").

Pure logic + a fail-safe reader over the driver's existing read surface. No writes here.
"""
from __future__ import annotations

from dataclasses import dataclass

HIGH, MEDIUM, LOW, UNKNOWN = "high", "medium", "low", "unknown"
_RANK = {HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3}

# Per-key drift severity + human wording. A rule may be conditional on the direction
# of change (desired->observed) so "indoor Vehicle turned back ON" outranks the reverse.
# (desired, observed) with sentinel `...` = any value.
_CHANNEL_KEYS = ("smd_enable", "smd_human", "smd_vehicle", "smd_sensitivity", "title")
_GLOBAL_KEYS = ("ntp_enabled", "dst_enabled")


def _severity(key: str, desired, observed) -> tuple[str, str]:
    if key == "smd_vehicle" and desired is False and observed is True:
        return HIGH, "indoor Vehicle classification was re-enabled — the exact false-positive this site was remediated to remove"
    if key == "smd_human" and desired is True and observed is False:
        return HIGH, "Human classification was disabled — the camera has stopped classifying people"
    if key == "smd_enable" and desired is True and observed is False:
        return HIGH, "Smart Motion Detection was turned off entirely on this channel"
    if key == "ntp_enabled" and desired is True and observed is False:
        return MEDIUM, "NTP time sync was disabled — the recorder clock will drift and timestamps will be wrong"
    if key == "dst_enabled" and desired is False and observed is True:
        return MEDIUM, "Daylight Saving was re-enabled — recorded/event times will be off by an hour"
    if key == "title":
        return LOW, "channel title differs from the WatchLog-managed name"
    if key == "smd_sensitivity":
        return LOW, "SMD sensitivity differs from the configured value"
    # Any other managed key that simply disagrees.
    return LOW, "value differs from the WatchLog-managed baseline"


@dataclass(frozen=True)
class Drift:
    scope: str            # 'channel' | 'time'
    channel: str | None
    key: str
    desired: object
    observed: object
    severity: str         # high | medium | low | unknown
    reason: str
    readable: bool        # False = recorder did not return this setting (UNREADABLE, not drift)

    def as_dict(self) -> dict:
        return {"scope": self.scope, "channel": self.channel, "key": self.key,
                "desired": self.desired, "observed": self.observed,
                "severity": self.severity, "reason": self.reason, "readable": self.readable}


_TRUE_WORDS = {"true", "yes", "on"}
_FALSE_WORDS = {"false", "no", "off"}


def _norm(v):
    """Compare on normalized form so True/'true'/'True' match, and 3/'3' match, without
    conflating numeric strings ('1'/'0' stay strings so sensitivity isn't read as a bool)."""
    if v is None or isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return s


def _diff_section(scope, channel, keys, desired: dict, observed: dict, out: list) -> None:
    for key in keys:
        if key not in desired:
            continue                      # desired is the source of truth for "managed"
        want = desired[key]
        if key not in observed or observed.get(key) is None:
            out.append(Drift(scope, channel, key, want, None, UNKNOWN,
                             "recorder did not return this setting — cannot confirm", False))
            continue
        got = observed[key]
        if _norm(want) == _norm(got):
            continue
        sev, reason = _severity(key, want, got)
        out.append(Drift(scope, channel, key, want, got, sev, reason, True))


def diff(desired: dict, observed: dict) -> list[Drift]:
    """Compare a DESIRED baseline against an OBSERVED recorder config.

    Both are normalized dicts: {'channels': {ch: {smd_human, smd_vehicle, smd_sensitivity,
    smd_enable, title}}, 'time': {ntp_enabled, dst_enabled}}. Only keys present in `desired`
    are checked. Returns findings sorted most-severe first, UNREADABLE last.
    """
    out: list[Drift] = []
    d_ch = (desired or {}).get("channels", {}) or {}
    o_ch = (observed or {}).get("channels", {}) or {}
    for ch in sorted(d_ch, key=lambda c: (len(str(c)), str(c))):
        _diff_section("channel", str(ch), _CHANNEL_KEYS, d_ch[ch] or {}, o_ch.get(ch, {}) or {}, out)
    _diff_section("time", None, _GLOBAL_KEYS,
                  (desired or {}).get("time", {}) or {}, (observed or {}).get("time", {}) or {}, out)
    out.sort(key=lambda x: (_RANK[x.severity], x.scope, str(x.channel or ""), x.key))
    return out


def summarize(drifts: list[Drift]) -> dict:
    real = [d for d in drifts if d.readable]
    return {
        "in_drift": bool(real),
        "counts": {sev: sum(1 for d in real if d.severity == sev) for sev in (HIGH, MEDIUM, LOW)},
        "unreadable": sum(1 for d in drifts if not d.readable),
        "findings": [d.as_dict() for d in drifts],
    }


def observe(driver, channels) -> dict:
    """Read the OBSERVED config from a recorder via the driver's existing read surface.

    Fail-safe: a getter that errors or returns nothing leaves that key absent (-> UNREADABLE
    at diff time), never a fabricated value. Read-only; issues no writes.
    """
    obs: dict = {"channels": {}, "time": {}}
    for ch in channels:
        ch = str(ch)
        entry: dict = {}
        try:
            smd = driver.get_smd(ch) or {}
            if smd.get("enable") is not None:
                entry["smd_enable"] = smd.get("enable")
            if smd.get("human") is not None:
                entry["smd_human"] = smd.get("human")
            if smd.get("vehicle") is not None:
                entry["smd_vehicle"] = smd.get("vehicle")
            if smd.get("sensitivity") is not None:
                entry["smd_sensitivity"] = smd.get("sensitivity")
        except Exception:                 # noqa: BLE001 — unreadable, not fatal
            pass
        try:
            title = driver.get_channel_title(ch)
            if title is not None:
                entry["title"] = title
        except Exception:                 # noqa: BLE001
            pass
        if entry:
            obs["channels"][ch] = entry
    try:
        clock = driver.get_clock() or {}
        if clock.get("supported"):
            if clock.get("ntp_enabled") is not None:
                obs["time"]["ntp_enabled"] = clock.get("ntp_enabled")
            if clock.get("dst_enabled") is not None:
                obs["time"]["dst_enabled"] = clock.get("dst_enabled")
    except Exception:                     # noqa: BLE001
        pass
    return obs


def check(driver, desired: dict) -> dict:
    """observe(desired's channels) then diff against desired. Convenience for a drift worker."""
    channels = list((desired or {}).get("channels", {}).keys())
    return summarize(diff(desired, observe(driver, channels)))


__all__ = ["Drift", "diff", "summarize", "observe", "check",
           "HIGH", "MEDIUM", "LOW", "UNKNOWN"]

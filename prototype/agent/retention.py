#!/usr/bin/env python3
"""Recorder retention-depth discovery (0.4.4 P7) — how far back can we actually recover?

Retention depth bounds what an outage can backfill, so the Site Status panel and coverage reporting
need it. Rather than a deep, expensive scan every health cycle, this probes a FIXED, bounded set of
look-back points and reports the deepest point that still has retrievable footage — an honest
approximation the caller caches and refreshes periodically.

Driver-agnostic: uses the same ``enumerate_historical_events`` archive interface as recovery, so it
works for any archive-capable recorder and is fully testable with a fake driver. Honest states:
  * 'measured'    a boundary was found within the horizon (oldest_recording + retention_days);
  * 'at_least'    footage exists at the deepest probe — retention is AT LEAST the horizon (capped);
  * 'empty'       the archive is queryable but no footage was found at any probe point;
  * 'unsupported' the recorder/driver cannot enumerate an archive;
  * 'unknown'     the archive could not be queried.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    import backfill
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import backfill

SUPPORTED = backfill.SUPPORTED
DEFAULT_PROBE_DAYS = (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90)
_PROBE_WINDOW_SECONDS = 900          # 15-min window at each probe point — bounded, one page each


def _as_dt(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _has_footage(driver, channel, at: datetime) -> bool:
    """Bounded: is there any recorded segment in a small window at `at`? Enumerate one page only."""
    end = at + timedelta(seconds=_PROBE_WINDOW_SECONDS)
    res = driver.enumerate_historical_events(channel, at, end, cursor=None, limit=1) or {}
    return res.get("status") == SUPPORTED and bool(res.get("events"))


def estimate_retention(driver, channel, *, now=None, probe_days=DEFAULT_PROBE_DAYS) -> dict:
    """Best-effort, bounded retention estimate. NEVER raises. Returns a dict with `status` and,
    when measured, `retention_days` + `oldest_recording` (the deepest probe point with footage)."""
    now = _as_dt(now or datetime.now(timezone.utc))
    result = {"status": "unknown", "retention_days": None, "oldest_recording": None,
              "probes": 0, "channel": str(channel)}

    # capability gate — honest 'unsupported' for a recorder with no archive enumeration
    try:
        if hasattr(driver, "historical_capability"):
            cap = driver.historical_capability() or {}
            if cap.get("segments") == "unsupported" and cap.get("events") == "unsupported":
                result["status"] = "unsupported"
                return result
    except Exception:  # noqa: BLE001
        pass
    if not hasattr(driver, "enumerate_historical_events"):
        result["status"] = "unsupported"
        return result

    deepest_hit = None
    probes = 0
    try:
        for d in sorted(set(int(x) for x in probe_days)):
            at = now - timedelta(days=d)
            probes += 1
            if _has_footage(driver, channel, at):
                deepest_hit = d
            else:
                # once a probe point has no footage, deeper points won't either (retention is a
                # trailing window) — stop early, bounded.
                if deepest_hit is not None:
                    break
    except Exception:  # noqa: BLE001 — an unreachable/ambiguous recorder is honest-unknown
        result["probes"] = probes
        return result

    result["probes"] = probes
    if deepest_hit is None:
        # queryable but nothing at any probe point (brand-new recorder / recording off)
        result["status"] = "empty"
        return result

    oldest = now - timedelta(days=deepest_hit)
    result["oldest_recording"] = oldest.astimezone(timezone.utc).isoformat()
    result["retention_days"] = float(deepest_hit)
    # if the DEEPEST configured probe still had footage, retention is at least that — not measured
    result["status"] = "at_least" if deepest_hit == max(probe_days) else "measured"
    return result


__all__ = ["estimate_retention", "DEFAULT_PROBE_DAYS"]

"""Recorder storage and recording-configuration truth, shared by the Dahua and Hikvision drivers.

Pure parsing and rollup: no I/O. A state is positive only with positive evidence. Anything the
recorder did not say, or said in a shape we do not recognise, stays UNKNOWN with a reason code;
absence is never read as healthy.

Storage rollup (one recorder, from its per-disk records):
  * no disks reported                     -> unknown, reason no_disks_reported
  * every disk faulted                     -> fault,   reason disk_error (no usable storage)
  * some disks faulted, at least one ok    -> degraded, reason disk_error (usable storage remains;
                                              a FAULT would mark every camera storage_fault)
  * any disk state unrecognised            -> unknown, reason disk_state_unknown
  * all disks ok, capacity not reported    -> unknown, reason capacity_unknown
  * all disks ok, free <= 2 % (or the
    recorder's own low-space signal)       -> degraded, reason disk_full
  * all disks ok, free > 2 %               -> ok

Recording configuration (per channel), from the recorder's record schedule:
  * disabled     the recorder is told not to record this channel (mode off, track disabled,
                 or a schedule with no recording at all)
  * continuous   regular/continuous recording covers the whole week, so recent archive is
                 expected at every moment
  * scheduled    recording is configured but only for part of the week or only on events, so
                 an empty recent archive is not proof of a fault
  * unknown      the schedule could not be read or understood
"""
from __future__ import annotations

LOW_SPACE_FREE_FRACTION = 0.02

DISK_OK = "ok"
DISK_FAULT = "fault"
DISK_UNKNOWN = "unknown"

CONFIG_DISABLED = "disabled"
CONFIG_CONTINUOUS = "continuous"
CONFIG_SCHEDULED = "scheduled"
CONFIG_UNKNOWN = "unknown"

MINUTES_PER_DAY = 1440
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY
# A schedule ending at 23:59 or 23:59:59 covers the day: firmware differ on 24:00:00.
DAY_END_TOLERANCE_MINUTES = 1


def to_bytes(value, unit: int = 1) -> int | None:
    """A non-negative integer byte count from a recorder number ("1000068870144.000000"), or None."""
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:          # NaN or negative
        return None
    return int(round(number * unit))


def disk_record(*, disk_id: str, state: str, reason: str, path: str | None = None,
                disk_type: str | None = None, total_bytes: int | None = None,
                free_bytes: int | None = None) -> dict:
    """One disk as reported to the cloud. Sizes are both present or both None."""
    if total_bytes is None or free_bytes is None:
        total_bytes = free_bytes = None
    elif free_bytes > total_bytes:
        # A recorder that reports more free than total said nothing usable about its capacity.
        total_bytes = free_bytes = None
    return {"id": str(disk_id)[:64], "path": (str(path)[:128] if path else None),
            "type": (str(disk_type)[:32] if disk_type else None),
            "state": state if state in (DISK_OK, DISK_FAULT, DISK_UNKNOWN) else DISK_UNKNOWN,
            "reason": reason, "total_bytes": total_bytes, "free_bytes": free_bytes}


def rollup_storage(disks: list[dict], *, vendor_lowspace: bool = False) -> dict:
    """Recorder storage verdict from per-disk records (module docstring). ``state`` is the
    driver vocabulary: 'ok' | 'degraded' | 'fault' | None (unknown)."""
    sized = [d for d in disks if d.get("total_bytes") is not None and d.get("free_bytes") is not None]
    total = sum(d["total_bytes"] for d in sized) if sized else None
    free = sum(d["free_bytes"] for d in sized) if sized else None
    out = {"supported": True, "native_fatal": False, "native_lowspace": bool(vendor_lowspace),
           "disks": disks, "disk_count": len(disks), "total_bytes": total, "free_bytes": free}

    faults = [d for d in disks if d.get("state") == DISK_FAULT]
    oks = [d for d in disks if d.get("state") == DISK_OK]
    if not disks:
        state, reason = None, "no_disks_reported"
    elif faults and not oks:
        state, reason = "fault", "disk_error"
    elif faults:
        state, reason = "degraded", "disk_error"
    elif len(oks) != len(disks):
        state, reason = None, "disk_state_unknown"
    else:
        ok_total = sum(d["total_bytes"] or 0 for d in oks) if all(
            d.get("total_bytes") is not None and d.get("free_bytes") is not None for d in oks) else None
        ok_free = sum(d["free_bytes"] for d in oks) if ok_total is not None else None
        if vendor_lowspace:
            state, reason = "degraded", "disk_full"
        elif not ok_total:
            state, reason = None, "capacity_unknown"
        elif ok_free / ok_total <= LOW_SPACE_FREE_FRACTION:
            state, reason = "degraded", "disk_full"
        else:
            state, reason = "ok", "ok"
    if vendor_lowspace and state is None and not disks:
        state, reason = "degraded", "disk_full"      # the recorder itself said space is low
    out.update(state=state, reason=reason)
    return out


def worsen(storage: dict, state: str, reason: str) -> dict:
    """Apply an additional NEGATIVE signal: it can only make the verdict worse, never better."""
    rank = {"fault": 3, "degraded": 2, "ok": 1, None: 0}
    if rank.get(state, 0) > rank.get(storage.get("state"), 0):
        storage = dict(storage, state=state, reason=reason)
    return storage


# ---- record schedules ------------------------------------------------------------------

def hms_minutes(text) -> int | None:
    """'HH:MM[:SS]' -> minutes since midnight (24:00:00 -> 1440), or None."""
    parts = str(text or "").strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0
    except ValueError:
        return None
    if not (0 <= h <= 24 and 0 <= m < 60 and 0 <= s < 60) or (h == 24 and (m or s)):
        return None
    return h * 60 + m


def covers_week(intervals: list[tuple[int, int]]) -> bool:
    """True when the union of week-minute intervals [start, end) covers the whole week.

    Each day may end one minute short (23:59 / 23:59:59): firmware differ on 24:00:00."""
    marks = sorted((max(0, a), min(MINUTES_PER_WEEK, b)) for a, b in intervals if b > a)
    reach = 0
    for start, end in marks:
        # A gap no larger than the tolerance at a day boundary is not a gap.
        if start > reach:
            boundary = (reach % MINUTES_PER_DAY) >= MINUTES_PER_DAY - DAY_END_TOLERANCE_MINUTES
            if not (boundary and start - reach <= DAY_END_TOLERANCE_MINUTES):
                return False
        reach = max(reach, end)
    tail = MINUTES_PER_WEEK - reach
    return 0 <= tail <= DAY_END_TOLERANCE_MINUTES if marks else False


__all__ = ["LOW_SPACE_FREE_FRACTION", "DISK_OK", "DISK_FAULT", "DISK_UNKNOWN",
           "CONFIG_DISABLED", "CONFIG_CONTINUOUS", "CONFIG_SCHEDULED", "CONFIG_UNKNOWN",
           "to_bytes", "disk_record", "rollup_storage", "worsen", "hms_minutes", "covers_week",
           "MINUTES_PER_DAY", "MINUTES_PER_WEEK"]

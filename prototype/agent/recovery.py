#!/usr/bin/env python3
"""Automatic outage detection + NVR recovery orchestration (0.4.4 directive §1/§2/§5).

Ties the pieces together so an Agent/Internet outage becomes DELAYED, not lost, intelligence:

  * detect_outage(): on reconnect, the persisted last-live timestamp vs now yields the exact
    missed interval, reported to the cloud as a PENDING recovery interval (UNVERIFIED — recovery
    pending). Persistence survives an Agent restart / PC reboot.
  * RecoveryRunner: claims pending recovery intervals and backfills each from the NVR archive in
    BOUNDED, RESUMABLE, IDEMPOTENT chunks — checkpointing after every chunk (survives restart),
    de-duplicating via a persisted seen-set (no duplicate events), yielding to LIVE monitoring
    (live always has priority), throttling between chunks, and marking the interval
    recovered / partial / unrecoverable truthfully. Recovered events carry recorder_archive
    provenance (never masquerade as live).

Pure orchestration: the cloud client, historical driver and event sink are injected, so it is
fully testable with the reference archive driver and no hardware.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

try:
    import backfill
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import backfill

DEFAULT_OUTAGE_THRESHOLD = 180        # seconds; below this a reconnect is not an "outage"
DEFAULT_CHUNK_SECONDS = 3600          # recover one hour of archive per bounded chunk


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _as_dt(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


# ---- last-live persistence (durable across Agent restart / reboot) -----------

def persist_last_live(path, when: datetime) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"last_live": _iso(when)}), encoding="utf-8")
    tmp.replace(p)                     # atomic


def read_last_live(path):
    try:
        return _as_dt(json.loads(Path(path).read_text(encoding="utf-8")).get("last_live"))
    except Exception:                  # noqa: BLE001 — absent / corrupt -> no known last-live
        return None


def detect_outage(last_live, now, threshold_seconds: int = DEFAULT_OUTAGE_THRESHOLD):
    """The missed interval (last_live -> now) when the gap exceeds the threshold, else None."""
    last_live, now = _as_dt(last_live), _as_dt(now)
    if last_live is None or now is None:
        return None
    if (now - last_live).total_seconds() < max(1, threshold_seconds):
        return None
    return (last_live, now)


class RecoveryRunner:
    """Drives automatic NVR backfill for claimed recovery intervals. Live monitoring first."""

    def __init__(self, cloud, agent_id, agent_key, driver, on_event, *,
                 chunk_seconds: int = DEFAULT_CHUNK_SECONDS, throttle_seconds: float = 0.0,
                 live_pending=None, log=print):
        self.cloud, self.agent_id, self.agent_key = cloud, agent_id, agent_key
        self.driver, self.on_event = driver, on_event
        self.chunk_seconds = max(60, int(chunk_seconds))
        self.throttle_seconds = max(0.0, float(throttle_seconds))
        self.live_pending = live_pending or (lambda: False)
        self._log = log

    def report_outage(self, last_live, now, cameras=None):
        """Report a detected outage as a pending recovery interval (idempotent server-side)."""
        return self.cloud.call("wl_open_recovery_interval", p_agent_id=self.agent_id,
                               p_agent_key=self.agent_key, p_started_at=_iso(_as_dt(last_live)),
                               p_ended_at=_iso(_as_dt(now)), p_cameras=list(cameras or []))

    def _complete(self, interval_id, status, recovered, seen, cursor):
        self.cloud.call("wl_complete_recovery", p_agent_id=self.agent_id, p_agent_key=self.agent_key,
                        p_id=interval_id, p_status=status, p_recovered_count=recovered,
                        p_checkpoint={"cursor": _iso(cursor) if cursor else None,
                                      "seen_keys": sorted(seen)[:20000]})

    def _recover_interval(self, iv) -> dict:
        seen = set((iv.get("checkpoint") or {}).get("seen_keys") or [])
        resume = _as_dt((iv.get("checkpoint") or {}).get("cursor"))
        start = resume or _as_dt(iv["started_at"])
        end = _as_dt(iv["ended_at"])
        cams = list(iv.get("cameras") or []) or [None]     # None => driver decides / all
        recovered, any_unsupported, any_supported = 0, False, False

        for chunk_start, chunk_end in backfill._windows(start, end, self.chunk_seconds):
            if self.live_pending():
                # LIVE has priority — checkpoint progress and yield; a later claim resumes here.
                self._complete(iv["id"], "in_progress", recovered, seen, chunk_start)
                return {"id": iv["id"], "status": "in_progress", "recovered": recovered, "yielded": True}
            for cam in cams:
                res = backfill.backfill_events(self.driver, cam if cam is not None else "1",
                                               chunk_start, chunk_end, seen=seen, on_event=self.on_event)
                st = res.get("status")
                if st == backfill.SUPPORTED:
                    any_supported = True
                    recovered += res.get("recovered", 0)
                else:
                    any_unsupported = True
            self._complete(iv["id"], "in_progress", recovered, seen, chunk_end)   # checkpoint per chunk
            if self.throttle_seconds:
                import time
                time.sleep(self.throttle_seconds)

        # Truthful terminal status: fully supported -> recovered; mixed -> partial; none -> unrecoverable.
        status = "recovered" if (any_supported and not any_unsupported) else \
                 ("partial" if any_supported else "unrecoverable")
        self._complete(iv["id"], status, recovered, seen, end)
        return {"id": iv["id"], "status": status, "recovered": recovered, "yielded": False}

    def run_once(self, limit: int = 1) -> list[dict]:
        """Claim up to `limit` pending recovery intervals and recover each. Returns per-interval outcomes."""
        if self.live_pending():
            return []                  # never start recovery while live work is pending
        claimed = self.cloud.call("wl_agent_claim_recovery", p_agent_id=self.agent_id,
                                  p_agent_key=self.agent_key, p_limit=limit) or []
        return [self._recover_interval(iv) for iv in claimed]


__all__ = ["detect_outage", "persist_last_live", "read_last_live", "RecoveryRunner",
           "DEFAULT_OUTAGE_THRESHOLD", "DEFAULT_CHUNK_SECONDS"]

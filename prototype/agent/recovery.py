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
    import recovery_ai
except ImportError:                                   # pragma: no cover - path shim
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import backfill
    import recovery_ai

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
                 recorder_id=None,
                 chunk_seconds: int = DEFAULT_CHUNK_SECONDS, throttle_seconds: float = 0.0,
                 live_pending=None, detector=None, frame_provider=None, ai_max_frames=None,
                 snapshot_interval_seconds=recovery_ai.DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
                 log=print):
        self.cloud, self.agent_id, self.agent_key = cloud, agent_id, agent_key
        self.driver = driver
        self.recorder_id = str(recorder_id) if recorder_id else None
        self._raw_on_event = on_event
        self.on_event = self._emit_event
        self.chunk_seconds = max(60, int(chunk_seconds))
        self.throttle_seconds = max(0.0, float(throttle_seconds))
        self.live_pending = live_pending or (lambda: False)
        # Deep recovery (§1): when a detector is available, WatchLog also runs its on-site AI over
        # recovered FOOTAGE (not just recorder-native event replay). Both are optional/injected so
        # the runner stays testable and degrades honestly when neither footage nor codec is present.
        self.detector = detector
        self.frame_provider = frame_provider
        self.ai_max_frames = ai_max_frames
        self.snapshot_interval_seconds = max(30, int(snapshot_interval_seconds))
        self._log = log

    def _emit_event(self, event):
        """Stamp recovered events with recorder provenance at the context boundary."""
        if self.recorder_id:
            if isinstance(event, dict):
                event = dict(event)
                event["recorder_id"] = self.recorder_id
            elif hasattr(event, "with_recorder_id"):
                event = event.with_recorder_id(self.recorder_id)
        if self._raw_on_event:
            self._raw_on_event(event)

    def _open_rpc(self):
        return "wl_open_recorder_recovery_interval" if self.recorder_id else "wl_open_recovery_interval"

    def _claim_rpc(self):
        return "wl_agent_claim_recorder_recovery" if self.recorder_id else "wl_agent_claim_recovery"

    def _complete_rpc(self):
        return "wl_complete_recorder_recovery" if self.recorder_id else "wl_complete_recovery"

    def report_outage(self, last_live, now, cameras=None):
        """Report a detected outage as a pending recovery interval (idempotent server-side)."""
        args = dict(
            p_agent_id=self.agent_id,
            p_agent_key=self.agent_key,
            p_started_at=_iso(_as_dt(last_live)),
            p_ended_at=_iso(_as_dt(now)),
        )
        if self.recorder_id:
            args["p_recorder_id"] = self.recorder_id
            args["p_channels"] = list(cameras or [])
        else:
            args["p_cameras"] = list(cameras or [])
        return self.cloud.call(self._open_rpc(), **args)

    def _complete(self, interval_id, status, recovered, seen, cursor):
        args = dict(
            p_agent_id=self.agent_id,
            p_agent_key=self.agent_key,
            p_id=interval_id,
            p_status=status,
            p_recovered_count=recovered,
            p_checkpoint={"cursor": _iso(cursor) if cursor else None,
                          "seen_keys": sorted(seen)[:20000]},
        )
        if self.recorder_id:
            args["p_recorder_id"] = self.recorder_id
        self.cloud.call(self._complete_rpc(), **args)

    def _recover_interval(self, iv) -> dict:
        seen = set((iv.get("checkpoint") or {}).get("seen_keys") or [])
        resume = _as_dt((iv.get("checkpoint") or {}).get("cursor"))
        start = resume or _as_dt(iv["started_at"])
        end = _as_dt(iv["ended_at"])
        # Recorder-aware claims expose explicit archive channels. Never fall
        # back to canonical camera UUIDs or guess channel 1 on this path.
        # Missing channels mean the archive target is unknown, so complete the
        # interval truthfully as unrecoverable instead of querying the wrong
        # recorder channel. The legacy singleton path keeps its historical
        # driver-decides/all fallback.
        if self.recorder_id:
            cams = list(iv.get("channels") or [])
            if not cams:
                self._complete(iv["id"], "unrecoverable", 0, seen, start)
                return {
                    "id": iv["id"],
                    "status": "unrecoverable",
                    "recovered": 0,
                    "yielded": False,
                    "reason": "missing_channels",
                }
        else:
            cams = list(iv.get("cameras") or []) or [None]
        recovered, any_unsupported, any_supported = 0, False, False

        for chunk_start, chunk_end in backfill._windows(start, end, self.chunk_seconds):
            if self.live_pending():
                # LIVE has priority — checkpoint progress and yield; a later claim resumes here.
                self._complete(iv["id"], "in_progress", recovered, seen, chunk_start)
                return {"id": iv["id"], "status": "in_progress", "recovered": recovered, "yielded": True}
            for cam in cams:
                ch = cam if cam is not None else "1"
                # (a) recorder-native event replay (the recorder's OWN recorded events)
                res = backfill.backfill_events(self.driver, ch, chunk_start, chunk_end,
                                               seen=seen, on_event=self.on_event)
                if res.get("status") == backfill.SUPPORTED:
                    any_supported = True
                    recovered += res.get("recovered", 0)
                else:
                    any_unsupported = True
                # (b) visual backfill over recovered FOOTAGE. This ALWAYS runs when the
                # archive supports segments: even with no detector, decoded historical frames
                # are emitted as recovered_snapshot so a cloud/PC gap does not erase the visual
                # timeline. When the detector is present, activity is classified on the same frames.
                ai = recovery_ai.backfill_intelligence(
                    self.driver, self.detector, ch, chunk_start, chunk_end, seen=seen,
                    on_event=self.on_event, frame_provider=self.frame_provider,
                    max_frames=self.ai_max_frames,
                    snapshot_interval_seconds=self.snapshot_interval_seconds)
                if ai.get("status") == backfill.SUPPORTED:
                    any_supported = True
                    recovered += ai.get("recovered", 0)
                else:
                    # Explicit segment UNSUPPORTED means this recorder only offers
                    # native historical events; preserve that older capability
                    # without falsely calling it a visual-recovery failure. But if
                    # segments ARE supported and frames could not be decoded, the
                    # interval is partial/unknown rather than falsely recovered.
                    try:
                        seg_cap = (self.driver.historical_capability() or {}).get("segments")
                    except Exception:
                        seg_cap = None
                    if seg_cap != backfill.UNSUPPORTED:
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
        args = dict(
            p_agent_id=self.agent_id,
            p_agent_key=self.agent_key,
            p_limit=limit,
        )
        if self.recorder_id:
            args["p_recorder_id"] = self.recorder_id
        claimed = self.cloud.call(self._claim_rpc(), **args) or []
        return [self._recover_interval(iv) for iv in claimed]


__all__ = ["detect_outage", "persist_last_live", "read_last_live", "RecoveryRunner",
           "DEFAULT_OUTAGE_THRESHOLD", "DEFAULT_CHUNK_SECONDS"]

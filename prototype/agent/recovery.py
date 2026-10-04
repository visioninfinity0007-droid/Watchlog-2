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
import uuid
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
DEFAULT_MAX_ATTEMPTS = 24             # claims in a row that move no cursor before it is closed
DEFAULT_MAX_ERROR_ATTEMPTS = 3        # consecutive claims ended by a failed archive read


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


def _is_uuid(v) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except (TypeError, ValueError):
        return False


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
                 live_pending=None, detector=None, frame_provider=None, ai_max_frames=None,
                 snapshot_interval_seconds=recovery_ai.DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
                 camera_channels=None, max_attempts=DEFAULT_MAX_ATTEMPTS,
                 max_error_attempts=DEFAULT_MAX_ERROR_ATTEMPTS, log=print):
        self.cloud, self.agent_id, self.agent_key = cloud, agent_id, agent_key
        self.driver, self.on_event = driver, on_event
        # {cloud camera UUID: recorder channel}. Intervals name cameras by UUID
        # (recovery_intervals.cameras is uuid[]); the archive driver reads channels.
        self.camera_channels = {str(k): str(v) for k, v in (camera_channels or {}).items()}
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
        self.max_attempts = max_attempts
        self.max_error_attempts = max(1, int(max_error_attempts))
        self._log = log

    def report_outage(self, last_live, now, cameras=None):
        """Report a detected outage as a pending recovery interval (idempotent server-side)."""
        return self.cloud.call("wl_open_recovery_interval", p_agent_id=self.agent_id,
                               p_agent_key=self.agent_key, p_started_at=_iso(_as_dt(last_live)),
                               p_ended_at=_iso(_as_dt(now)), p_cameras=list(cameras or []))

    def _complete(self, interval_id, status, recovered, seen, cursor, *, errors=0, progress=0,
                  detail=None):
        checkpoint = {"cursor": _iso(cursor) if cursor else None, "seen_keys": sorted(seen)[:20000]}
        if errors:
            checkpoint["errors"] = errors     # consecutive failed claims, carried to the next claim
        if progress:
            checkpoint["progress_attempt"] = progress   # the claim that last moved the cursor
        params = dict(p_agent_id=self.agent_id, p_agent_key=self.agent_key,
                      p_id=interval_id, p_status=status, p_recovered_count=recovered,
                      p_checkpoint=checkpoint)
        if detail:
            params["p_detail"] = detail
        self.cloud.call("wl_complete_recovery", **params)

    def _close(self, iv, status, seen, cursor, reason) -> dict:
        """Complete an interval that will not be read (further), saying why."""
        self._complete(iv["id"], status, 0, seen, cursor, detail={"reason": reason})
        return {"id": iv["id"], "status": status, "recovered": 0, "yielded": False, "reason": reason}

    def _channels(self, cameras):
        """(recorder channels to read, cameras that could not be resolved) for an interval.

        An empty camera list is a whole-site interval: read every camera the Agent knows. A
        channel is never guessed, so with no known inventory nothing is read at all."""
        if not cameras:
            return list(dict.fromkeys(self.camera_channels.values())), 0
        channels, unresolved = [], 0
        for cam in cameras:
            ch = self.camera_channels.get(str(cam))
            if ch is None and not _is_uuid(cam):
                ch = str(cam)                   # already a recorder channel (reference drivers)
            if ch is None:
                unresolved += 1                 # a camera UUID this Agent cannot map
            elif ch not in channels:
                channels.append(ch)
        return channels, unresolved

    def _recover_interval(self, iv) -> dict:
        checkpoint = iv.get("checkpoint") or {}
        seen = set(checkpoint.get("seen_keys") or [])
        resume = _as_dt(checkpoint.get("cursor"))
        errors = int(checkpoint.get("errors") or 0)
        attempts = int(iv.get("attempts") or 0)
        progress = int(checkpoint.get("progress_attempt") or 0)
        start = resume or _as_dt(iv["started_at"])
        end = _as_dt(iv["ended_at"])
        cams, unresolved = self._channels(iv.get("cameras") or [])
        if not cams:
            # Nothing can be read truthfully: never scan a guessed channel and never call the
            # interval (or the site) recovered.
            return self._close(iv, "unrecoverable", seen, resume, "missing_channels")
        if self.max_attempts and attempts - progress > self.max_attempts:
            # Re-claimed too often without the cursor moving (stale claims, crash loops): stop
            # replaying it against the recorder. Earlier progress makes it partial, never recovered.
            return self._close(iv, "partial" if seen else "unrecoverable", seen, resume,
                               "attempts_exhausted")
        # A camera that cannot be mapped to a channel cannot be read, so the interval cannot be
        # fully recovered.
        recovered, any_unsupported, any_supported = 0, unresolved > 0, False
        failed, failed_at, failure = set(), None, None    # failed archive reads in this claim

        def save(cursor):
            # A failed read in this claim is counted even when the claim then yields; a claim that
            # moved the cursor without one ends the run of failed claims and resets the attempts cap.
            moved = cursor > start
            self._complete(iv["id"], "in_progress", recovered, seen, cursor,
                           errors=errors + 1 if failed_at is not None else (0 if moved else errors),
                           progress=attempts if moved else progress)

        for chunk_start, chunk_end in backfill._windows(start, end, self.chunk_seconds):
            if self.live_pending():
                if failed_at is not None and errors + 1 >= self.max_error_attempts:
                    break                   # out of retries: settle below instead of yielding
                # LIVE has priority — checkpoint progress and yield; a later claim resumes here.
                save(failed_at or chunk_start)
                return {"id": iv["id"], "status": "in_progress", "recovered": recovered, "yielded": True}
            for ch in cams:
                if ch in failed:
                    continue                # read again from failed_at on the next claim
                try:
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
                except Exception as e:      # noqa: BLE001 — a failed archive read backs off, then ends
                    failed.add(ch)
                    failed_at, failure = failed_at or chunk_start, type(e).__name__
                    self._log(f"recovery: archive read failed on channel {ch}: {failure}")
            if len(failed) == len(cams):
                break                       # nothing left to read in this claim
            save(failed_at or chunk_end)    # checkpoint per chunk
            if self.throttle_seconds:
                import time
                time.sleep(self.throttle_seconds)

        detail = None
        if failed_at is not None:
            if errors + 1 < self.max_error_attempts:
                # Back off: stay in progress from the first chunk that failed. The server re-offers
                # the interval once this claim goes stale, and the seen-set skips what was recovered.
                save(failed_at)
                return {"id": iv["id"], "status": "in_progress", "recovered": recovered,
                        "yielded": False, "error": failure}
            any_unsupported = True          # out of retries: what could not be read stays unrecovered
            detail = {"reason": "archive_error", "error": failure}

        # Truthful terminal status: fully supported -> recovered; mixed -> partial; none -> unrecoverable.
        status = "recovered" if (any_supported and not any_unsupported) else \
                 ("partial" if any_supported else "unrecoverable")
        self._complete(iv["id"], status, recovered, seen, end, detail=detail)
        return {"id": iv["id"], "status": status, "recovered": recovered, "yielded": False}

    def run_once(self, limit: int = 1) -> list[dict]:
        """Claim up to `limit` pending recovery intervals and recover each. Returns per-interval outcomes."""
        if self.live_pending():
            return []                  # never start recovery while live work is pending
        claimed = self.cloud.call("wl_agent_claim_recovery", p_agent_id=self.agent_id,
                                  p_agent_key=self.agent_key, p_limit=limit) or []
        return [self._recover_interval(iv) for iv in claimed]


__all__ = ["detect_outage", "persist_last_live", "read_last_live", "RecoveryRunner",
           "DEFAULT_OUTAGE_THRESHOLD", "DEFAULT_CHUNK_SECONDS", "DEFAULT_MAX_ATTEMPTS",
           "DEFAULT_MAX_ERROR_ATTEMPTS"]

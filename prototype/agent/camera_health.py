#!/usr/bin/env python3
"""Phase A — increment 4: camera HYBRID health (agent side).

Turns real recorder signals into camera health, feeding the Increment-1 state machine
(health_model.CameraHealthMachine):

  * NATIVE fault  — a VideoLoss/disconnect event is authoritative: immediate OFFLINE.
  * BOUNDED PROBE — for quiet cameras, an authenticated snapshot verifies liveness.

Two invariants are enforced here, not left to callers:

  1. An upper-layer failure is NEVER a camera failure. A snapshot probe that fails is
     disambiguated by re-checking the recorder: 401/403 -> NVR_AUTH_FAILED, a dead recorder
     -> NVR_UNREACHABLE, and in BOTH cases every dependent camera goes UNKNOWN. Only a
     channel-specific failure against a PROVEN-HEALTHY recorder may degrade/offline the one
     camera. If we cannot prove the recorder healthy, we do not blame the camera.

  2. The recorder is protected: exactly one authenticated recorder assessment per cycle,
     a fair round-robin over cameras, and a strict concurrency cap so we never probe the
     whole wall at once. Probe failures/exceptions are contained — run_cycle never raises,
     so a stall can never reach the caller's heartbeat/event loop.

Telemetry safety: snapshot bytes are used only to decide live/not-live and are discarded;
the report carries channel/health/reason strings only — no images, no URLs, no credentials.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from drivers.base import DriverError, NvrAuthFailed, NvrUnreachable
from health_model import CameraHealthMachine, Health, Inventory, Nvr, Probe, Reason

_JPEG_MAGIC = b"\xff\xd8"

_NVR_STATE = {"ok": Nvr.OK, "unreachable": Nvr.UNREACHABLE,
              "auth_failed": Nvr.AUTH_FAILED, "unknown": Nvr.UNKNOWN}

# Provenance of a camera's CURRENT health, derived from its reason — so the local health
# store (increment 5) can record where a transition came from (native/probe/inventory/upper).
_SOURCE_BY_REASON = {
    "video_loss": "native",
    "nvr_unreachable": "upper_layer", "nvr_auth_failed": "upper_layer",
    "agent_unreachable": "upper_layer",
    "channel_missing": "inventory", "channel_disabled": "inventory",
}


def source_for_reason(reason: str) -> str:
    return _SOURCE_BY_REASON.get(reason, "probe")


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    upper: Optional[str] = None      # None | 'nvr_auth_failed' | 'nvr_unreachable'
    reason: str = "ok"


def classify_snapshot_probe(snapshot: Optional[bytes],
                            recheck: Callable[[], None]) -> ProbeResult:
    """A snapshot probe result, disambiguated so an upper-layer fault is never a camera fault.

    `recheck` re-verifies the recorder; it should raise NvrAuthFailed / NvrUnreachable (or any
    DriverError) when the recorder itself is unhealthy, and return normally when it is proven
    healthy. Only a clean recheck lets us fault the individual channel.
    """
    if snapshot and snapshot[:2] == _JPEG_MAGIC:
        return ProbeResult(ok=True, reason="ok")
    try:
        recheck()
    except NvrAuthFailed:
        return ProbeResult(ok=False, upper="nvr_auth_failed", reason="nvr_auth_failed")
    except NvrUnreachable:
        return ProbeResult(ok=False, upper="nvr_unreachable", reason="nvr_unreachable")
    except DriverError:
        # Could not PROVE the recorder healthy -> do not blame the camera; treat as upper.
        return ProbeResult(ok=False, upper="nvr_unreachable", reason="nvr_unreachable")
    return ProbeResult(ok=False, upper=None, reason="probe_timeout")


def round_robin_batch(cursor: int, channels: List[str], batch_size: int) -> Tuple[List[str], int]:
    """Fair rotation: pick the next `batch_size` channels starting at `cursor`; return the batch
    and the advanced cursor so successive cycles cover every camera evenly."""
    n = len(channels)
    if n == 0:
        return [], 0
    size = max(1, min(batch_size, n))
    batch = [channels[(cursor + i) % n] for i in range(size)]
    return batch, (cursor + size) % n


def make_probe_fn(driver) -> Callable[[str], ProbeResult]:
    """Production probe: a bounded snapshot + a recorder re-check to disambiguate failures.
    Snapshot bytes are inspected for a JPEG header and then dropped — never uploaded."""
    def probe(channel: str) -> ProbeResult:
        try:
            img = driver.get_snapshot(channel)
        except DriverError:
            img = None
        except Exception:                     # noqa: BLE001 — a broken driver is not a camera fault
            img = None

        def recheck() -> None:
            driver.probe()                     # raises NvrAuthFailed/NvrUnreachable if recorder down
        return classify_snapshot_probe(img, recheck)
    return probe


class CameraHealthMonitor:
    """Per-channel health machines fed by native faults + a bounded round-robin probe."""

    def __init__(self, channels, *, batch_size: int = 2, concurrency: int = 2,
                 fail_threshold: int = 3, recover_threshold: int = 2):
        self.channels = [str(c) for c in channels]
        self.machines: Dict[str, CameraHealthMachine] = {
            c: CameraHealthMachine(fail_threshold, recover_threshold) for c in self.channels}
        self.batch_size = max(1, batch_size)
        self.concurrency = max(1, concurrency)
        self._cursor = 0
        self._tick = 0
        self._lock = threading.RLock()

    # -- public API ----------------------------------------------------------------

    def run_cycle(self, assess_fn: Callable[[], dict],
                  probe_fn: Callable[[str], ProbeResult]) -> dict:
        """One health cycle. Never raises — a probe stall cannot reach the caller's loop."""
        try:
            assessment = assess_fn()                         # exactly ONE recorder assessment
        except Exception:                                     # noqa: BLE001
            # Could not even assess the recorder -> make no camera claim this cycle.
            with self._lock:
                now = self._tick_locked()
                for c in self.channels:
                    self.machines[c].observe(now, inventory=Inventory.PRESENT, nvr=Nvr.UNKNOWN)
                return self._report_locked()

        nvr = _NVR_STATE.get((assessment.get("nvr") or {}).get("state"), Nvr.UNKNOWN)
        chans = assessment.get("channels") or {}
        enumerated = bool(chans.get("enumerated"))
        reported = {str(c["channel"]) for c in chans.get("reported", [])}
        disabled = {str(c["channel"]) for c in chans.get("reported", []) if not c.get("enabled", True)}
        # Authoritative present-tense recorder faults (increment: startup/reconnect reconciliation).
        # A channel the recorder currently reports in VideoLoss is OFFLINE now — even if it was lost
        # before this process started (no event transition) and even if its snapshot returns the
        # recorder's black placeholder JPEG (which the liveness probe would misread as live). Only
        # trusted when the driver could actually query current state (supported); otherwise empty.
        cf = chans.get("current_faults") or {}
        current_loss = ({str(c) for c in (cf.get("video_loss") or [])}
                        if cf.get("supported") else set())

        # ---- upper layer down: every camera UNKNOWN, no probing (rule 1) ----
        if nvr is not Nvr.OK:
            with self._lock:
                now = self._tick_locked()
                for c in self.channels:
                    self.machines[c].observe(now, inventory=Inventory.PRESENT, nvr=nvr)
                return self._report_locked()

        # ---- recorder proven healthy this cycle: probe a fair, bounded batch ----
        with self._lock:
            batch, self._cursor = round_robin_batch(self._cursor, self.channels, self.batch_size)
            inv = self._inventory_map(reported, disabled, enumerated)

        results = self._probe_batch(batch, probe_fn)          # bounded concurrency, outside lock

        with self._lock:
            now = self._tick_locked()
            upper = next((r.upper for r in results.values() if r.upper), None)
            if upper:
                # a probe surfaced an upper-layer failure -> the WHOLE recorder is at fault
                unvr = Nvr.AUTH_FAILED if upper == "nvr_auth_failed" else Nvr.UNREACHABLE
                for c in self.channels:
                    self.machines[c].observe(now, inventory=Inventory.PRESENT, nvr=unvr)
                return self._report_locked()

            for c in self.channels:
                if c in current_loss:
                    # Recorder says this channel is in video loss right now: authoritative OFFLINE,
                    # independent of the round-robin probe (which the placeholder frame fools).
                    self.machines[c].observe(now, inventory=inv[c], nvr=Nvr.OK,
                                             native_video_loss=True)
                    continue
                pr = results.get(c)
                probe = None
                if pr is not None:
                    probe = Probe(ok=pr.ok, reason=(Reason.OK if pr.ok else Reason.PROBE_TIMEOUT))
                self.machines[c].observe(now, inventory=inv[c], nvr=Nvr.OK, probe=probe)
            return self._report_locked()

    def record_native_fault(self, channel) -> None:
        """A native VideoLoss/disconnect event: immediate OFFLINE for that channel."""
        channel = str(channel)
        with self._lock:
            m = self.machines.get(channel)
            if m is None:
                return
            now = self._tick_locked()
            m.observe(now, inventory=Inventory.PRESENT, nvr=Nvr.OK, native_video_loss=True)

    def report(self) -> dict:
        with self._lock:
            return self._report_locked()

    # -- internals -----------------------------------------------------------------

    def _tick_locked(self) -> int:
        self._tick += 1
        return self._tick

    def _inventory_map(self, reported, disabled, enumerated) -> Dict[str, Inventory]:
        out = {}
        for c in self.channels:
            if not enumerated:
                out[c] = Inventory.UNKNOWN
            elif c in disabled:
                out[c] = Inventory.DISABLED
            elif c in reported:
                out[c] = Inventory.PRESENT
            else:
                out[c] = Inventory.MISSING
        return out

    def _probe_batch(self, batch, probe_fn) -> Dict[str, ProbeResult]:
        results: Dict[str, ProbeResult] = {}
        if not batch:
            return results
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futs = {ex.submit(probe_fn, c): c for c in batch}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    results[c] = f.result()
                except Exception:                             # noqa: BLE001 — contain probe crashes
                    results[c] = ProbeResult(ok=False, upper=None, reason="probe_timeout")
        return results

    def _report_locked(self) -> dict:
        return {"cameras": [{"channel": c,
                             "health": self.machines[c].state.value,
                             "reason": self.machines[c].reason.value,
                             "source": source_for_reason(self.machines[c].reason.value)}
                            for c in self.channels]}


__all__ = ["ProbeResult", "classify_snapshot_probe", "round_robin_batch",
           "make_probe_fn", "CameraHealthMonitor", "source_for_reason"]

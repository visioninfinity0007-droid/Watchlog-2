#!/usr/bin/env python3
"""Phase A — Health Foundation: the authoritative camera health STATE MACHINE.

Pure logic, no I/O, no clock of its own (the caller passes `now`). This module is the
single source of truth for how a channel's observations become a health verdict, per the
approved design (docs/design/OPERATIONAL_INTELLIGENCE_ARCHITECTURE.md §§5-6).

Design invariants encoded here:

  * INVENTORY is separate from HEALTH. A channel that is MISSING or DISABLED is UNKNOWN,
    never OFFLINE — we make no availability claim about a camera the operator removed.
  * LAYERS DOMINATE. If an upper layer is down (NVR unreachable / auth-failed, or the
    agent/NVR state is unknown) the camera is UNKNOWN, never OFFLINE: we have lost the
    ability to observe it, so we must not assert it is broken.
  * HYSTERESIS. A single probe failure is DEGRADED, not OFFLINE (anti-flap). OFFLINE
    requires K consecutive hard failures. Recovery from OFFLINE requires J consecutive
    clean probes. A native video-loss fault is authoritative and bypasses hysteresis.
  * HEALTH IS NEVER INFERRED FROM DETECTION VOLUME. Only active probes and native fault
    events move the machine. (Analytics quietness is a separate, lower-confidence signal
    handled elsewhere and never promoted to OFFLINE here.)

The machine is per-channel; the caller keys one instance per (site, channel).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

# Default hysteresis thresholds (overridable per instance). K fail -> OFFLINE; J ok -> recovered.
FAIL_THRESHOLD = 3
RECOVER_THRESHOLD = 2


class Inventory(enum.Enum):
    """Is the channel configured on the recorder? (Separate from whether it works.)"""
    PRESENT = "present"
    MISSING = "missing"      # operator removed the channel — NOT a fault
    DISABLED = "disabled"    # channel exists but is administratively disabled — NOT a fault
    UNKNOWN = "unknown"      # we could not enumerate inventory this cycle


class Health(enum.Enum):
    """Operational health of a PRESENT channel, when upper layers are up."""
    OPERATIONAL = "operational"
    DEGRADED = "degraded"    # transient trouble / soft fault — do not alarm as down
    OFFLINE = "offline"      # sustained, confirmed loss of the video signal
    UNKNOWN = "unknown"      # not observable right now (upper layer down / disabled / missing)


class RecordingState(enum.Enum):
    """Whether the recorder is actually retaining footage for the channel.

    Part of the Phase A state contract (§5). The camera machine does not drive it — a
    channel can be OPERATIONAL yet NOT_RECORDING — but it is defined here so persistence
    and the read model share one vocabulary.
    """
    RECORDING = "recording"
    NOT_RECORDING = "not_recording"
    STORAGE_FAULT = "storage_fault"
    UNKNOWN = "unknown"


class Nvr(enum.Enum):
    """Recorder-layer reachability/auth as reported by the agent for this cycle."""
    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"
    UNKNOWN = "unknown"      # agent did not report NVR state (treated as loss of visibility)


class Reason(enum.Enum):
    """Machine-readable cause for the current health verdict (drives operator-facing copy)."""
    OK = "ok"
    UNKNOWN = "unknown"
    PROBE_TIMEOUT = "probe_timeout"        # active probe failed / timed out
    STALE_FRAME = "stale_frame"            # probe returned, but the frame was stale -> soft degrade
    VIDEO_LOSS = "video_loss"              # recorder reported native VIDEO LOSS -> authoritative
    CHANNEL_MISSING = "channel_missing"    # inventory: operator removed the channel
    CHANNEL_DISABLED = "channel_disabled"  # inventory: channel administratively disabled
    NVR_UNREACHABLE = "nvr_unreachable"    # recorder layer down
    NVR_AUTH_FAILED = "nvr_auth_failed"    # recorder rejected our credentials
    AGENT_UNREACHABLE = "agent_unreachable"  # we have no fresh agent/NVR state to judge by


@dataclass(frozen=True)
class Probe:
    """Result of one active liveness probe against a channel.

    `ok=True, reason=STALE_FRAME` is a valid *soft* result: we reached the stream but the
    frame was not fresh — that degrades, but does not count as a hard failure.
    """
    ok: bool
    reason: Optional[Reason] = None


@dataclass(frozen=True)
class Transition:
    """The outcome of one observe() call — what a persistence layer records when `changed`."""
    at: object                 # caller-supplied timestamp/tick
    frm: Health
    to: Health
    reason: Reason
    changed: bool              # True iff the HEALTH state changed this cycle
    inventory: Inventory
    nvr: Nvr
    consecutive_fail: int
    consecutive_ok: int


class CameraHealthMachine:
    """Per-channel health state machine. Feed observations via observe(); read .state/.reason."""

    def __init__(self, fail_threshold: int = FAIL_THRESHOLD,
                 recover_threshold: int = RECOVER_THRESHOLD):
        if fail_threshold < 1 or recover_threshold < 1:
            raise ValueError("thresholds must be >= 1")
        self.fail_threshold = fail_threshold
        self.recover_threshold = recover_threshold

        self.state: Health = Health.UNKNOWN
        self.reason: Reason = Reason.UNKNOWN
        self.inventory: Inventory = Inventory.UNKNOWN
        self.nvr: Nvr = Nvr.UNKNOWN

        self.consecutive_fail = 0
        self.consecutive_ok = 0

        self.last_change_at = None
        self.last_offline_at = None
        self.last_recovery_at = None

    # -- public API ------------------------------------------------------------------

    def observe(self, now, inventory: Inventory, nvr: Nvr,
                probe: Optional[Probe] = None,
                native_video_loss: bool = False) -> Transition:
        """Fold one cycle of observation into the machine and return the Transition."""
        prev_state = self.state
        prev_reason = self.reason
        self.inventory = inventory
        self.nvr = nvr

        upper = self._upper_layer_reason(inventory, nvr)
        if upper is not None:
            # We have lost the ability to observe this camera: hold no probe verdict.
            self.consecutive_fail = 0
            self.consecutive_ok = 0
            new_state, new_reason = Health.UNKNOWN, upper
        else:
            new_state, new_reason = self._evaluate_signal(now, prev_state, probe,
                                                          native_video_loss)

        changed = new_state != prev_state
        self.state = new_state
        self.reason = new_reason
        if changed:
            self.last_change_at = now
            if new_state == Health.OFFLINE:
                self.last_offline_at = now

        return Transition(
            at=now, frm=prev_state, to=new_state, reason=new_reason, changed=changed,
            inventory=inventory, nvr=nvr,
            consecutive_fail=self.consecutive_fail, consecutive_ok=self.consecutive_ok,
        )

    # -- internals -------------------------------------------------------------------

    @staticmethod
    def _upper_layer_reason(inventory: Inventory, nvr: Nvr) -> Optional[Reason]:
        """Return a UNKNOWN-forcing reason if an upper layer precludes a camera verdict."""
        if inventory == Inventory.DISABLED:
            return Reason.CHANNEL_DISABLED
        if inventory == Inventory.MISSING:
            return Reason.CHANNEL_MISSING
        if inventory == Inventory.UNKNOWN:
            return Reason.AGENT_UNREACHABLE
        # inventory PRESENT below — now the recorder layer:
        if nvr == Nvr.UNREACHABLE:
            return Reason.NVR_UNREACHABLE
        if nvr == Nvr.AUTH_FAILED:
            return Reason.NVR_AUTH_FAILED
        if nvr == Nvr.UNKNOWN:
            return Reason.AGENT_UNREACHABLE
        return None  # PRESENT + NVR OK -> the camera is observable; judge it on its own signal

    def _evaluate_signal(self, now, prev_state: Health, probe: Optional[Probe],
                         native_video_loss: bool):
        """Camera-layer verdict when the channel is observable (PRESENT + NVR OK)."""
        # 1. Native VIDEO LOSS is an authoritative recorder fault — bypass hysteresis.
        if native_video_loss:
            self.consecutive_ok = 0
            self.consecutive_fail = max(self.consecutive_fail, self.fail_threshold)
            return Health.OFFLINE, Reason.VIDEO_LOSS

        # 2. No probe evidence this cycle: hold the prior verdict, disturb nothing.
        if probe is None:
            return prev_state, self.reason

        # 3. Hard failure: count toward OFFLINE with hysteresis.
        if not probe.ok:
            self.consecutive_ok = 0
            self.consecutive_fail += 1
            reason = probe.reason or Reason.PROBE_TIMEOUT
            if self.consecutive_fail >= self.fail_threshold:
                return Health.OFFLINE, reason
            return Health.DEGRADED, reason

        # 4. Probe returned but flagged a soft problem (e.g. stale frame): degrade, not fail.
        if probe.reason not in (None, Reason.OK):
            self.consecutive_ok = 0
            self.consecutive_fail = 0
            return Health.DEGRADED, probe.reason

        # 5. Clean healthy probe.
        self.consecutive_fail = 0
        self.consecutive_ok += 1
        if prev_state == Health.OFFLINE:
            # Recovery from OFFLINE is gated: require J consecutive clean probes.
            if self.consecutive_ok >= self.recover_threshold:
                self.last_recovery_at = now
                return Health.OPERATIONAL, Reason.OK
            return Health.OFFLINE, self.reason  # still recovering — do not flap green yet
        # From UNKNOWN / DEGRADED / OPERATIONAL a clean probe is immediately OPERATIONAL.
        return Health.OPERATIONAL, Reason.OK


__all__ = [
    "Inventory", "Health", "RecordingState", "Nvr", "Reason",
    "Probe", "Transition", "CameraHealthMachine",
    "FAIL_THRESHOLD", "RECOVER_THRESHOLD",
]

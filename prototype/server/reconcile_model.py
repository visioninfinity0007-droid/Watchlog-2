#!/usr/bin/env python3
"""Phase A — increment 5: reconciliation spec (dedupe / ordering / forward-only / coverage).

Pure logic for folding a batch of locally-retained health transitions + observation
checkpoints back into the cloud after an outage. wl_reconcile_health (0046) mirrors this;
test_health_reconciliation_contract.py pins them together.

Load-bearing rules (design §9/§18 + the increment-5 brief):
  * idempotent — a transition is identified by a STABLE id; a resend adds nothing.
  * ordered by OBSERVED time (device_ts, then seq) — never by upload arrival.
  * forward-only — a late/out-of-order upload never regresses current state.
  * a cloud gap proven locally monitored by checkpoints becomes "locally monitored"
    (recovering availability) but REMAINS a cloud connectivity gap — the two coverages
    are distinct and the caller keeps the cloud gap intact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import math

import coverage_model as cov   # reuse the interval algebra (merge/clip/subtract/total)

# A device clock more than this far AHEAD of the server is not trusted for ordering — see
# effective_ts(). Configurable at the reconcile boundary (SQL: p_max_future_skew_seconds).
DEFAULT_MAX_FUTURE_SKEW = 300   # seconds


@dataclass(frozen=True)
class Transition:
    id: str                     # stable dedupe key (e.g. "<agent>:<seq>")
    seq: int                    # monotonic per agent — tie-breaker within equal device_ts
    layer: str                  # 'camera' | 'nvr'
    entity: str                 # channel (camera) or agent id (nvr)
    frm: Optional[str]
    to: str
    reason: str
    source: str                 # 'native' | 'probe' | 'inventory' | 'upper_layer'
    device_ts: float            # when the agent OBSERVED it (not when uploaded)


def effective_ts(device_ts: float, now: float,
                 max_future_skew: float = DEFAULT_MAX_FUTURE_SKEW):
    """Server-safe ordering timestamp for CURRENT-STATE advancement.

    The raw device-observed time is kept elsewhere for evidence, but it must NOT be used
    directly as the forward-only watermark: a device clock set to next week would write a
    watermark in the future and freeze current state until server time caught up. So a
    device_ts more than `max_future_skew` AHEAD of the server is clamped to `now`. In-window
    times (including small, legitimate skew) pass through unchanged, preserving observed order.

    Returns (effective_ts, was_clamped).
    """
    if device_ts > now + max_future_skew:
        return now, True
    return device_ts, False


def _is_finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def latest_by_effective(transitions, now: float, max_future_skew: float = DEFAULT_MAX_FUTURE_SKEW
                        ) -> Dict[str, "Transition"]:
    """Winning transition per entity by (effective_ts, seq) — the deterministic tie rule.

    Ordering key is (effective_ts, seq): effective_ts first (clamped, server-safe), then the
    NUMERIC per-epoch sequence. Within a batch (one store epoch) seq fully orders ties — including
    when future-skew clamps many transitions to the same effective time. Upload/array order is
    never the key. Cross-epoch ordering is resolved by effective_ts alone (a later reconcile's
    clamp uses the server's monotonically-increasing now(), so it never sits behind an earlier one).
    """
    best: Dict[str, tuple] = {}
    for t in transitions:
        eff, _ = effective_ts(t.device_ts, now, max_future_skew)
        key = (eff, t.seq)
        cur = best.get(t.entity)
        if cur is None or key > cur[0]:
            best[t.entity] = (key, t)
    return {e: v[1] for e, v in best.items()}


# --- full row classification (mirrors 0046) so `received = valid + rejected` holds ----

_TX_REASONS = frozenset({"ok", "unknown", "probe_timeout", "stale_frame", "video_loss",
                         "channel_missing", "channel_disabled", "nvr_unreachable",
                         "nvr_auth_failed", "agent_unreachable", "storage_fault",
                         "not_recording", "tamper", "disk_error", "disk_full"})


def classify_transition(row: dict, known_channels) -> str:
    """Verdict for one retained transition, in the SAME precedence order as wl_reconcile_health.
    Returns 'valid' or a rejection category (observable). One bad row is isolated, not fatal."""
    known = {str(c) for c in known_channels}
    if not row.get("id"):
        return "missing_id"
    if (row.get("layer") or "camera") != "camera":
        return "wrong_layer"
    if not row.get("to"):
        return "missing_state"
    if not _is_finite_number(row.get("device_ts")):
        return "invalid_timestamp"
    if not _is_int(row.get("seq")):
        return "invalid_sequence"
    if str(row.get("entity")) not in known:
        return "unmapped_channel"
    return "valid"


def classify_checkpoint(row: dict) -> str:
    """Verdict for one retained checkpoint. Only id + a parseable device_ts are essential;
    malformed seq/cameras_observed/cycle_ok are safe-defaulted (row kept), never fatal."""
    if not row.get("id"):
        return "missing_id"
    if not _is_finite_number(row.get("device_ts")):
        return "invalid_timestamp"
    return "valid"


class Reconciler:
    """Stateful server model for CURRENT-STATE across SEPARATE reconcile calls — the exact
    cross-batch/replay semantics of wl_reconcile_health that a single-batch check cannot prove.

    Two durability rules make it replay-safe and correctly ordered across batches:
      * the LEDGER is immutable: a transition's server-safe effective_at and its server-assigned
        first-seen order (`ingest`) are fixed the FIRST time its dedupe id is inserted. A replay
        reuses the stored values — it never re-clamps against a later now().
      * the watermark is a full ORDERING TUPLE, not just a timestamp: current state advances only
        if the incoming transition is strictly newer by (effective_at, then numeric seq WITHIN the
        same store epoch, then server first-seen order ACROSS epochs). Random-UUID epochs never
        imply chronology — only the server first-seen order breaks an equal-time cross-epoch tie.
    """

    def __init__(self, max_future_skew: float = DEFAULT_MAX_FUTURE_SKEW):
        self.max_future_skew = max_future_skew
        self.ledger: Dict[str, dict] = {}    # dedupe_key -> immutable ordering row
        self.state: Dict[str, dict] = {}     # entity -> current watermark row
        self._ingest = 0                     # server-assigned monotonic first-seen counter
        self.rejected_mixed = 0              # transitions rejected because a batch spanned >1 epoch

    @staticmethod
    def _newer(n: dict, c: dict) -> bool:
        """The DURABLE cross-batch comparator. effective_at first; then, at equal time, numeric
        seq WITHIN the same store epoch and server first-seen (`ingest`) ACROSS epochs."""
        if n["effective_at"] != c["effective_at"]:
            return n["effective_at"] > c["effective_at"]
        if n["epoch"] == c["epoch"]:
            return n["seq"] > c["seq"]          # same epoch: numeric sequence is authoritative
        return n["ingest"] > c["ingest"]        # cross-epoch tie: server first-seen order only

    def reconcile(self, transitions, now: float) -> Dict[str, dict]:
        # PROTOCOL INVARIANT: one store epoch per reconcile call (the agent's store holds exactly
        # one epoch per lifetime). A batch spanning >1 epoch cannot arise legitimately and its
        # within-batch cross-epoch ordering is inherently ambiguous, so reject it wholesale
        # (observable) rather than mis-resolve it. Cross-epoch chronology is handled ACROSS calls
        # by the watermark comparator, never within one batch.
        if len({t["epoch"] for t in transitions}) > 1:
            self.rejected_mixed += len(transitions)
            return self.state

        for t in sorted(transitions, key=lambda x: x["seq"]):   # deterministic first-seen order
            key = t["id"]
            if key not in self.ledger:          # FIRST time: fix effective_at + first-seen forever
                eff, _ = effective_ts(t["device_ts"], now, self.max_future_skew)
                self._ingest += 1
                self.ledger[key] = {"effective_at": eff, "epoch": t["epoch"], "seq": t["seq"],
                                    "ingest": self._ingest, "to_state": t["to"], "entity": t["entity"]}

        # within one (single-epoch) batch, the winner per entity is max by (effective_at, seq) —
        # order-independent because seq is unique within an epoch.
        touched: Dict[str, dict] = {}
        for t in transitions:
            row = self.ledger.get(t["id"])
            if row is None:
                continue
            cur = touched.get(row["entity"])
            if cur is None or (row["effective_at"], row["seq"]) > (cur["effective_at"], cur["seq"]):
                touched[row["entity"]] = row
        for entity, row in touched.items():     # advance current state by the durable comparator
            w = self.state.get(entity)
            if w is None or self._newer(row, w):
                self.state[entity] = row
        return self.state


def partition_valid(rows: Iterable[dict]):
    """Split retained rows into (valid, rejected) so a single malformed row cannot fail the whole
    batch. A row is valid only if it has a non-empty id, a non-empty target state, and a
    parseable (finite-number) device_ts. Rejected rows are RETURNED (counted/observable), never
    silently swallowed and never left to spin the batch forever."""
    valid, rejected = [], []
    for r in rows:
        if r.get("id") and r.get("to") and _is_finite_number(r.get("device_ts")):
            valid.append(r)
        else:
            rejected.append(r)
    return valid, rejected


def dedupe(existing_ids: Set[str], incoming: Iterable[Transition]) -> List[Transition]:
    """Drop transitions whose id the server already holds; preserve input order. Resending an
    already-applied batch yields []."""
    out, seen = [], set(existing_ids)
    for t in incoming:
        if t.id not in seen:
            out.append(t)
            seen.add(t.id)      # also collapse duplicates WITHIN the batch
    return out


def order(transitions: Iterable[Transition]) -> List[Transition]:
    """Stable order by observed time then seq — the true occurrence order, regardless of the
    order they were uploaded in."""
    return sorted(transitions, key=lambda t: (t.device_ts, t.seq))


def fold_current_state(transitions: Iterable[Transition],
                       prior: Dict[str, Transition]
                       ) -> Tuple[Dict[str, Transition], Set[str]]:
    """Advance per-entity current state to the latest OBSERVED transition, forward-only.

    `prior` maps entity -> the transition currently reflected in the state table. A transition
    older than what we already have (by device_ts, then seq) is ignored (no regression).
    Returns (new_state_by_entity, entities_that_advanced).
    """
    state = dict(prior)
    advanced: Set[str] = set()
    for t in order(transitions):
        cur = state.get(t.entity)
        if cur is None or (t.device_ts, t.seq) > (cur.device_ts, cur.seq):
            state[t.entity] = t
            advanced.add(t.entity)
    return state, advanced


def checkpoints_to_intervals(times: Iterable[float], max_gap: float) -> List[Tuple[float, float]]:
    """Turn checkpoint timestamps into monitored intervals: consecutive checkpoints no more
    than `max_gap` apart bridge into one span. An isolated checkpoint proves only an instant
    (a zero-length interval), never a fabricated span."""
    ts = sorted(times)
    if not ts:
        return []
    out: List[Tuple[float, float]] = []
    start = prev = ts[0]
    for t in ts[1:]:
        if t - prev <= max_gap:
            prev = t
        else:
            out.append((start, prev))
            start = prev = t
    out.append((start, prev))
    return out


def reclassify(cloud_gaps: Iterable[Tuple[float, float]],
               monitored: Iterable[Tuple[float, float]]
               ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Split each cloud gap into (locally_monitored, still_unverified) using local checkpoint
    intervals. The cloud gap itself is NOT consumed — the caller keeps it as a cloud
    connectivity gap; this only tells availability which parts were actually observed."""
    gaps = cov.merge(list(cloud_gaps))
    mon = cov.merge(list(monitored))
    local: List[Tuple[float, float]] = []
    for lo, hi in gaps:
        local.extend(cov.clip(mon, lo, hi))
    local = cov.merge(local)
    unverified = cov.subtract(gaps, local)
    return local, unverified


__all__ = ["Transition", "dedupe", "order", "fold_current_state",
           "checkpoints_to_intervals", "reclassify",
           "effective_ts", "partition_valid", "latest_by_effective",
           "classify_transition", "classify_checkpoint", "Reconciler",
           "DEFAULT_MAX_FUTURE_SKEW"]

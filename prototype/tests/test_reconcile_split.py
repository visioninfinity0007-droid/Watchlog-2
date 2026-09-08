#!/usr/bin/env python3
"""Phase A — increment 6: reconcile_health() layer-split + PER-ID acknowledgement (both RPCs).

The agent splits ONE local batch into camera-video (+ checkpoints) -> wl_reconcile_health and
recording/storage -> wl_reconcile_recording_storage. Two independent durability rules are proven:

  * PER-SUBSET ack (cross-RPC): if one RPC fails, only its subset stays pending; the other subset is
    not lost and not re-uploaded (0046-fail/0047-ok and 0047-fail/0046-ok).
  * PER-ID ack (within-RPC): RPC success does NOT mean every row was accepted. BOTH RPCs return
    accepted_ids / duplicate_ids / rejected[{id,reason}] (and wl_reconcile_health also
    checkpoints_accepted_ids / _duplicate_ids / _rejected_ids). Only ledger-durable (accepted +
    duplicate) ids are acknowledged. Structural poison is quarantined at once (observable); a
    transient unmapped_channel is bounded-retried; a rejected checkpoint is quarantined — never lost,
    never silently acked, never infinite.

Server reconciliation is idempotent, so replaying an already-accepted subset/row later is safe.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import watchlog_agent as wa  # noqa: E402
from health_store import HealthStore  # noqa: E402

STATE = {"agent_id": "agent-xyz", "agent_key": "k"}


class FakeCloud:
    """Stand-in for the cloud RPC surface. Mirrors the PER-ID disposition contract now returned by
    BOTH wl_reconcile_health and wl_reconcile_recording_storage (accepted/duplicate/rejected over an
    idempotent server ledger), plus per-checkpoint dispositions for wl_reconcile_health."""

    def __init__(self, fail=(), reject=None, reject_cp=None):
        self.fail = set(fail)
        self.reject = dict(reject or {})        # transition dedupe_key -> reason
        self.reject_cp = dict(reject_cp or {})  # checkpoint id -> reason
        self.calls = []
        self._ledger = set()                    # transition ids the server already holds
        self._cp_ledger = set()                 # checkpoint ids the server already holds
        self.accept_counts = {}                 # id -> times NEWLY accepted (idempotency: must stay 1)

    def preload_ledger(self, *ids):
        self._ledger.update(ids)

    def _dispose(self, items, reject_map, ledger):
        accepted, duplicate, rejected = [], [], []
        for it in items:
            tid = it["id"]
            if tid in reject_map:
                rejected.append({"id": tid, "reason": reject_map[tid]})
            elif tid in ledger:
                duplicate.append(tid)                       # already ledgered -> idempotent replay
            else:
                ledger.add(tid)
                accepted.append(tid)
                self.accept_counts[tid] = self.accept_counts.get(tid, 0) + 1
        return accepted, duplicate, rejected

    def call(self, fn, **params):
        self.calls.append(fn)
        if fn in self.fail:
            raise RuntimeError(f"{fn} unavailable")
        acc, dup, rej = self._dispose(params.get("p_transitions") or [], self.reject, self._ledger)
        out = {"transitions_applied": len(acc), "transitions_duplicate": len(dup),
               "transitions_rejected": len(rej), "accepted_ids": acc, "duplicate_ids": dup,
               "rejected": rej}
        if fn == "wl_reconcile_health":
            cacc, cdup, crej = self._dispose(params.get("p_checkpoints") or [],
                                             self.reject_cp, self._cp_ledger)
            out.update({"checkpoints_applied": len(cacc), "checkpoints_accepted_ids": cacc,
                        "checkpoints_duplicate_ids": cdup, "checkpoints_rejected_ids": crej})
        return out


class ScriptedCloud:
    """Returns a FIXED response dict for every call — to test the agent's handling of malformed,
    old-style, or hostile server responses (fail-closed, intersect-with-sent, epoch-qualified ids)."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def call(self, fn, **params):
        self.calls.append(fn)
        return dict(self.response)


def _seed(tmp):
    s = HealthStore(tmp / "health.sqlite", agent_id=STATE["agent_id"])
    s.observe("camera", "1", "offline", "video_loss", "native", "t1")          # camera video
    s.observe("camera_recording", "1", "not_recording", "not_recording", "probe", "t2")
    s.observe("nvr_storage", STATE["agent_id"], "fault", "storage_fault", "probe", "t3")
    s.checkpoint("t1", "ok", 8)
    return s


def _seed_rs(tmp, channels=("1", "2", "3")):
    """Recording/storage only (no camera-video, no checkpoints) -> exercises ONLY the rec/storage RPC."""
    s = HealthStore(tmp / "health.sqlite", agent_id=STATE["agent_id"])
    for ch in channels:
        s.observe("camera_recording", ch, "not_recording", "not_recording", "probe", "t1")
    s.observe("nvr_storage", STATE["agent_id"], "fault", "storage_fault", "probe", "t2")
    return s


def _seed_cam(tmp, channels=("1", "2", "3"), checkpoints=0):
    """Camera-video (+ optional checkpoints) only -> exercises ONLY wl_reconcile_health."""
    s = HealthStore(tmp / "health.sqlite", agent_id=STATE["agent_id"])
    for ch in channels:
        s.observe("camera", ch, "offline", "video_loss", "native", "t1")
    for i in range(checkpoints):
        s.checkpoint(f"t{i}", "ok", 8)
    return s


def _pending_layers(store):
    return {t["layer"] for t in store.pending_transitions(100)}


def _pending_ids(store):
    return {t["dedupe_key"] for t in store.pending_transitions(100)}


def _tx_id(store, layer, entity):
    return next(t["dedupe_key"] for t in store.pending_transitions(100)
               if t["layer"] == layer and t["entity"] == entity)


def _cp_ids(store):
    return [c["id"] for c in store.export_batch(100)["checkpoints"]]   # epoch-qualified, as server sees them


# ============================ cross-RPC per-SUBSET acknowledgement ============================

def test_camera_reconcile_fails_only_recording_storage_uploaded(tmp_path):   # 0046 fail / 0047 ok
    s = _seed(tmp_path)
    holder = {"store": s}
    cloud = FakeCloud(fail={"wl_reconcile_health"})
    wa.reconcile_health(holder, STATE, cloud)
    assert _pending_layers(s) == {"camera"}                 # camera video + checkpoints stay pending
    assert len(s.pending_checkpoints(100)) == 1


def test_recording_storage_reconcile_fails_only_camera_uploaded(tmp_path):   # 0047 fail / 0046 ok
    s = _seed(tmp_path)
    holder = {"store": s}
    cloud = FakeCloud(fail={"wl_reconcile_recording_storage"})
    wa.reconcile_health(holder, STATE, cloud)
    assert _pending_layers(s) == {"camera_recording", "nvr_storage"}
    assert len(s.pending_checkpoints(100)) == 0             # camera video + checkpoints cleared


def test_repeated_partial_success_loses_nothing(tmp_path):
    s = _seed(tmp_path)
    holder = {"store": s}
    wa.reconcile_health(holder, STATE, FakeCloud(fail={"wl_reconcile_recording_storage"}))
    assert _pending_layers(s) == {"camera_recording", "nvr_storage"}
    wa.reconcile_health(holder, STATE, FakeCloud())
    assert s.pending_transitions(100) == []                 # everything eventually delivered, none lost


def test_both_succeed_clears_all(tmp_path):
    s = _seed(tmp_path)
    holder = {"store": s}
    cloud = FakeCloud()
    wa.reconcile_health(holder, STATE, cloud)
    assert s.pending_transitions(100) == [] and s.pending_checkpoints(100) == []
    assert set(cloud.calls) == {"wl_reconcile_health", "wl_reconcile_recording_storage"}


# ==================== within-RPC per-ID acknowledgement — recording/storage (0047) ====================

def test_within_rpc_partial_only_accepted_acknowledged(tmp_path):
    s = _seed_rs(tmp_path, channels=("1", "2", "3"))
    holder = {"store": s}
    victim = _tx_id(s, "camera_recording", "3")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={victim: "unmapped_channel"}))
    assert _pending_ids(s) == {victim}                      # only the rejected row remains
    assert s.quarantined_count() == 0                       # unmapped_channel is transient


def test_duplicate_is_acknowledged(tmp_path):
    s = _seed_rs(tmp_path, channels=("1",))
    holder = {"store": s}
    ids = [t["dedupe_key"] for t in s.pending_transitions(100)]
    cloud = FakeCloud()
    cloud.preload_ledger(ids[0])
    wa.reconcile_health(holder, STATE, cloud)
    assert s.pending_transitions(100) == [] and s.quarantined_count() == 0


def test_malformed_poison_is_quarantined_observably(tmp_path):
    s = _seed_rs(tmp_path, channels=("1", "2"))
    holder = {"store": s}
    victim = _tx_id(s, "camera_recording", "2")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={victim: "missing_state"}))
    q = {t["dedupe_key"]: t["last_reason"] for t in s.quarantined_transitions(100)}
    assert q.get(victim) == "missing_state" and victim not in _pending_ids(s)


def test_transient_unmapped_channel_remains_retryable_then_succeeds(tmp_path):
    s = _seed_rs(tmp_path, channels=("1",))
    holder = {"store": s}
    victim = _tx_id(s, "camera_recording", "1")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={victim: "unmapped_channel"}))
    assert victim in _pending_ids(s) and s.quarantined_count() == 0
    wa.reconcile_health(holder, STATE, FakeCloud())
    assert s.pending_transitions(100) == [] and s.quarantined_count() == 0


def test_retry_after_transient_success_writes_ledger_once(tmp_path):
    s = _seed_rs(tmp_path, channels=("1",))
    holder = {"store": s}
    victim = _tx_id(s, "camera_recording", "1")
    cloud = FakeCloud()
    cloud.reject = {victim: "unmapped_channel"}
    wa.reconcile_health(holder, STATE, cloud)
    cloud.reject = {}
    wa.reconcile_health(holder, STATE, cloud)
    assert s.pending_transitions(100) == []
    assert cloud.accept_counts.get(victim) == 1 and all(c == 1 for c in cloud.accept_counts.values())


def test_transient_becomes_quarantined_after_bounded_attempts(tmp_path):
    s = HealthStore(tmp_path / "health.sqlite", agent_id=STATE["agent_id"], max_transient_attempts=3)
    s.observe("camera_recording", "9", "not_recording", "not_recording", "probe", "t1")
    holder = {"store": s}
    victim = s.pending_transitions(100)[0]["dedupe_key"]
    for _ in range(3):
        assert s.quarantined_count() == 0
        wa.reconcile_health(holder, STATE, FakeCloud(reject={victim: "unmapped_channel"}))
    assert victim in {t["dedupe_key"] for t in s.quarantined_transitions(100)}
    assert s.pending_transitions(100) == []


# ============ within-RPC per-ID acknowledgement — camera-video + checkpoints (0046 parity) ============

def test_camera_video_within_rpc_partial_only_accepted(tmp_path):
    s = _seed_cam(tmp_path, channels=("1", "2", "3"))
    holder = {"store": s}
    victim = _tx_id(s, "camera", "3")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={victim: "unmapped_channel"}))
    assert _pending_ids(s) == {victim} and s.quarantined_count() == 0


def test_camera_video_duplicate_is_acknowledged(tmp_path):
    s = _seed_cam(tmp_path, channels=("1",))
    holder = {"store": s}
    dk = _tx_id(s, "camera", "1")
    cloud = FakeCloud()
    cloud.preload_ledger(dk)
    wa.reconcile_health(holder, STATE, cloud)
    assert s.pending_transitions(100) == []


def test_camera_video_malformed_poison_quarantined(tmp_path):
    s = _seed_cam(tmp_path, channels=("1",))
    holder = {"store": s}
    dk = _tx_id(s, "camera", "1")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={dk: "missing_state"}))
    assert dk in {t["dedupe_key"] for t in s.quarantined_transitions(100)} and dk not in _pending_ids(s)


def test_camera_video_transient_unmapped_eventually_succeeds(tmp_path):
    s = _seed_cam(tmp_path, channels=("1",))
    holder = {"store": s}
    dk = _tx_id(s, "camera", "1")
    wa.reconcile_health(holder, STATE, FakeCloud(reject={dk: "unmapped_channel"}))
    assert dk in _pending_ids(s) and s.quarantined_count() == 0
    wa.reconcile_health(holder, STATE, FakeCloud())
    assert s.pending_transitions(100) == []


def test_one_rejected_checkpoint_among_valid(tmp_path):
    s = _seed_cam(tmp_path, channels=(), checkpoints=3)
    holder = {"store": s}
    victim = _cp_ids(s)[1]
    wa.reconcile_health(holder, STATE, FakeCloud(reject_cp={victim: "invalid_timestamp"}))
    assert len(s.pending_checkpoints(100)) == 0             # the two valid checkpoints acknowledged
    q = s.quarantined_checkpoints(100)
    assert len(q) == 1 and q[0]["last_reason"] == "invalid_timestamp"   # rejected one parked, observable


def test_duplicate_checkpoint_is_acknowledged(tmp_path):
    s = _seed_cam(tmp_path, channels=(), checkpoints=1)
    holder = {"store": s}
    cid = _cp_ids(s)[0]
    cloud = FakeCloud()
    cloud._cp_ledger.add(cid)                               # server already holds it -> duplicate
    wa.reconcile_health(holder, STATE, cloud)
    assert s.pending_checkpoints(100) == []


def test_no_rejected_record_acked_merely_because_rpc_succeeded(tmp_path):
    # the core guarantee: RPC returns success, yet a rejected transition AND a rejected checkpoint both
    # stay OUT of 'uploaded' (quarantined), while the valid ones are acknowledged.
    s = _seed_cam(tmp_path, channels=("1", "2"), checkpoints=2)
    holder = {"store": s}
    bad_tx = _tx_id(s, "camera", "2")
    bad_cp = _cp_ids(s)[0]
    wa.reconcile_health(holder, STATE,
                        FakeCloud(reject={bad_tx: "missing_state"}, reject_cp={bad_cp: "invalid_timestamp"}))
    assert bad_tx not in _pending_ids(s)                                        # not left dangling...
    assert bad_tx in {t["dedupe_key"] for t in s.quarantined_transitions(100)}  # ...quarantined instead
    assert len(s.quarantined_checkpoints(100)) == 1                             # rejected checkpoint parked
    assert len(s.pending_checkpoints(100)) == 0                                 # valid checkpoint acked


# ==================== acknowledgement invariants: fail-closed + identity discipline ====================

def test_aggregate_only_response_acknowledges_nothing(tmp_path):
    # FAIL CLOSED: a successful RPC whose body lacks the disposition arrays (older DB function) must
    # acknowledge ZERO records — never infer acceptance from ok/aggregate counts.
    s = _seed(tmp_path)
    holder = {"store": s}
    before_tx, before_cp = _pending_ids(s), len(s.pending_checkpoints(100))
    old_style = {"ok": True, "transitions_applied": 3, "transitions_duplicate": 0,
                 "transitions_rejected": 0, "checkpoints_applied": 1}
    wa.reconcile_health(holder, STATE, ScriptedCloud(old_style))
    assert _pending_ids(s) == before_tx and len(s.pending_checkpoints(100)) == before_cp
    assert s.quarantined_count() == 0                    # nothing acked, nothing parked


def test_bogus_and_unsent_dispositions_are_ignored(tmp_path):
    # accepted/duplicate/rejected ids the agent never sent must be intersected out and ignored.
    s = _seed_rs(tmp_path, channels=("1",))
    holder = {"store": s}
    sent = _pending_ids(s)
    hostile = {"ok": True, "accepted_ids": ["not-in-batch:1"], "duplicate_ids": ["also-bogus:2"],
               "rejected": [{"id": "bogus:3", "reason": "missing_state"}]}
    wa.reconcile_health(holder, STATE, ScriptedCloud(hostile))
    assert _pending_ids(s) == sent                       # nothing acknowledged (all ids unsent)
    assert s.quarantined_count() == 0                    # unsent 'rejected' id must not quarantine ours


def test_foreign_epoch_checkpoint_id_cannot_acknowledge(tmp_path):
    # checkpoint identity is the epoch-qualified checkpoint_id; an id from another epoch must not ack
    # a local checkpoint, and a bare seq is never authoritative.
    s = _seed_cam(tmp_path, channels=(), checkpoints=1)
    holder = {"store": s}
    foreign = {"ok": True, "checkpoints_accepted_ids": [f"{STATE['agent_id']}:OTHEREPOCH:cp:1"],
               "checkpoints_duplicate_ids": ["1"]}       # bare seq — also not authoritative
    wa.reconcile_health(holder, STATE, ScriptedCloud(foreign))
    assert len(s.pending_checkpoints(100)) == 1          # still pending — foreign/bare id ignored


def test_present_but_empty_dispositions_acknowledge_nothing(tmp_path):
    # PRESENT-but-empty is a valid new-server response ("accepted/rejected nothing") — rows stay
    # pending. Distinct mechanism from the absent-contract case (early return), same safe outcome.
    s = _seed(tmp_path)
    holder = {"store": s}
    before = _pending_ids(s)
    empty = {"ok": True, "accepted_ids": [], "duplicate_ids": [], "rejected": [],
             "checkpoints_accepted_ids": [], "checkpoints_duplicate_ids": [], "checkpoints_rejected_ids": []}
    wa.reconcile_health(holder, STATE, ScriptedCloud(empty))
    assert _pending_ids(s) == before and len(s.pending_checkpoints(100)) == 1
    assert s.quarantined_count() == 0


def test_accepted_id_from_another_batch_ignored(tmp_path):
    # a well-formed id, but for a seq the agent did NOT send this RPC -> intersect with sent drops it.
    s = _seed_rs(tmp_path, channels=("1",))
    holder = {"store": s}
    sent = _pending_ids(s)
    other = f"{STATE['agent_id']}:{s.epoch}:99999"          # same agent+epoch, a seq never sent
    wa.reconcile_health(holder, STATE, ScriptedCloud(
        {"ok": True, "accepted_ids": [other], "duplicate_ids": [], "rejected": []}))
    assert _pending_ids(s) == sent                          # nothing acknowledged


def test_local_epoch_checkpoint_id_acks_exactly_that_checkpoint(tmp_path):
    # positive control: the CURRENT batch's epoch-qualified id maps back to its local seq and acks
    # EXACTLY that checkpoint — the other stays pending.
    s = _seed_cam(tmp_path, channels=(), checkpoints=2)
    holder = {"store": s}
    cids = _cp_ids(s)
    wa.reconcile_health(holder, STATE, ScriptedCloud(
        {"ok": True, "checkpoints_accepted_ids": [cids[0]], "checkpoints_duplicate_ids": [],
         "checkpoints_rejected_ids": []}))
    assert {c["id"] for c in s.export_batch(100)["checkpoints"]} == {cids[1]}   # exactly one cleared


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

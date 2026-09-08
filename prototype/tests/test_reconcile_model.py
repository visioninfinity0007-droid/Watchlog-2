#!/usr/bin/env python3
"""Phase A — increment 5: reconciliation oracle (dedupe / ordering / forward-only / coverage).

Pure spec for how the server folds a batch of locally-retained health transitions +
observation checkpoints back in after a cloud outage, WITHOUT (a) duplicating on resend,
(b) reordering by upload arrival, (c) regressing current state on a late upload, or (d)
inventing a camera outage for a cloud gap. Checkpoints can prove a cloud-gap window was
locally monitored — which reduces the UNVERIFIED-for-availability portion while the window
stays a cloud connectivity gap. wl_reconcile_health (0046) mirrors this; the contract pins it.

Red before reconcile_model.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import reconcile_model as rc  # noqa: E402
from reconcile_model import Transition, dedupe, order, fold_current_state, \
    checkpoints_to_intervals, reclassify, effective_ts, partition_valid, \
    latest_by_effective, classify_transition, classify_checkpoint, \
    DEFAULT_MAX_FUTURE_SKEW  # noqa: E402


def T(id, seq, to, device_ts, *, layer="camera", entity="1", frm=None,
      reason="ok", source="probe"):
    return Transition(id=id, seq=seq, layer=layer, entity=entity, frm=frm, to=to,
                      reason=reason, source=source, device_ts=device_ts)


# --- dedupe / idempotent resend -----------------------------------------------

def test_dedupe_drops_already_seen_ids():
    incoming = [T("a:1", 1, "offline", 100), T("a:2", 2, "operational", 200)]
    assert [t.id for t in dedupe({"a:1"}, incoming)] == ["a:2"]


def test_resending_same_batch_twice_yields_nothing_new():
    batch = [T("a:1", 1, "offline", 100), T("a:2", 2, "operational", 200)]
    first = dedupe(set(), batch)
    seen = {t.id for t in first}
    second = dedupe(seen, batch)          # exact resend
    assert first and second == []


def test_replay_within_one_epoch_is_idempotent():
    batch = [T("A:e1:1", 1, "offline", 100), T("A:e1:2", 2, "operational", 200)]
    seen = {t.id for t in dedupe(set(), batch)}
    assert dedupe(seen, batch) == []


def test_same_seq_from_a_later_epoch_is_new_evidence():
    # after a local-store corruption rebuild the seq restarts at 1, but the epoch differs, so
    # the reconstituted id is NOT a replay — it must be accepted, not silently discarded.
    existing = {"A:e1:1"}
    later = [T("A:e2:1", 1, "offline", 500)]      # seq 1 again, new epoch
    assert [t.id for t in dedupe(existing, later)] == ["A:e2:1"]


# --- ordering by OBSERVED time, never upload arrival --------------------------

def test_order_is_by_device_ts_then_seq():
    # uploaded out of order; must sort by observed time
    got = order([T("a:3", 3, "operational", 300), T("a:1", 1, "offline", 100),
                 T("a:2", 2, "degraded", 200)])
    assert [t.id for t in got] == ["a:1", "a:2", "a:3"]


def test_order_breaks_ties_by_seq_not_arrival():
    got = order([T("a:2", 2, "operational", 100), T("a:1", 1, "offline", 100)])
    assert [t.id for t in got] == ["a:1", "a:2"]      # same ts -> seq order


# --- forward-only current state ------------------------------------------------

def test_multiple_transitions_fold_to_latest_observed():
    txs = [T("a:1", 1, "offline", 100, entity="1"),
           T("a:2", 2, "operational", 200, entity="1")]
    state, advanced = fold_current_state(txs, prior={})
    assert state["1"].to == "operational" and state["1"].device_ts == 200
    assert advanced == {"1"}


def test_late_upload_does_not_regress_current_state():
    # current already at observed ts=200 (operational); a late-arriving ts=100 offline
    prior = {"1": T("a:2", 2, "operational", 200, entity="1")}
    late = [T("a:1", 1, "offline", 100, entity="1")]
    state, advanced = fold_current_state(late, prior=prior)
    assert state["1"].to == "operational" and state["1"].device_ts == 200   # unchanged
    assert advanced == set()                                                # nothing moved


def test_out_of_order_batch_still_lands_on_latest():
    prior = {"1": T("a:0", 0, "unknown", 0, entity="1")}
    batch = [T("a:2", 2, "operational", 200, entity="1"),
             T("a:1", 1, "offline", 100, entity="1")]     # reversed on the wire
    state, _ = fold_current_state(batch, prior=prior)
    assert state["1"].to == "operational" and state["1"].device_ts == 200


def test_camera_fault_reconciles_to_observed_time_not_upload_time():
    # the fault happened at observed ts=150 during the gap; upload happens "now" (ts irrelevant)
    txs = [T("a:9", 9, "offline", 150, entity="4", source="native", reason="video_loss")]
    state, _ = fold_current_state(txs, prior={})
    assert state["4"].device_ts == 150 and state["4"].source == "native"


# --- checkpoints -> local monitoring intervals --------------------------------

def test_checkpoints_bridge_into_a_monitored_interval():
    # checkpoints every ~60s, max_gap 90 -> one continuous monitored span
    ivals = checkpoints_to_intervals([100, 160, 220, 280], max_gap=90)
    assert ivals == [(100, 280)]


def test_isolated_checkpoint_does_not_fabricate_a_span():
    ivals = checkpoints_to_intervals([100, 1000], max_gap=90)   # far apart
    # each proves an instant, not a span -> no coverage-worthy interval
    assert all(e - s == 0 for s, e in ivals)


# --- reclassify a cloud gap: locally monitored vs still unverified ------------

def test_internet_gap_with_checkpoints_becomes_locally_monitored():
    cloud_gaps = [(100, 400)]                       # cloud could not receive for [100,400]
    monitored = [(100, 400)]                        # but local checkpoints covered it
    local, unverified = reclassify(cloud_gaps, monitored)
    assert local == [(100, 400)] and unverified == []      # coverage recovered
    # NOTE: the caller keeps cloud_gaps intact — this was still a cloud connectivity gap


def test_pc_off_interval_with_no_checkpoints_stays_unverified():
    cloud_gaps = [(100, 400)]
    monitored = []                                  # PC was off -> nothing observed locally
    local, unverified = reclassify(cloud_gaps, monitored)
    assert local == [] and unverified == [(100, 400)]      # truly unverified, not invented


def test_partial_local_coverage_splits_the_gap():
    cloud_gaps = [(0, 300)]
    monitored = [(0, 100)]                          # agent alive first 100s, then PC died
    local, unverified = reclassify(cloud_gaps, monitored)
    assert local == [(0, 100)] and unverified == [(100, 300)]


# --- clock-skew: a wild future device clock must not freeze current state ------

SKEW = DEFAULT_MAX_FUTURE_SKEW   # seconds


def test_future_skew_24h_is_clamped_not_a_future_watermark():
    now = 1000.0
    eff, clamped = effective_ts(now + 86400, now, SKEW)   # +24h
    assert clamped is True and eff == now                 # watermark = now, NOT tomorrow


def test_small_allowed_skew_preserves_observed_ordering():
    now = 1000.0
    a, ca = effective_ts(now + 30, now, SKEW)             # within tolerance
    b, cb = effective_ts(now + 60, now, SKEW)
    assert ca is False and cb is False and a < b          # relative order preserved


def test_future_skewed_transition_keeps_raw_but_effective_is_distinct():
    now = 1000.0
    raw = now + 86400
    eff, clamped = effective_ts(raw, now, SKEW)
    assert clamped and eff != raw and eff == now          # raw preserved for evidence; effective distinct


def test_valid_state_can_advance_after_a_clamp():
    now = 1000.0
    clamped_eff, _ = effective_ts(now + 86400, now, SKEW)          # frozen? no -> clamped to 1000
    later_eff, _ = effective_ts(now + 300, now + 400, SKEW)        # a later genuine observation
    assert clamped_eff == 1000.0 and later_eff > clamped_eff       # so it advances normally


# --- poison rows: one malformed row must not fail the whole batch --------------

def test_malformed_row_does_not_reject_valid_rows_in_the_batch():
    rows = [{"id": "a:e:1", "to": "offline", "device_ts": 100.0},
            {"id": "a:e:2", "to": "offline", "device_ts": "not-a-timestamp"},  # poison
            {"id": "a:e:3", "to": "operational", "device_ts": 200.0}]
    valid, rejected = partition_valid(rows)
    assert [r["id"] for r in valid] == ["a:e:1", "a:e:3"]
    assert [r["id"] for r in rejected] == ["a:e:2"]        # isolated, not fatal


def test_rejected_records_are_observable():
    rows = [{"id": "", "to": "offline", "device_ts": 1.0},     # no id
            {"id": "a:e:1", "to": "", "device_ts": 1.0},       # no state
            {"id": "a:e:2", "to": "offline", "device_ts": None}]  # bad ts
    valid, rejected = partition_valid(rows)
    assert valid == [] and len(rejected) == 3                  # all counted (observable)


# --- current-state tie ordering must be by NUMERIC seq, not lexical id --------

def E(entity, seq, to, device_ts):
    return T(f"a:e:{seq}", seq, to, device_ts, entity=entity)


def test_seq_9_vs_10_at_same_effective_time_10_wins():
    now = 1000.0
    win = latest_by_effective([E("1", 9, "offline", now), E("1", 10, "operational", now)], now)
    assert win["1"].seq == 10 and win["1"].to == "operational"   # lexical "9">"10" would be wrong


def test_twelve_clamped_to_same_effective_latest_seq_wins():
    now = 1000.0
    txs = [E("1", s, "offline", now + 86400) for s in range(1, 13)]   # all clamp to now
    win = latest_by_effective(txs, now)
    assert win["1"].seq == 12                                    # highest numeric seq


def test_reversed_upload_order_still_latest_seq_wins():
    now = 1000.0
    txs = list(reversed([E("1", s, "offline", now) for s in range(1, 11)]))
    win = latest_by_effective(txs, now)
    assert win["1"].seq == 10                                    # array order is irrelevant


def test_recovery_after_offline_same_time_recovery_wins():
    now = 1000.0
    txs = [E("1", 5, "offline", now), E("1", 6, "operational", now)]   # recovery has later seq
    win = latest_by_effective(txs, now)
    assert win["1"].to == "operational" and win["1"].seq == 6


# --- classification: received = valid + rejected, categorized --------------------

KNOWN = ["1", "2", "3"]


def test_transition_received_equals_valid_plus_rejected():
    batch = [
        {"id": "a:e:1", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": 1, "entity": "1"},
        {"id": "", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": 2, "entity": "1"},      # missing_id
        {"id": "a:e:3", "layer": "nvr", "to": "offline", "device_ts": 100.0, "seq": 3, "entity": "1"},    # wrong_layer
        {"id": "a:e:4", "layer": "camera", "to": "", "device_ts": 100.0, "seq": 4, "entity": "1"},        # missing_state
        {"id": "a:e:5", "layer": "camera", "to": "offline", "device_ts": "bad", "seq": 5, "entity": "1"}, # invalid_timestamp
        {"id": "a:e:6", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": "x", "entity": "1"},# invalid_sequence
        {"id": "a:e:7", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": 7, "entity": "9"}, # unmapped_channel
    ]
    verdicts = [classify_transition(r, KNOWN) for r in batch]
    valid = [v for v in verdicts if v == "valid"]
    rejected = [v for v in verdicts if v != "valid"]
    assert len(valid) + len(rejected) == len(batch)             # the invariant
    assert valid == ["valid"]
    assert set(rejected) == {"missing_id", "wrong_layer", "missing_state",
                             "invalid_timestamp", "invalid_sequence", "unmapped_channel"}


def test_one_bad_seq_does_not_reject_the_valid_transitions():
    batch = [
        {"id": "a:e:1", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": 1, "entity": "1"},
        {"id": "a:e:2", "layer": "camera", "to": "offline", "device_ts": 100.0, "seq": None, "entity": "2"},
        {"id": "a:e:3", "layer": "camera", "to": "operational", "device_ts": 200.0, "seq": 3, "entity": "3"},
    ]
    v = [classify_transition(r, KNOWN) for r in batch]
    assert v == ["valid", "invalid_sequence", "valid"]          # bad seq isolated, others survive


# --- checkpoints: malformed metadata is safe-defaulted, not poison --------------

def test_checkpoint_received_equals_valid_plus_rejected():
    batch = [
        {"id": "a:e:cp:1", "device_ts": 100.0, "seq": 1, "cameras_observed": 8, "cycle_ok": True},
        {"id": "", "device_ts": 100.0},                           # missing_id
        {"id": "a:e:cp:3", "device_ts": "bad"},                   # invalid_timestamp
    ]
    v = [classify_checkpoint(r) for r in batch]
    assert [x for x in v if x == "valid"] == ["valid"]
    assert len([x for x in v if x != "valid"]) == 2
    assert len(v) == len(batch)                                  # received = valid + rejected


def test_one_bad_seq_or_metadata_does_not_poison_valid_checkpoints():
    # a bad seq / cameras_observed / boolean is metadata: the checkpoint stays VALID (defaulted)
    for bad in ({"seq": "x"}, {"cameras_observed": "NaN"}, {"cycle_ok": "maybe"}):
        row = {"id": "a:e:cp:1", "device_ts": 100.0, **bad}
        assert classify_checkpoint(row) == "valid"               # kept, not rejected, never fatal


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

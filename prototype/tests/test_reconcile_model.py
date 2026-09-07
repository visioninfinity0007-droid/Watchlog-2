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
    checkpoints_to_intervals, reclassify  # noqa: E402


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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

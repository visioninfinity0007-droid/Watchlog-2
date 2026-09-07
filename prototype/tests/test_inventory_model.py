#!/usr/bin/env python3
"""Phase A — increment 3: server-side INVENTORY derivation oracle.

The cloud is authoritative for "which channels SHOULD exist" (the cameras table), so it —
not the agent — decides PRESENT/MISSING/DISABLED/UNKNOWN by comparing the agent's reported
channel set against its own cameras. This module is the spec; wl_report_health (0044)
mirrors it and test_health_report_contract.py pins them together.

Rules (design §5/§6):
  * only when the recorder is reachable AND enumeration succeeded can we classify;
    otherwise every known channel is UNKNOWN (never MISSING — absence we could not observe
    is not removal).
  * a known channel the recorder no longer reports -> MISSING (operator removed it).
  * a reported channel flagged disabled -> DISABLED (never OFFLINE — it is not broken).
  * transitions are emitted only when a channel's state actually changes (idempotency).

Red before inventory_model.py exists.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
sys.path.insert(0, str(ROOT / "prototype" / "agent"))

import inventory_model as inv  # noqa: E402
from inventory_model import classify_inventory, diff_transitions, PRESENT, MISSING, DISABLED, UNKNOWN  # noqa: E402

EIGHT = [str(i) for i in range(1, 9)]


def test_good_nvr_all_present():
    states, unmapped = classify_inventory(EIGHT, reported=EIGHT, disabled=[], determinable=True)
    assert all(v == PRESENT for v in states.values()) and len(states) == 8
    assert unmapped == set()


def test_removed_channel_is_missing():
    states, _ = classify_inventory(EIGHT, reported=[str(i) for i in range(1, 8)],
                                   disabled=[], determinable=True)
    assert states["8"] == MISSING
    assert all(states[c] == PRESENT for c in EIGHT if c != "8")


def test_disabled_channel_is_disabled_not_missing_or_offline():
    states, _ = classify_inventory(EIGHT, reported=EIGHT, disabled=["3"], determinable=True)
    assert states["3"] == DISABLED
    assert all(states[c] == PRESENT for c in EIGHT if c != "3")


def test_enumeration_failure_makes_all_unknown_not_missing():
    states, unmapped = classify_inventory(EIGHT, reported=[], disabled=[], determinable=False)
    assert all(v == UNKNOWN for v in states.values())     # NOT missing — we could not look
    assert unmapped == set()


def test_upper_layer_down_makes_all_unknown():
    # nvr unreachable/auth-failed is passed through as determinable=False
    states, _ = classify_inventory(EIGHT, reported=[], disabled=[], determinable=False)
    assert all(v == UNKNOWN for v in states.values())


def test_reported_channel_not_in_cameras_is_unmapped_not_persisted():
    states, unmapped = classify_inventory(["1", "2"], reported=["1", "2", "9"],
                                          disabled=[], determinable=True)
    assert set(states) == {"1", "2"} and unmapped == {"9"}   # ch9 not a known camera


def test_diff_is_empty_when_nothing_changed():   # idempotency
    s = {"1": PRESENT, "2": MISSING}
    assert diff_transitions(s, s) == []


def test_diff_reports_only_changed_channels():
    prev = {"1": PRESENT, "2": PRESENT, "3": PRESENT}
    new = {"1": PRESENT, "2": DISABLED, "3": MISSING}
    got = sorted(diff_transitions(prev, new))
    assert got == [("2", PRESENT, DISABLED), ("3", PRESENT, MISSING)]


def test_diff_first_observation_transitions_from_none():
    new = {"1": PRESENT, "2": PRESENT}
    got = sorted(diff_transitions({}, new))
    assert got == [("1", None, PRESENT), ("2", None, PRESENT)]


def test_constants_match_health_model_domain():
    import health_model as hm
    assert {PRESENT, MISSING, DISABLED, UNKNOWN} == {m.value for m in hm.Inventory}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

#!/usr/bin/env python3
"""Phase A — increment 6: recording + storage classifier (pure), tightened vendor-truth semantics.

Rules (design §5/§6, §26.3-4, increment-6 review):
  * READ don't infer; config-only ('should record') -> UNKNOWN; a JPEG never proves recording.
  * upper layer down -> recording+storage UNKNOWN with cause.
  * storage: healthy -> OK; low-space/partial-but-usable -> DEGRADED (not FAULT); no usable storage
    -> FAULT; unsupported/ambiguous -> UNKNOWN.
  * a storage FAULT dominates recording (STORAGE_FAULT); a storage DEGRADED does NOT blanket-fault.
  * MISSING/DISABLED inventory -> recording UNKNOWN, never NOT_RECORDING.

Red before the classifier gains these semantics.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from recording_model import (classify_storage, classify_recording,  # noqa: E402
                             STORAGE_OK, STORAGE_DEGRADED, STORAGE_FAULT, STORAGE_UNKNOWN,
                             REC_RECORDING, REC_NOT, REC_STORAGE_FAULT, REC_UNKNOWN)


def rec(**kw):
    base = dict(nvr_state="ok", storage_state=STORAGE_OK, inventory_state="present",
               supported=True, raw_channel_state="recording")
    base.update(kw)
    return classify_recording(**base)


# --- storage ------------------------------------------------------------------

def test_storage_ok():
    assert classify_storage(nvr_state="ok", supported=True, raw_state="ok")[0] == STORAGE_OK


def test_storage_fault_read_is_fault():
    assert classify_storage(nvr_state="ok", supported=True, raw_state="fault")[0] == STORAGE_FAULT


def test_low_space_is_degraded_not_fault():
    assert classify_storage(nvr_state="ok", supported=True, raw_state="degraded")[0] == STORAGE_DEGRADED
    # a native StorageLowSpace with no status read is still only DEGRADED
    assert classify_storage(nvr_state="ok", supported=False, raw_state=None,
                            native_lowspace=True)[0] == STORAGE_DEGRADED


def test_native_fatal_is_fault():
    st, _ = classify_storage(nvr_state="ok", supported=True, raw_state="ok", native_fatal=True)
    assert st == STORAGE_FAULT           # StorageFailure/NotExist -> no usable storage


def test_storage_unsupported_or_unreadable_is_unknown():
    assert classify_storage(nvr_state="ok", supported=False, raw_state=None)[0] == STORAGE_UNKNOWN
    assert classify_storage(nvr_state="ok", supported=True, raw_state=None)[0] == STORAGE_UNKNOWN


def test_storage_upper_layer_down_is_unknown_with_cause():
    assert classify_storage(nvr_state="unreachable", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "nvr_unreachable")
    assert classify_storage(nvr_state="auth_failed", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "nvr_auth_failed")
    assert classify_storage(nvr_state="unknown", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "agent_unreachable")


# --- recording ----------------------------------------------------------------

def test_recording_active_evidence():
    assert rec(raw_channel_state="recording")[0] == REC_RECORDING


def test_recording_disabled_is_not_recording():               # §26.4
    st, reason = rec(raw_channel_state="not_recording")
    assert st == REC_NOT and reason == "not_recording"


def test_config_only_is_unknown_not_recording():
    # driver could only read a schedule/mode ('should record') -> None -> UNKNOWN, never RECORDING
    assert rec(raw_channel_state=None)[0] == REC_UNKNOWN


def test_storage_fault_forces_recording_storage_fault():       # §26.3, dominates even if video is fine
    st, reason = rec(storage_state=STORAGE_FAULT, raw_channel_state="recording")
    assert st == REC_STORAGE_FAULT and reason == "storage_fault"


def test_storage_degraded_does_not_blanket_fault_recording():
    # low space -> the channel keeps its own recording state, NOT a blanket STORAGE_FAULT
    assert rec(storage_state=STORAGE_DEGRADED, raw_channel_state="recording")[0] == REC_RECORDING
    assert rec(storage_state=STORAGE_DEGRADED, raw_channel_state="not_recording")[0] == REC_NOT


def test_recording_unsupported_is_unknown():
    assert rec(supported=False, raw_channel_state=None)[0] == REC_UNKNOWN


def test_missing_or_disabled_inventory_is_unknown_never_not_recording():
    assert rec(inventory_state="missing", raw_channel_state="not_recording") == (REC_UNKNOWN, "channel_missing")
    assert rec(inventory_state="disabled", raw_channel_state="not_recording") == (REC_UNKNOWN, "channel_disabled")


def test_recording_upper_layer_down_is_unknown():
    st, reason = rec(nvr_state="unreachable", storage_state=STORAGE_UNKNOWN, raw_channel_state="recording")
    assert st == REC_UNKNOWN and reason == "nvr_unreachable"


def test_recording_independent_of_video_health():
    # no camera health input exists here — OPERATIONAL video + NOT_RECORDING is a valid pairing
    assert rec(raw_channel_state="not_recording")[0] == REC_NOT


def test_constants_match_domains():
    import health_model as hm
    assert {REC_RECORDING, REC_NOT, REC_STORAGE_FAULT, REC_UNKNOWN} == {m.value for m in hm.RecordingState}
    assert {STORAGE_OK, STORAGE_DEGRADED, STORAGE_FAULT, STORAGE_UNKNOWN} == {"ok", "degraded", "fault", "unknown"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

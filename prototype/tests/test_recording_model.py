#!/usr/bin/env python3
"""Phase A — increment 6: recording + storage health classifier (pure).

The rules (design §5/§6, §26.3-4):
  * recording/storage is read from the vendor storage/record API; where the API is missing or
    unreadable it is explicitly UNKNOWN — WatchLog NEVER infers "recording" from a snapshot.
  * an upper layer down (NVR unreachable/auth-failed/unknown) makes recording+storage UNKNOWN.
  * a storage FAULT dominates: a channel over faulted storage is recording_state STORAGE_FAULT,
    even if its video is fine (a JPEG proves an image, not that the NVR is recording it).
  * recording is INDEPENDENT of camera health: a camera can be OPERATIONAL yet NOT_RECORDING.

Red before recording_model.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import recording_model as rm  # noqa: E402
from recording_model import (classify_storage, classify_recording,  # noqa: E402
                             STORAGE_OK, STORAGE_DEGRADED, STORAGE_FAULT, STORAGE_UNKNOWN,
                             REC_RECORDING, REC_NOT, REC_STORAGE_FAULT, REC_UNKNOWN)


# --- storage ------------------------------------------------------------------

def test_storage_ok():
    assert classify_storage(nvr_state="ok", supported=True, raw_state="ok")[0] == STORAGE_OK


def test_storage_fault_read():
    st, reason = classify_storage(nvr_state="ok", supported=True, raw_state="fault")
    assert st == STORAGE_FAULT and reason in ("storage_fault", "disk_error")


def test_storage_low_space_is_degraded():
    assert classify_storage(nvr_state="ok", supported=True, raw_state="degraded")[0] == STORAGE_DEGRADED


def test_storage_native_fault_dominates():
    # a native StorageFailure/StorageNotExist event -> fault even if the status read looked ok
    st, _ = classify_storage(nvr_state="ok", supported=True, raw_state="ok", native_fault=True)
    assert st == STORAGE_FAULT


def test_storage_unsupported_is_unknown_not_ok():
    assert classify_storage(nvr_state="ok", supported=False, raw_state=None)[0] == STORAGE_UNKNOWN


def test_storage_unreadable_is_unknown():
    assert classify_storage(nvr_state="ok", supported=True, raw_state=None)[0] == STORAGE_UNKNOWN


def test_storage_upper_layer_down_is_unknown_with_cause():
    assert classify_storage(nvr_state="unreachable", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "nvr_unreachable")
    assert classify_storage(nvr_state="auth_failed", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "nvr_auth_failed")
    assert classify_storage(nvr_state="unknown", supported=True, raw_state="ok") == (STORAGE_UNKNOWN, "agent_unreachable")


# --- recording ----------------------------------------------------------------

def test_recording_on_with_healthy_storage():
    st, _ = classify_recording(nvr_state="ok", storage_state=STORAGE_OK, supported=True,
                               raw_channel_state="recording")
    assert st == REC_RECORDING


def test_recording_disabled_is_not_recording():   # §26.4
    st, reason = classify_recording(nvr_state="ok", storage_state=STORAGE_OK, supported=True,
                                    raw_channel_state="not_recording")
    assert st == REC_NOT and reason == "not_recording"


def test_storage_fault_makes_recording_storage_fault():   # §26.3 — dominates even if video works
    st, reason = classify_recording(nvr_state="ok", storage_state=STORAGE_FAULT, supported=True,
                                    raw_channel_state="recording")
    assert st == REC_STORAGE_FAULT and reason == "storage_fault"


def test_recording_unsupported_is_unknown_not_recording():
    st, _ = classify_recording(nvr_state="ok", storage_state=STORAGE_OK, supported=False,
                               raw_channel_state=None)
    assert st == REC_UNKNOWN            # never assume recording when we cannot read it


def test_recording_unreadable_channel_is_unknown():
    st, _ = classify_recording(nvr_state="ok", storage_state=STORAGE_OK, supported=True,
                               raw_channel_state=None)
    assert st == REC_UNKNOWN


def test_recording_upper_layer_down_is_unknown():
    st, reason = classify_recording(nvr_state="unreachable", storage_state=STORAGE_UNKNOWN,
                                    supported=True, raw_channel_state="recording")
    assert st == REC_UNKNOWN and reason == "nvr_unreachable"


def test_recording_is_independent_of_camera_video_health():
    # nothing about camera OPERATIONAL/OFFLINE is an input here — recording stands on its own
    st, _ = classify_recording(nvr_state="ok", storage_state=STORAGE_OK, supported=True,
                               raw_channel_state="not_recording")
    assert st == REC_NOT               # OPERATIONAL video + NOT_RECORDING is a valid, distinct state


def test_constants_match_domains():
    import health_model as hm
    assert {REC_RECORDING, REC_NOT, REC_STORAGE_FAULT, REC_UNKNOWN} == {m.value for m in hm.RecordingState}
    assert {STORAGE_OK, STORAGE_DEGRADED, STORAGE_FAULT, STORAGE_UNKNOWN} == {"ok", "degraded", "fault", "unknown"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

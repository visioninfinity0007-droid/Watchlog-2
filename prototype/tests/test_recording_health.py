#!/usr/bin/env python3
"""Phase A — increment 6: AGENT-side recording + storage assessment.

assess_recording_storage() reads the recorder's vendor storage/record API (only when the NVR
layer is up) and produces the cloud report: NVR storage state + per-channel recording state.
It never probes the recorder through a down NVR, never infers recording from a snapshot, and
never puts a secret into the payload.

Red before recording_health.py exists.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))
sys.path.insert(0, str(ROOT / "prototype" / "server"))

import recording_health  # noqa: E402
from recording_health import assess_recording_storage  # noqa: E402
from drivers.base import DriverError  # noqa: E402

SECRET_PW = "sup3rSecretRecorderPw!"


class FakeDriver:
    def __init__(self, *, storage=None, recording=None, storage_exc=None, recording_exc=None):
        self._storage = storage
        self._recording = recording
        self._storage_exc = storage_exc
        self._recording_exc = recording_exc
        self.username = "admin"
        self.password = SECRET_PW
        self.base_url = "http://192.168.1.108"

    def storage_status(self):
        if self._storage_exc:
            raise self._storage_exc
        return self._storage

    def recording_status(self, channels=None):
        if self._recording_exc:
            raise self._recording_exc
        return self._recording

    def get_snapshot(self, ch):
        raise AssertionError("recording/storage must not be inferred from a snapshot")


EIGHT = [str(i) for i in range(1, 9)]


def test_good_nvr_storage_ok_all_recording():
    d = FakeDriver(storage={"supported": True, "state": "ok"},
                   recording={"supported": True, "channels": {c: "recording" for c in EIGHT}})
    r = assess_recording_storage(d, EIGHT, nvr_state="ok")
    assert r["storage"]["state"] == "ok"
    assert all(c["state"] == "recording" for c in r["recording"]["channels"])


def test_recording_disabled_channel():
    rec = {"supported": True, "channels": {c: "recording" for c in EIGHT}}
    rec["channels"]["3"] = "not_recording"
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec)
    by = {c["channel"]: c for c in assess_recording_storage(d, EIGHT, "ok")["recording"]["channels"]}
    assert by["3"]["state"] == "not_recording" and by["1"]["state"] == "recording"


def test_storage_fault_forces_recording_storage_fault():
    d = FakeDriver(storage={"supported": True, "state": "fault"},
                   recording={"supported": True, "channels": {c: "recording" for c in EIGHT}})
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "fault"
    assert all(c["state"] == "storage_fault" for c in r["recording"]["channels"])


def test_unsupported_storage_and_recording_are_unknown():
    d = FakeDriver(storage={"supported": False, "state": None},
                   recording={"supported": False, "channels": {}})
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "unknown"
    assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_driver_errors_degrade_to_unknown_not_crash():
    d = FakeDriver(storage_exc=DriverError("storageDevice: HTTP 500"),
                   recording_exc=DriverError("RecordMode: HTTP 404"))
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "unknown"
    assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_nvr_down_makes_everything_unknown_without_probing():
    # storage_status/recording_status must NOT be called when the NVR layer is down
    d = FakeDriver(storage_exc=AssertionError("must not probe a down recorder"),
                   recording_exc=AssertionError("must not probe a down recorder"))
    r = assess_recording_storage(d, EIGHT, nvr_state="unreachable")
    assert r["storage"]["state"] == "unknown" and r["storage"]["reason"] == "nvr_unreachable"
    assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_no_secrets_in_payload():
    d = FakeDriver(storage={"supported": True, "state": "ok"},
                   recording={"supported": True, "channels": {c: "recording" for c in EIGHT}})
    blob = json.dumps(assess_recording_storage(d, EIGHT, "ok")).lower()
    for secret in (SECRET_PW.lower(), "admin", "password", "authorization", "192.168.1.108", "http://"):
        assert secret not in blob


def test_repeated_assessment_is_identical():
    d = FakeDriver(storage={"supported": True, "state": "ok"},
                   recording={"supported": True, "channels": {c: "recording" for c in EIGHT}})
    assert assess_recording_storage(d, EIGHT, "ok") == assess_recording_storage(d, EIGHT, "ok")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

#!/usr/bin/env python3
"""Phase A — increment 6: AGENT-side recording + storage assessment.

Reads the recorder's vendor API ONLY when the NVR layer is up; degrades any read failure to
UNKNOWN; honours inventory (MISSING/DISABLED -> recording UNKNOWN); never infers recording from a
snapshot; never puts a secret in the payload.

Red before recording_health.py gains inventory awareness / tightened semantics.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))
sys.path.insert(0, str(ROOT / "prototype" / "server"))

from recording_health import assess_recording_storage  # noqa: E402
from drivers.base import DriverError  # noqa: E402

SECRET_PW = "sup3rSecretRecorderPw!"


class FakeDriver:
    def __init__(self, *, storage=None, recording=None, storage_exc=None, recording_exc=None):
        self._storage = storage
        self._recording = recording
        self._storage_exc = storage_exc
        self._recording_exc = recording_exc
        self.username, self.password, self.base_url = "admin", SECRET_PW, "http://192.168.1.108"

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


def rec_map(default="recording", **overrides):
    m = {c: default for c in EIGHT}
    m.update(overrides)
    return {"supported": True, "channels": m}


def by_channel(report):
    return {c["channel"]: c for c in report["recording"]["channels"]}


def test_good_nvr_storage_ok_all_recording():
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec_map())
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "ok"
    assert all(c["state"] == "recording" for c in r["recording"]["channels"])


def test_recording_disabled_channel_is_not_recording():
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec_map(**{"3": "not_recording"}))
    b = by_channel(assess_recording_storage(d, EIGHT, "ok"))
    assert b["3"]["state"] == "not_recording" and b["1"]["state"] == "recording"


def test_storage_fault_forces_recording_storage_fault():
    d = FakeDriver(storage={"supported": True, "state": "fault"}, recording=rec_map())
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "fault"
    assert all(c["state"] == "storage_fault" for c in r["recording"]["channels"])


def test_storage_degraded_does_not_blanket_fault_cameras():
    d = FakeDriver(storage={"supported": True, "state": "degraded"}, recording=rec_map())
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "degraded"
    assert all(c["state"] == "recording" for c in r["recording"]["channels"])   # NOT storage_fault


def test_unsupported_apis_are_unknown():
    d = FakeDriver(storage={"supported": False, "state": None}, recording={"supported": False, "channels": {}})
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "unknown"
    assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_config_only_recording_is_unknown_not_recording():
    # a driver that can only read the mode/schedule returns None per channel -> UNKNOWN
    d = FakeDriver(storage={"supported": True, "state": "ok"},
                   recording={"supported": True, "channels": {c: None for c in EIGHT}})
    assert all(c["state"] == "unknown" for c in assess_recording_storage(d, EIGHT, "ok")["recording"]["channels"])


def test_mixed_recording_and_unknown_not_all_claimed_recording():
    m = {c: "recording" for c in EIGHT}
    m["5"], m["6"] = None, None                     # unreadable on two channels
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording={"supported": True, "channels": m})
    b = by_channel(assess_recording_storage(d, EIGHT, "ok"))
    assert b["5"]["state"] == "unknown" and b["6"]["state"] == "unknown"
    assert b["1"]["state"] == "recording"           # the readable ones are recording; the unknowns stay unknown


def test_missing_or_disabled_inventory_channel_is_unknown():
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec_map(**{"7": "not_recording"}))
    inv = {"7": "missing", "8": "disabled"}
    b = by_channel(assess_recording_storage(d, EIGHT, "ok", inventory=inv))
    assert b["7"]["state"] == "unknown" and b["7"]["reason"] == "channel_missing"
    assert b["8"]["state"] == "unknown" and b["8"]["reason"] == "channel_disabled"


def test_nvr_unreachable_or_auth_failed_unknown_without_probing():
    for nvr, reason in (("unreachable", "nvr_unreachable"), ("auth_failed", "nvr_auth_failed")):
        d = FakeDriver(storage_exc=AssertionError("must not probe a down recorder"),
                       recording_exc=AssertionError("must not probe a down recorder"))
        r = assess_recording_storage(d, EIGHT, nvr_state=nvr)
        assert r["storage"]["state"] == "unknown" and r["storage"]["reason"] == reason
        assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_driver_errors_degrade_to_unknown_not_crash():
    d = FakeDriver(storage_exc=DriverError("storageDevice: HTTP 500"),
                   recording_exc=DriverError("RecordMode: HTTP 404"))
    r = assess_recording_storage(d, EIGHT, "ok")
    assert r["storage"]["state"] == "unknown"
    assert all(c["state"] == "unknown" for c in r["recording"]["channels"])


def test_no_secrets_in_payload():
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec_map())
    blob = json.dumps(assess_recording_storage(d, EIGHT, "ok")).lower()
    for secret in (SECRET_PW.lower(), "admin", "password", "authorization", "192.168.1.108", "http://"):
        assert secret not in blob


def test_repeated_assessment_is_identical():
    d = FakeDriver(storage={"supported": True, "state": "ok"}, recording=rec_map())
    assert assess_recording_storage(d, EIGHT, "ok") == assess_recording_storage(d, EIGHT, "ok")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

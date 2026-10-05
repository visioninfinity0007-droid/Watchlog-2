#!/usr/bin/env python3
"""Setup's "Recorder" / "Unknown model" placeholders are never stored as observed identity.

test_recorder shows a recorder that reported no vendor or model as "Recorder" /
"Unknown model". The 5.1 registry paths (Manage Recorders > Add, an additional recorder in
the first install, the reinstall re-point of the continuity recorder) stored those display
strings as the row's vendor and model, so every wl_sync_recorders descriptor sent "Unknown
model" to WatchLog as if a recorder had reported it, and update_observed_identity (which only
overwrites with a truthy value) never cleared it. Unknown now stays None in recorders.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))

import recorder_registry as rr  # noqa: E402
import setup_backend as sb  # noqa: E402
from test_multi_recorder_initial_install import _Env  # noqa: E402


def _tested(**reported):
    """What test_recorder returns for a recorder that reported only ``reported``."""
    return {"url": "http://192.0.2.30", "driver": "onvif", "firmware": "",
            "serial": reported.get("serial", ""),
            "vendor": reported.get("vendor") or sb.VENDOR_PLACEHOLDER,
            "model": reported.get("model") or sb.MODEL_PLACEHOLDER,
            "channels": [{"channel": "1", "name": "Camera 1"}]}


def _stage_primary(env):
    env.ini.write_text("[watchlog]\nnvr_url = http://192.0.2.10\nnvr_driver = onvif\n",
                       encoding="utf-8")
    sb.credential_store.save_nvr_credential("admin", "pw")
    return rr.migrate_legacy_singleton(env.ini)


def test_an_additional_recorder_without_a_model_keeps_it_unknown():
    with _Env() as env:
        _stage_primary(env)
        entry = {"address": "192.0.2.30", "username": "admin", "password": "pw",
                 "display_name": "Recorder 2"}
        row, added = sb._stage_additional_recorder(entry, _tested(serial="SER-2"))
        assert added
        assert row["vendor"] is None and row["model"] is None
        stored = rr.recorder(row["local_id"])
        assert stored["vendor"] is None and stored["model"] is None
        descriptors = {d["local_key"]: d for d in rr.registry_cloud_descriptors()}
        assert descriptors[row["local_id"]].get("model") is None
        assert descriptors[row["local_id"]].get("vendor") is None


def test_a_reported_vendor_is_kept_and_only_the_missing_model_stays_unknown():
    with _Env() as env:
        _stage_primary(env)
        entry = {"address": "192.0.2.30", "username": "admin", "password": "pw",
                 "display_name": "Recorder 2"}
        row, _ = sb._stage_additional_recorder(entry, _tested(vendor="Dahua", serial="SER-2"))
        assert row["vendor"] == "Dahua" and row["model"] is None


def test_the_continuity_recorder_re_point_keeps_unknown_unknown():
    with _Env() as env:
        primary = _stage_primary(env)
        state = {"site_id": "site-1"}
        sb._stage_recorder_registry(env.ini, _tested(serial="SER-1"), "admin", "pw",
                                    {"site_id": "site-1"}, state)
        row = rr.recorder(primary["local_id"])
        assert row["vendor"] is None and row["model"] is None


def test_only_the_placeholders_read_as_unknown():
    assert sb._observed({"vendor": sb.VENDOR_PLACEHOLDER}, "vendor") is None
    assert sb._observed({"model": " DS-7608 "}, "model") == "DS-7608"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

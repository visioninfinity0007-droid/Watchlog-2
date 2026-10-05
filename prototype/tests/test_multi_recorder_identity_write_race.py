#!/usr/bin/env python3
"""A busy recorders.json does not cost a recorder its startup camera sync (MNVR-021, RV-4).

Probes now run while the site workers are already reading recorders.json. On Windows,
replacing that file fails with PermissionError while another thread has it open, and the
failed observed-identity write became the recorder's probe error before its cameras were
synced. The write is now retried briefly and, if the file stays busy, skipped (the facts
are a non-secret cache rewritten on the next probe); the camera sync still runs.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
from test_multi_recorder_cloud_binding import (  # noqa: E402
    Driver, Env, FakeCloud, info, seed_two)

STATE = {"agent_id": "agent", "agent_key": "key"}
IDS = ("aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002")


def _prepare(a, b, base, cloud):
    # Two physical recorders, two serials: one serial is never saved on two rows.
    def open_driver(cfg):
        label = "A" if cfg.recorder_local_id == a else "B"
        return Driver(label), info("Hikvision", label, f"SER-{label}")
    return {x.context.local_id: x for x in mro.prepare_recorders(
        base, STATE, cloud, open_driver)}


def test_a_busy_registry_does_not_skip_the_camera_sync(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        cloud = FakeCloud({a: IDS[0], b: IDS[1]})

        def busy(*_a, **_k):
            raise PermissionError(5, "Access is denied", "recorders.json")

        monkeypatch.setattr(rr, "update_observed_identity", busy)
        monkeypatch.setattr(mro, "_IDENTITY_WRITE_RETRY_SECONDS", (0.0, 0.0), raising=False)
        by_local = _prepare(a, b, base, cloud)
        for local_id, cloud_id in ((a, IDS[0]), (b, IDS[1])):
            assert by_local[local_id].error is None, by_local[local_id].error
            assert by_local[local_id].camera_mapping == {"1": f"camera-{cloud_id}-1"}
        synced = {kw["p_recorder_id"] for n, kw in cloud.calls
                  if n == "wl_sync_recorder_cameras"}
        assert synced == set(IDS)


def test_a_briefly_busy_registry_still_saves_the_observed_identity(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        cloud = FakeCloud({a: IDS[0], b: IDS[1]})
        real = rr.update_observed_identity
        failed = set()

        def flaky(local_id, **kw):
            if local_id not in failed:
                failed.add(local_id)
                raise PermissionError(5, "Access is denied", "recorders.json")
            return real(local_id, **kw)

        monkeypatch.setattr(rr, "update_observed_identity", flaky)
        monkeypatch.setattr(mro, "_IDENTITY_WRITE_RETRY_SECONDS", (0.0, 0.0), raising=False)
        by_local = _prepare(a, b, base, cloud)
        assert by_local[a].error is None
        assert rr.recorder(a)["vendor"] == "Hikvision"
        assert rr.recorder(a)["identity_fingerprint"] == "serial:SER-A"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

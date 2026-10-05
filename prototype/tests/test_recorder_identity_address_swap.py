#!/usr/bin/env python3
"""Another recorder answering at a recorder's address is refused, not adopted (contract 3, 8, 12).

probe_and_sync_recorder opened the recorder at its registry URL, saved whatever serial that
device reported as the row's identity_fingerprint, and synced that device's channels under the
row's cloud recorder id. After a DHCP lease swap (sites often reuse one admin password)
recorder A's address answered as physical recorder B: A's fingerprint became B's serial, two
rows shared one serial, and B's cameras were bound to A, so B's events, clips and stills were
stamped as A's with no error. The health cycle's inventory retry did the same with no identity
check at all.

The saved serial is now enforced wherever the runtime opens a registry recorder: a different
serial is refused (no camera sync, no collector, no health inventory), and a probe never writes
a known serial over with a different one or gives a row another row's serial. Unknown stays
unknown: with no saved or no reported serial nothing is refused. Real address-swap behaviour on
recorder hardware stays IMPLEMENTED_UNVERIFIED.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))
sys.path.insert(0, str(TESTS))

import multi_recorder_orchestrator as mro  # noqa: E402
import recorder_registry as rr  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DriverError, RecorderIdentityMismatch  # noqa: E402
from test_multi_recorder_cloud_binding import Driver, Env, FakeCloud, info, seed_two  # noqa: E402

STATE = {"agent_id": "agent", "agent_key": "key"}
A_URL, B_URL = "http://192.0.2.10", "http://192.0.2.20"


def _fingerprints(a, b, a_fp="serial:A-SERIAL", b_fp="serial:B-SERIAL"):
    reg = rr.load_registry()
    for row in reg["recorders"]:
        row["identity_fingerprint"] = {a: a_fp, b: b_fp}[row["local_id"]]
    rr.save_registry(reg)


def _swapped(monkeypatch):
    """Both addresses answer as the other physical recorder (same login works on both)."""
    answers = {A_URL: ("B", "B-SERIAL"), B_URL: ("A", "A-SERIAL")}

    def connect(cfg, base_url):
        label, serial = answers[base_url]
        return Driver(label), info("Hikvision", label, serial)

    monkeypatch.setattr(core, "_connect_recorder", connect)


def _prepare(base, mapping):
    base.require_nvr = lambda: None
    cloud = FakeCloud(mapping)
    prepared = mro.prepare_recorders(base, STATE, cloud, core.open_driver)
    return cloud, {item.context.local_id: item for item in prepared}


def test_an_address_swap_is_refused_and_keeps_both_saved_identities(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        _fingerprints(a, b)
        _swapped(monkeypatch)
        mapping = {a: "11111111-1111-1111-1111-111111111111",
                   b: "22222222-2222-2222-2222-222222222222"}
        cloud, by_local = _prepare(base, mapping)

        assert by_local[a].error and "different serial" in by_local[a].error
        assert by_local[b].error and "different serial" in by_local[b].error
        assert [kw for name, kw in cloud.calls if name == "wl_sync_recorder_cameras"] == [], (
            "another recorder's channels were bound to this recorder's cloud id")
        assert rr.recorder(a)["identity_fingerprint"] == "serial:A-SERIAL"
        assert rr.recorder(b)["identity_fingerprint"] == "serial:B-SERIAL"


def test_a_row_never_takes_another_rows_serial(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        _fingerprints(a, b, a_fp=None)          # A's serial was never observed
        _swapped(monkeypatch)
        mapping = {a: "11111111-1111-1111-1111-111111111111",
                   b: "22222222-2222-2222-2222-222222222222"}
        cloud, by_local = _prepare(base, mapping)

        assert by_local[a].error and "another recorder of this site" in by_local[a].error
        synced = {kw["p_recorder_id"] for name, kw in cloud.calls
                  if name == "wl_sync_recorder_cameras"}
        assert mapping[a] not in synced
        assert rr.recorder(a)["identity_fingerprint"] is None


def test_the_right_recorder_and_an_unknown_serial_are_still_accepted(monkeypatch):
    with Env() as env:
        a, b, base = seed_two(env.root)
        _fingerprints(a, b, b_fp=None)
        answers = {A_URL: ("A", "a-serial"), B_URL: ("B", None)}   # serial case-insensitive

        def connect(cfg, base_url):
            label, serial = answers[base_url]
            return Driver(label), info("Hikvision", label, serial)

        monkeypatch.setattr(core, "_connect_recorder", connect)
        mapping = {a: "11111111-1111-1111-1111-111111111111",
                   b: "22222222-2222-2222-2222-222222222222"}
        cloud, by_local = _prepare(base, mapping)
        assert by_local[a].error is None and by_local[b].error is None
        synced = {kw["p_recorder_id"] for name, kw in cloud.calls
                  if name == "wl_sync_recorder_cameras"}
        assert synced == set(mapping.values())


def test_workers_opening_the_recorder_are_refused_too(monkeypatch):
    monkeypatch.setattr(core, "_connect_recorder", lambda cfg, url: (
        Driver("B"), info("Hikvision", "B", "B-SERIAL")))
    cfg = SimpleNamespace(nvr_url=A_URL, nvr_driver="hikvision", nvr_username="u",
                          nvr_password="p", recorder_identity_fingerprint="serial:A-SERIAL",
                          require_nvr=lambda: None)
    with pytest.raises(RecorderIdentityMismatch):
        core.open_driver(cfg)
    assert not core._is_auth_failure(RecorderIdentityMismatch("x"))


class _HealthCloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        return {}


class _SwappedDriver:
    name = "hikvision-isapi"

    def probe(self):
        return info("Hikvision", "B", "B-SERIAL")

    def list_channels(self):
        return [SimpleNamespace(channel="1", name="B Camera 1", enabled=True)]

    def current_faults(self):
        return {"supported": False}

    def capabilities(self):
        return {}

    def close(self):
        pass


def test_the_health_cycle_does_not_sync_another_recorders_inventory(monkeypatch):
    monkeypatch.setattr(core, "build", lambda *a, **k: _SwappedDriver())
    monkeypatch.setattr(core, "log", lambda _m: None)
    monkeypatch.setattr(core, "_credential_generation_for_cfg", lambda _cfg: "g1")
    cfg = SimpleNamespace(
        nvr_driver="hikvision-isapi", nvr_url=A_URL, nvr_username="u", nvr_password="p",
        recorder_local_id="local-a", credential_generation_seen="g1",
        recorder_cloud_id="11111111-1111-1111-1111-111111111111",
        recorder_identity_fingerprint="serial:A-SERIAL", health_batch=4, health_concurrency=1)
    cloud = _HealthCloud()
    core.health_cycle(cloud, STATE, cfg, {})
    names = [name for name, _ in cloud.calls]
    assert "wl_sync_recorder_cameras" not in names
    report = next(kw["p_report"] for name, kw in cloud.calls
                  if name == "wl_report_recorder_health")
    assert report["nvr"]["state"] == "unknown"
    assert report["channels"]["enumerated"] is False


def test_setup_may_still_record_a_retested_recorders_new_serial():
    with Env() as env:
        a, b, _base = seed_two(env.root)
        _fingerprints(a, b)
        with pytest.raises(rr.RecorderIdentityConflict):
            rr.update_observed_identity(a, identity_fingerprint="serial:NEW-UNIT")
        updated = rr.update_observed_identity(a, identity_fingerprint="serial:NEW-UNIT",
                                              verified_by_setup=True)
        assert updated["identity_fingerprint"] == "serial:NEW-UNIT"


def test_identity_mismatch_is_not_reported_as_unreachable_or_auth():
    import nvr_health
    report = nvr_health.assess_from_error(RecorderIdentityMismatch("other recorder"))
    assert report["nvr"]["state"] == "unknown"
    assert isinstance(RecorderIdentityMismatch("x"), DriverError)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

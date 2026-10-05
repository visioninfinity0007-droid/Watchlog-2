#!/usr/bin/env python3
"""Recording/storage current proof names its recorder (MNVR-013, Agent side).

The fan-out runs one health cycle per recorder, but each cycle published its present-tense
recording/storage proof through the site-scoped wl_report_recording_storage_current. With
recorder A recording and recorder B offline, A's report marked B's same-numbered channels
as recording and the storage state flipped between the two recorders every cycle. A bound
recorder now reports through wl_report_recorder_recording_storage_current with its own
cloud id; the site-scoped RPC is used only by a recorder-less runtime, or, on a database
without the recorder RPC, only on a site with a single recorder.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import recording_current as rc  # noqa: E402
import recording_health  # noqa: E402
import watchlog_agent as core  # noqa: E402

A = "aaaaaaaa-0000-4000-8000-000000000001"
STATE = {"agent_id": "agent", "agent_key": "key"}
REPORT = {"storage": {"state": "ok"}, "recording": {"supported": True, "channels": []}}


class Cloud:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name in self.missing:
            raise core.CloudError(name, 404, "PGRST202",
                                  f"Could not find the function public.{name}")
        return {"ok": True}

    def names(self):
        return [name for name, _ in self.calls]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(rc, "_RECORDER_RPC_ABSENT", False)
    monkeypatch.setattr(core, "log", lambda _m: None)


def configured(monkeypatch, count):
    monkeypatch.setattr(rc.recorder_registry, "recorders", lambda: [
        {"local_id": str(n), "is_configured": True} for n in range(count)])


def test_a_bound_recorder_reports_its_own_proof(monkeypatch):
    configured(monkeypatch, 2)
    cloud = Cloud()
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), REPORT)
    assert cloud.calls == [("wl_report_recorder_recording_storage_current", {
        "p_agent_id": "agent", "p_agent_key": "key", "p_recorder_id": A, "p_report": REPORT})]


def test_a_recorder_less_runtime_keeps_the_site_scoped_rpc(monkeypatch):
    configured(monkeypatch, 0)
    cloud = Cloud()
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=None), REPORT)
    assert cloud.names() == ["wl_report_recording_storage_current"]


def test_an_older_database_falls_back_only_on_a_single_recorder_site(monkeypatch):
    configured(monkeypatch, 1)
    cloud = Cloud(missing={"wl_report_recorder_recording_storage_current"})
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), REPORT)
    assert cloud.names() == ["wl_report_recorder_recording_storage_current",
                             "wl_report_recording_storage_current"]
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), REPORT)
    assert cloud.names()[-1] == "wl_report_recording_storage_current"
    assert cloud.names().count("wl_report_recorder_recording_storage_current") == 1, \
        "a missing recorder RPC is not asked for again every cycle"


def test_an_older_database_never_gets_one_recorders_proof_for_the_whole_site(monkeypatch):
    configured(monkeypatch, 2)
    cloud = Cloud(missing={"wl_report_recorder_recording_storage_current"})
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), REPORT)
    rc.report_current(core, cloud, STATE, SimpleNamespace(recorder_cloud_id=A), REPORT)
    assert "wl_report_recording_storage_current" not in cloud.names()


def test_the_installed_health_cycle_passes_its_recorder(monkeypatch):
    configured(monkeypatch, 2)
    monkeypatch.setattr(rc, "_INSTALLED", False)
    monkeypatch.setattr(recording_health, "assess_recording_storage",
                        recording_health.assess_recording_storage)
    fake_core = SimpleNamespace(log=lambda _m: None)

    def health_cycle(cloud, state, cfg, holder):
        recording_health.assess_recording_storage(None, ["1"], "unreachable")

    fake_core.health_cycle = health_cycle
    rc.install(fake_core)
    cloud = Cloud()
    fake_core.health_cycle(cloud, STATE, SimpleNamespace(recorder_cloud_id=A), {})
    assert [kw.get("p_recorder_id") for _n, kw in cloud.calls] == [A]
    assert cloud.names() == ["wl_report_recorder_recording_storage_current"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

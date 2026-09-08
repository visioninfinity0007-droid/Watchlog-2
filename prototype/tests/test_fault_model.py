#!/usr/bin/env python3
"""Phase A — increment 7: operational-fault oracle (derive / dedupe / reconcile).

Faults are reliability ("your CCTV needs attention"), never security alarms. The model turns the
CONFIRMED current health of a site into the set of faults that should be OPEN, honouring the two
invariants the whole health model insists on: an observer that is DOWN suppresses every layer beneath
it (its readings are UNKNOWN/stale), and UNKNOWN is never itself a fault. wl_reconcile_site_faults
(0048) mirrors this; test_operational_faults_contract.py pins them.

Red before fault_model.py exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import fault_model as fm  # noqa: E402
from fault_model import CameraState, desired_faults, reconcile_faults  # noqa: E402

AID = "agent-1"


def cam(cid, health, inv="present", rec="unknown"):
    return CameraState(camera_id=cid, health_state=health, inventory_state=inv, recording_state=rec)


def keys(d):
    return set(d.keys())


# ---- layered suppression: a down observer never fabricates faults beneath it ----

def test_agent_unreachable_is_the_only_fault_when_agent_down():
    # even though cameras were last seen offline and storage faulted, we CANNOT observe them now.
    d = desired_faults(agent_id=AID, agent_reachable=False, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="fault",
                       cameras=[cam("c1", "offline"), cam("c2", "offline", rec="not_recording")])
    assert keys(d) == {fm.agent_key(AID)}
    only = next(iter(d.values()))
    assert only.domain == fm.DOMAIN_AGENT and only.severity == fm.SEV_CRITICAL


def test_nvr_unreachable_suppresses_cameras_and_storage():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=False, nvr_auth_ok=None,
                       storage_state="unknown", cameras=[cam("c1", "offline")])
    assert keys(d) == {fm.nvr_reach_key(AID)}
    assert next(iter(d.values())).domain == fm.DOMAIN_NVR_CONNECTIVITY


def test_nvr_auth_failed_suppresses_lower_layers():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=False,
                       storage_state="unknown", cameras=[cam("c1", "offline")])
    assert keys(d) == {fm.nvr_auth_key(AID)}
    assert next(iter(d.values())).domain == fm.DOMAIN_NVR_AUTH


# ---- UNKNOWN / inventory never fabricate a fault ----

def test_unknown_states_open_no_fault():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="unknown",
                       cameras=[cam("c1", "unknown", rec="unknown"), cam("c2", "operational", rec="recording")])
    assert d == {}


def test_missing_or_disabled_camera_is_not_a_fault():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="ok",
                       cameras=[cam("c1", "offline", inv="missing"), cam("c2", "offline", inv="disabled")])
    assert d == {}                                     # removed/disabled channels are inventory, not faults


# ---- positively-confirmed bad states open exactly one deduped fault each ----

def test_confirmed_camera_offline_and_recording_stopped():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="ok",
                       cameras=[cam("c1", "offline"), cam("c2", "operational", rec="not_recording")])
    assert keys(d) == {fm.camera_offline_key("c1"), fm.camera_recording_key("c2")}
    assert d[fm.camera_offline_key("c1")].domain == fm.DOMAIN_CAMERA
    assert d[fm.camera_recording_key("c2")].domain == fm.DOMAIN_RECORDING


def test_storage_fault_is_critical_degraded_is_warning():
    fault = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                           storage_state="fault", cameras=[])
    assert fault[fm.storage_key(AID)].severity == fm.SEV_CRITICAL
    deg = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                         storage_state="degraded", cameras=[])
    assert deg[fm.storage_key(AID)].severity == fm.SEV_WARNING


def test_channel_storage_fault_recording_domain():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="ok", cameras=[cam("c9", "operational", rec="storage_fault")])
    f = d[fm.camera_recording_key("c9")]
    assert f.domain == fm.DOMAIN_RECORDING and f.reason_code == "storage_fault"


def test_dedupe_key_is_one_per_condition():
    # the same offline camera in two evaluations yields the SAME key -> one live row, not a storm.
    d1 = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                        storage_state="ok", cameras=[cam("c1", "offline")])
    d2 = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                        storage_state="ok", cameras=[cam("c1", "offline")])
    assert keys(d1) == keys(d2) == {fm.camera_offline_key("c1")}


# ---- reconcile: open new, resolve cleared, leave persistent ----

def test_reconcile_opens_only_new():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="fault", cameras=[cam("c1", "offline")])
    to_open, to_resolve = reconcile_faults(set(), d)
    assert {f.dedupe_key for f in to_open} == keys(d) and to_resolve == []


def test_reconcile_resolves_cleared_and_leaves_persistent():
    open_keys = {fm.camera_offline_key("c1"), fm.storage_key(AID)}
    # c1 recovered; storage still faulted -> resolve c1 only, leave storage untouched
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="fault", cameras=[cam("c1", "operational")])
    to_open, to_resolve = reconcile_faults(open_keys, d)
    assert to_resolve == [fm.camera_offline_key("c1")]
    assert to_open == []                               # storage already open -> not reopened


def test_reconcile_is_idempotent_when_settled():
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="fault", cameras=[cam("c1", "offline")])
    to_open, to_resolve = reconcile_faults(keys(d), d)
    assert to_open == [] and to_resolve == []


def test_agent_recovery_resolves_agent_fault_and_reveals_real_state():
    # while agent was down, only the agent fault was open. On recovery the real (now observable)
    # camera offline is opened and the agent fault resolved.
    open_keys = {fm.agent_key(AID)}
    d = desired_faults(agent_id=AID, agent_reachable=True, nvr_reachable=True, nvr_auth_ok=True,
                       storage_state="ok", cameras=[cam("c1", "offline")])
    to_open, to_resolve = reconcile_faults(open_keys, d)
    assert to_resolve == [fm.agent_key(AID)]
    assert {f.dedupe_key for f in to_open} == {fm.camera_offline_key("c1")}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

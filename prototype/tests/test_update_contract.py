#!/usr/bin/env python3
"""5.1.1 Agent-only vs full-component update contract.

Every in-app update path replaces watchlog-agent.exe only. The signed manifest's release entry
says whether that is enough (update_class, min_installed_components; produced by
tools/release_update_contract.py). The Agent refuses an Agent-only update when the release needs
the Repair/Upgrade package, when the field is absent across a major/minor change, or when the
installed component set (ARP ComponentsVersion / DisplayVersion) is older than required, and
says a Repair package is needed. The Site Status "Update WatchLog" path no longer swaps the
installed Agent from inside itself (audit B). Runs everywhere (no Windows needed).
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "agent"))
sys.path.insert(0, str(ROOT / "tools"))

import release_update_contract as contract  # noqa: E402
import remote_update  # noqa: E402
import status_controller  # noqa: E402
import updater  # noqa: E402
import watchlog_agent as wa  # noqa: E402

AGENT_ONLY, REPAIR = updater.AGENT_ONLY_COMPATIBLE, updater.REQUIRES_REPAIR_PACKAGE


def manifest(version, **rel):
    entry = {"version": version, "url": f"https://dl.example/{version}/watchlog-agent.exe",
             "sha256": "a" * 64, "size": 10, **rel}
    return {"schema": updater.MANIFEST_SCHEMA, "channels": {"production": entry}}


def plan(version, current, components=updater.NOT_CHECKED, **rel):
    return updater.plan_update(manifest(version, **rel), current, "production",
                               signature_state=True, installed_components=components)


# --- the decision ------------------------------------------------------------------------

def test_5_0_26_to_5_1_1_requires_the_repair_package():
    p = plan("5.1.1", "5.0.26")                              # field absent, minor change
    assert p["action"] == "blocked" and p["reason"] == "requires_repair_package"
    p = plan("5.1.1", "5.0.26", update_class=AGENT_ONLY)     # even if wrongly marked agent-only,
    assert p["action"] == "update"                            # ...the components still decide:
    p = plan("5.1.1", "5.0.26", components="5.0.26", update_class=AGENT_ONLY)
    assert p["action"] == "blocked" and p["required_components"] == "5.1.0"


def test_an_absent_field_allows_only_a_patch_on_the_same_line():
    assert plan("5.1.2", "5.1.1")["action"] == "update"
    assert plan("5.1.2", "5.1.1")["update_class"] == AGENT_ONLY
    assert plan("5.2.0", "5.1.1")["reason"] == "requires_repair_package"
    assert plan("6.0.0", "5.1.1")["reason"] == "requires_repair_package"


def test_an_explicit_requires_repair_or_an_unknown_class_is_refused():
    assert plan("5.1.2", "5.1.1", update_class=REPAIR)["reason"] == "requires_repair_package"
    assert plan("5.1.2", "5.1.1", update_class="SOMETHING_NEW")["reason"] == "requires_repair_package"
    assert plan("5.1.2", "5.1.1", update_class="agent_only_compatible")["action"] == "update"


def test_installed_components_must_meet_the_release_minimum():
    ok = plan("5.1.2", "5.1.1", components="5.1.1", update_class=AGENT_ONLY,
              min_installed_components="5.1.1")
    assert ok["action"] == "update" and ok["update_class"] == AGENT_ONLY
    old = plan("5.1.2", "5.1.1", components="5.1.0", update_class=AGENT_ONLY,
               min_installed_components="5.1.1")
    assert old["action"] == "blocked" and old["installed_components"] == "5.1.0"
    assert "Repair/Upgrade package" in old["detail"]
    unknown = plan("5.1.2", "5.1.1", components=None, update_class=AGENT_ONLY)
    assert unknown["action"] == "blocked" and "could not be identified" in unknown["detail"]


def test_up_to_date_and_signature_rules_are_unchanged():
    assert plan("5.1.1", "5.1.1", components=None)["action"] == "up-to-date"
    unsigned = updater.plan_update(manifest("5.1.2"), "5.1.1", "production", signature_state=None,
                                   installed_components="5.1.1")
    assert unsigned["reason"] == "manifest_unsigned"


def test_the_plan_carries_the_released_build():
    assert plan("5.1.2", "5.1.1", build_sha="abc1234")["build_sha"] == "abc1234"


def test_installed_components_reads_components_version_then_display_version():
    values = {"ComponentsVersion": "5.1.1", "DisplayVersion": "5.0.26"}
    assert updater.installed_components_version(_read=values.__getitem__) == "5.1.1"
    del values["ComponentsVersion"]
    assert updater.installed_components_version(_read=values.__getitem__) == "5.0.26"
    assert updater.installed_components_version(_read={}.__getitem__) is None


# --- the paths that use it -----------------------------------------------------------------

class Cloud:
    def __init__(self):
        self.calls = []

    def call(self, name, **kw):
        self.calls.append((name, kw))
        return {}


def test_remote_update_refuses_and_reports_a_release_that_needs_the_repair_package(tmp_path, monkeypatch):
    cfg = SimpleNamespace(state_path=tmp_path / "agent_state.json",
                          update_url="https://dl.example/manifest.json", update_public_key="k",
                          update_require_signature=True, update_channel="production")

    class Resp:
        text = json.dumps(manifest("5.2.0"))

        def raise_for_status(self):
            pass
    monkeypatch.setattr(remote_update.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(remote_update.updater, "verify_manifest_signature", lambda m, k: True)
    monkeypatch.setattr(remote_update.updater, "installed_components_version", lambda: "5.1.1")
    monkeypatch.setattr(remote_update.wl_version, "version_string", lambda: "5.1.1")
    cloud = Cloud()
    out = remote_update.stage_latest(cloud, {"agent_id": "a", "agent_key": "k"}, cfg,
                                     {"request_id": "req-1"})
    assert out["restart"] is False
    name, kw = cloud.calls[-1]
    assert name == "wl_agent_complete_update_request" and kw["p_ok"] is False
    assert kw["p_detail"] == "requires_repair_package"
    assert not remote_update._pending_path(cfg).exists()


def _cfg():
    return SimpleNamespace(update_url="https://dl.example/manifest.json", update_channel="production",
                           update_public_key="", update_require_signature=False)


def _run(fn, version):
    buf = io.StringIO()
    with redirect_stdout(buf), patch.object(updater, "installed_components_version",
                                            return_value=updater_version()):
        code = fn(_cfg(), _fetch=lambda url: json.dumps(manifest(version)))
    return code, buf.getvalue()


def updater_version():
    import wl_version
    return wl_version.VERSION


def same_line_patch():
    major, minor, _ = updater.parse_version(updater_version())
    return f"{major}.{minor}.99"


def test_site_status_update_never_swaps_the_installed_agent_in_process():
    """audit B: --update ran wl-upgrade preflight from the installed exe, which stops every
    process of that path (itself and the Site Status window) after disabling the task."""
    with patch.object(updater, "apply_update", side_effect=AssertionError("must not run")):
        code, out = _run(wa.cmd_update, same_line_patch())
    assert code == 2
    result = json.loads(out.split("UPDATE_APPLY_JSON ", 1)[1].splitlines()[0])
    assert result["handoff"] == "remote_update" and result["ok"] is False
    assert result["rolled_back"] is False


def test_site_status_update_reports_a_repair_package_requirement():
    code, out = _run(wa.cmd_update, "99.0.0")
    assert code == 2
    result = json.loads(out.split("UPDATE_APPLY_JSON ", 1)[1].splitlines()[0])
    assert result["reason"] == "requires_repair_package"
    code, out = _run(wa.cmd_check_update, "99.0.0")
    assert code == 2 and "Repair/Upgrade package" in out


def _controller(outputs):
    c = status_controller.StatusController.__new__(status_controller.StatusController)
    c._run = lambda args: outputs[args[0]]
    c._tagged = status_controller.StatusController._tagged
    return c


def test_status_window_messages_are_honest():
    plan_json = json.dumps({"action": "blocked", "reason": "requires_repair_package", "target": "5.2.0"})
    c = _controller({"--check-update": (2, "UPDATE_JSON " + plan_json)})
    assert "WatchLog-Repair-Upgrade.exe" in c.check_update()["headline"]
    handoff = json.dumps({"ok": False, "handoff": "remote_update", "rolled_back": False,
                          "detail": wa.UPDATE_HANDOFF_DETAIL})
    c = _controller({"--update": (2, "UPDATE_APPLY_JSON " + handoff)})
    r = c.apply_update()
    assert r["ok"] is False and r["message"] == wa.UPDATE_HANDOFF_DETAIL
    gui = (ROOT / "prototype" / "agent" / "site_status_gui.py").read_text(encoding="utf-8")
    act = gui[gui.index("def act_update"):gui.index("def act_support_bundle")]
    assert "apply_update" not in act


# --- the release tooling that produces the fields -----------------------------------------

def test_release_tooling_classifies_5_1_1_as_requiring_the_repair_package():
    c = contract.classify("5.1.1", "5.0.26", ["prototype/installer/apply-remote-update.ps1"])
    assert c["update_class"] == REPAIR and c["min_installed_components"] == "5.1.0"
    assert any("major/minor" in r for r in c["reasons"])
    # Agents before 5.1.1 ignore update_class; min_agent_version makes them refuse it too.
    assert c["min_agent_version"] == "5.1.1"
    # The 5.0.17 and 5.0.26 updaters (3d02a7b2 / a3266326 updater.py:163-165) block on exactly:
    #   min_agent and parse_version(current) < parse_version(min_agent) -> "agent_too_old"
    for fielded in ("5.0.17", "5.0.26"):
        assert updater.parse_version(fielded) < updater.parse_version(c["min_agent_version"])
    this_agent = updater.plan_update(manifest("5.1.1", min_agent_version=c["min_agent_version"]),
                                     "5.0.26", "production", signature_state=True)
    assert this_agent["action"] == "blocked" and this_agent["reason"] == "requires_repair_package"
    c = contract.classify("5.1.1", "5.1.0", ["prototype/installer/apply-remote-update.ps1",
                                             "prototype/installer/run-agent.ps1"])
    assert c["update_class"] == REPAIR


def test_release_tooling_allows_a_clean_patch_agent_only():
    c = contract.classify("5.1.2", "5.1.1", [], build_sha="abc")
    assert c == {"version": "5.1.2", "update_class": AGENT_ONLY, "min_installed_components": "5.1.0",
                 "build_sha": "abc", "reasons": ["patch release; no installed script changed"]}


@pytest.mark.parametrize("previous,changed,flag", [(None, [], ""), ("5.1.1", None, ""),
                                                   ("5.1.1", [], "registry format changed")])
def test_release_tooling_is_conservative_when_unsure(previous, changed, flag):
    assert contract.classify("5.1.2", previous, changed, requires_repair=flag)["update_class"] == REPAIR


@pytest.mark.parametrize("kw", [{"changed_formats": ["recorder registry schema"]},
                                {"changed_formats": None},
                                {"changed_setup_ui": ["prototype/agent/setup_gui.py"]},
                                {"changed_setup_ui": None}])
def test_a_setup_ui_or_data_format_change_requires_the_repair_package(kw):
    # An Agent-only runtime must never need a newer Setup UI, registry schema or credential
    # format than the installed components provide (A-Z section 25).
    c = contract.classify("5.1.2", "5.1.1", [], **kw)
    assert c["update_class"] == REPAIR and c["min_agent_version"] == "5.1.2"


def _git_tree(tmp_path, files):
    import subprocess
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    refs = []
    for snapshot in files:
        for rel, text in snapshot.items():
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "x")
        out = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True)
        refs.append(out.stdout.strip())
    return refs


def test_release_tooling_detects_a_changed_declared_format(tmp_path, monkeypatch):
    reg = "prototype/agent/recorder_registry.py"
    cred = "prototype/agent/windows_secret.py"
    a, b, c = _git_tree(tmp_path, [
        {reg: 'REGISTRY_SCHEMA = "watchlog.recorders.v1"\n', cred: "CREDENTIAL_STORE_VERSION = 2\n"},
        {reg: 'REGISTRY_SCHEMA = "watchlog.recorders.v1"  # same\nX = 1\n'},
        {cred: "CREDENTIAL_STORE_VERSION = 3\n"}])
    monkeypatch.setattr(contract, "ROOT", tmp_path)
    assert contract.changed_contract_formats(a, b) == []
    assert contract.changed_contract_formats(b, c) == ["credential store format"]
    assert contract.changed_contract_formats(a, "no-such-ref") is None


def test_release_tooling_watches_the_real_contract_constants():
    for path, name, _label in contract.CONTRACT_CONSTANTS:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert f"\n{name} = " in text, (path, name)
    for path in contract.SETUP_UI_SOURCES + contract.COMPONENT_SCRIPTS:
        assert (ROOT / path).is_file(), path
    # The camera-choices schema is written by Setup and read by the Agent: one value.
    import periodic_stills, setup_backend
    assert periodic_stills.RECORDER_CAMERA_PROFILES_SCHEMA == setup_backend.RECORDER_CAMERA_PROFILES_SCHEMA


BOOT = {"release_tag": "watchlog-production", "min_remote_update_version": "5.0.24"}


def test_a_repair_release_is_enforced_through_the_deployed_manifest_builder():
    # The deployed watchlog-update-manifest signs min_agent_version (not update_class): the
    # payload's min_agent_version is the release's own version, which every fielded Agent refuses.
    c = contract.classify("5.1.1", "5.0.26", ["prototype/installer/run-agent.ps1"], build_sha="abc")
    p = contract.update_payload(c, BOOT, "A" * 64, 40_000_000, "2026-10-06T00:00:00Z")
    assert p["min_agent_version"] == "5.1.1" and p["update_class"] == REPAIR
    assert p["url"] == ("https://github.com/visioninfinity0007-droid/Watchlog-2/releases/download/"
                        "watchlog-production/watchlog-agent-5.1.1.exe")
    assert p["sha256"] == "a" * 64 and p["size"] == 40_000_000
    signed = {k: p[k] for k in ("version", "url", "sha256", "size", "min_agent_version")}
    for fielded in ("5.0.17", "5.0.26", "5.1.0"):
        r = updater.plan_update(manifest(**signed), fielded, "production", signature_state=True)
        assert r["action"] == "blocked", fielded
    # A 5.1.1+ Agent reads the same signed marker as "needs the Repair package", not "too old".
    later = contract.update_payload(contract.classify("5.1.2", "5.1.1", None), BOOT, "b" * 64, 1,
                                    "x")
    r = updater.plan_update(manifest(**{k: later[k] for k in signed}), "5.1.1", "production",
                            signature_state=True)
    assert r["reason"] == "requires_repair_package" and r["target"] == "5.1.2"


def test_an_agent_only_release_keeps_the_channel_floor():
    c = contract.classify("5.1.2", "5.1.1", [], changed_formats=[], changed_setup_ui=[])
    p = contract.update_payload(c, BOOT, "c" * 64, 1, "x")
    assert c["update_class"] == AGENT_ONLY and p["min_agent_version"] == "5.0.24"
    r = updater.plan_update(manifest(**{k: p[k] for k in ("version", "url", "sha256", "size",
                                                          "min_agent_version")}),
                            "5.1.1", "production", signature_state=True, installed_components="5.1.1")
    assert r["action"] == "update"


def test_the_release_build_writes_the_contract_but_never_publishes_it():
    wf = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
    assert "tools/release_update_contract.py" in wf and "--payload-out" in wf
    assert "fetch-depth: 0" in wf                  # the contract diffs against the previous release
    assert "dist-installer/watchlog-update-payload.json" in wf
    assert "gh release upload" not in wf and "gh release create" not in wf


def test_release_tooling_output_is_accepted_by_the_agent():
    c = contract.classify("5.1.2", "5.1.1", [], build_sha="abc1234")
    p = plan("5.1.2", "5.1.1", components="5.1.1", update_class=c["update_class"],
             min_installed_components=c["min_installed_components"], build_sha=c["build_sha"])
    assert p["action"] == "update" and p["build_sha"] == "abc1234"


def test_the_incompatibilities_are_documented():
    doc = (ROOT / "docs" / "release" / "WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md").read_text(encoding="utf-8")
    assert "Agent-only vs full-component updates" in doc
    assert "5.0.26 -> 5.1.x" in doc and "REQUIRES_REPAIR_PACKAGE" in doc


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

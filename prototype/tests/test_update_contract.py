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

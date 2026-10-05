"""Setup window flow for a first install with several recorders (audit WP-12).

Runs the Setup UI's own --ui-selftest, standalone and as the installer child, against
the functional Qt fake in qt_fake.py, so the multi-recorder first-install path
(add another recorder before Connect, one login and one name per recorder, cameras
grouped by recorder with overlapping channel numbers, and the finalize_install
arguments) executes on every CI run. The Windows build job also runs the same
selftest inside the frozen executable with real Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "agent"))

import qt_fake  # noqa: E402

GUI_SOURCE = (TESTS.parent / "agent" / "setup_gui.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gui():
    return qt_fake.load_setup_gui_with_fake_qt()


@pytest.fixture(autouse=True)
def no_network_discovery(gui, monkeypatch):
    """These tests never scan a real network."""
    def refuse(*_a, **_k):
        raise AssertionError("a GUI test started real recorder discovery")
    monkeypatch.setattr(gui.backend, "discover_recorders", refuse)
    monkeypatch.setattr(gui.backend, "test_recorder", refuse)


@pytest.mark.parametrize("installer_child", [False, True])
def test_the_setup_ui_selftest_passes(gui, installer_child):
    assert gui._run_ui_selftest(installer_child=installer_child) == 0


def _window(gui, tmp_path, **kw):
    gui.QApplication.instance() or gui.QApplication([])
    window = gui.SetupWindow(tmp_path / "watchlog.ini", **kw)
    window.show()
    return window


def _verified(url, serial, names):
    return {"url": url, "vendor": "Test", "model": "NVR", "firmware": "", "serial": serial,
            "driver": "onvif", "verified_against_hardware": False, "capabilities": None,
            "channels": [{"channel": str(i + 1), "name": n} for i, n in enumerate(names)]}


def test_a_single_recorder_install_sends_exactly_what_it_used_to(gui, tmp_path):
    window = _window(gui, tmp_path, installer_child=True)
    window.code_edit.setText("WL-ABCD-1234")
    window.recorder_address, window.recorder_user, window.recorder_password = \
        "10.0.0.5", "admin", "pw"
    window.connection_ok(_verified("http://10.0.0.5", "S1", ["Gate"]))

    plan = window.install_plan()

    assert plan["args"][2:] == ("WL-ABCD-1234", "10.0.0.5", "admin", "pw", "custom",
                                [{"channel": "1", "name": "Gate", "purpose": "custom",
                                  "monitored": True, "analytics_enabled": True}])
    assert plan["kwargs"]["additional_recorders"] is None
    assert plan["kwargs"]["primary_display_name"] is None
    assert plan["kwargs"]["verified_recorder"]["serial"] == "S1"


def test_recorders_cannot_share_a_name(gui, tmp_path):
    window = _window(gui, tmp_path)
    window._discovered = {"10.0.0.6": {"ip": "10.0.0.6", "label": "Recorder"}}
    window.recorder_address = "10.0.0.5"
    window.connection_ok(_verified("http://10.0.0.5", "S1", ["Gate"]))
    window.add_install_recorder()
    window.recorder_address = "10.0.0.6"
    window.connection_ok(_verified("http://10.0.0.6", "S2", ["Yard"]))
    window.recorder_name_edit.setText("primary recorder")
    gui.QMessageBox.messages.clear()

    window.add_install_recorder()

    assert len(window.install_recorders) == 1
    assert gui.QMessageBox.messages[-1][2] == "Give each recorder a different name."


def test_the_post_install_add_path_still_uses_the_existing_site_activation(gui, tmp_path):
    window = _window(gui, tmp_path, manage_recorders=True)
    assert window.add_another_btn.isHidden()
    assert window.recorder_name_edit.isHidden()
    assert "backend.add_existing_site_recorder" in GUI_SOURCE


def test_manage_recorders_shows_what_a_disabled_recorder_kept():
    toggle = GUI_SOURCE[GUI_SOURCE.find("    def toggle_selected"):]
    toggle = toggle[:toggle.find("\n    def ", 10)]
    assert "backend.disabled_recorder_message" in toggle
    refresh = GUI_SOURCE[GUI_SOURCE.find("    def refresh"):]
    refresh = refresh[:refresh.find("\n    def ", 10)]
    assert "backend.managed_recorder_state(row)" in refresh

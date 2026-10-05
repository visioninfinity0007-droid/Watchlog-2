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
    monkeypatch.setattr(gui.backend, "finalize_install", refuse)


@pytest.mark.parametrize("installer_child", [False, True])
def test_the_setup_ui_selftest_passes(gui, installer_child):
    assert gui._run_ui_selftest(installer_child=installer_child) == 0


@pytest.mark.parametrize("installer_child", [False, True])
def test_the_selftest_discovery_check_does_not_depend_on_machine_load(
        gui, monkeypatch, installer_child):
    """A loaded CI runner made the simulated discovery sweep exceed a 5 s wall-clock
    budget, and the selftest returned 30 although discovery was fine. Simulate that load:
    the machine stalls for 6 s during every sweep."""
    import time
    import discover
    real_monotonic, real_sweep, stall = time.monotonic, discover.sweep, [0.0]

    def stalled_sweep(*args, **kwargs):
        try:
            return real_sweep(*args, **kwargs)
        finally:
            stall[0] += 6.0

    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + stall[0])
    monkeypatch.setattr(discover, "sweep", stalled_sweep)
    assert gui._run_ui_selftest(installer_child=installer_child) == 0
    assert stall[0] > 0, "the selftest no longer exercises the discovery engine"


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
    window.recorder_password = "pw-b"            # every recorder is signed in to
    window.connection_ok(_verified("http://10.0.0.6", "S2", ["Yard"]))
    window.recorder_name_edit.setText("primary recorder")

    window.add_install_recorder()

    assert len(window.install_recorders) == 1 and window.stack.currentIndex() == 4
    assert window.cameras_error.text() == "Give each recorder a different name."


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


# --- review round: leaving, removing and checking recorders before Connect -------------

def _settle(gui, seconds):
    """Run queued signals and due timers for ``seconds`` (e.g. a 1.4 s auto-close)."""
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        gui.QApplication.instance().processEvents()
        time.sleep(0.02)
    gui.QApplication.instance().processEvents()


def _fresh_window(gui, tmp_path, **kw):
    qt_fake._TIMERS.clear()                    # no timer left over from another window
    window = _window(gui, tmp_path, **kw)
    window.code_edit.setText("WL-ABCD-1234")
    window._discovered = {ip: {"ip": ip, "label": "Recorder"}
                          for ip in ("10.0.0.5", "10.0.0.6")}
    return window


def _first_recorder_added(gui, tmp_path, **kw):
    """The first recorder ("Shop") verified, then "Add another recorder" pressed."""
    window = _fresh_window(gui, tmp_path, **kw)
    window.recorder_address, window.recorder_user, window.recorder_password = \
        "10.0.0.5", "admin-a", "pw-a"
    window.connection_ok(_verified("http://10.0.0.5", "S1", ["Gate", "Till"]))
    window.recorder_name_edit.setText("Shop")
    window.add_install_recorder()
    assert window.stack.currentIndex() == 2 and len(window.install_recorders) == 1
    return window


def _second_recorder_on_screen(gui, tmp_path, name="Warehouse", **kw):
    window = _first_recorder_added(gui, tmp_path, **kw)
    window.manual_ip.setText("10.0.0.6")
    window.recorder_continue()
    window.recorder_user, window.recorder_password = "admin-b", "pw-b"
    window.connection_ok(_verified("http://10.0.0.6", "S2", ["Yard"]))
    window.recorder_name_edit.setText(name)
    assert window.stack.currentIndex() == 4
    return window


def _assert_plan_is_only_the_first_recorder(window):
    plan = window.install_plan()
    assert plan["args"][3:6] == ("10.0.0.5", "admin-a", "pw-a")
    assert plan["kwargs"]["additional_recorders"] is None
    assert plan["kwargs"]["primary_display_name"] == "Shop"
    assert plan["kwargs"]["verified_recorder"]["serial"] == "S1"


def _table(window):
    return [tuple(window.camera_table.item(r, c).text() for c in range(3))
            for r in range(window.camera_table.rowCount())]


def test_find_recorder_can_go_back_to_the_cameras_without_another_recorder(gui, tmp_path):
    window = _first_recorder_added(gui, tmp_path, installer_child=True)
    assert not window.back_to_cameras_btn.isHidden()

    window.back_to_cameras_btn.clicked.emit()

    assert window.stack.currentIndex() == 4
    assert window.install_recorders == []
    assert window.recorder_name_edit.text() == "Shop"
    assert _table(window) == [("Shop", "1", "Gate"), ("Shop", "2", "Till")]
    _assert_plan_is_only_the_first_recorder(window)
    assert window.back_to_cameras_btn.isHidden()


def test_a_second_recorder_that_cannot_sign_in_can_be_abandoned(gui, tmp_path):
    """Offline recorder or unknown password: the technician is not forced to cancel."""
    window = _first_recorder_added(gui, tmp_path, installer_child=True)
    window.manual_ip.setText("10.0.0.6")
    window.recorder_continue()
    assert window.stack.currentIndex() == 3
    window._worker_error("The recorder rejected this username or password.")
    assert not window.login_back_to_cameras_btn.isHidden()

    window.login_back_to_cameras_btn.clicked.emit()

    assert window.stack.currentIndex() == 4 and window.exit_code == 1
    _assert_plan_is_only_the_first_recorder(window)


def test_back_to_cameras_is_offered_only_when_a_recorder_is_already_chosen(gui, tmp_path):
    window = _fresh_window(gui, tmp_path)
    window.go(2)
    assert window.back_to_cameras_btn.isHidden()
    window.go(3)
    assert window.login_back_to_cameras_btn.isHidden()
    manage = _fresh_window(gui, tmp_path, manage_recorders=True)
    manage.go(2)
    assert manage.back_to_cameras_btn.isHidden()


def test_a_chosen_recorder_can_be_removed_before_connect(gui, tmp_path):
    window = _second_recorder_on_screen(gui, tmp_path)
    assert not window.remove_recorder_btn.isHidden()
    assert window.install_list.count() == 2

    window.install_list.setCurrentRow(0)                    # remove "Shop"
    window.remove_recorder_btn.clicked.emit()

    plan = window.install_plan()
    assert plan["args"][3:6] == ("10.0.0.6", "admin-b", "pw-b")
    assert plan["kwargs"]["additional_recorders"] is None
    assert plan["kwargs"]["primary_display_name"] == "Warehouse"
    assert _table(window) == [("Warehouse", "1", "Yard")]
    assert window.remove_recorder_btn.isHidden()            # one recorder left


def test_removing_the_recorder_on_screen_brings_back_the_previous_one(gui, tmp_path):
    window = _second_recorder_on_screen(gui, tmp_path)
    window.install_list.setCurrentRow(1)                    # remove "Warehouse"
    window.remove_recorder_btn.clicked.emit()

    assert window.stack.currentIndex() == 4
    _assert_plan_is_only_the_first_recorder(window)


def test_duplicate_names_keep_an_installer_child_on_the_camera_step(gui, tmp_path):
    window = _second_recorder_on_screen(gui, tmp_path, name="shop", installer_child=True)

    window.begin_finalize()
    _settle(gui, 1.7)                     # longer than the installer-child auto-close

    assert window.stack.currentIndex() == 4
    assert window.exit_code == 1 and window.isVisible()
    assert window._active_worker == 0     # finalize_install never started
    assert window.cameras_error.text() == "Give each recorder a different name."


def test_a_blank_name_is_refused_once_there_are_several_recorders(gui, tmp_path):
    window = _second_recorder_on_screen(gui, tmp_path, name="   ", installer_child=True)

    window.begin_finalize()

    assert window.stack.currentIndex() == 4 and window._active_worker == 0
    assert window.cameras_error.text() == "Give each recorder a name."


def test_the_same_recorder_twice_is_refused_on_the_camera_step(gui, tmp_path):
    window = _second_recorder_on_screen(gui, tmp_path, installer_child=True)
    window.install_recorders.append(dict(window.install_recorders[0], display_name="Again"))

    window.begin_finalize()

    assert window.stack.currentIndex() == 4 and window._active_worker == 0
    assert "same recorder" in window.cameras_error.text()


def test_the_prefilled_name_never_repeats_an_earlier_recorders_name(gui, tmp_path):
    window = _fresh_window(gui, tmp_path)
    window.recorder_address = "10.0.0.5"
    window.connection_ok(_verified("http://10.0.0.5", "S1", ["Gate"]))
    window.recorder_name_edit.setText("Recorder 2")
    window.add_install_recorder()
    window.recorder_address = "10.0.0.6"
    window.connection_ok(_verified("http://10.0.0.6", "S2", ["Yard"]))

    assert window.recorder_name_edit.text() == "Recorder 3"


def test_a_single_recorder_install_still_connects_with_a_blank_name(gui, tmp_path):
    window = _fresh_window(gui, tmp_path)
    window.recorder_address = "10.0.0.5"
    window.connection_ok(_verified("http://10.0.0.5", "S1", ["Gate"]))
    window.recorder_name_edit.setText("")
    started = []
    window.run_worker = lambda fn, args, *a, **kw: started.append((fn, args, kw))

    window.begin_finalize()

    assert window.stack.currentIndex() == 5 and len(started) == 1
    assert started[0][2]["primary_display_name"] is None
    assert started[0][2]["additional_recorders"] is None


def test_a_connect_failure_stays_retryable_in_installer_child_mode(gui, tmp_path):
    window = _fresh_window(gui, tmp_path, installer_child=True)
    window.go(5)

    window._worker_error("WatchLog could not link this site's recorders. Please try again.")
    _settle(gui, 1.7)

    assert window.isVisible() and window.exit_code == 1
    assert not window.retry_btn.isHidden()
    assert not window.incomplete_bundle_btn.isHidden()
    assert not window.incomplete_exit_btn.isHidden()
    assert "could not link" in window.connect_error.text()

    window.incomplete_exit_btn.clicked.emit()               # explicit Exit: NSIS stops
    assert window.exit_code == 1 and not window.isVisible()


def test_a_connect_watchdog_timeout_still_ends_an_installer_child(gui, tmp_path):
    """The timed-out finalize thread cannot be stopped; only process exit stops it."""
    window = _fresh_window(gui, tmp_path, installer_child=True)
    window.go(5)
    window._active_worker = 7

    window._worker_timeout(7, "WatchLog could not finish the site connection.")
    _settle(gui, 1.7)

    assert window.exit_code == 2 and not window.isVisible()

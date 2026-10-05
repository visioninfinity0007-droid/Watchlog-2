"""The installer launches Setup as an installer child (MNVR-067 sub-item).

watchlog.nsi ExecWaits watchlog-setup-ui.exe and only registers the background task,
the uninstaller and Add/Remove Programs after it returns. setup_gui has an
--installer-child mode built for exactly that wait: a finished setup closes itself and
returns 0, a failed one returns non-zero. NSIS never passed the flag, so a successful
install sat on the Ready page until someone clicked Finish. The Start Menu shortcuts are
interactive tools and stay without it.

In installer-child mode a Connect failure that returned (network blip, partial camera
sync, a refusal) keeps Retry, Export Support Bundle and Exit in the same window, as an
install without the flag always did; only Exit or Cancel returns non-zero to NSIS. Two
paths stay terminal by design: the Connect watchdog (the timed-out thread cannot be
stopped, so a Retry would run a second finalize beside it) and a background Agent that
did not start (the installer restores the previous version on an upgrade).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NSIS = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text(encoding="utf-8")
GUI = (ROOT / "prototype/agent/setup_gui.py").read_text(encoding="utf-8")


def _setup_launches():
    return re.findall(r"ExecWait '\"\$INSTDIR\\watchlog-setup-ui\.exe\"([^']*)'", NSIS)


def test_every_interactive_setup_launch_is_an_installer_child():
    launches = [args for args in _setup_launches() if "--migrate-only" not in args]
    assert len(launches) == 2, launches          # fresh install + upgrade credential repair
    assert all(args.strip().startswith("--installer-child --config") for args in launches)


def test_the_migration_launch_and_the_shortcuts_are_unchanged():
    assert any("--migrate-only" in args and "--installer-child" not in args
               for args in _setup_launches())
    shortcuts = [line for line in NSIS.splitlines() if line.strip().startswith("CreateShortcut")]
    assert shortcuts and not any("--installer-child" in line for line in shortcuts)


def test_setup_ui_accepts_the_flag_and_adds_recorders_before_connect_in_that_mode():
    assert 'parser.add_argument("--installer-child", action="store_true")' in GUI
    # The first-install "Add another recorder" path must not be gated on the mode.
    assert "self.add_another_btn.setVisible(not self.manage_recorders)" in GUI
    add = GUI[GUI.find("    def add_install_recorder"):]
    add = add[:add.find("\n    def ", 10)]
    assert "installer_child" not in add


def test_only_the_connect_watchdog_ends_an_installer_child_on_a_connect_failure():
    error = GUI[GUI.find("    def _worker_error(self"):]
    error = error[:error.find("\n    def ", 10)]
    assert "_terminal_installer_failure" not in error
    assert "self.retry_btn.show()" in error
    timeout = GUI[GUI.find("    def _worker_timeout(self"):]
    timeout = timeout[:timeout.find("\n    def ", 10)]
    assert "self._terminal_installer_failure(message)" in timeout

"""Branded graphical first-run setup for WatchLog on Windows.

Customer path:
Welcome -> Site Code -> Find Recorder -> Recorder Login -> Camera Check ->
Connect -> Ready.

All network/recorder work runs off the Qt UI thread. Detailed diagnostic output
is redirected to ProgramData; the customer sees concise actionable messages.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# A PyInstaller --windowed process has no console streams. Existing recorder
# libraries use print() for diagnostics, so give them a local file instead of
# letting a diagnostic print crash the GUI.
_LOG_HANDLE = None
if os.name == "nt":
    # Guarded: this runs at IMPORT, before any handler exists. A locked or unwritable
    # setup.log (Defender, a support-bundle read, a full disk) would raise here and kill a
    # --windowed build with NO window and no message -- the customer sees the installer do
    # nothing at all. Fall back to temp, then to leaving stdio alone.
    for _candidate in (Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog",
                       Path(tempfile.gettempdir())):
        try:
            _candidate.mkdir(parents=True, exist_ok=True)
            _LOG_HANDLE = (_candidate / "setup.log").open("a", encoding="utf-8", buffering=1)
            sys.stdout = _LOG_HANDLE
            sys.stderr = _LOG_HANDLE
            break
        except Exception:  # noqa: BLE001 - logging may never prevent setup from running
            _LOG_HANDLE = None

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHeaderView,
)

import setup_backend as backend
from status_controller import StatusController

ICE = "#72D4FF"
BLUE = "#1748D3"
VIOLET = "#5B21FF"
NAVY = "#07111F"
PANEL = "#0D1A2A"
TEXT = "#F7FAFF"
MUTED = "#A9B6C7"
BORDER = "#203047"

STYLE = f"""
QWidget {{ background:{NAVY}; color:{TEXT}; font-family:'Segoe UI'; font-size:14px; }}
QLabel {{ background:transparent; }}
QLabel#brand {{ font-size:22px; font-weight:700; }}
QLabel#title {{ font-size:30px; font-weight:700; }}
QLabel#eyebrow {{ color:{ICE}; font-size:12px; font-weight:700; }}
QLabel#muted {{ color:{MUTED}; }}
QFrame#card {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:14px; }}
QLineEdit, QComboBox, QListWidget, QTableWidget {{ background:#0A1624; border:1px solid {BORDER}; border-radius:8px; padding:10px; selection-background-color:{BLUE}; }}
QLineEdit:focus, QComboBox:focus, QListWidget:focus {{ border:1px solid {ICE}; }}
QPushButton {{ background:{BLUE}; border:0; border-radius:8px; padding:11px 18px; font-weight:600; }}
QPushButton:hover {{ background:#235AE4; }}
QPushButton:disabled {{ background:#243247; color:#6F7E91; }}
QPushButton#secondary {{ background:#122238; border:1px solid {BORDER}; }}
QPushButton#ghost {{ background:transparent; color:{MUTED}; }}
QProgressBar {{ border:0; border-radius:4px; background:#142237; height:8px; text-align:center; color:transparent; }}
QProgressBar::chunk {{ border-radius:4px; background:{VIOLET}; }}
QHeaderView::section {{ background:#101E30; color:{MUTED}; padding:8px; border:0; border-bottom:1px solid {BORDER}; }}
QTableWidget {{ gridline-color:{BORDER}; }}
"""


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            kwargs = dict(self.kwargs)
            kwargs["progress"] = self.signals.progress.emit
            result = self.fn(*self.args, **kwargs)
            self.signals.finished.emit(result)
        except Exception as exc:  # customer-safe conversion happens in backend
            self.signals.failed.emit(str(exc) or "WatchLog setup could not complete this step.")


def label(text: str, object_name: str = "") -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(True)
    return widget


def card_layout() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    return frame, layout


class SetupWindow(QMainWindow):
    STEPS = ["Welcome", "Site Code", "Recorder", "Login", "Cameras", "Connecting", "Ready"]

    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.public = backend.read_public_defaults(config_path)
        self.pool = QThreadPool.globalInstance()
        self.exit_code = 1
        self.site_connected = False   # set once the agent is registered + running
        self.recorder_address = self.public.get("nvr_url", "")
        self.recorder_user = self.public.get("nvr_username", "admin")
        self.recorder_password = ""
        self.recorder_result = None
        self.recorder_hint = None
        self._discovered = {}
        self.final_result = None
        self._busy = False

        self.setWindowTitle("WatchLog Setup")
        self.setMinimumSize(900, 630)
        self.resize(980, 680)
        self.setStyleSheet(STYLE)
        icon = self._asset("setup.ico")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setFixedWidth(230)
        side.setStyleSheet("QFrame{background:#081522;border-right:1px solid #18283C;}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(26, 28, 20, 24)
        sl.addWidget(label("W  WatchLog", "brand"))
        sl.addWidget(label("SITE CONNECTION SETUP", "eyebrow"))
        sl.addSpacing(24)
        self.step_labels = []
        for i, name in enumerate(self.STEPS):
            item = QLabel(f"{i + 1:02d}   {name}")
            item.setStyleSheet(f"color:{MUTED};padding:8px 0;font-size:13px;")
            self.step_labels.append(item)
            sl.addWidget(item)
        sl.addStretch(1)
        sl.addWidget(label("Recorder credentials stay on this PC. WatchLog connects outward only.", "muted"))
        outer.addWidget(side)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(42, 34, 42, 28)
        self.status = label("", "muted")
        self.status.setMinimumHeight(22)
        self.stack = QStackedWidget()
        self._build_pages()
        bl.addWidget(self.stack, 1)
        bl.addWidget(self.status)
        outer.addWidget(body, 1)
        self.go(0)

    def _asset(self, name: str) -> Path:
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / name
        return Path(__file__).resolve().parent.parent / "installer" / name

    def _page(self, eyebrow: str, title: str, body: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(label(eyebrow.upper(), "eyebrow"))
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(body, "muted"))
        layout.addSpacing(8)
        return page, layout

    def _nav(self, layout, back=None, primary="Continue", action=None):
        row = QHBoxLayout()
        if back is not None:
            b = QPushButton("Back")
            b.setObjectName("secondary")
            b.clicked.connect(lambda: self.go(back))
            row.addWidget(b)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.cancel)
        row.addWidget(cancel)
        p = QPushButton(primary)
        if action:
            p.clicked.connect(action)
        row.addWidget(p)
        layout.addStretch(1)
        layout.addLayout(row)
        return p

    def _build_pages(self):
        # Welcome
        page, l = self._page("Welcome", "Connect this site to WatchLog",
            "WatchLog connects securely to the CCTV recorder already installed at this site. No port forwarding, inbound firewall changes or replacement cameras are required.")
        c, cl = card_layout()
        for text in ["✓  Recorder stays on your local network",
                     "✓  Recorder password is protected on this Windows PC",
                     "✓  Existing CCTV recording continues normally",
                     "✓  Only WatchLog business events and approved stills sync outward"]:
            cl.addWidget(label(text))
        l.addWidget(c)
        self._nav(l, None, "Get Started", lambda: self.go(1))
        self.stack.addWidget(page)

        # Code
        page, l = self._page("WatchLog site", "Enter your WatchLog site code",
            "Find this one-time code in WatchLog under Settings → Sites & Setup. It identifies which site this PC belongs to.")
        c, cl = card_layout()
        cl.addWidget(label("SITE CODE", "eyebrow"))
        # Only pre-fill a real WatchLog code. A stale/garbage enrollment_code
        # left in watchlog.ini (e.g. carried across an upgrade) must not
        # pre-populate the field and confuse the operator.
        prefill = self.public.get("enrollment_code", "").strip()
        if not prefill.upper().startswith("WL-"):
            prefill = ""
        self.code_edit = QLineEdit(prefill)
        self.code_edit.setPlaceholderText("WL-XXXX-XXXX")
        self.code_edit.setMinimumHeight(44)
        cl.addWidget(self.code_edit)
        cl.addWidget(label("The code is verified during the secure connection step and is never used as your recorder password.", "muted"))
        l.addWidget(c)
        self._nav(l, 0, "Continue", self.code_continue)
        self.stack.addWidget(page)

        # Recorder discovery
        page, l = self._page("Recorder", "Find your CCTV recorder",
            "WatchLog searches only this local network. If automatic discovery misses the recorder, enter its local IP address manually.")
        c, cl = card_layout()
        row = QHBoxLayout()
        self.search_btn = QPushButton("Search Network")
        self.search_btn.clicked.connect(self.search_recorders)
        row.addWidget(self.search_btn)
        row.addStretch(1)
        cl.addLayout(row)
        self.recorder_list = QListWidget()
        self.recorder_list.setMinimumHeight(150)
        self.recorder_list.itemSelectionChanged.connect(self.recorder_selected)
        cl.addWidget(self.recorder_list)
        cl.addWidget(label("OR ENTER THE LOCAL ADDRESS", "eyebrow"))
        self.manual_ip = QLineEdit(self.recorder_address)
        self.manual_ip.setPlaceholderText("192.168.1.108")
        cl.addWidget(self.manual_ip)
        l.addWidget(c)
        self.recorder_next = self._nav(l, 1, "Continue", self.recorder_continue)
        self.stack.addWidget(page)

        # Login
        page, l = self._page("Recorder login", "Sign in to your recorder",
            "Use the recorder's own account. WatchLog verifies it locally and does not send the password to WatchLog.")
        c, cl = card_layout()
        self.selected_recorder = label("Recorder: —", "muted")
        cl.addWidget(self.selected_recorder)
        cl.addWidget(label("USERNAME", "eyebrow"))
        self.user_edit = QLineEdit(self.recorder_user)
        cl.addWidget(self.user_edit)
        cl.addWidget(label("PASSWORD", "eyebrow"))
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        cl.addWidget(self.password_edit)
        cl.addWidget(label("Your recorder password is protected securely on this PC after setup succeeds.", "muted"))
        l.addWidget(c)
        self.login_next = self._nav(l, 2, "Test Connection", self.test_connection)
        self.stack.addWidget(page)

        # Cameras — connectivity proof only. Naming/purpose/analytics belong in the portal.
        page, l = self._page("Camera check", "Confirm the recorder channels",
            "WatchLog has signed in to the recorder and read its camera inventory. Camera naming, purpose and analytics configuration are done later in the WatchLog portal.")
        c, cl = card_layout()
        self.recorder_summary = label("", "muted")
        cl.addWidget(self.recorder_summary)
        self.camera_table = QTableWidget(0, 2)
        self.camera_table.setHorizontalHeaderLabels(["Channel", "Recorder camera name"])
        self.camera_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.camera_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.camera_table.setMinimumHeight(220)
        cl.addWidget(self.camera_table)
        cl.addWidget(label(
            "All discovered channels are connected by default. You can rename, ignore or assign camera purposes from the portal after setup.",
            "muted"))
        l.addWidget(c)
        self._nav(l, 3, "Connect WatchLog", self.begin_finalize)
        self.stack.addWidget(page)

        # Connecting
        page, l = self._page("Connecting", "Connecting this site to WatchLog",
            "These are real setup checks. WatchLog will not mark the installation complete if a required step fails.")
        c, cl = card_layout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        cl.addWidget(self.progress_bar)
        self.progress_label = label("Preparing…")
        cl.addWidget(self.progress_label)
        self.connect_error = label("", "muted")
        cl.addWidget(self.connect_error)
        actions = QHBoxLayout()
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setObjectName("secondary")
        self.retry_btn.clicked.connect(self.begin_finalize)
        self.retry_btn.hide()
        actions.addWidget(self.retry_btn)
        # Actions available when WatchLog could NOT verify the installation (fail-closed, P1.1).
        self.incomplete_status_btn = QPushButton("Open Site Status")
        self.incomplete_status_btn.setObjectName("secondary")
        self.incomplete_status_btn.clicked.connect(self.open_site_status)
        self.incomplete_status_btn.hide()
        actions.addWidget(self.incomplete_status_btn)
        self.incomplete_bundle_btn = QPushButton("Export Support Bundle")
        self.incomplete_bundle_btn.setObjectName("secondary")
        self.incomplete_bundle_btn.clicked.connect(self.export_support_bundle_action)
        self.incomplete_bundle_btn.hide()
        actions.addWidget(self.incomplete_bundle_btn)
        self.incomplete_exit_btn = QPushButton("Exit")
        self.incomplete_exit_btn.setObjectName("ghost")
        self.incomplete_exit_btn.clicked.connect(self.cancel)
        self.incomplete_exit_btn.hide()
        actions.addWidget(self.incomplete_exit_btn)
        actions.addStretch(1)
        cl.addLayout(actions)
        l.addWidget(c)
        l.addStretch(1)
        self.stack.addWidget(page)

        # Success
        page, l = self._page("Ready", "WatchLog is ready",
            "This WatchLog connection is ready and will run securely in the background when setup closes.")
        c, cl = card_layout()
        self.success_summary = label("")
        cl.addWidget(self.success_summary)
        cl.addWidget(label("You can now return to the WatchLog portal. The Site Connector will keep the recorder connection, camera health, events and evidence services online automatically.", "muted"))
        l.addWidget(c)
        row = QHBoxLayout()
        row.addStretch(1)
        open_status = QPushButton("Open Site Status")
        open_status.setObjectName("secondary")
        open_status.clicked.connect(self.open_site_status)
        row.addWidget(open_status)
        finish = QPushButton("Finish")
        finish.clicked.connect(self.finish)
        row.addWidget(finish)
        l.addStretch(1)
        l.addLayout(row)
        self.stack.addWidget(page)

    def go(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, item in enumerate(self.step_labels):
            if i < index:
                item.setStyleSheet(f"color:{ICE};padding:8px 0;font-size:13px;")
            elif i == index:
                item.setStyleSheet(f"color:{TEXT};padding:8px 0;font-size:13px;font-weight:700;")
            else:
                item.setStyleSheet(f"color:{MUTED};padding:8px 0;font-size:13px;")
        self.status.setText("")

    def set_busy(self, busy: bool, message: str = ""):
        self._busy = busy
        self.status.setText(message)
        self.search_btn.setEnabled(not busy)
        self.login_next.setEnabled(not busy)

    def run_worker(self, fn, args, on_success, busy_message: str, **kwargs):
        self.set_busy(True, busy_message)
        worker = Worker(fn, *args, **kwargs)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.finished.connect(lambda result: self._worker_ok(on_success, result))
        worker.signals.failed.connect(self._worker_error)
        self.pool.start(worker)

    def _on_progress(self, message: str):
        """Surface live worker progress. The Connecting page has its own label, so mirror it there
        as well — an indeterminate bar with no changing text is indistinguishable from a hang."""
        self.status.setText(message)
        if self.stack.currentIndex() == 5:
            self.progress_label.setText(message)

    def _worker_ok(self, callback, result):
        self.set_busy(False)
        callback(result)

    def _worker_error(self, message: str):
        self.set_busy(False)
        if self.stack.currentIndex() == 5:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.connect_error.setText(message)
            self.retry_btn.show()
            # Retry was the ONLY control here, so a technician whose setup failed had no
            # way to export a support bundle and no way out except killing the window --
            # which then aborted the NSIS install. Mirror the acceptance-failure page.
            self.incomplete_bundle_btn.show()
            self.incomplete_exit_btn.show()
        else:
            QMessageBox.warning(self, "WatchLog Setup", message)

    def code_continue(self):
        code = self.code_edit.text().strip()
        if len(code) < 6:
            QMessageBox.warning(self, "WatchLog Setup", "Enter the site code shown in WatchLog Settings → Sites & Setup.")
            return
        self.go(2)
        if not self.recorder_list.count():
            self.search_recorders()

    def search_recorders(self):
        self.recorder_list.clear()
        self.run_worker(backend.discover_recorders, (), self.show_recorders, "Searching the local network…")

    def show_recorders(self, rows):
        self._discovered = {row["ip"]: row for row in rows}
        if not rows:
            self.status.setText("No recorder was found automatically. Enter its local IP address below.")
            return
        for row in rows:
            text = f"{row['ip']}   {row.get('label') or 'Recorder'}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, row["ip"])
            self.recorder_list.addItem(item)
        recorder_word = "recorder" if len(rows) == 1 else "recorders"
        found = f"Found {len(rows)} possible {recorder_word}."
        if len(rows) > 1:
            # WatchLog monitors exactly ONE recorder per installation: the agent holds a
            # single nvr_url, and cameras are unique per (site_id, channel), so pointing a
            # second recorder at the same site SILENTLY overwrites the first one's camera
            # rows and stops monitoring it. A technician who picks one here and leaves on a
            # green screen would believe a 2-recorder site was fully covered. Say it.
            found += (" WatchLog monitors ONE recorder per installation - pick the one this"
                      " PC should monitor. A second recorder needs its own WatchLog site and"
                      " its own PC.")
        self.status.setText(found)

    def recorder_selected(self):
        items = self.recorder_list.selectedItems()
        if items:
            self.manual_ip.setText(items[0].data(Qt.UserRole))

    def recorder_continue(self):
        address = self.manual_ip.text().strip()
        if not address:
            QMessageBox.warning(self, "WatchLog Setup", "Select a discovered recorder or enter its local IP address.")
            return
        self.recorder_address = address
        row = self._discovered.get(address)
        self.recorder_hint = ({"ports": row.get("ports"),
                               "vendor_hint": row.get("vendor_hint"),
                               "source": row.get("source")} if row else None)
        detail = f"  ({row['label']})" if row and row.get("label") else ""
        self.selected_recorder.setText(f"Recorder: {address}{detail}")
        self.go(3)

    def test_connection(self):
        address = self.recorder_address
        user = self.user_edit.text().strip()
        password = self.password_edit.text()
        if not user or not password:
            QMessageBox.warning(self, "WatchLog Setup", "Enter the recorder username and password.")
            return
        self.recorder_user, self.recorder_password = user, password
        self.run_worker(backend.test_recorder, (address, user, password), self.connection_ok,
                        "Testing the recorder connection…", hint=self.recorder_hint)

    def connection_ok(self, result):
        self.recorder_result = result
        self.recorder_summary.setText(
            f"{result['vendor']} {result['model']}  •  {len(result['channels'])} camera(s)  •  Connection verified")
        self.populate_cameras()
        self.go(4)

    def populate_cameras(self):
        channels = self.recorder_result["channels"] if self.recorder_result else []
        self.camera_table.setRowCount(len(channels))
        for row, camera in enumerate(channels):
            channel_item = QTableWidgetItem(str(camera["channel"]))
            channel_item.setFlags(channel_item.flags() & ~Qt.ItemIsEditable)
            name_item = QTableWidgetItem(camera.get("name") or f"Camera {camera['channel']}")
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.camera_table.setItem(row, 0, channel_item)
            self.camera_table.setItem(row, 1, name_item)

    def profiles(self):
        """Connectivity-first defaults; richer camera configuration belongs in portal."""
        channels = self.recorder_result["channels"] if self.recorder_result else []
        return [{
            "channel": str(camera["channel"]),
            "name": camera.get("name") or f"Camera {camera['channel']}",
            "purpose": "custom",
            "monitored": True,
            "analytics_enabled": True,
        } for camera in channels]

    def begin_finalize(self):
        if self._busy:
            return
        self.go(5)
        self.retry_btn.hide()
        self.incomplete_status_btn.hide()
        self.incomplete_bundle_btn.hide()
        self.incomplete_exit_btn.hide()
        self.connect_error.setText("")
        self.progress_bar.setRange(0, 0)
        public = dict(self.public)
        args = (self.config_path, public, self.code_edit.text().strip(), self.recorder_address,
                self.recorder_user, self.recorder_password,
                "custom", self.profiles())
        self.run_worker(backend.finalize_install, args, self.finalize_ok, "Connecting to WatchLog…",
                        hint=self.recorder_hint)
        # On the connecting page use the page-local progress label too.
        self.status.setText("")

    def finalize_ok(self, result):
        # Install is proven; now run the FULL acceptance suite before declaring Ready. The setup
        # never shows a green Ready state after a hard acceptance failure (0.4.4 P8).
        self.final_result = result
        # The background agent was already started inside finalize_install, on the worker
        # thread. It must NOT be started from here: this is the GUI thread, and blocking it
        # freezes the window mid-repaint -- which is exactly why 0.4.7 appeared to hang on
        # "Confirming the WatchLog connection" while it was really running my own code.
        self.agent_start = (result or {}).get("agent_start") or {}
        self.site_connected = bool((result or {}).get("connected"))
        self.progress_label.setText("Running final acceptance checks…")
        from status_controller import StatusController
        ctrl = StatusController()
        self.run_worker(lambda progress=None: ctrl.run_acceptance(progress=progress), (),
                        self.acceptance_done, "Running final acceptance checks…")

    def _push_line(self) -> str:
        """PC-free reporting status. Only claims active when the RECORDER confirmed the
        config on read-back; anything else states what actually happened."""
        info = (getattr(self, "final_result", None) or {}).get("recorder_push") or {}
        if info.get("verified"):
            return "✓ PC-free reporting active (the recorder reports even if this PC is off)"
        if info.get("configured"):
            return f"! PC-free reporting not confirmed by the recorder — {info.get('detail', '')}"
        return "· PC-free reporting not enabled (this PC does the reporting)"

    def _background_line(self) -> str:
        """Say plainly whether the BACKGROUND service is running.

        Only claims what was actually verified: register-service.ps1 throws unless the
        scheduled task reaches Running, so "started" is a real check, not an assumption.
        It deliberately does NOT claim the site is "reporting" -- the local agent log is
        written through a PowerShell redirection that does not reach disk promptly, and
        0.4.8 wrongly reported failure by trusting it."""
        info = getattr(self, "agent_start", None) or {}
        if info.get("started"):
            return "✓ WatchLog is running in the background (starts automatically at boot)"
        return ("! WatchLog is NOT running in the background yet — this site will not "
                "report until that is fixed")

    def acceptance_done(self, acc):
        result = self.final_result or {}
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        acc = acc or {}
        verdict = acc.get("verdict", StatusController.CANNOT_VERIFY)
        # FAIL CLOSED (P1.1): advance to the green Ready page ONLY when acceptance really passed.
        # A missing / unparseable / could-not-run report BLOCKS Ready — it never reaches READY.
        if acc.get("ready", False):
            base = (f"✓ Recorder verified\n✓ WatchLog site linked\n"
                    f"✓ {result.get('camera_count', 0)} camera(s) connected\n"
                    f"✓ Recorder credential encrypted on this PC\n"
                    f"{self._background_line()}\n"
                    f"{self._push_line()}\n\n"
                    f"{result.get('vendor', '')} {result.get('model', '')}")
            if acc.get("warnings"):
                base += "\n\nWarnings:\n" + "\n".join(f"• {w}" for w in acc["warnings"])
            self.success_summary.setText(f"{verdict}\n\n{base}")
            self.go(6)
        else:
            detail = (acc.get("failed") or [])
            reason = ("\n\nThese required checks need attention before WatchLog is ready:\n" +
                      "\n".join(f"• {f}" for f in detail)) if detail else \
                     "\n\nWatchLog could not verify this installation. Fix the issue and retry, or "\
                     "export a support bundle for help."
            self.connect_error.setText(verdict + reason)
            self.retry_btn.setText("Retry")
            self.retry_btn.show()
            self.incomplete_status_btn.show()
            self.incomplete_bundle_btn.show()
            self.incomplete_exit_btn.show()

    def open_site_status(self):
        # Post-install, the same WatchLog app opens the Site Status / control panel.
        import site_status_gui
        self._status_win = site_status_gui.SiteStatusWindow(self.config_path)
        self._status_win.show()

    def export_support_bundle_action(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Save Support Bundle", "watchlog-support.zip",
                                              "Zip Archive (*.zip)")
        if not dest:
            return
        ctrl = StatusController()
        self.run_worker(lambda progress=None: ctrl.export_support_bundle(dest_path=dest), (),
                        lambda r: QMessageBox.information(self, "Support Bundle",
                            f"Saved to:\n{r['path']}" if r.get("ok") else "Support bundle could not be created."),
                        "Creating support bundle…")

    def finish(self):
        self.exit_code = 0
        self.close()

    def cancel(self):
        if self._busy:
            reply = QMessageBox.question(self, "Cancel WatchLog Setup",
                "A setup check is still running. Cancel setup?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        # A CONNECTED site is a SUCCESSFUL install, even if the customer closes the
        # window during verification. Exiting non-zero makes NSIS abort and skip the
        # uninstaller + Add/Remove Programs entries, leaving a working site that Windows
        # does not know is installed. Verification status belongs on the screen, not in
        # the installer's exit code.
        self.exit_code = 0 if getattr(self, "site_connected", False) else 1
        self.close()

    def closeEvent(self, event: QCloseEvent):
        # The window X / Alt+F4 does NOT go through cancel(), so it kept the exit_code=1
        # default even on a fully connected site. NSIS treats non-zero as a failed install
        # and Aborts, skipping WriteUninstaller and the Add/Remove Programs keys -- which is
        # exactly what left a customer with a working, reporting site that Windows did not
        # know was installed. Apply the same rule here: a connected site is a successful
        # install however the window was closed.
        if getattr(self, "site_connected", False) and self.exit_code != 0:
            self.exit_code = 0
        event.accept()


def _emit_line(line: str) -> None:
    """Write a line to the parent console even from this windowed (noconsole)
    frozen build, so `--version` is readable by support and CI."""
    import os
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.AttachConsole(-1)  # ATTACH_PARENT_PROCESS
            with open("CONOUT$", "w", encoding="utf-8") as con:
                con.write(line + "\n")
                con.flush()
            return
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--status", action="store_true",
                        help="open the WatchLog Site Status window instead of first-run setup")
    parser.add_argument("--version", action="store_true")
    args, _unknown = parser.parse_known_args()
    if args.version:
        _emit_line(f"watchlog-setup-ui {backend.SETUP_AGENT_VERSION}")
        return 0
    config_path = Path(args.config) if args.config else Path(sys.executable).resolve().parent / "watchlog.ini"

    if args.status:
        # Post-install: the same WatchLog app opens into the Site Status / control panel.
        import site_status_gui
        return site_status_gui.main(config_path)

    if args.migrate_only:
        try:
            backend.migrate_legacy_credentials(config_path)
            return 0
        except Exception:
            return 2

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WatchLog Setup")
    app.setStyle("Fusion")
    window = SetupWindow(config_path)
    window.show()
    app.exec()
    return window.exit_code


if __name__ == "__main__":
    code = main()
    if _LOG_HANDLE:
        _LOG_HANDLE.flush()
    raise SystemExit(code)

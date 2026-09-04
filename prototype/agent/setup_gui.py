"""Branded graphical first-run setup for WatchLog on Windows.

Customer path:
Welcome -> Site Code -> Find Recorder -> Recorder Login -> Camera Context ->
Connect -> Ready.

All network/recorder work runs off the Qt UI thread. Detailed diagnostic output
is redirected to ProgramData; the customer sees concise actionable messages.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# A PyInstaller --windowed process has no console streams. Existing recorder
# libraries use print() for diagnostics, so give them a local file instead of
# letting a diagnostic print crash the GUI.
_LOG_HANDLE = None
if os.name == "nt":
    log_dir = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WatchLog"
    log_dir.mkdir(parents=True, exist_ok=True)
    _LOG_HANDLE = (log_dir / "setup.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = _LOG_HANDLE
    sys.stderr = _LOG_HANDLE

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHeaderView,
)

import setup_backend as backend

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
        self.recorder_address = self.public.get("nvr_url", "")
        self.recorder_user = self.public.get("nvr_username", "admin")
        self.recorder_password = ""
        self.recorder_result = None
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
        sl.addWidget(label("SITE AGENT SETUP", "eyebrow"))
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
        self.code_edit = QLineEdit(self.public.get("enrollment_code", ""))
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
        cl.addWidget(label("Protected with Windows DPAPI on this PC after setup succeeds.", "muted"))
        l.addWidget(c)
        self.login_next = self._nav(l, 2, "Test Connection", self.test_connection)
        self.stack.addWidget(page)

        # Cameras
        page, l = self._page("Camera context", "Confirm what WatchLog found",
            "Camera names come from the recorder. Purpose suggestions help Analytics Studio start with useful defaults and can be changed later.")
        c, cl = card_layout()
        self.recorder_summary = label("", "muted")
        cl.addWidget(self.recorder_summary)
        site_row = QHBoxLayout()
        site_row.addWidget(label("SITE TYPE", "eyebrow"))
        self.site_type = QComboBox()
        for key, text in backend.SITE_TYPES:
            self.site_type.addItem(text, key)
        self.site_type.currentIndexChanged.connect(self.refresh_purpose_suggestions)
        site_row.addWidget(self.site_type, 1)
        cl.addLayout(site_row)
        self.camera_table = QTableWidget(0, 3)
        self.camera_table.setHorizontalHeaderLabels(["Channel", "Camera", "Purpose"])
        self.camera_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.camera_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.camera_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.camera_table.setMinimumHeight(220)
        cl.addWidget(self.camera_table)
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
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setObjectName("secondary")
        self.retry_btn.clicked.connect(self.begin_finalize)
        self.retry_btn.hide()
        cl.addWidget(self.retry_btn)
        l.addWidget(c)
        l.addStretch(1)
        self.stack.addWidget(page)

        # Success
        page, l = self._page("Ready", "WatchLog is ready",
            "This Site Agent is connected and will run securely in the background when setup closes.")
        c, cl = card_layout()
        self.success_summary = label("")
        cl.addWidget(self.success_summary)
        cl.addWidget(label("You can now return to the WatchLog portal. Site Health and Analytics will update as the background agent checks in.", "muted"))
        l.addWidget(c)
        row = QHBoxLayout()
        row.addStretch(1)
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

    def run_worker(self, fn, args, on_success, busy_message: str):
        self.set_busy(True, busy_message)
        worker = Worker(fn, *args)
        worker.signals.progress.connect(self.status.setText)
        worker.signals.finished.connect(lambda result: self._worker_ok(on_success, result))
        worker.signals.failed.connect(self._worker_error)
        self.pool.start(worker)

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
        if not rows:
            self.status.setText("No recorder was found automatically. Enter its local IP address below.")
            return
        for row in rows:
            text = f"{row['ip']}   {row.get('label') or 'Recorder'}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, row["ip"])
            self.recorder_list.addItem(item)
        self.status.setText(f"Found {len(rows)} recorder candidate(s).")

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
        self.selected_recorder.setText(f"Recorder: {address}")
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
                        "Testing the recorder connection…")

    def connection_ok(self, result):
        self.recorder_result = result
        note = "field-validated driver" if result["verified_against_hardware"] else "protocol connected; model still needs field acceptance"
        self.recorder_summary.setText(
            f"{result['vendor']} {result['model']}  •  {len(result['channels'])} camera(s)  •  {note}")
        site_default = self.public.get("site_type", "custom")
        idx = self.site_type.findData(site_default)
        self.site_type.setCurrentIndex(idx if idx >= 0 else self.site_type.findData("custom"))
        self.populate_cameras()
        self.go(4)

    def populate_cameras(self):
        channels = self.recorder_result["channels"] if self.recorder_result else []
        self.camera_table.setRowCount(len(channels))
        site = self.site_type.currentData() or "custom"
        for row, camera in enumerate(channels):
            ch = QTableWidgetItem(camera["channel"])
            ch.setFlags(ch.flags() & ~Qt.ItemIsEditable)
            name = QTableWidgetItem(camera["name"])
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self.camera_table.setItem(row, 0, ch)
            self.camera_table.setItem(row, 1, name)
            combo = QComboBox()
            for key, text in backend.PURPOSES:
                combo.addItem(text, key)
            suggested = backend.suggest_purpose(camera["name"], site)
            idx = combo.findData(suggested)
            combo.setCurrentIndex(max(0, idx))
            self.camera_table.setCellWidget(row, 2, combo)

    def refresh_purpose_suggestions(self):
        if self.recorder_result:
            self.populate_cameras()

    def profiles(self):
        rows = []
        for row in range(self.camera_table.rowCount()):
            combo = self.camera_table.cellWidget(row, 2)
            rows.append({
                "channel": self.camera_table.item(row, 0).text(),
                "name": self.camera_table.item(row, 1).text(),
                "purpose": combo.currentData() if combo else "custom",
                "analytics_enabled": True,
            })
        return rows

    def begin_finalize(self):
        if self._busy:
            return
        self.go(5)
        self.retry_btn.hide()
        self.connect_error.setText("")
        self.progress_bar.setRange(0, 0)
        public = dict(self.public)
        args = (self.config_path, public, self.code_edit.text().strip(), self.recorder_address,
                self.recorder_user, self.recorder_password,
                self.site_type.currentData() or "custom", self.profiles())
        self.run_worker(backend.finalize_install, args, self.finalize_ok, "Connecting to WatchLog…")
        # On the connecting page use the page-local progress label too.
        self.status.setText("")

    def finalize_ok(self, result):
        self.final_result = result
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.success_summary.setText(
            f"✓ Recorder verified\n✓ WatchLog site linked\n✓ {result['camera_count']} camera(s) synchronized\n"
            f"✓ Recorder credential protected on this PC\n\n{result['vendor']} {result['model']}")
        self.go(6)

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
        self.exit_code = 1
        self.close()

    def closeEvent(self, event: QCloseEvent):
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="")
    parser.add_argument("--migrate-only", action="store_true")
    args, _unknown = parser.parse_known_args()
    config_path = Path(args.config) if args.config else Path(sys.executable).resolve().parent / "watchlog.ini"

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

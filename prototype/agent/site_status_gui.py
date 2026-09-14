"""WatchLog Site Status — the post-install status/control window (installer wave P1/P2/P3).

A thin PySide6 view over status_controller.StatusController: it renders the agent's --status-json
document and wires the operator action buttons (Test Recorder, Rediscover/Configure Cameras,
Recheck Recording/Archive, Run Acceptance, Restart Agent, Check for Updates, Update WatchLog,
Export Support Bundle, Open Logs) to the already-proven agent CLI. All work runs off the UI thread;
no PowerShell/terminal is shown; no secret is rendered.

Business logic lives in status_controller (unit-tested); this file is presentation only, so it is
built/frozen by the Windows setup-ui job rather than imported by the headless test suite.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QFileDialog, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)

from setup_gui import STYLE, Worker, label, card_layout, MUTED, ICE, TEXT
from status_controller import StatusController

# customer-facing state -> (badge text, colour)
_OK = "#3FD07A"
_WARN = "#E8B94A"
_BAD = "#E8626A"
_DIM = MUTED
_COLOR = {
    "running": _OK, "connected": _OK, "ok": _OK, "enrolled": _OK, "verified": _OK,
    "available": _OK, "healthy": _OK, "monitored": _OK,
    "warning": _WARN, "pending": _WARN, "partial": _WARN, "degraded": _WARN,
    "available_frame_unverified": _WARN, "unknown": _DIM, "unused": _DIM, "not_enrolled": _DIM,
    "stopped": _BAD, "disconnected": _BAD, "failed": _BAD, "offline": _BAD, "unsupported": _BAD,
    "empty": _WARN, "unavailable": _DIM,
}


def _badge(value) -> QLabel:
    text = str(value)
    lab = QLabel(text.replace("_", " "))
    lab.setStyleSheet(f"color:{_COLOR.get(text, TEXT)};font-weight:600;")
    return lab


class SiteStatusWindow(QMainWindow):
    def __init__(self, config_path: Path, controller: StatusController = None):
        super().__init__()
        self.controller = controller or StatusController()
        self.setWindowTitle("WatchLog Site Status")
        self.setMinimumSize(940, 680)
        self.setStyleSheet(STYLE)
        self._busy = False

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(label("WatchLog Site Status", "title"))
        head.addStretch(1)
        self.verdict = label("", "eyebrow")
        head.addWidget(self.verdict)
        outer.addLayout(head)
        self.status_line = label("Loading site status…", "muted")
        outer.addWidget(self.status_line)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.sections = {}
        for i, key in enumerate(("agent", "recorder", "cameras", "recording", "archive", "storage")):
            frame, lay = card_layout()
            lay.addWidget(label(key.upper(), "eyebrow"))
            body = QLabel("—")
            body.setWordWrap(True)
            body.setTextFormat(Qt.RichText)
            lay.addWidget(body)
            self.sections[key] = body
            grid.addWidget(frame, i // 2, i % 2)
        outer.addLayout(grid, 1)

        self.camera_table = QTableWidget(0, 4)
        self.camera_table.setHorizontalHeaderLabels(["Channel", "Camera", "Status", "Recording"])
        self.camera_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.camera_table.setMaximumHeight(190)
        outer.addWidget(self.camera_table)

        outer.addLayout(self._action_bar())
        self.refresh()

    # --- actions ------------------------------------------------------------
    def _action_bar(self):
        bar = QHBoxLayout()
        self._buttons = []
        specs = [
            ("Test Recorder", self.act_test_recorder, "secondary"),
            ("Rediscover Cameras", self.act_rediscover, "secondary"),
            ("Recheck Recording", self.act_recheck_recording, "secondary"),
            ("Recheck Archive", self.act_recheck_archive, "secondary"),
            ("Run Acceptance", self.act_acceptance, "secondary"),
            ("Restart Agent", self.act_restart, "secondary"),
            ("Check for Updates", self.act_check_update, "secondary"),
            ("Update WatchLog", self.act_update, ""),
            ("Support Bundle", self.act_support_bundle, "secondary"),
            ("Open Logs", self.act_open_logs, "ghost"),
        ]
        for text, handler, obj in specs:
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(handler)
            self._buttons.append(b)
            bar.addWidget(b)
        return bar

    def _set_busy(self, busy, message=""):
        self._busy = busy
        for b in self._buttons:
            b.setEnabled(not busy)
        if message:
            self.status_line.setText(message)

    def _run(self, fn, on_done, busy_message):
        if self._busy:
            return
        self._set_busy(True, busy_message)
        worker = Worker(lambda progress=None: fn())
        worker.signals.finished.connect(lambda result: self._done(on_done, result))
        worker.signals.failed.connect(lambda msg: self._done(lambda _r: self._notify("WatchLog", msg), None))
        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().start(worker)

    def _done(self, on_done, result):
        self._set_busy(False)
        if on_done:
            on_done(result)

    def _notify(self, title, message):
        QMessageBox.information(self, title, message)

    # --- data refresh -------------------------------------------------------
    def refresh(self):
        self._run(self.controller.snapshot, self._render, "Refreshing site status…")

    def _render(self, result):
        status = (result or {}).get("status")
        if not status:
            self.status_line.setText("WatchLog Agent did not return a status. Is it installed and running?")
            return
        self.status_line.setText("Site status updated.")
        a = status.get("agent", {})
        self.sections["agent"].setText(self._rich([
            ("Agent", a.get("running")), ("Version", a.get("version")),
            ("Build", (a.get("build_sha") or "—")), ("Channel", a.get("channel")),
            ("Enrollment", a.get("enrollment")), ("Cloud", a.get("cloud")),
            ("Spool backlog", a.get("spool_backlog")), ("Recovery backlog", a.get("recovery_backlog")),
        ]))
        r = status.get("recorder", {})
        self.sections["recorder"].setText(self._rich([
            ("Connection", r.get("connection")), ("Auth", r.get("authentication")),
            ("Vendor", r.get("vendor")), ("Model", r.get("model")), ("Driver", r.get("driver")),
            ("Archive", r.get("archive_capability")),
        ]))
        cam = status.get("cameras", {}).get("summary", {})
        self.sections["cameras"].setText(self._rich([
            ("Monitored", cam.get("headline")), ("Offline", cam.get("offline")),
            ("Degraded", cam.get("degraded")), ("Unused", cam.get("unused")),
        ]))
        self._render_cameras(status.get("cameras", {}).get("cameras", []))
        rec = status.get("recording", {}).get("summary", {})
        self.sections["recording"].setText(self._rich([
            ("Verified", rec.get("verified")), ("Warning", rec.get("warning")),
            ("Unknown", rec.get("unknown")),
        ]))
        ar = status.get("archive", {})
        self.sections["archive"].setText(self._rich([
            ("Archive access", ar.get("archive_access")), ("Recovery", ar.get("recovery")),
            ("Last proof", ar.get("last_archive_proof")), ("Backlog", ar.get("recovery_backlog")),
        ]))
        st = status.get("storage", {})
        self.sections["storage"].setText(self._rich([
            ("Health", st.get("health")), ("Total", st.get("total_gb")),
            ("Free", st.get("free_gb")), ("Retention (days)", st.get("retention_days")),
            ("Oldest", st.get("oldest_recording")),
        ]))

    def _rich(self, rows):
        parts = []
        for k, v in rows:
            colour = _COLOR.get(str(v), TEXT)
            parts.append(f"<div style='margin:2px 0'>{k}: <span style='color:{colour}'>"
                         f"{str(v).replace('_', ' ')}</span></div>")
        return "".join(parts)

    def _render_cameras(self, cams):
        self.camera_table.setRowCount(len(cams))
        for row, cam in enumerate(cams):
            for col, val in enumerate((cam.get("channel"), cam.get("name"), cam.get("status"),
                                       cam.get("recording_state") or "—")):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.camera_table.setItem(row, col, item)

    def act_test_recorder(self):
        self._run(self.controller.test_recorder,
                  lambda r: (self._notify("Recorder", "Recorder reachable." if r.get("ok")
                             else "Recorder could not be reached."), self.refresh()), "Testing recorder…")

    def act_rediscover(self):
        self._run(self.controller.rediscover_cameras, lambda r: self.refresh(), "Rediscovering cameras…")

    def act_recheck_recording(self):
        self._run(self.controller.recheck_recording, lambda r: self.refresh(), "Rechecking recording…")

    def act_recheck_archive(self):
        self._run(self.controller.recheck_archive, lambda r: self.refresh(), "Rechecking archive…")

    def act_acceptance(self):
        self._run(self.controller.run_acceptance,
                  lambda r: self._notify("WatchLog Acceptance", r.get("verdict", "")), "Running acceptance…")

    def act_restart(self):
        self._run(self.controller.restart_agent,
                  lambda r: (self._notify("WatchLog Agent", r.get("detail", "")), self.refresh()),
                  "Restarting WatchLog Agent…")

    def act_check_update(self):
        self._run(self.controller.check_update,
                  lambda r: self._notify("Software Update", r.get("headline", "")), "Checking for updates…")

    def act_update(self):
        def after_check(r):
            if r.get("action") != "update":
                self._notify("Software Update", r.get("headline", "WatchLog is up to date."))
                return
            reply = QMessageBox.question(self, "Update WatchLog",
                                        f"{r.get('headline')}\n\nUpdate now? WatchLog keeps running while it "
                                        f"downloads, and automatically rolls back if the new version fails.",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return
            self._run(self.controller.apply_update,
                      lambda res: (self._notify("Software Update", res.get("message", "")), self.refresh()),
                      "Updating WatchLog…")
        self._run(self.controller.check_update, after_check, "Checking for updates…")

    def act_support_bundle(self):
        dest, _ = QFileDialog.getSaveFileName(self, "Save Support Bundle", "watchlog-support.zip",
                                              "Zip Archive (*.zip)")
        if not dest:
            return
        self._run(lambda: self.controller.export_support_bundle(dest_path=dest),
                  lambda r: self._notify("Support Bundle", f"Saved to:\n{r['path']}" if r.get("ok")
                            else "Support bundle could not be created."), "Creating support bundle…")

    def act_open_logs(self):
        info = self.controller.open_logs()
        QDesktopServices.openUrl(QUrl.fromLocalFile(info["path"]))


def main(config_path: Path = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("WatchLog Site Status")
    app.setStyle("Fusion")
    win = SiteStatusWindow(config_path or Path(os.environ.get("PROGRAMDATA", ".")) / "WatchLog" / "watchlog.ini")
    win.show()
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

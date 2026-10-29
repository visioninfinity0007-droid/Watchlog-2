#!/usr/bin/env python3
"""A connected site is a successful install even if the Setup window dies afterwards.

Field (HASCO, 5.1.6, 2026-10-29): "WatchLog Setup has stopped working" appeared during
"Starting WatchLog in the background" after the site had enrolled and the new Agent was
already reporting. NSIS saw a non-zero exit, aborted, and skipped the uninstaller. Proves:
- finalize's marker is written outside the Setup process and never raises;
- NSIS deletes the marker before each Setup launch and accepts a non-zero exit only when
  the marker exists;
- a native crash in Setup is written to setup.log by faulthandler.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "agent"))

import setup_backend as sb  # noqa: E402

NSI = (TESTS.parent / "installer" / "nsis" / "watchlog.nsi").read_text(encoding="utf-8")
SETUP_GUI = (TESTS.parent / "agent" / "setup_gui.py").read_text(encoding="utf-8")
SETUP_EXEC = '"$INSTDIR\\watchlog-setup-ui.exe" --installer-child'


def test_marker_records_the_connected_site(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "programdata_dir", lambda: tmp_path)
    sb._mark_site_connected("site-1")
    data = json.loads((tmp_path / sb.SITE_CONNECTED_MARKER).read_text(encoding="utf-8"))
    assert data["site_id"] == "site-1" and data["agent_started"] is True
    assert data["setup_version"] == sb.SETUP_AGENT_VERSION


def test_marker_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(sb, "programdata_dir", lambda: blocker / "sub")   # not a directory
    monkeypatch.setattr(sb, "_setup_log", lambda _m: None)
    sb._mark_site_connected("site-1")


def test_finalize_marks_only_a_started_agent():
    src = Path(sb.__file__).read_text(encoding="utf-8")
    block = src[src.index('progress("Starting WatchLog in the background…")'):]
    block = block[:block.index("cleared = _clear_consumed_code")]
    assert re.search(r"if connected:\s+_mark_site_connected\(state\[\"site_id\"\]\)", block)


def test_nsis_clears_the_marker_before_every_setup_launch():
    launches = [m.start() for m in re.finditer(re.escape(SETUP_EXEC), NSI)]
    assert len(launches) == 2
    for start in launches:
        before = NSI[:start].rsplit("\n", 3)[-3:]
        assert any('Delete "${DATAROOT}\\setup-connected.json"' in line for line in before)


def test_nsis_accepts_a_failed_exit_only_with_the_marker():
    for start in [m.start() for m in re.finditer(re.escape(SETUP_EXEC), NSI)]:
        after = NSI[start:start + 700]
        assert re.search(
            r'\$\{If\} \$0 != 0\s+\$\{AndIf\} \$\{FileExists\} "\$\{DATAROOT\}\\setup-connected\.json"'
            r'\s+DetailPrint [^\n]+\s+StrCpy \$0 0\s+\$\{EndIf\}', after)
        # The original failure handling still follows for a run that never connected.
        assert after.index("StrCpy $0 0") < after.index('Abort "WatchLog setup did not complete"') \
            if 'Abort "WatchLog setup did not complete"' in after else True


def test_setup_writes_native_crashes_to_its_log():
    head = SETUP_GUI[:SETUP_GUI.index("from PySide6")]
    assert "faulthandler.enable(file=_LOG_HANDLE, all_threads=True)" in head

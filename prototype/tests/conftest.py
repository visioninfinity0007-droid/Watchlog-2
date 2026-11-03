"""pytest configuration for prototype/tests: no test may write to the real %PROGRAMDATA%.

Importing programdata_sandbox here points PROGRAMDATA at a throwaway directory before any test
module is collected (some modules resolve their data directory at import), and the autouse
fixture then gives every test its own empty one, so no state leaks between tests either.
test_programdata_isolation_guard.py keeps this in place.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (activates the sandbox at import)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "recorder_playback: keep the Hikvision RTSP playback step switched on")


@pytest.fixture(autouse=True)
def _no_hikvision_playback(request, monkeypatch):
    """Recorder RTSP playback (FFmpeg) never runs for real in tests. Hikvision clips try it
    before the ISAPI export, and tests built on fake ISAPI sessions model a recorder without that
    service, so the step answers 'no footage' unless a test opts in with
    @pytest.mark.recorder_playback."""
    playback = sys.modules.get("rtsp_playback")
    if playback is not None:
        # No test runs the real FFmpeg against a recorder: a test that models playback
        # provides its own fake (test_recorder_playback's ffmpeg fixture).
        monkeypatch.setattr(playback, "_ffmpeg", lambda: None)
    module = sys.modules.get("hikvision_archive")
    if module is None or request.node.get_closest_marker("recorder_playback"):
        return

    def _no_playback(*_args, **_kwargs):
        raise module.ClipNotReturned(detail="playback: not modelled by this test")

    monkeypatch.setattr(module, "_playback_clip", _no_playback)


@pytest.fixture(autouse=True)
def _isolated_programdata(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("programdata")
    monkeypatch.setenv("PROGRAMDATA", str(root))
    monkeypatch.setenv(programdata_sandbox._MARKER, str(root))
    yield root

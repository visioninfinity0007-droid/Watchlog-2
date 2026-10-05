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


@pytest.fixture(autouse=True)
def _isolated_programdata(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("programdata")
    monkeypatch.setenv("PROGRAMDATA", str(root))
    monkeypatch.setenv(programdata_sandbox._MARKER, str(root))
    yield root

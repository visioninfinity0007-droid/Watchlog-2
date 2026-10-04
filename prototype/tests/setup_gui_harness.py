"""Import prototype/agent/setup_gui.py inside a test process, safely.

setup_gui redirects stdout/stderr into %PROGRAMDATA%\\WatchLog\\setup.log at IMPORT time.
On a developer PC that is the real ProgramData of an installed WatchLog, so this helper
points PROGRAMDATA at a scratch directory for the import and restores the test runner's
streams afterwards. When PySide6 cannot be imported (headless Linux CI) a minimal stand-in
is installed: setup_gui only needs Qt class names and Signal() at import time, and these
tests never construct a window.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"


def _install_qt_stand_in() -> None:
    class _Anything:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return _Anything()

        def __call__(self, *args, **kwargs):
            return _Anything()

    def stand_in(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__getattr__ = lambda attr: type(attr, (_Anything,), {})  # PEP 562
        return module

    package = stand_in("PySide6")
    package.__path__ = []
    sys.modules["PySide6"] = package
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        module = stand_in(f"PySide6.{sub}")
        sys.modules[module.__name__] = module
        setattr(package, sub, module)
    sys.modules["PySide6.QtCore"].Signal = lambda *args, **kwargs: None


def import_setup_gui():
    if "setup_gui" in sys.modules:
        return sys.modules["setup_gui"]
    if str(AGENT) not in sys.path:
        sys.path.insert(0, str(AGENT))
    try:
        import PySide6.QtWidgets  # noqa: F401
    except Exception:  # noqa: BLE001 - ImportError or a missing native Qt library
        _install_qt_stand_in()

    saved_pd = os.environ.get("PROGRAMDATA")
    saved_streams = (sys.stdout, sys.stderr)
    os.environ["PROGRAMDATA"] = tempfile.mkdtemp(prefix="wl-setup-gui-import-")
    try:
        module = importlib.import_module("setup_gui")
    finally:
        sys.stdout, sys.stderr = saved_streams
        if saved_pd is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = saved_pd
    handle = getattr(module, "_LOG_HANDLE", None)
    if handle is not None:
        handle.close()
        module._LOG_HANDLE = None
    return module

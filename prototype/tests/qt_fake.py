"""A small functional stand-in for the parts of PySide6 that setup_gui.py uses.

PySide6 is not installed on the Linux CI runner or on every developer PC, while the
packaged Setup UI's own --ui-selftest runs only in the Windows build job. This fake keeps
real state (text, visibility, enabled, list and table contents, the stacked page, worker
threads, queued signals and single-shot timers) so the SetupWindow flow, including
_run_ui_selftest, can execute without Qt. It is not a renderer: geometry, styling and
painting are no-ops.

Usage: ``module = load_setup_gui_with_fake_qt()`` returns a private copy of setup_gui
bound to this fake; sys.modules is left as it was found.
"""
from __future__ import annotations

import importlib.util
import os
import queue
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
_MAIN = threading.main_thread()
_EVENTS: "queue.Queue" = queue.Queue()
_TIMERS: list = []
_TIMER_LOCK = threading.Lock()


def _process_events() -> None:
    while True:
        try:
            fn, args = _EVENTS.get_nowait()
        except queue.Empty:
            break
        fn(*args)
    now = time.monotonic()
    with _TIMER_LOCK:
        due = [t for t in _TIMERS if t[0] <= now]
        for t in due:
            _TIMERS.remove(t)
    for _deadline, fn in sorted(due, key=lambda t: t[0]):
        fn()


class _BoundSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            if threading.current_thread() is _MAIN:
                slot(*args)
            else:                                  # queued connection, like Qt across threads
                _EVENTS.put((slot, args))


class Signal:
    """Descriptor: each instance gets its own bound signal."""

    def __init__(self, *_types):
        self._name = None

    def __set_name__(self, owner, name):
        self._name = "_signal_" + name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = obj.__dict__[self._name] = _BoundSignal()
        return bound


class QObject:
    def __init__(self, *args, **kwargs):
        pass


class QRunnable:
    def __init__(self, *args, **kwargs):
        pass


class QThreadPool:
    _instance = None

    @classmethod
    def globalInstance(cls):
        cls._instance = cls._instance or cls()
        return cls._instance

    def start(self, runnable):
        threading.Thread(target=runnable.run, daemon=True).start()


class QTimer:
    @staticmethod
    def singleShot(ms, fn):
        with _TIMER_LOCK:
            _TIMERS.append((time.monotonic() + int(ms) / 1000.0, fn))


class Qt:
    UserRole = 256
    ItemIsEditable = 2


class QIcon:
    def __init__(self, *args):
        pass


class QCloseEvent:
    def accept(self):
        pass


class QHeaderView:
    ResizeToContents = 3
    Stretch = 1

    def setSectionResizeMode(self, *args):
        pass


class _Widget:
    """State-keeping base widget. Unknown styling/geometry calls are no-ops."""

    def __init__(self, *args, **kwargs):
        self._visible_flag = True
        self._shown = False
        self._enabled = True
        self._text = args[0] if args and isinstance(args[0], str) else ""
        self._parent = None
        self.clicked = _BoundSignal()

    _NO_OPS = frozenset({"resize", "raise_", "activateWindow", "repaint", "update"})

    def __getattr__(self, name):
        if name.startswith(("set", "add")) or name in self._NO_OPS:
            return lambda *a, **k: None
        raise AttributeError(name)

    # text
    def setText(self, text):
        self._text = str(text)

    def text(self):
        return self._text

    def clear(self):
        self._text = ""

    # visibility / enabled
    def setVisible(self, value):
        self._visible_flag = bool(value)

    def show(self):
        self._visible_flag = True
        self._shown = True

    def hide(self):
        self._visible_flag = False

    def isHidden(self):
        return not self._visible_flag

    def isVisible(self):
        return self._visible_flag and self._shown

    def setEnabled(self, value):
        self._enabled = bool(value)

    def isEnabled(self):
        return self._enabled

    def click(self):
        self.clicked.emit()

    def close(self):
        self._shown = False
        app = QApplication.instance()
        if app is not None and self in app._top:
            app._top.remove(self)
        return True


class QWidget(_Widget):
    pass


class QFrame(_Widget):
    pass


class QLabel(_Widget):
    pass


class QLineEdit(_Widget):
    Password = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.textChanged = _BoundSignal()

    def setText(self, text):
        changed = str(text) != self._text
        super().setText(text)
        if changed:
            self.textChanged.emit(self._text)

    def clear(self):
        self.setText("")


class QPushButton(_Widget):
    pass


class QProgressBar(_Widget):
    def setRange(self, low, high):
        self._range = (low, high)

    def setValue(self, value):
        self._value = value


class _Layout:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class QVBoxLayout(_Layout):
    pass


class QHBoxLayout(_Layout):
    pass


class QStackedWidget(_Widget):
    def __init__(self, *args):
        super().__init__()
        self._pages = []
        self._index = -1

    def addWidget(self, page):
        self._pages.append(page)

    def setCurrentIndex(self, index):
        self._index = int(index)

    def currentIndex(self):
        return self._index


class QListWidgetItem:
    def __init__(self, text=""):
        self._text = text
        self._data = {}
        self._selected = False

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)

    def setSelected(self, value):
        self._selected = bool(value)

    def isSelected(self):
        return self._selected

    def text(self):
        return self._text


class QListWidget(_Widget):
    def __init__(self, *args):
        super().__init__()
        self._items = []
        self._current = -1
        self.itemSelectionChanged = _BoundSignal()
        self.itemClicked = _BoundSignal()

    def clear(self):
        self._items, self._current = [], -1

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def setCurrentRow(self, index):
        self._current = index

    def currentItem(self):
        return self._items[self._current] if 0 <= self._current < len(self._items) else None

    def selectedItems(self):
        return [item for item in self._items if item.isSelected()]


class QTableWidgetItem:
    def __init__(self, text=""):
        self._text = str(text)
        self._flags = 3
        self._data = {}

    def text(self):
        return self._text

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)


class QTableWidget(_Widget):
    SelectRows = 1
    SingleSelection = 1

    def __init__(self, rows=0, cols=0, *args):
        super().__init__()
        self._rows, self._cols, self._cells = rows, cols, {}
        self._header = QHeaderView()
        self.itemSelectionChanged = _BoundSignal()

    def setRowCount(self, rows):
        self._rows = rows
        self._cells = {k: v for k, v in self._cells.items() if k[0] < rows}

    def rowCount(self):
        return self._rows

    def setItem(self, row, col, item):
        self._cells[(row, col)] = item

    def item(self, row, col):
        return self._cells.get((row, col))

    def horizontalHeader(self):
        return self._header


class QMainWindow(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        app = QApplication.instance()
        if app is not None:
            app._top.append(self)

    def close(self):
        event = QCloseEvent()
        handler = getattr(self, "closeEvent", None)
        if handler is not None:
            handler(event)
        return super().close()


class QDialog(QMainWindow):
    Accepted = 1


class QMessageBox:
    Yes, No = 16384, 65536
    messages: list = []

    @classmethod
    def warning(cls, _parent, title, text):
        cls.messages.append(("warning", title, text))

    @classmethod
    def information(cls, _parent, title, text):
        cls.messages.append(("information", title, text))

    @classmethod
    def question(cls, *_args, **_kwargs):
        return cls.No


class QApplication:
    _app = None

    def __init__(self, *args):
        QApplication._app = self
        self._top = []
        self.exit_code = None

    @classmethod
    def instance(cls):
        return cls._app

    def processEvents(self):
        _process_events()

    def topLevelWidgets(self):
        return list(self._top)

    def exit(self, code=0):
        self.exit_code = code

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Unused:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _modules() -> dict:
    core = types.ModuleType("PySide6.QtCore")
    for obj in (QObject, QRunnable, Qt, QThreadPool, QTimer, Signal):
        setattr(core, obj.__name__, obj)
    gui = types.ModuleType("PySide6.QtGui")
    gui.QCloseEvent, gui.QIcon = QCloseEvent, QIcon
    widgets = types.ModuleType("PySide6.QtWidgets")
    for obj in (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
                QHeaderView):
        setattr(widgets, obj.__name__, obj)
    for name in ("QComboBox", "QFileDialog", "QInputDialog"):
        setattr(widgets, name, type(name, (_Unused,), {}))
    package = types.ModuleType("PySide6")
    package.__path__ = []
    package.QtCore, package.QtGui, package.QtWidgets = core, gui, widgets
    return {"PySide6": package, "PySide6.QtCore": core, "PySide6.QtGui": gui,
            "PySide6.QtWidgets": widgets}


def load_setup_gui_with_fake_qt():
    """Import a private copy of setup_gui.py bound to this fake Qt."""
    if str(AGENT) not in sys.path:
        sys.path.insert(0, str(AGENT))
    names = ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets")
    saved_modules = {name: sys.modules.get(name) for name in names}
    saved_pd = os.environ.get("PROGRAMDATA")
    saved_streams = (sys.stdout, sys.stderr)
    sys.modules.update(_modules())
    os.environ["PROGRAMDATA"] = tempfile.mkdtemp(prefix="wl-setup-gui-fake-qt-")
    try:
        spec = importlib.util.spec_from_file_location("setup_gui_fake_qt", AGENT / "setup_gui.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.stdout, sys.stderr = saved_streams
        if saved_pd is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = saved_pd
        for name, mod in saved_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
    handle = getattr(module, "_LOG_HANDLE", None)
    if handle is not None:
        handle.close()
        module._LOG_HANDLE = None
    return module

"""Shared fakes for running native_event_collector.collector without a recorder.

Not a test module itself. Imported by the collector tests, which run it on a thread with
core.open_driver, core.vision.build and the credential generation patched out.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import native_event_collector  # noqa: E402
import watchlog_agent as core  # noqa: E402


class Cfg:
    snapshots = True
    snapshot_min_interval = 0

    def load_recorder_credential(self):
        pass


class Spool:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def trim(self):
        return 0


class Info:
    vendor = "TestVendor"
    model = "T-1"
    firmware = "1.0"


def run_collector(monkeypatch, open_driver, *, holder=None, until=None, timeout=10.0,
                  reconnect_wait=None, cfg=None):
    """Run the packaged collector on a thread until ``until(holder, spool)`` is true (or
    the timeout passes), then stop it. Returns (spool, holder, finished_in_time)."""
    monkeypatch.setattr(core, "open_driver", open_driver)
    monkeypatch.setattr(core.vision, "build", lambda _cfg, _log: None)
    monkeypatch.setattr(core.credential_store, "credential_generation", lambda: "gen-1")
    if reconnect_wait is not None:
        monkeypatch.setattr(core, "_reconnect_wait", reconnect_wait)
    holder = {} if holder is None else holder
    spool = Spool()
    stop = threading.Event()
    worker = threading.Thread(target=native_event_collector.collector,
                              args=(cfg or Cfg(), spool, stop, holder), daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    ok = False
    while time.monotonic() < deadline:
        if until is not None and until(holder, spool):
            ok = True
            break
        if not worker.is_alive():
            break
        time.sleep(0.02)
    if until is None:
        ok = not worker.is_alive()
    elif not ok:
        ok = bool(until(holder, spool))    # the collector may have stopped itself first
    stop.set()
    worker.join(timeout=10)
    assert not worker.is_alive(), "collector did not stop"
    return spool, holder, ok


def stop_after_first_wait(stop_calls):
    """A _reconnect_wait stand-in that records its arguments and ends the collector."""
    def wait(stop, cfg, auth_failures, last_gen, *args, **kwargs):
        stop_calls.append((auth_failures, args, kwargs))
        stop.set()
        return "stop", last_gen
    return wait

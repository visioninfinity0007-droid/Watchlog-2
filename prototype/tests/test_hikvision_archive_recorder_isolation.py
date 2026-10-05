#!/usr/bin/env python3
"""Hikvision archive work is serialized per recorder, not per process (MNVR-025).

One process-wide HIKVISION_HTTP_LOCK serialized archive search and download across every
Hikvision recorder on a multi-recorder site: a slow export on recorder A held recorder B's
incident clip, recovery and recording proof for minutes. The lock now belongs to one
recorder: two recorders never wait for each other, two transports to the same recorder
still take turns, and a clip's time budget starts only once its recorder's lock is held.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
from drivers.base import DriverError  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

T0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=30)
CLIP = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
EMPTY_SEARCH = (b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">'
                b"<responseStatusStrg>NO MATCHES</responseStatusStrg><matchList/>"
                b"</CMSearchResult>")


class Response:
    def __init__(self, status=200, content=b"", gate=None):
        self.status_code = status
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self._gate = gate

    def iter_content(self, chunk_size=1):
        if self._gate is not None:
            self._gate()
        yield CLIP

    def close(self):
        pass


class Session:
    def __init__(self, gate=None):
        self.auth = None
        self.calls = []
        self._gate = gate

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        self.calls.append(("POST", url))
        return Response(200, EMPTY_SEARCH)

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        self.calls.append((method, url))
        return Response(200, gate=self._gate)

    def close(self):
        pass


def recorder(host: str, gate=None) -> HikvisionDriver:
    driver = HikvisionDriver(f"http://{host}", "admin", "secret", timeout=2)
    driver.s = Session(gate)
    return driver


@pytest.fixture(autouse=True)
def no_probe(monkeypatch):
    monkeypatch.setattr(ha, "_probe_clip", lambda data: None, raising=False)


def test_a_slow_export_on_one_recorder_does_not_hold_another_recorders_clip(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_LOCK_WAIT_SECONDS", 1)
    exporting, release = threading.Event(), threading.Event()

    def slow():
        exporting.set()
        release.wait(5)

    slow_a = recorder("192.0.2.64", gate=slow)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault(
        "a", ha.get_clip(slow_a, "1", T0, T1)))
    worker.start()
    assert exporting.wait(3), "recorder A's export started"
    try:
        started = time.monotonic()
        assert ha.get_clip(recorder("192.0.2.65"), "1", T0, T1) == CLIP
        assert time.monotonic() - started < 1.0, "recorder B waited for recorder A"
    finally:
        release.set()
        worker.join(5)
    assert result["a"] == CLIP


def test_two_transports_to_one_recorder_still_take_turns(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_LOCK_WAIT_SECONDS", 0.3)
    exporting, release = threading.Event(), threading.Event()

    def slow():
        exporting.set()
        release.wait(5)

    worker = threading.Thread(target=lambda: ha.get_clip(
        recorder("192.0.2.64", gate=slow), "1", T0, T1))
    worker.start()
    assert exporting.wait(3)
    other = recorder("192.0.2.64")              # e.g. recovery's own archive driver
    try:
        with pytest.raises(DriverError) as caught:
            ha.get_clip(other, "1", T0, T1)
    finally:
        release.set()
        worker.join(5)
    assert getattr(caught.value, "unsupported", False) is False
    assert other.s.calls == [], "nothing is sent to a recorder whose archive is busy"


def test_each_recorder_has_its_own_lock_and_one_transport_reuses_it():
    a1, a2, b = recorder("192.0.2.64"), recorder("192.0.2.64:80"), recorder("192.0.2.65")
    assert ha._http_lock(a1) is ha._http_lock(a2)
    assert ha._http_lock(a1) is not ha._http_lock(b)
    assert not hasattr(ha, "HIKVISION_HTTP_LOCK"), "no process-wide archive lock remains"


def test_the_budget_starts_once_this_recorders_lock_is_held(monkeypatch):
    monkeypatch.setattr(ha, "CLIP_TOTAL_SECONDS", 1)
    driver = recorder("192.0.2.66")
    held = threading.Event()

    def holder():
        with ha._http_lock(driver):          # e.g. recovery mid-download on this recorder
            held.set()
            time.sleep(1.5)

    worker = threading.Thread(target=holder)
    worker.start()
    held.wait(2)
    try:
        assert ha.get_clip(driver, "1", T0, T1) == CLIP
    finally:
        worker.join()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

#!/usr/bin/env python3
"""Incident-footage worker outcomes are truthful (MNVR-030 and MNVR-057, worker side).

footage_worker turned every falsy get_clip result into a terminal 'unsupported' with the text
"This recorder does not expose on-demand incident footage...", including an empty archive search
and an ONVIF fallback after the vendor-native attempt failed. Non-driver errors were formatted
with their raw text, which carried "HTTPConnectionPool(host='192.168.x.x', ...)" into the
tenant-visible error. These contracts drive the real worker with a fake cloud and driver.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import hikvision_archive as ha  # noqa: E402
import incident_evidence as ie  # noqa: E402
from drivers.base import DeviceInfo, DriverError, NvrDriver  # noqa: E402
from drivers.hikvision import HikvisionDriver  # noqa: E402

CFG = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                      nvr_url="http://nvr-office.lan:8080")
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
ROW = {"request_id": "11111111-2222-3333-4444-555555555555", "channel": "1",
       "start_at": "2026-09-25T08:00:00Z", "end_at": "2026-09-25T08:00:30Z"}
CLIP = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class FakeCloud:
    def __init__(self, stop):
        self.calls = []
        self._stop = stop
        self._rows = [dict(ROW)]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_agent_claim_clip_requests":
            if self._rows:
                return [self._rows.pop(0)]
            self._stop.set()
            return []
        return {"ok": True}

    def named(self, name):
        return [kw for call, kw in self.calls if call == name]


def run_worker(monkeypatch, driver, vendor="Hikvision"):
    stop = threading.Event()
    cloud = FakeCloud(stop)
    logs = []
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_archive_driver",
                        lambda cfg: (driver, DeviceInfo(vendor=vendor, model="X")))
    monkeypatch.setattr(ie.core, "log", logs.append)
    ie.footage_worker(CFG, STATE, stop)
    return cloud, logs


def only_failure(cloud):
    failures = cloud.named("wl_agent_fail_clip")
    assert len(failures) == 1, cloud.calls
    return failures[0]


def assert_tenant_safe(reason: str):
    low = reason.lower()
    for leak in ("192.168", "host=", "://", "connectionpool", "nvr-office"):
        assert leak not in low, reason


class ClipDriver(NvrDriver):
    name = "dahua-cgi"

    def __init__(self, outcome):
        super().__init__("http://192.168.1.108", "admin", "secret")
        self._outcome = outcome

    def get_clip(self, channel, start, end):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


class OnvifOnly(NvrDriver):
    name = "onvif"          # no recorded-media implementation on this transport


def test_empty_archive_result_is_a_failure_not_unsupported(monkeypatch):
    cloud, _ = run_worker(monkeypatch, ClipDriver(None), vendor="Dahua")
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is False
    assert "does not expose" not in failure["p_reason"]


def test_native_attempt_that_fell_back_to_onvif_is_not_unsupported(monkeypatch):
    # open_archive_driver keeps ONVIF for a Hikvision/Dahua recorder only when the
    # vendor-native probe failed (timeout, refused login): unknown, not a capability verdict.
    for vendor in ("Hikvision", "Dahua Technology"):
        cloud, _ = run_worker(monkeypatch, OnvifOnly("http://192.168.1.64"), vendor=vendor)
        failure = only_failure(cloud)
        assert failure["p_unsupported"] is False, vendor
        assert_tenant_safe(failure["p_reason"])


def test_transport_without_any_footage_path_stays_unsupported(monkeypatch):
    cloud, _ = run_worker(monkeypatch, OnvifOnly("http://192.168.1.64"), vendor="Acme ONVIF")
    assert only_failure(cloud)["p_unsupported"] is True


def test_driver_affirmative_rejection_is_unsupported(monkeypatch):
    class Rejected(DriverError):
        unsupported = True
    cloud, _ = run_worker(monkeypatch, ClipDriver(Rejected("This recorder rejected it.")))
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is True
    assert failure["p_reason"] == "This recorder rejected it."


def test_raw_network_error_text_is_redacted(monkeypatch):
    pool = urllib3.HTTPConnectionPool("192.168.1.64", 80)
    error = requests.exceptions.ConnectionError(
        urllib3.exceptions.ReadTimeoutError(pool, None, "Read timed out."))
    cloud, _ = run_worker(monkeypatch, ClipDriver(error))
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is False
    assert_tenant_safe(failure["p_reason"])


def test_configured_recorder_hostname_is_redacted(monkeypatch):
    cloud, _ = run_worker(monkeypatch, ClipDriver(DriverError("nvr-office.lan:8080 refused")))
    assert_tenant_safe(only_failure(cloud)["p_reason"])


def test_error_without_text_does_not_kill_the_worker(monkeypatch):
    cloud, _ = run_worker(monkeypatch, ClipDriver(DriverError()))
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is False and failure["p_reason"]
    cloud, _ = run_worker(monkeypatch, ClipDriver(TimeoutError()))
    assert only_failure(cloud)["p_reason"]


# --- end to end through the real Hikvision archive code --------------------------------------

def search_xml() -> bytes:
    uri = ("rtsp://192.168.1.64/Streaming/tracks/101/"
           "?starttime=20260925T080000Z&endtime=20260925T080100Z&name=ch01")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<CMSearchResult xmlns="http://www.isapi.org/ver20/XMLSchema">'
            "<responseStatusStrg>OK</responseStatusStrg><matchList><searchMatchItem>"
            "<trackID>101</trackID><timeSpan><startTime>2026-09-25T08:00:00Z</startTime>"
            "<endTime>2026-09-25T08:01:00Z</endTime></timeSpan><mediaSegmentDescriptor>"
            f"<playbackURI>{escape(uri)}</playbackURI></mediaSegmentDescriptor>"
            "</searchMatchItem></matchList></CMSearchResult>").encode()


class Response:
    def __init__(self, status=200, chunks=(), content=b""):
        self.status_code = status
        self.headers = {}
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self._chunks = list(chunks) or [content]

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def close(self):
        pass


class Session:
    def __init__(self, download):
        self.auth = None
        self._download = download

    def post(self, url, data=None, headers=None, stream=False, timeout=None):
        return Response(200, content=search_xml())

    def request(self, method, url, data=None, headers=None, stream=False, timeout=None):
        return self._download(method, data.decode())

    def close(self):
        pass


def hikvision(download) -> HikvisionDriver:
    ha.install()
    d = HikvisionDriver("http://192.168.1.64", "admin", "secret", timeout=2)
    d.s = Session(download)
    return d


@pytest.fixture
def private_lock(monkeypatch):
    monkeypatch.setattr(ha, "HIKVISION_HTTP_LOCK", threading.RLock())
    monkeypatch.setattr(ha, "_probe_clip", lambda data: None, raising=False)


def test_oversize_hikvision_export_is_failed_not_unsupported(monkeypatch, private_lock):
    mib = b"\x00" * (1024 * 1024)
    cloud, _ = run_worker(monkeypatch, hikvision(lambda m, b: Response(chunks=[mib] * 33)))
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is False
    assert "32" in failure["p_reason"]
    assert_tenant_safe(failure["p_reason"])


def test_hikvision_playback_permission_refusal_is_failed_not_unsupported(monkeypatch, private_lock):
    cloud, _ = run_worker(monkeypatch, hikvision(lambda m, b: Response(403, content=b"no")))
    failure = only_failure(cloud)
    assert failure["p_unsupported"] is False
    assert "login" in failure["p_reason"].lower()
    assert_tenant_safe(failure["p_reason"])


def test_hikvision_lock_contention_does_not_end_unsupported(monkeypatch, private_lock):
    monkeypatch.setattr(ha, "CLIP_TOTAL_SECONDS", 1)
    held = threading.Event()

    def holder():
        with ha.HIKVISION_HTTP_LOCK:
            held.set()
            threading.Event().wait(1.5)

    worker = threading.Thread(target=holder)
    worker.start()
    held.wait(2)
    try:
        cloud, _ = run_worker(monkeypatch, hikvision(lambda m, b: Response(chunks=[CLIP])))
    finally:
        worker.join()
    assert cloud.named("wl_agent_fail_clip") == []
    complete = cloud.named("wl_agent_complete_clip")
    assert len(complete) == 1 and complete[0]["p_total_bytes"] == len(CLIP)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

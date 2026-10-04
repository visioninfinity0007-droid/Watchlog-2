#!/usr/bin/env python3
"""Incident stills: real images are uploaded, and a missing image is not 'unsupported'.

stills_worker marked every still that came back empty as p_unsupported=True. In 0058 that status
is terminal and skips the retry budget. ONVIF sites (MNVR-001) hit it on every request, because
the ONVIF driver opened by open_driver() could not resolve the channel to a profile, and Hikvision
turns a timeout into an empty still (MNVR-030). An empty answer from a driver that implements
stills is now a retryable failure. A driver that returns image bytes, such as the ONVIF driver
once it resolves channels, gets them uploaded.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest
import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import incident_evidence as ie  # noqa: E402
from drivers.base import DeviceInfo, DriverError, NvrDriver  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402

CFG = SimpleNamespace(supabase_url="https://cloud.invalid", publishable_key="pk",
                      nvr_url="http://192.168.1.64")
STATE = {"agent_id": "agent-1", "agent_key": "key-1"}
ROW = {"request_id": "99999999-2222-3333-4444-555555555555", "channel": "4"}
JPEG = b"\xff\xd8\xff\xe0" + b"\x10" * 512


class FakeCloud:
    def __init__(self, stop):
        self.calls = []
        self._stop = stop
        self._rows = [dict(ROW)]

    def call(self, name, **kw):
        self.calls.append((name, kw))
        if name == "wl_agent_claim_incident_stills":
            if self._rows:
                return [self._rows.pop(0)]
            self._stop.set()
            return []
        return {"ok": True}

    def named(self, name):
        return [kw for call, kw in self.calls if call == name]


def run_worker(monkeypatch, driver, vendor="ONVIF device"):
    stop = threading.Event()
    cloud = FakeCloud(stop)
    monkeypatch.setattr(ie.core, "Cloud", lambda url, key: cloud)
    monkeypatch.setattr(ie.core, "open_driver",
                        lambda cfg: (driver, DeviceInfo(vendor=vendor, model="X")))
    monkeypatch.setattr(ie.core, "log", lambda msg: None)
    ie.stills_worker(CFG, STATE, stop)
    return cloud


class NoNetwork:
    """A session for the real ONVIF driver: every request fails fast, nothing leaves the PC."""

    def post(self, url, **kw):
        raise requests.exceptions.ConnectTimeout(f"HTTPConnectionPool(host='192.168.1.64'): {url}")

    get = post

    def close(self):
        pass


def test_onvif_driver_without_a_resolved_channel_fails_retryably(monkeypatch):
    driver = OnvifDriver("http://192.168.1.64", "admin", "secret", timeout=1)
    driver.s = NoNetwork()
    cloud = run_worker(monkeypatch, driver)
    failures = cloud.named("wl_agent_fail_incident_still")
    assert len(failures) == 1
    assert failures[0]["p_unsupported"] is False
    assert "192.168" not in failures[0]["p_reason"]
    assert cloud.named("wl_agent_upload_incident_still") == []


def test_onvif_driver_that_returns_an_image_is_uploaded(monkeypatch):
    class ResolvedOnvif(NvrDriver):
        name = "onvif"

        def get_snapshot(self, channel):
            assert channel == "4"
            return JPEG

    cloud = run_worker(monkeypatch, ResolvedOnvif("http://192.168.1.64"))
    assert cloud.named("wl_agent_fail_incident_still") == []
    upload = cloud.named("wl_agent_upload_incident_still")
    assert len(upload) == 1
    assert upload[0]["p_content_type"] == "image/jpeg"
    assert base64.b64decode(upload[0]["p_image_b64"]) == JPEG
    assert upload[0]["p_sha256"] == hashlib.sha256(JPEG).hexdigest()


def test_empty_still_from_a_still_capable_driver_is_retryable(monkeypatch):
    class Empty(NvrDriver):
        name = "hikvision-isapi"

        def get_snapshot(self, channel):
            return None             # e.g. a timeout swallowed by the driver

    cloud = run_worker(monkeypatch, Empty("http://192.168.1.64"), vendor="Hikvision")
    assert cloud.named("wl_agent_fail_incident_still")[0]["p_unsupported"] is False


def test_transport_without_a_still_path_stays_unsupported(monkeypatch):
    class NoStills(NvrDriver):
        name = "mock"

    cloud = run_worker(monkeypatch, NoStills("http://192.168.1.64"))
    assert cloud.named("wl_agent_fail_incident_still")[0]["p_unsupported"] is True


def test_still_errors_are_redacted_and_retryable(monkeypatch):
    pool = urllib3.HTTPConnectionPool("192.168.1.64", 80)

    class Broken(NvrDriver):
        name = "hikvision-isapi"

        def get_snapshot(self, channel):
            raise requests.exceptions.ConnectionError(
                urllib3.exceptions.ReadTimeoutError(pool, None, "Read timed out."))

    cloud = run_worker(monkeypatch, Broken("http://192.168.1.64"))
    failure = cloud.named("wl_agent_fail_incident_still")[0]
    assert failure["p_unsupported"] is False
    assert "192.168" not in failure["p_reason"] and "host=" not in failure["p_reason"]


def test_still_driver_error_is_retryable(monkeypatch):
    class Refused(NvrDriver):
        name = "hikvision-isapi"

        def get_snapshot(self, channel):
            raise DriverError("snapshot endpoint answered HTTP 500")

    cloud = run_worker(monkeypatch, Refused("http://192.168.1.64"))
    assert cloud.named("wl_agent_fail_incident_still")[0]["p_unsupported"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

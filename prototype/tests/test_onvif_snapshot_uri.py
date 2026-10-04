#!/usr/bin/env python3
"""ONVIF URIs are rehosted onto the reachable recorder address with their
query intact.

Devices advertise service, snapshot and subscription URIs on their own idea
of their address, so the driver swaps in the host it can reach. It used to
keep only the path: a snapshot URI that names its profile or channel in the
query then became the same URL for every camera.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import onvif_fake_recorder as fx  # noqa: E402
from drivers.onvif_driver import OnvifDriver  # noqa: E402


def test_rehost_keeps_path_and_query_and_swaps_host():
    d = OnvifDriver("http://192.0.2.10:8080", "local-user", "local-password", timeout=1)
    assert d._rehost("http://0.0.0.0/onvif/snapshot?channel=3&subtype=0") == \
        "http://192.0.2.10:8080/onvif/snapshot?channel=3&subtype=0"
    assert d._rehost("http://10.0.0.99:80/onvif/Subscription?Idx=7") == \
        "http://192.0.2.10:8080/onvif/Subscription?Idx=7"
    assert d._rehost("/onvif/media_service") == "http://192.0.2.10:8080/onvif/media_service"
    d.close()


def test_each_camera_still_comes_from_its_own_snapshot_uri(monkeypatch):
    rec = fx.FakeRecorder()
    rec.install(monkeypatch)
    d = OnvifDriver("http://192.0.2.10", "local-user", "local-password", timeout=1)
    d.list_channels()
    two, five = d.get_snapshot("2"), d.get_snapshot("5")
    d.close()

    assert rec.snapshot_urls == [
        f"http://192.0.2.10/onvif/snapshot?profile={fx.profile_token(2, 'main')}",
        f"http://192.0.2.10/onvif/snapshot?profile={fx.profile_token(5, 'main')}",
    ]
    assert two == fx.JPEG + fx.profile_token(2, "main").encode()
    assert five == fx.JPEG + fx.profile_token(5, "main").encode()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

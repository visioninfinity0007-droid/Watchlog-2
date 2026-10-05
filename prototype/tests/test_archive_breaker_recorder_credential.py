#!/usr/bin/env python3
"""A recorder's own credential change in Setup clears its vendor-native archive breaker at once.

5.0.28 (MNVR-036) stops open_archive_driver from re-probing a vendor CGI/ISAPI that rejected the
on-site login on every open: confirmed auth failures back off per recorder 5 -> 15 -> 30 min, and
a credential change in Setup clears the breaker at once. The breaker read the change from the
single legacy credential (credential_store.credential_generation). In the multi-recorder Agent
each recorder's login is its own DPAPI blob (recorder_credential_generation(local_id)), so fixing
recorder B's password in Setup did not clear B's breaker: recorded media stayed on the ONVIF
fallback for up to 30 minutes after the operator had fixed it. Hermetic: no recorder, no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))

import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, NvrAuthFailed  # noqa: E402

LOCAL_A = "0a000000-0000-4000-8000-00000000000a"
LOCAL_B = "0b000000-0000-4000-8000-00000000000b"


class _Onvif:
    name = "onvif"

    def close(self):
        pass


class _RefusingNative:
    name = "dahua-cgi"

    def __init__(self, probes, url):
        self._probes, self._url = probes, url

    def probe(self):
        self._probes.append(self._url)
        raise NvrAuthFailed(f"{self._url}/cgi-bin/magicBox.cgi: HTTP 401 — "
                            "recorder rejected the username or password")

    def close(self):
        pass


def _recorder_cfg(local_id, url):
    return SimpleNamespace(recorder_local_id=local_id, nvr_url=url,
                           nvr_username="local-user", nvr_password="local-password")


class RecorderCredentialClearsTheBreaker(unittest.TestCase):
    def setUp(self):
        core._NATIVE_ARCHIVE_AUTH.clear()
        self.addCleanup(core._NATIVE_ARCHIVE_AUTH.clear)
        self.probes = []
        self.generations = {LOCAL_A: "a-1", LOCAL_B: "b-1"}
        info = DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="onvif")
        patches = [
            mock.patch.object(core, "open_driver", lambda _cfg: (_Onvif(), info)),
            mock.patch.object(core, "build", lambda _name, url, *_a, **_k:
                              _RefusingNative(self.probes, url)),
            mock.patch.object(core, "log", lambda *_a, **_k: None),
            mock.patch.object(core.time, "monotonic", lambda: 1000.0),
            # The legacy singleton credential never changes in these tests.
            mock.patch.object(core.credential_store, "credential_generation", lambda: "legacy-1"),
            mock.patch.object(core.credential_store, "recorder_credential_generation",
                              lambda local_id: self.generations[local_id]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.a = _recorder_cfg(LOCAL_A, "http://192.0.2.10")
        self.b = _recorder_cfg(LOCAL_B, "http://192.0.2.11")

    def _open(self, cfg):
        driver, _info = core.open_archive_driver(cfg)
        self.assertEqual(driver.name, "onvif")     # the live ONVIF path is always kept

    def test_fixing_a_recorders_login_in_setup_retries_its_archive_now(self):
        self._open(self.b)
        self._open(self.b)
        self.assertEqual(self.probes, ["http://192.0.2.11"], "a rejected login must back off")
        self.generations[LOCAL_B] = "b-2"           # Setup rewrote recorder B's credential
        self._open(self.b)
        self.assertEqual(self.probes, ["http://192.0.2.11"] * 2)

    def test_another_recorders_credential_change_keeps_the_breaker(self):
        self._open(self.b)
        self.generations[LOCAL_A] = "a-2"           # only recorder A's login changed
        self._open(self.b)
        self.assertEqual(self.probes, ["http://192.0.2.11"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

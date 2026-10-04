#!/usr/bin/env python3
"""MNVR-036: a vendor-native archive login that was rejected is not retried on every open.

open_archive_driver runs every recovery cycle and for every footage request or archive-scan
camera. On an ONVIF site it probes the vendor CGI/ISAPI with the same credential, and the Dahua
and Hikvision drivers retry a 401 with Basic, so one rejected credential is two failed logins
per open (about 24 an hour from recovery alone). Confirmed auth failures must back off per
recorder like the live collector (5 -> 15 -> 30 min) and a credential change in Setup must clear
the breaker at once. Hermetic: no recorder, no network.
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
from drivers.base import DeviceInfo, NvrAuthFailed, NvrUnreachable  # noqa: E402


class _Onvif:
    name = "onvif"

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _RefusingNative:
    """A dahua-cgi reader whose probe always fails with ``error``; counts every login attempt."""

    name = "dahua-cgi"

    def __init__(self, counter, error):
        self._counter = counter
        self._error = error

    def probe(self):
        self._counter.append(1)
        raise self._error

    def close(self):
        pass


def _cfg(url="http://192.0.2.10"):
    return SimpleNamespace(nvr_url=url, nvr_username="local-user", nvr_password="local-password")


class ArchiveProbeAuthBackoff(unittest.TestCase):
    def setUp(self):
        getattr(core, "_NATIVE_ARCHIVE_AUTH", {}).clear()
        self.clock = [1000.0]
        self.generation = ["gen-1"]
        self.probes = []
        self.error = NvrAuthFailed("http://192.0.2.10/cgi-bin/magicBox.cgi: HTTP 401 — "
                                   "recorder rejected the username or password")
        self.info = DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="onvif")
        patches = [
            mock.patch.object(core, "open_driver", lambda _cfg: (_Onvif(), self.info)),
            mock.patch.object(core, "build", lambda name, *_a, **_k:
                              _RefusingNative(self.probes, self.error)),
            mock.patch.object(core, "log", lambda *_a, **_k: None),
            mock.patch.object(core.time, "monotonic", lambda: self.clock[0]),
            mock.patch.object(core.credential_store, "credential_generation",
                              lambda: self.generation[0]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(lambda: getattr(core, "_NATIVE_ARCHIVE_AUTH", {}).clear())

    def _open(self, cfg=None):
        driver, _info = core.open_archive_driver(cfg or _cfg())
        self.assertEqual(driver.name, "onvif")     # the live ONVIF path is always kept
        return driver

    def test_rejected_login_is_not_retried_on_every_open(self):
        for _ in range(3):
            self._open()
        self.assertEqual(len(self.probes), 1)

    def test_backoff_escalates_5_15_30_minutes(self):
        self._open()
        self.clock[0] += 299
        self._open()
        self.assertEqual(len(self.probes), 1)
        self.clock[0] += 2                          # 5 min elapsed: one more attempt
        self._open()
        self.assertEqual(len(self.probes), 2)
        self.clock[0] += 899
        self._open()
        self.assertEqual(len(self.probes), 2)
        self.clock[0] += 2                          # 15 min elapsed
        self._open()
        self.assertEqual(len(self.probes), 3)
        self.clock[0] += 1799
        self._open()
        self.assertEqual(len(self.probes), 3)
        self.clock[0] += 2                          # 30 min, and 30 min is the ceiling
        self._open()
        self.assertEqual(len(self.probes), 4)
        self.clock[0] += 1801
        self._open()
        self.assertEqual(len(self.probes), 5)

    def test_credential_change_in_setup_retries_now(self):
        self._open()
        self.generation[0] = "gen-2"
        self._open()
        self.assertEqual(len(self.probes), 2)

    def test_backoff_is_per_recorder(self):
        self._open(_cfg("http://192.0.2.10"))
        self._open(_cfg("http://192.0.2.11"))
        self.assertEqual(len(self.probes), 2)

    def test_transient_failure_is_retried_next_time(self):
        # Only a rejected login risks a lockout; a timeout must not hold recorded media back.
        self.error = NvrUnreachable("http://192.0.2.10/cgi-bin/magicBox.cgi: timed out")
        self._open()
        self._open()
        self.assertEqual(len(self.probes), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

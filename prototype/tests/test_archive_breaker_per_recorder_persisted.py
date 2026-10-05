#!/usr/bin/env python3
"""The recorder auth breaker is per recorder AND kept beside the Agent state across processes.

Two rules meet here. The multi-recorder Agent keys the vendor-native archive breaker (and the
recovery thread's primary-login breaker) per recorder and clears it when THAT recorder's own
credential changes in Setup (_credential_generation(cfg)). 5.0.28 keeps the breaker in
recorder_auth_backoff.json beside the Agent state, so --status-json, --accept and
--recheck-archive-json (fresh processes) respect a refusal the running Agent saw. Every
recorder shares one Agent state directory, so one file holds every recorder's entry: a refusal
persisted for recorder B must hold B back in the next process, and must never hold recorder A
back, and only B's own credential change clears it. Hermetic: no recorder, no network.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

AGENT = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import programdata_sandbox  # noqa: E402,F401  (before any agent import: no writes to the real ProgramData)

import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, NvrAuthFailed  # noqa: E402

LOCAL_A = "0a000000-0000-4000-8000-00000000000a"
LOCAL_B = "0b000000-0000-4000-8000-00000000000b"
URL_A, URL_B = "http://192.0.2.10", "http://192.0.2.11"


class _Onvif:
    name = "onvif"

    def close(self):
        pass


class _Native:
    name = "dahua-cgi"

    def __init__(self, test, url):
        self._test, self._url = test, url

    def probe(self):
        self._test.probes.append(self._url)
        if self._url in self._test.refusing:
            raise NvrAuthFailed(f"{self._url}/cgi-bin/magicBox.cgi: HTTP 401 — "
                                "recorder rejected the username or password")
        return DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="dahua-cgi")

    def channels(self):
        return []

    def list_channels(self):
        return []

    def close(self):
        pass


class PerRecorderPersistedBreaker(unittest.TestCase):
    def setUp(self):
        core._NATIVE_ARCHIVE_AUTH.clear()
        self.addCleanup(core._NATIVE_ARCHIVE_AUTH.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.probes: list = []
        self.refusing = {URL_B}
        self.generations = {LOCAL_A: "a-1", LOCAL_B: "b-1"}
        self.wall = [1_800_000_000.0]
        info = DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="onvif")
        patches = [
            mock.patch.object(core, "open_driver", lambda _cfg: (_Onvif(), info)),
            mock.patch.object(core, "build", lambda _name, url, *_a, **_k: _Native(self, url)),
            mock.patch.object(core, "log", lambda *_a, **_k: None),
            mock.patch.object(core.time, "monotonic", lambda: 1000.0),
            mock.patch.object(core.time, "time", lambda: self.wall[0]),
            mock.patch.object(core.credential_store, "credential_generation", lambda: "legacy-1"),
            mock.patch.object(core.credential_store, "recorder_credential_generation",
                              lambda local_id: self.generations[local_id]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        state = Path(self.tmp.name) / "agent_state.json"   # one Agent state for every recorder
        self.a = SimpleNamespace(recorder_local_id=LOCAL_A, nvr_url=URL_A, nvr_username="u",
                                 nvr_password="p", state_path=state)
        self.b = SimpleNamespace(recorder_local_id=LOCAL_B, nvr_url=URL_B, nvr_username="u",
                                 nvr_password="p", state_path=state)

    def _new_process(self, cfg):
        """One short-lived process (--status-json / --accept): fresh memory, same disk."""
        core._NATIVE_ARCHIVE_AUTH.clear()
        core.open_archive_driver(cfg)

    def test_a_refusal_persisted_for_one_recorder_holds_only_that_recorder(self):
        self._new_process(self.b)                   # B refuses: persisted
        self._new_process(self.b)                   # next process: B held back
        self.assertEqual(self.probes, [URL_B])
        self._new_process(self.a)                   # A is not held back by B's refusal
        self._new_process(self.a)                   # nor by anything (A accepts its login)
        self.assertEqual(self.probes, [URL_B, URL_A, URL_A])
        self.assertTrue((Path(self.tmp.name) / "recorder_auth_backoff.json").exists())

    def test_two_refusing_recorders_back_off_independently(self):
        self.refusing = {URL_A, URL_B}
        self._new_process(self.a)
        self._new_process(self.b)
        self._new_process(self.a)
        self._new_process(self.b)
        self.assertEqual(self.probes, [URL_A, URL_B])
        self.generations[LOCAL_A] = "a-2"           # Setup fixed only recorder A's login
        self._new_process(self.a)
        self._new_process(self.b)
        self.assertEqual(self.probes, [URL_A, URL_B, URL_A],
                         "A's credential change retries A at once; B stays backed off")

    def test_the_recovery_login_breaker_is_per_recorder_and_persisted(self):
        def refused():
            raise NvrAuthFailed("HTTP 401 — recorder rejected the username or password")
        calls = []

        def opener(cfg):
            def _open():
                calls.append(cfg.nvr_url)
                if cfg.nvr_url in self.refusing:
                    refused()
                return "driver"
            return _open

        with self.assertRaises(NvrAuthFailed):
            core._recovery_login(self.b, opener(self.b))
        core._NATIVE_ARCHIVE_AUTH.clear()           # another process
        with self.assertRaises(NvrAuthFailed):
            core._recovery_login(self.b, opener(self.b))
        self.assertEqual(core._recovery_login(self.a, opener(self.a)), "driver")
        self.assertEqual(calls, [URL_B, URL_A])
        self.generations[LOCAL_B] = "b-2"
        core._NATIVE_ARCHIVE_AUTH.clear()
        with self.assertRaises(NvrAuthFailed):
            core._recovery_login(self.b, opener(self.b))
        self.assertEqual(calls, [URL_B, URL_A, URL_B])


if __name__ == "__main__":
    unittest.main(verbosity=2)

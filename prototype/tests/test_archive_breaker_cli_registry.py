#!/usr/bin/env python3
"""A refusal the running Agent persisted holds back --status-json/--accept/--recheck on a
registry site, where those commands run on the base Config (no recorder_local_id).

The running 5.1.0 Agent opens the archive with the recorder's bound Config: its breaker entry
carries that recorder's own credential generation (recorder_credential_generation). The
short-lived commands run in main() on the base Config, which logs in with the legacy singleton
credential -- on a registry site the mirror of the continuity recorder's (replace_recorder_
credential(mirror_legacy=True)), a separate file with its own token. Both processes use the
same breaker key (driver, URL, username), so the generation must agree too: otherwise each
process deletes the other's entry and probes the refused login again (RV-AF2-01, MNVR-036).
Hermetic: no recorder, no network; the registry lives in the sandboxed PROGRAMDATA.
"""
from __future__ import annotations

import json
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

import recorder_registry  # noqa: E402
import watchlog_agent as core  # noqa: E402
from drivers.base import DeviceInfo, NvrAuthFailed  # noqa: E402

LOCAL_A = "0a000000-0000-4000-8000-00000000000a"   # continuity owner (the legacy singleton)
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

    def list_channels(self):
        return []

    def close(self):
        pass


class CliRespectsTheRunningAgentsRefusal(unittest.TestCase):
    def setUp(self):
        core._NATIVE_ARCHIVE_AUTH.clear()
        self.addCleanup(core._NATIVE_ARCHIVE_AUTH.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        registry = recorder_registry.registry_path()
        self.assertTrue(str(registry).startswith(programdata_sandbox.sandbox_root()))
        self.addCleanup(lambda: registry.unlink(missing_ok=True))
        recorder_registry.save_registry({"schema": recorder_registry.REGISTRY_SCHEMA, "recorders": [
            {"local_id": LOCAL_A, "display_name": "Recorder A", "url": URL_A,
             "driver": "onvif", "is_primary": True, "continuity_owner": True},
            {"local_id": LOCAL_B, "display_name": "Recorder B", "url": URL_B,
             "driver": "onvif", "is_primary": False, "continuity_owner": False},
        ]})
        self.probes: list = []
        self.refusing = {URL_A}
        # Two separate DPAPI files: their tokens never agree, even right after a mirrored write.
        self.legacy = ["legacy-blob-1"]
        self.generations = {LOCAL_A: "a-blob-1", LOCAL_B: "b-blob-1"}
        info = DeviceInfo(vendor="Dahua", model="DH-XVR1B08-I", driver="onvif")
        patches = [
            mock.patch.object(core, "open_driver", lambda _cfg: (_Onvif(), info)),
            mock.patch.object(core, "build", lambda _name, url, *_a, **_k: _Native(self, url)),
            mock.patch.object(core, "log", lambda *_a, **_k: None),
            mock.patch.object(core.time, "monotonic", lambda: 1000.0),
            mock.patch.object(core.time, "time", lambda: 1_800_000_000.0),
            mock.patch.object(core.credential_store, "credential_generation",
                              lambda: self.legacy[0]),
            mock.patch.object(core.credential_store, "recorder_credential_generation",
                              lambda local_id: self.generations[local_id]),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.state = Path(self.tmp.name) / "agent_state.json"
        self.breaker_file = self.state.parent / "recorder_auth_backoff.json"
        # The running Agent: the continuity recorder's bound Config.
        self.running = SimpleNamespace(recorder_local_id=LOCAL_A, nvr_url=URL_A,
                                       nvr_username="admin", nvr_password="p",
                                       state_path=self.state)
        # main()'s base Config for --status-json / --accept / --recheck-archive-json.
        self.cli = SimpleNamespace(nvr_url=URL_A, nvr_username="admin", nvr_password="p",
                                   state_path=self.state)

    def _process(self, cfg):
        """One process opening the archive: fresh memory, same disk."""
        core._NATIVE_ARCHIVE_AUTH.clear()
        core.open_archive_driver(cfg)

    def _entries(self):
        return json.loads(self.breaker_file.read_text(encoding="utf-8"))["entries"]

    def test_the_cli_respects_and_keeps_the_running_agents_refusal(self):
        self._process(self.running)                 # the running Agent's login is refused
        before = self._entries()
        self.assertEqual(self.probes, [URL_A])
        self._process(self.cli)                     # --status-json
        self._process(self.cli)                     # --accept
        self.assertEqual(self.probes, [URL_A], "the CLI must not probe the refused login again")
        self.assertEqual(self._entries(), before, "the running Agent's entry is kept as is")
        self._process(self.running)                 # and the Agent itself still holds back
        self.assertEqual(self.probes, [URL_A])

    def test_the_running_agent_respects_a_refusal_the_cli_saw(self):
        self._process(self.cli)
        self._process(self.running)
        self.assertEqual(self.probes, [URL_A])

    def test_only_that_recorders_own_credential_change_clears_it(self):
        self._process(self.running)
        self.generations[LOCAL_B] = "b-blob-2"      # Setup changed another recorder's login
        self._process(self.cli)
        self.assertEqual(self.probes, [URL_A])
        self.assertEqual(len(self._entries()), 1)
        # Setup changed the continuity recorder's login (both files move together).
        self.generations[LOCAL_A] = "a-blob-2"
        self.legacy[0] = "legacy-blob-2"
        self.refusing = set()
        self._process(self.cli)
        self.assertEqual(self.probes, [URL_A, URL_A], "a changed login is tried at once")
        self.assertEqual(self._entries(), {})

    def test_a_singleton_site_keeps_the_legacy_credential_generation(self):
        recorder_registry.registry_path().unlink()  # 5.0.x: no recorder registry
        self._process(self.cli)
        self._process(self.cli)
        self.assertEqual(self.probes, [URL_A])
        self.assertEqual([e["generation"] for e in self._entries().values()], ["legacy-blob-1"])
        self.legacy[0] = "legacy-blob-2"
        self._process(self.cli)
        self.assertEqual(self.probes, [URL_A, URL_A])

    def test_the_legacy_login_is_never_matched_to_a_non_continuity_recorder(self):
        # The base Config's credential is the continuity recorder's mirror; pointed at
        # recorder B's address it is not B's login, so B's generation is not borrowed.
        cli_b = SimpleNamespace(nvr_url=URL_B, nvr_username="admin", nvr_password="p",
                                state_path=self.state)
        self.refusing = {URL_B}
        self._process(cli_b)
        self.assertEqual([e["generation"] for e in self._entries().values()], ["legacy-blob-1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

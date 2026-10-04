#!/usr/bin/env python3
"""The Setup UI's GUI-free recorder-registry entry points.

  --registry-selftest                    isolated round trip in a private ProgramData
                                         (run against the FROZEN exe by CI)
  --registry-selftest --existing-site    read-only Repair/Upgrade validation of THIS site:
                                         registry, every per-recorder credential, the Agent's
                                         own context loader and (recorder mode) a probe of
                                         every recorder
  --registry-migrate / --registry-rollback
                                         Repair/Upgrade staging of the legacy recorder into
                                         the registry, and its exact undo

Windows DPAPI + ACL hardening needs an elevated token, so these tests replace only that
secret layer with an obfuscating in-memory stand-in; registry, credential_store logic,
recorder_runtime and the entry points themselves run for real. Every test runs inside its
own temporary PROGRAMDATA, set inside this process.

    python -m pytest -q prototype/tests/test_setup_registry_selftest.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from setup_gui_harness import import_setup_gui  # noqa: E402

sg = import_setup_gui()

import credential_store  # noqa: E402
import recorder_registry  # noqa: E402
from drivers import DriverError  # noqa: E402
from windows_secret import SecretError  # noqa: E402


def _fake_write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64encode(json.dumps(obj).encode("utf-8"))[::-1])


def _fake_read(path):
    try:
        return json.loads(base64.b64decode(Path(path).read_bytes()[::-1]))
    except Exception as exc:  # noqa: BLE001
        raise SecretError(f"corrupt secret {Path(path).name}") from exc


class _Site(unittest.TestCase):
    """A temporary ProgramData + install dir holding a legacy singleton recorder."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="wl-registry-test-")
        self.root = Path(self._td.name)
        self._old_pd = os.environ.get("PROGRAMDATA")
        os.environ["PROGRAMDATA"] = str(self.root)
        self.data = self.root / "WatchLog"
        self.data.mkdir()
        self.assertEqual(recorder_registry.data_dir(), self.data)
        self.ini = self.root / "install" / "watchlog.ini"
        self.ini.parent.mkdir()
        self.ini.write_text(
            "[watchlog]\n"
            "supabase_url = https://example.supabase.co\n"
            "supabase_publishable_key = public-key-value\n"
            "nvr_url = http://192.0.2.64\n"
            "nvr_driver = hikvision-isapi\n"
            f"state_dir = {self.data}\n",
            encoding="utf-8",
        )
        self.result = self.root / "result.json"
        patches = [
            patch.object(credential_store, "write_json_secret", _fake_write),
            patch.object(credential_store, "read_json_secret", _fake_read),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        credential_store.save_nvr_credential("admin", "legacy-recorder-pw")

    def tearDown(self):
        if self._old_pd is None:
            os.environ.pop("PROGRAMDATA", None)
        else:
            os.environ["PROGRAMDATA"] = self._old_pd
        self._td.cleanup()

    def body(self):
        return json.loads(self.result.read_text(encoding="utf-8"))

    def two_recorder_registry(self):
        primary = recorder_registry.migrate_legacy_singleton(self.ini)
        second = recorder_registry.add_recorder(
            display_name="Warehouse", url="http://192.0.2.65", driver="dahua-cgi",
            username="admin", password="second-recorder-pw")
        return primary, second


class IsolatedRegistrySelftest(_Site):
    def test_round_trip_passes_without_touching_this_programdata(self):
        code = sg._run_registry_selftest(str(self.result))
        body = self.body()
        self.assertEqual(code, 0, body)
        self.assertTrue(body["ok"])
        for name in ("legacy_migrated_to_primary", "per_recorder_blob_round_trip",
                     "legacy_credential_retained", "no_plaintext_in_blob",
                     "re_migration_keeps_stable_id", "second_credential_independent",
                     "runtime_loads_every_recorder", "secondary_state_is_recorder_scoped",
                     "unbound_rollback_deletes_its_credential"):
            self.assertTrue(body["checks"].get(name), name)
        self.assertEqual(os.environ["PROGRAMDATA"], str(self.root))
        self.assertFalse((self.data / "recorders.json").exists())
        self.assertFalse((self.data / "Secrets" / "recorders").exists())

    def test_a_broken_round_trip_fails_with_a_result(self):
        with patch.object(credential_store, "load_recorder_credential",
                          side_effect=SecretError("recorder credential missing")):
            code = sg._run_registry_selftest(str(self.result))
        self.assertEqual(code, 2)
        self.assertFalse(self.body()["ok"])
        self.assertIn("SecretError", self.body()["error"])

    def test_cli_dispatch_and_elevation(self):
        with patch.object(sys, "argv", ["watchlog-setup-ui.exe", "--registry-selftest",
                                        "--result-json", str(self.result)]), \
             patch.object(sg, "_is_elevated", return_value=True), \
             patch.object(sg, "_run_registry_selftest", return_value=0) as run:
            self.assertEqual(sg.main(), 0)
        run.assert_called_once_with(str(self.result))
        with patch.object(sys, "argv", ["watchlog-setup-ui.exe", "--registry-selftest"]), \
             patch.object(sg, "_is_elevated", return_value=False), \
             patch.object(sg, "_emit_line"), \
             patch.object(sg, "_relaunch_elevated",
                          side_effect=AssertionError("CI mode must never detach")):
            self.assertEqual(sg.main(), sg.ADMIN_REQUIRED_EXIT)


class ExistingSiteRegistryPreflight(_Site):
    def preflight(self, mode):
        return sg._run_registry_preflight(self.ini, mode=mode, result_path=str(self.result))

    def test_legacy_site_without_registry_is_left_to_the_agent_preflight(self):
        self.assertEqual(self.preflight("passive"), 0)
        self.assertEqual(self.body()["registry"], "absent")
        self.assertFalse((self.data / "recorders.json").exists(), "preflight must not migrate")

    def test_corrupt_registry_fails_closed(self):
        (self.data / "recorders.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.preflight("passive"), 2)
        self.assertFalse(self.body()["ok"])

    def test_undecryptable_secondary_credential_fails_closed_and_names_it(self):
        _primary, second = self.two_recorder_registry()
        credential_store.recorder_credential_path(second["local_id"]).unlink()
        self.assertEqual(self.preflight("passive"), 2)
        rows = {row["local_id"]: row for row in self.body()["recorders"]}
        self.assertEqual(rows[second["local_id"]]["credential"], "needs_attention")

    def test_passive_is_read_only_and_never_touches_a_recorder(self):
        primary, second = self.two_recorder_registry()
        registry_bytes = (self.data / "recorders.json").read_bytes()
        blobs = {p.name: p.read_bytes() for p in (self.data / "Secrets" / "recorders").iterdir()}
        with patch.object(sg.backend.core, "open_driver",
                          side_effect=AssertionError("passive mode probed a recorder")):
            self.assertEqual(self.preflight("passive"), 0)
        body = self.body()
        self.assertTrue(body["ok"])
        self.assertEqual(body["recorders_total"], 2)
        self.assertTrue(body["live_markers"])
        markers = {row["local_id"]: Path(row["live_marker"]) for row in body["recorders"]}
        self.assertEqual(markers[primary["local_id"]], self.data / "last_live.json")
        self.assertEqual(markers[second["local_id"]],
                         self.data / "recorders" / second["local_id"] / "last_live.json")
        self.assertEqual((self.data / "recorders.json").read_bytes(), registry_bytes)
        self.assertEqual({p.name: p.read_bytes()
                          for p in (self.data / "Secrets" / "recorders").iterdir()}, blobs)

    def test_recorder_mode_reports_an_offline_secondary_without_failing(self):
        primary, second = self.two_recorder_registry()

        class Driver:
            def list_channels(self):
                return [object(), object()]

            def close(self):
                pass

        def open_driver(cfg):
            if cfg.recorder_local_id == second["local_id"]:
                raise DriverError("connection to http://192.0.2.65 refused")
            return Driver(), object()

        with patch.object(sg.backend.core, "open_driver", side_effect=open_driver):
            self.assertEqual(self.preflight("recorder"), 0)
        rows = {row["local_id"]: row for row in self.body()["recorders"]}
        self.assertTrue(rows[primary["local_id"]]["live"])
        self.assertFalse(rows[second["local_id"]]["live"])
        self.assertEqual(rows[second["local_id"]]["detail"], "web_unreachable")
        self.assertNotIn("192.0.2", json.dumps(self.body()), "no recorder address in the result")

    def test_recorder_mode_requires_the_continuity_recorder(self):
        self.two_recorder_registry()
        with patch.object(sg.backend.core, "open_driver",
                          side_effect=DriverError("timed out")):
            self.assertEqual(self.preflight("recorder"), 2)
        self.assertFalse(self.body()["ok"])

    def test_one_hung_recorder_cannot_stall_the_probe(self):
        _primary, second = self.two_recorder_registry()
        release = threading.Event()

        class Driver:
            def list_channels(self):
                return [object()]

            def close(self):
                pass

        def open_driver(cfg):
            if cfg.recorder_local_id == second["local_id"]:
                release.wait(10)
            return Driver(), object()

        started = time.monotonic()
        with patch.object(sg, "RECORDER_PROBE_SECONDS", 0.5), \
             patch.object(sg.backend.core, "open_driver", side_effect=open_driver):
            self.assertEqual(self.preflight("recorder"), 0)
        release.set()
        self.assertLess(time.monotonic() - started, 5)
        rows = {row["local_id"]: row for row in self.body()["recorders"]}
        self.assertEqual(rows[second["local_id"]]["detail"], "timeout")


class RepairRegistryStaging(_Site):
    def migrate(self):
        return sg._run_registry_migration(self.ini, result_path=str(self.result))

    def blobs(self):
        folder = self.data / "Secrets" / "recorders"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_legacy_recorder_is_staged_and_the_legacy_credential_kept(self):
        ini_before = self.ini.read_bytes()
        self.assertEqual(self.migrate(), 0)
        body = self.body()
        self.assertTrue(body["ok"] and body["migrated"])
        local_id = str(uuid.UUID(body["local_id"]))
        primary = recorder_registry.primary_recorder()
        self.assertEqual(primary["local_id"], local_id)
        self.assertTrue(primary["continuity_owner"])
        self.assertEqual(primary["url"], "http://192.0.2.64")
        self.assertEqual(credential_store.load_recorder_credential(local_id)["password"],
                         "legacy-recorder-pw")
        self.assertEqual(credential_store.load_nvr_credential_readonly()["password"],
                         "legacy-recorder-pw")
        self.assertEqual(self.ini.read_bytes(), ini_before)

    def test_existing_registry_is_left_alone(self):
        self.two_recorder_registry()
        before = (self.data / "recorders.json").read_bytes()
        self.assertEqual(self.migrate(), 0)
        self.assertFalse(self.body()["migrated"])
        self.assertEqual((self.data / "recorders.json").read_bytes(), before)

    def test_a_failed_staging_removes_everything_it_created(self):
        with patch.object(recorder_registry, "save_registry",
                          side_effect=OSError("disk full")):
            self.assertEqual(self.migrate(), 2)
        self.assertTrue(self.body()["undone"])
        self.assertFalse((self.data / "recorders.json").exists())
        self.assertEqual(self.blobs(), [])
        self.assertEqual(credential_store.load_nvr_credential_readonly()["password"],
                         "legacy-recorder-pw")

    def test_rollback_restores_the_previous_state_exactly(self):
        self.assertEqual(self.migrate(), 0)
        local_id = self.body()["local_id"]
        self.assertEqual(
            sg._run_registry_rollback(local_id, result_path=str(self.result)), 0)
        self.assertEqual(self.body()["action"], "removed")
        self.assertFalse((self.data / "recorders.json").exists())
        self.assertEqual(self.blobs(), [])
        self.assertTrue(credential_store.nvr_credential_path().exists())

    def test_rollback_never_discards_a_bound_identity(self):
        self.assertEqual(self.migrate(), 0)
        local_id = self.body()["local_id"]
        recorder_registry.apply_cloud_mapping({local_id: str(uuid.uuid4())})
        before = (self.data / "recorders.json").read_bytes()
        self.assertEqual(
            sg._run_registry_rollback(local_id, result_path=str(self.result)), 2)
        self.assertEqual(self.body()["action"], "kept")
        self.assertEqual((self.data / "recorders.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

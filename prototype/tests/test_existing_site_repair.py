#!/usr/bin/env python3
"""Contracts for the existing-site Repair/Upgrade path.

These tests are intentionally source-level plus dependency-injected runtime checks.
The Windows Release workflow separately proves the frozen EXEs and NSIS artifact.
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "prototype" / "agent"
sys.path.insert(0, str(AGENT))

import analytics_agent  # noqa: E402
import recovery_ai  # noqa: E402
import watchlog_agent as wa  # noqa: E402


class FakeCfg:
    supabase_url = "https://example.supabase.co"
    publishable_key = "public-key-value"
    nvr_url = "http://192.168.1.64"
    nvr_username = "admin"
    nvr_password = "secret"
    nvr_driver = "hikvision-isapi"
    update_url = "https://updates.example/watchlog/manifest.json"
    update_public_key = "A" * 44
    update_require_signature = True
    state_path = Path("/tmp/agent_state.json")

    def require_nvr(self):
        if not self.nvr_url:
            raise SystemExit("no recorder")

    def require_cloud(self):
        if not self.supabase_url or not self.publishable_key:
            raise SystemExit("no cloud")


class FakeDriver:
    def list_channels(self):
        return [SimpleNamespace(channel="1", name="Camera 1")]

    def close(self):
        pass


class FakeCloud:
    calls: list[tuple[str, dict]] = []

    def __init__(self, url, key):
        self.url = url
        self.key = key

    def call(self, fn, **params):
        self.__class__.calls.append((fn, params))
        if fn != "wl_agent_preflight_auth":
            raise AssertionError(f"preflight must not call mutating/claim RPC {fn}")
        return {
            "agent_id": params["p_agent_id"],
            "site_id": "site-1",
            "tenant_id": "tenant-1",
        }


class ExistingSitePreflight(unittest.TestCase):
    def setUp(self):
        FakeCloud.calls = []
        self.state = {
            "agent_id": "agent-1",
            "agent_key": "encrypted-key-is-decrypted-by-test-fixture",
            "site_id": "site-1",
            "tenant_id": "tenant-1",
        }

    def test_preflight_is_read_only_and_passes_valid_site(self):
        device = SimpleNamespace(vendor="hikvision", model="DS-7608NI-Q1", driver="hikvision-isapi")
        with tempfile.TemporaryDirectory() as td,              patch.object(wa, "load_state", return_value=self.state),              patch.object(wa, "open_driver", return_value=(FakeDriver(), device)),              patch.object(wa, "Cloud", FakeCloud),              patch.object(recovery_ai, "decoder_selftest", return_value={"ok": True, "reason": ""}):
            out = Path(td) / "preflight.json"
            code = wa.cmd_existing_site_preflight(FakeCfg(), result_path=str(out))
            self.assertEqual(code, 0)
            body = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(body["ok"])
            self.assertTrue(body["checks"]["recorder"]["ok"])
            self.assertTrue(body["checks"]["cloud_identity"]["ok"])
            self.assertTrue(body["checks"]["signed_remote_update"]["ok"])
            self.assertEqual([fn for fn, _ in FakeCloud.calls], ["wl_agent_preflight_auth"])

    def test_unsigned_or_unconfigured_remote_update_blocks_upgrade(self):
        cfg = FakeCfg()
        cfg.update_public_key = ""
        device = SimpleNamespace(vendor="hikvision", model="NVR", driver="hikvision-isapi")
        with patch.object(wa, "load_state", return_value=self.state),              patch.object(wa, "open_driver", return_value=(FakeDriver(), device)),              patch.object(wa, "Cloud", FakeCloud),              patch.object(recovery_ai, "decoder_selftest", return_value={"ok": True, "reason": ""}):
            code = wa.cmd_existing_site_preflight(cfg)
            self.assertEqual(code, 2)


class StagedPublicDefaults(unittest.TestCase):
    def test_blank_legacy_update_fields_inherit_signed_candidate_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            defaults = root / "watchlog.defaults.ini"
            defaults.write_text(
                "[watchlog]\n"
                "supabase_url = https://example.supabase.co\n"
                "supabase_publishable_key = public-key-value\n"
                "update_url = https://updates.example/watchlog/manifest.json\n"
                "update_public_key = TEST-PUBLIC-KEY\n"
                "update_require_signature = true\n"
                "update_channel = production\n",
                encoding="utf-8",
            )
            existing = root / "existing.ini"
            existing.write_text(
                "[watchlog]\n"
                "nvr_url = http://192.168.1.64\n"
                "nvr_username = admin\n"
                "nvr_driver = hikvision-isapi\n"
                "update_url = \n"
                "update_public_key = \n",
                encoding="utf-8",
            )
            with patch.object(wa, "base_dir", return_value=root):
                cfg = wa.Config(existing, read_only_credentials=True)
            self.assertEqual(cfg.update_url, "https://updates.example/watchlog/manifest.json")
            self.assertEqual(cfg.update_public_key, "TEST-PUBLIC-KEY")
            self.assertTrue(cfg.update_require_signature)


class ProductionWrapperContract(unittest.TestCase):
    def test_analytics_config_accepts_core_staged_preflight_arguments(self):
        sig = inspect.signature(analytics_agent.Config.__init__)
        self.assertTrue(any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()))
        self.assertTrue(any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()))


class InstallerContract(unittest.TestCase):
    def test_repair_uses_two_phase_preflight_before_replacement(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        passive = ps.index('phase 1/2: passive candidate validation')
        stop = ps.index('$rc = Invoke-UpgradeHelper "preflight"')
        recorder = ps.index('phase 2/2: current WatchLog paused/backed up')
        replace = ps.index("Install-CandidatePayload", recorder)
        self.assertLess(passive, stop)
        self.assertLess(stop, recorder)
        self.assertLess(recorder, replace)
        self.assertIn('--preflight-mode "passive"', ps)
        self.assertIn("--preflight-mode recorder", ps)

    def test_repair_requires_fresh_runtime_health_not_just_process_alive(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        for marker in ("agent_version", "heartbeat_at", "recorder_seen_at", "remote_update_poll_at"):
            self.assertIn(marker, ps)
        self.assertIn("Wait-NewRuntimeHealth", ps)
        self.assertIn("previous WatchLog restored", ps)

    def test_repair_package_does_not_ship_qt_setup_ui_or_discovery_wizard(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog-repair.nsi").read_text(encoding="utf-8")
        self.assertIn('File "watchlog-agent.exe"', nsis)
        self.assertNotIn('File "watchlog-setup-ui.exe"', nsis)
        self.assertNotIn("Search Network", nsis)
        self.assertIn("Use WatchLog-Setup.exe for a new installation", nsis)

    def test_candidate_and_health_proof_are_integrity_protected(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        agent = (ROOT / "prototype/agent/watchlog_agent.py").read_text(encoding="utf-8")
        self.assertIn("Protect-CandidateDirectory", ps)
        self.assertIn('S-1-5-18', ps)
        self.assertIn('S-1-5-32-544', ps)
        self.assertIn('Secrets\\runtime-health.json', ps)
        self.assertIn('"Secrets" / "runtime-health.json"', agent)

    def test_full_setup_redirects_complete_existing_sites_before_stop(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text(encoding="utf-8")
        redirect = nsis.index("use WatchLog-Repair-Upgrade.exe instead of the full installer")
        stop = nsis.index("-Stage preflight")
        self.assertLess(redirect, stop)

    def test_full_setup_never_claims_rollback_restart_without_proof(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text(encoding="utf-8")
        self.assertIn("rollback restart not proven", nsis)
        self.assertIn("Agent restart was verified", nsis)
        self.assertNotIn("so the previous working version has been restored", nsis)

    def test_remote_update_health_marker_is_written_only_after_claim_rpc(self):
        src = (ROOT / "prototype/agent/remote_update.py").read_text(encoding="utf-8")
        claim = src.index('"wl_agent_claim_update_request"')
        marker = src.index("remote_update_poll_at")
        self.assertLess(claim, marker)
        self.assertIn("update_runtime_health", src)

    def test_repair_shutdown_helper_only_checks_files_the_repair_replaces(self):
        helper = (ROOT / "prototype/installer/nsis/wl-upgrade.ps1").read_text(encoding="utf-8")
        repair = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        self.assertIn("[ValidateSet('full','repair')]", helper)
        self.assertIn('$RepairPayloadFiles = @(', helper)
        self.assertIn('"watchlog-agent.exe"', helper)
        self.assertIn('"watchlog.defaults.ini"', helper)
        self.assertIn('$PayloadFiles = if ($PayloadProfile -eq "repair")', helper)
        self.assertIn('"-PayloadProfile","repair"', repair)

    def test_repair_failure_exits_cleanly_and_surfaces_real_stage(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog-repair.nsi").read_text(encoding="utf-8")
        self.assertIn('repair-upgrade-result.ini', nsis)
        self.assertIn('ReadINIStr $7', nsis)
        self.assertIn('ReadINIStr $8', nsis)
        self.assertIn('ReadINIStr $6', nsis)
        self.assertIn('SetErrorLevel $9', nsis)
        self.assertIn('Quit', nsis)
        self.assertNotIn('Abort "WatchLog Repair/Upgrade failed safely"', nsis)

    def test_repair_orchestrator_persists_failure_stage_and_traps_unexpected_errors(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        self.assertIn('repair-upgrade-result.ini', ps)
        self.assertIn('function Write-Result', ps)
        self.assertIn('stage=$(Clean-IniValue $Stage)', ps)
        self.assertIn('message=$(Clean-IniValue $Message)', ps)
        self.assertIn('recovery=$(Clean-IniValue $Recovery)', ps)
        self.assertIn('$script:CurrentStage = "pause and unlock current WatchLog"', ps)
        self.assertIn('$script:CurrentStage = "prove updated WatchLog health"', ps)
        self.assertIn('catch {', ps)
        self.assertIn('Fail 49 $msg', ps)

    def test_build_outputs_and_hashes_both_installers(self):
        build = (ROOT / "tools/build_windows_release.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows-release.yml").read_text(encoding="utf-8")
        for name in ("WatchLog-Setup.exe", "WatchLog-Repair-Upgrade.exe"):
            self.assertIn(name, build)
            self.assertIn(name, workflow)
        self.assertIn("WatchLog-Repair-Upgrade.exe.sha256", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)

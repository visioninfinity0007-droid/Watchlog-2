#!/usr/bin/env python3
"""Contracts for the existing-site Repair/Upgrade path.

These tests are intentionally source-level plus dependency-injected runtime checks.
The Windows Release workflow separately proves the frozen EXEs and NSIS artifact.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
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
        # On Windows the preflight reads the machine's DPAPI store; stand it in like the
        # identity, recorder and cloud so the test never depends on (or reads) this PC.
        with tempfile.TemporaryDirectory() as td,              patch.object(wa, "load_state", return_value=self.state),              patch.object(wa, "open_driver", return_value=(FakeDriver(), device)),              patch.object(wa, "Cloud", FakeCloud),              patch.object(wa.credential_store, "load_nvr_credential_readonly",
                          return_value={"username": "admin", "password": "secret"}),              patch.object(recovery_ai, "decoder_selftest", return_value={"ok": True, "reason": ""}):
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
            # Config lets WATCHLOG_<KEY> override the files; the Windows Release job exports
            # WATCHLOG_UPDATE_URL/_PUBLIC_KEY, so keep them out of this file-level contract.
            clean_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("WATCHLOG_")}
            # Config reads base_dir() where it is defined: agent_core (shared with Setup).
            import agent_core
            with patch.object(wa, "base_dir", return_value=root), \
                    patch.object(agent_core, "base_dir", return_value=root), \
                    patch.dict(os.environ, clean_env, clear=True):
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

    def test_repair_package_ships_setup_ui_but_never_runs_the_setup_wizard(self):
        # Manage Recorders lives only in watchlog-setup-ui.exe, so Repair/Upgrade must carry
        # it. It is staged for Manage Recorders and Site Status, never launched as the
        # first-run discovery wizard.
        nsis = (ROOT / "prototype/installer/nsis/watchlog-repair.nsi").read_text(encoding="utf-8")
        self.assertIn('File "watchlog-agent.exe"', nsis)
        self.assertIn('File "watchlog-setup-ui.exe"', nsis)
        self.assertNotIn('ExecWait \'"$INSTDIR\\watchlog-setup-ui.exe"', nsis)
        self.assertNotIn("Search Network", nsis)
        self.assertIn("Use WatchLog-Setup.exe for a new installation", nsis)

    def test_candidate_and_health_proof_are_integrity_protected(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        # runtime_health_path() moved with the heartbeat into agent_core.py (shared with Setup).
        agent = (ROOT / "prototype/agent/agent_core.py").read_text(encoding="utf-8")
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
        # The repair payload carries the Setup UI, so its lock check, backup and rollback do too.
        self.assertIn("watchlog-setup-ui.exe", _ps_array(helper, "$RepairPayloadFiles"))
        self.assertIn("watchlog-setup-ui.exe", _ps_array(repair, "$PayloadFiles"))
        self.assertNotIn("READ ME FIRST.txt", _ps_array(helper, "$RepairPayloadFiles"))

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

    def test_final_commit_uses_health_plus_task_not_cim_agent_visibility(self):
        helper = (ROOT / "prototype/installer/nsis/wl-upgrade.ps1").read_text(encoding="utf-8")
        commit = helper.split("'commit' {", 1)[1].split("'rollback' {", 1)[0]
        self.assertIn("fresh runtime health already proven", commit)
        self.assertIn("background task Running", commit)
        self.assertNotIn("Get-AgentRuntimeLeaves", commit)

    def test_rollback_repairs_task_even_if_previous_task_was_disabled(self):
        helper = (ROOT / "prototype/installer/nsis/wl-upgrade.ps1").read_text(encoding="utf-8")
        rollback = helper.split("'rollback' {", 1)[1]
        self.assertIn('Ensure-WatchLogBackgroundTask "rollback recovery"', rollback)
        self.assertIn("register-service.ps1", helper)
        self.assertNotIn("there is no enabled background task to restart it", rollback)

    def test_recorder_failure_keeps_structured_preflight_detail(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        self.assertIn("recorder-preflight:", ps)
        self.assertIn("recorder preflight exit=", ps)
        self.assertIn("Read that result even on non-zero exit", ps)

    def test_recorder_preflight_retries_transient_session_handoff(self):
        ps = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        self.assertIn("RecorderPreflightAttempts = 3", ps)
        self.assertIn("recorder preflight attempt $attempt/$attempts", ps)
        self.assertIn("recorder preflight not ready; retrying", ps)
        self.assertIn("if ([bool]$obj.ok) { return $obj }", ps)

    def test_build_outputs_and_hashes_both_installers(self):
        build = (ROOT / "tools/build_windows_release.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows-release.yml").read_text(encoding="utf-8")
        for name in ("WatchLog-Setup.exe", "WatchLog-Repair-Upgrade.exe"):
            self.assertIn(name, build)
            self.assertIn(name, workflow)
        self.assertIn("WatchLog-Repair-Upgrade.exe.sha256", workflow)


REPAIR_PS1 = ROOT / "prototype/installer/wl-repair-upgrade.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

# Loads ONLY the function definitions of wl-repair-upgrade.ps1 through the PowerShell AST,
# so the orchestrator body (which pauses and replaces a live install) never runs. Every
# path it touches points into this test's temporary directory.
_GATE_HARNESS = r"""
param([string]$Script, [string]$Work, [string]$Scenario)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
$s = Get-Content -LiteralPath $Scenario -Raw | ConvertFrom-Json
$DataRoot = $Work
$LogPath = Join-Path $Work "repair-upgrade.log"
$ResultPath = Join-Path $Work "repair-upgrade-result.ini"
$HealthPath = Join-Path $Work "runtime-health.json"
$HealthTimeoutSec = 2
$ExpectedVersion = "5.1.0"
$script:RecorderBaseline = $null
$script:RecorderReport = @()
if ($s.registry) {
  if (Get-Command Set-RecorderReport -ErrorAction SilentlyContinue) { Set-RecorderReport $s.registry }
  if (Get-Command Set-RecorderBaseline -ErrorAction SilentlyContinue) { Set-RecorderBaseline $s.registry }
}
$started = [DateTimeOffset]::Parse([string]$s.started_at).UtcDateTime
$h = Wait-NewRuntimeHealth $started
if (Get-Command Update-RecorderReportAfter -ErrorAction SilentlyContinue) { Update-RecorderReportAfter $started }
$script:CurrentStage = "complete"
Write-Result "success" 0 "complete" "done" "kept"
if ($h) { "GATE=PASS" } else { "GATE=ROLLBACK" }
"""


class RepairRecorderGate(unittest.TestCase):
    """Commit gate: continuity recorder live + no regression versus before the upgrade."""

    def setUp(self):
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required to execute the repair gate")
        self.work = Path(tempfile.mkdtemp(prefix="wl-repair-gate-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.started = datetime.now(timezone.utc) - timedelta(seconds=60)
        self.fresh = (self.started + timedelta(seconds=30)).isoformat()
        self.stale = (self.started - timedelta(hours=2)).isoformat()

    def marker(self, name, when):
        path = self.work / name / "last_live.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_live": when}), encoding="utf-8")
        return str(path)

    def run_gate(self, *, health, registry=None):
        health = {"schema": "watchlog.runtime_health.v1", "agent_version": "5.1.0",
                  "heartbeat_at": self.fresh, "remote_update_poll_at": self.fresh, **health}
        (self.work / "runtime-health.json").write_text(json.dumps(health), encoding="utf-8")
        scenario = self.work / "scenario.json"
        scenario.write_text(json.dumps({"started_at": self.started.isoformat(),
                                        "registry": registry}), encoding="utf-8")
        harness = self.work / "harness.ps1"
        harness.write_text(_GATE_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(REPAIR_PS1), "-Work", str(self.work),
             "-Scenario", str(scenario)],
            capture_output=True, text=True, timeout=120)
        self.assertIn("GATE=", proc.stdout, proc.stdout + proc.stderr)
        result = (self.work / "repair-upgrade-result.ini").read_text(encoding="ascii")
        return proc.stdout.strip().splitlines()[-1], result

    A = "aaaaaaaa-0000-4000-8000-000000000001"
    B = "bbbbbbbb-0000-4000-8000-000000000002"

    def two_recorders(self, *, b_live_before, a_marker=None, b_marker=None,
                      live_markers=True):
        # live_markers / live_marker are what Setup's preflight reports; the gate no longer
        # reads the marker files (a standard user can write them), only protected rows.
        return {"ok": True, "registry": "present", "live_markers": live_markers,
                "recorders": [
                    {"local_id": self.A,
                     "display_name": "Primary Recorder", "continuity_owner": True,
                     "credential": "ok", "live": True, "detail": "8 channel(s)",
                     "live_marker": self.marker("a", a_marker or self.stale)},
                    {"local_id": self.B,
                     "display_name": "Warehouse", "continuity_owner": False,
                     "credential": "ok", "live": b_live_before,
                     "detail": "4 channel(s)" if b_live_before else "web_unreachable",
                     "live_marker": self.marker("b", b_marker or self.stale)},
                ]}

    def multi_health(self, *, a=(False, None), b=(False, None)):
        """The new runtime's protected runtime-health: one row per recorder (live, last_live_at).
        Fan-out advances recorder_seen_at only when EVERY recorder is live."""
        rows = [{"local_id": local_id, "recorder_id": f"cloud-{local_id[:8]}",
                 "live": live, "last_live_at": seen}
                for local_id, (live, seen) in ((self.A, a), (self.B, b))]
        return {"recorder_seen_at": self.stale, "multi_recorder": True,
                "recorders_total": 2, "recorders_live": sum(1 for r in rows if r["live"]),
                "recorders": rows}

    def test_secondary_already_offline_before_upgrade_does_not_force_rollback(self):
        gate, result = self.run_gate(
            health=self.multi_health(a=(True, self.fresh)),
            registry=self.two_recorders(b_live_before=False))
        self.assertEqual(gate, "GATE=PASS")
        self.assertIn("[recorders]", result)
        self.assertIn("not_live_after=1", result)
        self.assertIn("Primary Recorder | id=aaaaaaaa-0000-4000-8000-000000000001 | "
                      "continuity=yes | credential=ok | before=live | after=live", result)
        self.assertIn("Warehouse", result)
        self.assertIn("before=offline (web_unreachable) | after=not seen since the update",
                      result)

    def test_recovery_off_secondary_offline_before_does_not_force_rollback(self):
        """With recovery off, Setup reported live_markers=False and the gate fell back to
        requiring every recorder, so one recorder already offline rolled the site back."""
        gate, _ = self.run_gate(
            health=self.multi_health(a=(True, self.fresh)),
            registry=self.two_recorders(b_live_before=False, live_markers=False))
        self.assertEqual(gate, "GATE=PASS")

    def test_secondary_live_before_and_dead_after_rolls_back(self):
        gate, result = self.run_gate(
            health=self.multi_health(a=(True, self.fresh), b=(False, self.stale)),
            registry=self.two_recorders(b_live_before=True))
        self.assertEqual(gate, "GATE=ROLLBACK")
        self.assertIn("before=live | after=not seen since the update", result)

    def test_continuity_recorder_must_be_live_again(self):
        gate, _ = self.run_gate(
            health=self.multi_health(a=(False, self.stale), b=(True, self.fresh)),
            registry=self.two_recorders(b_live_before=False))
        self.assertEqual(gate, "GATE=ROLLBACK")

    def test_a_forged_marker_and_a_returning_recorder_are_not_continuity_proof(self):
        """The count of live recorders was not tied to which recorders were live, and the
        rest of the proof was last_live.json under ProgramData, which a standard user can
        write. Down continuity recorder A + returning B made recorders_live=1 >= 1 required,
        and a fresh A marker committed the upgrade."""
        gate, result = self.run_gate(
            health=self.multi_health(a=(False, self.stale), b=(True, self.fresh)),
            registry=self.two_recorders(b_live_before=False, a_marker=self.fresh))
        self.assertEqual(gate, "GATE=ROLLBACK")
        self.assertIn("Primary Recorder | id=aaaaaaaa-0000-4000-8000-000000000001 | "
                      "continuity=yes | credential=ok | before=live | "
                      "after=not seen since the update", result)

    def test_markers_cannot_stand_in_for_a_protected_row(self):
        gate, _ = self.run_gate(
            health=self.multi_health(a=(True, self.fresh)),
            registry=self.two_recorders(a_marker=self.fresh, b_marker=self.fresh,
                                        b_live_before=True))
        self.assertEqual(gate, "GATE=ROLLBACK")

    def test_a_future_dated_row_is_not_proof(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        gate, _ = self.run_gate(
            health=self.multi_health(a=(True, future)),
            registry=self.two_recorders(b_live_before=False))
        self.assertEqual(gate, "GATE=ROLLBACK")

    def test_every_recorder_live_still_passes(self):
        health = self.multi_health(a=(True, self.fresh), b=(True, self.fresh))
        health["recorder_seen_at"] = self.fresh
        gate, result = self.run_gate(health=health,
                                     registry=self.two_recorders(b_live_before=True))
        self.assertEqual(gate, "GATE=PASS")
        self.assertIn("not_live_after=0", result)

    def test_single_recorder_site_keeps_the_recorder_seen_rule(self):
        self.assertEqual(self.run_gate(health={"recorder_seen_at": self.fresh})[0], "GATE=PASS")
        gate, result = self.run_gate(health={"recorder_seen_at": self.stale})
        self.assertEqual(gate, "GATE=ROLLBACK")
        self.assertNotIn("[recorders]", result)

    def test_old_runtime_health_never_counts(self):
        health = self.multi_health(a=(True, self.fresh), b=(True, self.fresh))
        health.update({"agent_version": "5.0.27", "recorder_seen_at": self.fresh})
        gate, _ = self.run_gate(health=health, registry=self.two_recorders(b_live_before=True))
        self.assertEqual(gate, "GATE=ROLLBACK")


_ROLLBACK_HARNESS = r"""
param([string]$Script, [string]$Work)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
# Never run the real payload rollback here.
function Invoke-UpgradeHelper([string]$Stage, [string[]]$Extra = @()) { "helper:$Stage" | Add-Content (Join-Path $Work "calls.log"); return 0 }
$DataRoot = $Work
$LogPath = Join-Path $Work "repair-upgrade.log"
$ResultPath = Join-Path $Work "repair-upgrade-result.ini"
$ConfigPath = Join-Path $Work "watchlog.ini"
$CandidateDir = $Work
$RegistryStepTimeoutSec = 30
$CandidateSetupUi = Join-Path $Work "fake-setup-ui.cmd"
$script:RecorderReport = @()
$script:RegistryState = ""
$script:StagedRecorderId = "aaaaaaaa-0000-4000-8000-000000000001"
$ok = Restore-Previous "health gate failed"
$script:CurrentStage = "automatic recovery"
Write-Result "failed" 38 "automatic recovery" "x" "y"
"RESTORED=$ok STAGED=[$($script:StagedRecorderId)]"
"""


class RepairRegistryStagingRollback(unittest.TestCase):
    def test_restore_previous_removes_a_registry_staged_by_this_repair(self):
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required to execute the repair rollback")
        work = Path(tempfile.mkdtemp(prefix="wl-repair-staging-"))
        self.addCleanup(shutil.rmtree, work, True)
        calls = work / "calls.log"
        # Stand-in for the candidate Setup UI: records its arguments, answers like
        # --registry-rollback would (result path is the 6th argument).
        (work / "fake-setup-ui.cmd").write_text(
            "@echo off\r\n"
            f'echo setup-ui:%* >> "{calls}"\r\n'
            'echo {"ok":true,"action":"removed"} > %6\r\n',
            encoding="ascii")
        harness = work / "harness.ps1"
        harness.write_text(_ROLLBACK_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(REPAIR_PS1), "-Work", str(work)],
            capture_output=True, text=True, timeout=120)
        self.assertIn("RESTORED=True STAGED=[]", proc.stdout, proc.stdout + proc.stderr)
        log = calls.read_text(encoding="ascii", errors="replace").splitlines()
        self.assertEqual(log[0].strip(), "helper:rollback")
        self.assertIn("--registry-rollback aaaaaaaa-0000-4000-8000-000000000001", log[1])
        result = (work / "repair-upgrade-result.ini").read_text(encoding="ascii")
        self.assertIn("registry=staging removed", result)


_SETUP_UI_STEP_HARNESS = r"""
param([string]$Script, [string]$Work, [string]$Scenario)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
$DataRoot = $Work
$LogPath = Join-Path $Work "repair-upgrade.log"
$ConfigPath = Join-Path $Work "watchlog.ini"
$CandidateDir = $Work
$RegistryStepTimeoutSec = 2
$CandidateSetupUi = Join-Path $Work "fake-setup-ui.cmd"
if ($Scenario -eq "timeout") {
  $r = Invoke-CandidateSetupUi @("--registry-selftest","--existing-site","--preflight-mode","passive") "slow step"
  "RESULT=$([bool]$r)"
} else {
  $a = Invoke-CandidateSetupUi @("--registry-selftest") "first step"
  $b = Invoke-CandidateSetupUi @("--registry-migrate") "second step"
  "MODES=$($a.mode),$($b.mode)"
}
"""


class CandidateSetupUiSteps(unittest.TestCase):
    """Registry steps run the candidate watchlog-setup-ui.exe, a one-file build whose real
    work runs in a child of its bootloader. A step that times out must leave nothing
    running, and no step may read another step's result."""

    def setUp(self):
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required to execute the repair steps")
        self.work = Path(tempfile.mkdtemp(prefix="wl-repair-steps-"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def run_steps(self, scenario, fake):
        (self.work / "fake-setup-ui.cmd").write_text(fake, encoding="ascii")
        harness = self.work / "harness.ps1"
        harness.write_text(_SETUP_UI_STEP_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(REPAIR_PS1), "-Work", str(self.work),
             "-Scenario", scenario],
            capture_output=True, text=True, timeout=120)
        return proc.stdout + proc.stderr

    def test_each_step_reads_its_own_result_file(self):
        calls = self.work / "calls.log"
        # Stand-in: answers in the result file named after --result-json, with its mode.
        out = self.run_steps("two-steps", (
            "@echo off\r\n"
            "setlocal\r\n"
            ":next\r\n"
            'if "%~1"=="" goto done\r\n'
            'if "%~1"=="--result-json" set "RES=%~2"\r\n'
            'if "%~1"=="--registry-selftest" set "MODE=isolated"\r\n'
            'if "%~1"=="--registry-migrate" set "MODE=migrate"\r\n'
            "shift\r\n"
            "goto next\r\n"
            ":done\r\n"
            f'echo %RES%>> "{calls}"\r\n'
            'echo {"ok":true,"mode":"%MODE%"} > "%RES%"\r\n'))
        self.assertIn("MODES=isolated,migrate", out)
        paths = [row.strip() for row in calls.read_text(encoding="ascii").splitlines()
                 if row.strip()]
        self.assertEqual(len(paths), 2, out)
        self.assertNotEqual(paths[0], paths[1])
        for path in paths:
            self.assertFalse(Path(path).exists(), f"{path} was left behind")

    def test_a_timed_out_step_stops_the_whole_process_tree(self):
        marker = self.work / "orphan-wrote.txt"
        # The batch file waits on a child process, like the one-file bootloader does.
        out = self.run_steps("timeout", (
            "@echo off\r\n"
            'powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 5; '
            f"Set-Content -LiteralPath '{marker}' -Value orphan\"\r\n"))
        self.assertIn("RESULT=False", out)
        self.assertIn("slow step timed out",
                      (self.work / "repair-upgrade.log").read_text(encoding="utf-8"))
        deadline = time.monotonic() + 9
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.5)
        self.assertFalse(marker.exists(), "the timed-out step's worker kept running")


class RepairRegistryOrchestration(unittest.TestCase):
    PS = REPAIR_PS1.read_text(encoding="utf-8")

    def test_registry_is_validated_before_pause_and_probed_before_replacement(self):
        ps = self.PS
        passive_agent = ps.index("passive preflight PASSED")
        passive_registry = ps.index('@("--registry-selftest","--existing-site","--preflight-mode","passive")')
        pause = ps.index('$rc = Invoke-UpgradeHelper "preflight"')
        recorder_agent = ps.index("phase 2/2: current WatchLog paused/backed up")
        probe = ps.index("Run-RegistryRecorderCandidate", recorder_agent)
        baseline = ps.index("Set-RecorderBaseline $reg", probe)
        replace = ps.index("Install-CandidatePayload", recorder_agent)
        self.assertLess(passive_agent, passive_registry)
        self.assertLess(passive_registry, pause)
        self.assertLess(pause, recorder_agent)
        self.assertLess(recorder_agent, probe)
        self.assertLess(probe, baseline)
        self.assertLess(baseline, replace)
        self.assertIn('"--registry-selftest","--existing-site","--preflight-mode","recorder"', ps)

    def test_commit_gate_uses_per_recorder_proof(self):
        gate = self.PS[self.PS.index("function Wait-NewRuntimeHealth"):]
        gate = gate[:gate.index("\n}\n")]
        self.assertIn("Test-RecorderProof $h $StartedAtUtc", gate)
        self.assertIn("Update-RecorderReportAfter $started", self.PS)

    def test_legacy_recorder_is_staged_after_validation_and_before_replacement(self):
        ps = self.PS
        recorder_agent = ps.index("phase 2/2: current WatchLog paused/backed up")
        probe = ps.index("Run-RegistryRecorderCandidate", recorder_agent)
        staging = ps.index('Invoke-CandidateSetupUi @("--registry-migrate")')
        replace = ps.index("Install-CandidatePayload", recorder_agent)
        self.assertLess(probe, staging)
        self.assertLess(staging, replace)
        block = ps[staging - 400:staging]
        self.assertIn("if (-not (Test-Path -LiteralPath $RegistryPath))", block)

    def test_rollback_undoes_staging_and_legacy_is_never_retired(self):
        ps = self.PS
        restore = ps[ps.index("function Restore-Previous"):]
        restore = restore[:restore.index("\n}\n")]
        self.assertLess(restore.index('Invoke-UpgradeHelper "rollback"'),
                        restore.index("Undo-RegistryStaging"))
        self.assertIn('@("--registry-rollback", $script:StagedRecorderId)', ps)
        # 5.1 still runs a one-recorder site from the legacy singleton: Repair/Upgrade
        # must keep it until a runtime cutover is proven.
        self.assertNotIn("Remove-Item -LiteralPath $RecorderCredentialPath", ps)
        self.assertNotIn("--retire-legacy", ps)

    def test_nsis_reports_a_degraded_commit_without_hiding_it(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog-repair.nsi").read_text(encoding="utf-8")
        self.assertIn('ReadINIStr $7 "${RESULTFILE}" "recorders" "not_live_after"', nsis)
        self.assertNotIn("the recorder is reachable", nsis)


UPGRADE_HELPER = ROOT / "prototype/installer/nsis/wl-upgrade.ps1"

# A real console exe whose file ProductVersion and --version output are both $Version.
_STAND_IN_EXE = r'''
param([string]$Out, [string]$Version)
$src = @"
using System.Reflection;
[assembly: AssemblyInformationalVersion("$Version")]
public static class Program { public static int Main(string[] a) { System.Console.WriteLine("$Version"); return 0; } }
"@
Add-Type -TypeDefinition $src -OutputAssembly $Out -OutputType ConsoleApplication
'''


_UNEXPECTED_HARNESS = r"""
param([string]$Script, [string]$Work, [string]$Scenario)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
# Never run the real payload rollback here.
$rollbackRc = if ($Scenario -eq "paused-rollback-fails") { 1 } else { 0 }
function Invoke-UpgradeHelper([string]$Stage, [string[]]$Extra = @()) { "helper:$Stage" | Add-Content (Join-Path $Work "calls.log"); return $rollbackRc }
$DataRoot = $Work
$LogPath = Join-Path $Work "repair-upgrade.log"
$ResultPath = Join-Path $Work "repair-upgrade-result.ini"
$ConfigPath = Join-Path $Work "watchlog.ini"
$CandidateDir = $Work
$RegistryStepTimeoutSec = 30
$CandidateSetupUi = Join-Path $Work "fake-setup-ui.cmd"
$script:RecorderReport = @()
$script:RegistryState = ""
$script:StagedRecorderId = ""
$script:CurrentStage = "install candidate files"
$script:RecoveryState = "The previous WatchLog payload is backed up and can be restored automatically."
$script:Paused = $Scenario -like "paused*"
Stop-Unexpected "unexpected Repair/Upgrade error: boom"
"""


class RepairUnexpectedErrorRestoresPreviousState(unittest.TestCase):
    """The orchestrator's catch-all used to Fail 49 without Restore-Previous: an unexpected
    error after the pause left the Agent task disabled and the site unmonitored."""

    def run_unexpected(self, scenario):
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required to execute the repair catch-all")
        work = Path(tempfile.mkdtemp(prefix="wl-repair-unexpected-"))
        self.addCleanup(shutil.rmtree, work, True)
        harness = work / "harness.ps1"
        harness.write_text(_UNEXPECTED_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(REPAIR_PS1), "-Work", str(work),
             "-Scenario", scenario],
            capture_output=True, text=True, timeout=120)
        calls = work / "calls.log"
        log = calls.read_text(encoding="ascii").split() if calls.exists() else []
        result = (work / "repair-upgrade-result.ini").read_text(encoding="ascii")
        return proc.returncode, log, result

    def test_an_unexpected_error_after_the_pause_restores_the_previous_watchlog(self):
        code, calls, result = self.run_unexpected("paused")
        self.assertEqual(code, 49)
        self.assertEqual(calls, ["helper:rollback"])
        self.assertIn("previous WatchLog restored", result)
        self.assertIn("Agent restart was verified", result)

    def test_an_unproven_restore_is_reported_as_such(self):
        code, calls, result = self.run_unexpected("paused-rollback-fails")
        self.assertEqual(code, 48)
        self.assertEqual(calls, ["helper:rollback"])
        self.assertIn("could not be proven running", result)

    def test_before_the_pause_nothing_is_rolled_back(self):
        code, calls, result = self.run_unexpected("not-paused")
        self.assertEqual(code, 49)
        self.assertEqual(calls, [])
        self.assertIn("unexpected Repair/Upgrade error: boom", result)

    def test_the_paused_flag_brackets_the_live_site_changes(self):
        ps = REPAIR_PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
        pause = ps.index('$rc = Invoke-UpgradeHelper "preflight"')
        paused = ps.index("$script:Paused = $true")
        phase2 = ps.index("phase 2/2: current WatchLog paused/backed up")
        committed = ps.index("$script:Paused = $false            # committed")
        self.assertLess(pause, paused)
        self.assertLess(paused, phase2)
        self.assertGreater(committed, ps.index('Invoke-UpgradeHelper "commit"'))
        catch_all = ps[ps.rindex("\ncatch {"):]
        self.assertIn("Stop-Unexpected $msg", catch_all[:catch_all.index("\n}")])


_STAGING_TIMEOUT_HARNESS = r"""
param([string]$Script, [string]$Work)
$ErrorActionPreference = "Stop"
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($fn.Extent.Text))
}
function Invoke-UpgradeHelper([string]$Stage, [string[]]$Extra = @()) { "helper:$Stage" | Add-Content (Join-Path $Work "calls.log"); return 0 }
$DataRoot = $Work
$LogPath = Join-Path $Work "repair-upgrade.log"
$ResultPath = Join-Path $Work "repair-upgrade-result.ini"
$ConfigPath = Join-Path $Work "watchlog.ini"
$RegistryPath = Join-Path $Work "recorders.json"
$CandidateDir = $Work
$RegistryStepTimeoutSec = 3
$CandidateSetupUi = Join-Path $Work "fake-setup-ui.cmd"
$script:RecorderReport = @()
$script:RegistryState = ""
$script:StagedRecorderId = ""
$script:Paused = $true
$staged = Invoke-CandidateSetupUi @("--registry-migrate") "registry staging"
Note-RegistryStaging $staged
"STAGED=[$($script:StagedRecorderId)] RESULT=$([bool]$staged)"
$ok = Restore-Previous "registry staging failed: no result"
Write-Result "failed" 42 "automatic recovery" "x" "y"
"RESTORED=$ok"
"""


class RepairTimedOutRegistryStaging(unittest.TestCase):
    """A --registry-migrate that published the registry and was then stopped for running too
    long returned no result, so Restore-Previous never removed what it had created."""

    def test_a_timed_out_migration_is_rolled_back(self):
        if not POWERSHELL:
            self.skipTest("Windows PowerShell is required to execute the repair staging")
        work = Path(tempfile.mkdtemp(prefix="wl-repair-staging-timeout-"))
        self.addCleanup(shutil.rmtree, work, True)
        calls = work / "calls.log"
        registry = work / "recorders.json"
        local_id = "aaaaaaaa-0000-4000-8000-000000000001"
        # Stand-in for the candidate Setup UI: --registry-migrate publishes the registry and
        # then hangs past the step timeout; --registry-rollback answers like the real one.
        (work / "fake-setup-ui.cmd").write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            'set "MODE="\r\n'
            ":next\r\n"
            'if "%~1"=="" goto done\r\n'
            'if "%~1"=="--result-json" set "RES=%~2"\r\n'
            'if "%~1"=="--registry-migrate" set "MODE=migrate"\r\n'
            'if "%~1"=="--registry-rollback" set "MODE=rollback %~2"\r\n'
            "shift\r\n"
            "goto next\r\n"
            ":done\r\n"
            f'echo setup-ui:%MODE%>> "{calls}"\r\n'
            'if "%MODE%"=="migrate" (\r\n'
            f'  echo {{"schema":1,"recorders":[{{"local_id":"{local_id}"}}]}}> "{registry}"\r\n'
            '  powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 20"\r\n'
            "  exit /b 0\r\n"
            ")\r\n"
            'echo {"ok":true,"action":"removed"} > "%RES%"\r\n',
            encoding="ascii")
        harness = work / "harness.ps1"
        harness.write_text(_STAGING_TIMEOUT_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(harness), "-Script", str(REPAIR_PS1), "-Work", str(work)],
            capture_output=True, text=True, timeout=120)
        out = proc.stdout + proc.stderr
        self.assertIn(f"STAGED=[{local_id}] RESULT=False", out)
        self.assertIn("RESTORED=True", out)
        log = [row.strip() for row in calls.read_text(encoding="ascii").splitlines()]
        self.assertEqual(log, ["setup-ui:migrate", "helper:rollback",
                               f"setup-ui:rollback {local_id}"])
        self.assertIn("registry=staging removed",
                      (work / "repair-upgrade-result.ini").read_text(encoding="ascii"))

    def test_a_migration_that_undid_itself_is_not_rolled_back_again(self):
        ps = REPAIR_PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
        note = ps[ps.index("function Note-RegistryStaging"):]
        note = note[:note.index("\n}\n")]
        self.assertIn("if ($Staged -and [bool]$Staged.undone) { return }", note)
        staging = ps.index('$staged = Invoke-CandidateSetupUi @("--registry-migrate")')
        self.assertLess(ps.index("Note-RegistryStaging $staged", staging),
                        ps.index("Restore-Previous", staging))


class UpgradeHelperSetupUiVersion(unittest.TestCase):
    """verify-version checks the Setup UI only when the caller says it replaced it.

    The in-app updater (Site Status > Check for Updates > watchlog-agent --update) stages
    only watchlog-agent.exe and runs the installed helper with the default profile, so the
    Setup UI beside it is legitimately still the previous version.
    """

    @classmethod
    def setUpClass(cls):
        if not POWERSHELL:
            raise unittest.SkipTest("Windows PowerShell is required to execute wl-upgrade.ps1")
        cls.bin = Path(tempfile.mkdtemp(prefix="wl-verify-version-"))
        script = cls.bin / "stand-in.ps1"
        script.write_text(_STAND_IN_EXE, encoding="utf-8")
        for version in ("5.1.0", "5.1.1"):
            exe = cls.bin / f"{version}.exe"
            subprocess.run(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), "-Out", str(exe), "-Version", version],
                check=True, capture_output=True, timeout=120)
            # A freshly built exe can be briefly unrunnable on a busy Windows host. Prove the
            # stand-in itself runs before the helper is judged on it.
            for _attempt in range(10):
                probe = subprocess.run([str(exe), "--version"], capture_output=True,
                                       text=True, timeout=60)
                if probe.returncode == 0 and probe.stdout.strip() == version:
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f"stand-in {version} exe does not run "
                                   f"(exit {probe.returncode}): {probe.stderr[:200]}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.bin, True)

    def verify(self, *, agent, setup_ui, extra=()):
        work = Path(tempfile.mkdtemp(prefix="wl-verify-install-"))
        self.addCleanup(shutil.rmtree, work, True)
        install = work / "WatchLog"
        install.mkdir()
        shutil.copy2(self.bin / f"{agent}.exe", install / "watchlog-agent.exe")
        shutil.copy2(self.bin / f"{setup_ui}.exe", install / "watchlog-setup-ui.exe")
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(UPGRADE_HELPER), "-Stage", "verify-version",
             "-InstallDir", str(install), "-ExpectedVersion", "5.1.1",
             "-DataRootOverride", str(work / "data"), *extra],
            capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def test_in_app_update_beside_an_older_setup_ui_passes(self):
        # Exactly what cmd_update leaves and runs: new Agent, untouched Setup UI and no
        # -PayloadProfile.
        code, out = self.verify(agent="5.1.1", setup_ui="5.1.0")
        self.assertEqual(code, 0, out)

    def test_repair_profile_rejects_an_older_setup_ui(self):
        code, out = self.verify(agent="5.1.1", setup_ui="5.1.0",
                                extra=("-PayloadProfile", "repair"))
        self.assertEqual(code, 11, out)
        self.assertIn("setup UI file ProductVersion '5.1.0'", out)

    def test_full_setup_rejects_an_older_setup_ui_it_wrote(self):
        code, out = self.verify(agent="5.1.1", setup_ui="5.1.0", extra=("-VerifySetupUi",))
        self.assertEqual(code, 11, out)

    def test_matching_setup_ui_passes_for_both_callers_that_replace_it(self):
        for extra in (("-PayloadProfile", "repair"), ("-VerifySetupUi",)):
            with self.subTest(extra=extra):
                code, out = self.verify(agent="5.1.1", setup_ui="5.1.1", extra=extra)
                self.assertEqual(code, 0, out)

    def test_full_setup_asks_for_the_setup_ui_check(self):
        nsis = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text(encoding="utf-8")
        line = next(row for row in nsis.splitlines() if "-Stage verify-version" in row)
        self.assertIn("-VerifySetupUi", line)


def _ps_array(source: str, name: str) -> list[str]:
    """Quoted entries of a PowerShell `$Name = @( ... )` literal."""
    start = source.index(name + " = @(")
    body = source[start:source.index(")", start)]
    return [line.strip().strip(",").strip('"') for line in body.splitlines()[1:] if line.strip()]


class ExistingSiteUpgradeReachesManageRecorders(unittest.TestCase):
    def test_existing_site_upgrade_reaches_manage_recorders(self):
        full = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text(encoding="utf-8")
        repair = (ROOT / "prototype/installer/nsis/watchlog-repair.nsi").read_text(encoding="utf-8")
        orchestrator = (ROOT / "prototype/installer/wl-repair-upgrade.ps1").read_text(encoding="utf-8")
        helper = (ROOT / "prototype/installer/nsis/wl-upgrade.ps1").read_text(encoding="utf-8")
        registry = (AGENT / "recorder_registry.py").read_text(encoding="utf-8")
        build = (ROOT / "tools/build_windows_release.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows-release.yml").read_text(encoding="utf-8")

        # (a) Full Setup refuses a connected site before touching anything and sends it to
        #     Repair/Upgrade.
        abort = full.index('Abort "Existing connected site: use WatchLog-Repair-Upgrade.exe"')
        self.assertIn('"${DATAROOT}\\Secrets\\agent_key.dpapi"', full[:abort])
        self.assertIn('"${DATAROOT}\\Secrets\\nvr_credential.dpapi"', full[:abort])
        self.assertLess(abort, full.index('File "watchlog-setup-ui.exe"'))
        # (b) 5.1 keeps the legacy singleton credential, so that redirect still fires after the
        #     site runs 5.1: full Setup can never deliver Manage Recorders to it.
        self.assertIn("COPY-ONLY", registry)
        self.assertNotIn("nvr_credential_path().unlink", registry)

        # (c) Repair/Upgrade therefore delivers the Setup UI itself, as part of the payload it
        #     locks, backs up, replaces and rolls back together with the Agent.
        self.assertIn('File "watchlog-setup-ui.exe"', repair)
        self.assertIn("watchlog-setup-ui.exe", _ps_array(orchestrator, "$PayloadFiles"))
        self.assertIn("watchlog-setup-ui.exe", _ps_array(helper, "$RepairPayloadFiles"))
        for stage in ("Backup-Payload", "Restore-Payload", "Get-LockedPayloadFiles"):
            body = helper[helper.index(f"function {stage}"):]
            self.assertIn("foreach ($name in $PayloadFiles)", body[:body.index("\n}\n")])

        # (d) Version-verified like the Agent: the candidate UI before the live site is
        #     paused, and the installed UI after the copy (verify-version, repair profile).
        checks = orchestrator[orchestrator.index('$script:CurrentStage = "candidate integrity and version checks"'):
                              orchestrator.index('$script:CurrentStage = "passive compatibility validation"')]
        self.assertIn("File-Version $CandidateSetupUi", checks)
        self.assertIn("-ne $ExpectedVersion", checks)
        verify = helper.split("'verify-version' {", 1)[1].split("'commit' {", 1)[0]
        self.assertIn("Get-FileProductVersion $SetupExe", verify)
        self.assertIn('$PayloadProfile -eq "repair"', verify)

        # (e) The Manage Recorders and Site Status shortcuts are created only after the new
        #     version is proven; a rolled-back site keeps its previous Start Menu.
        failure = repair.index("SetErrorLevel $9")
        success = repair.index('WriteRegStr HKLM "${ARPKEY}" "DisplayVersion"')
        for shortcut, argument in (("WatchLog Manage Recorders.lnk", "--manage-recorders --config"),
                                   ("WatchLog Site Status.lnk", "--status --config")):
            line = next(row for row in repair.splitlines()
                        if "CreateShortcut" in row and shortcut in row)
            self.assertIn('"$INSTDIR\\watchlog-setup-ui.exe"', line)
            self.assertIn(argument, line)
            self.assertGreater(repair.index(line), failure)
            self.assertGreater(repair.index(line), success)

        # (f) The release no longer rejects a Repair/Upgrade that carries the Setup UI.
        self.assertNotIn("$repairBytes -ge $setupBytes", build)
        self.assertNotIn("no Qt Setup UI", build)
        self.assertNotIn("$repair.Length -ge $setup.Length", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)

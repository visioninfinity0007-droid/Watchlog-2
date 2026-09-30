#!/usr/bin/env python3
"""
0.4.11 — the remaining confirmed audit findings, closed.

A 5-domain audit (189 agents, 23 findings confirmed after 3-lens adversarial review)
found that several things believed to be working had never worked at all. These tests
pin the ones not already covered by test_boot_persistence / test_push_bridge_dahua.

    pytest -q prototype/tests/test_pilot_hardening.py
"""

from __future__ import annotations

import configparser
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "agent"))

import setup_backend as sb  # noqa: E402

NSI = (ROOT / "installer" / "nsis" / "watchlog.nsi").read_text(encoding="utf-8")
UPGRADE = (ROOT / "installer" / "nsis" / "wl-upgrade.ps1").read_text(encoding="utf-8")
BUILD = (REPO / "tools" / "build_windows_release.ps1").read_text(encoding="utf-8")
GUI = (ROOT / "agent" / "setup_gui.py").read_text(encoding="utf-8")
BACKEND = (ROOT / "agent" / "setup_backend.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase" / "migrations"
             / "0110_recorder_push_agent_semantics.sql").read_text(encoding="utf-8")


class PushBridgeUrlReachesTheInstallerTests(unittest.TestCase):
    """THE reason recorder-push never worked: no build ever emitted push_bridge_url, so
    provision_recorder_push returned 'no push bridge configured in this build' on its
    first line in every installer ever shipped. The whole 0013/0108 path was dead code."""

    def test_the_build_accepts_and_resolves_a_push_bridge_url(self):
        self.assertIn("[string]$PushBridgeUrl", BUILD)
        self.assertIn("$env:WATCHLOG_PUSH_BRIDGE_URL", BUILD)
        self.assertIn('$cfg["PUSH_BRIDGE_URL"]', BUILD)

    def test_it_is_written_into_the_staged_public_config(self):
        self.assertIn("push_bridge_url = $pushUrl", BUILD)

    def test_it_gets_the_same_exact_equality_gate_as_the_other_public_keys(self):
        """The anti-config-corruption gate exists so a parameter shift cannot bake a
        different value than we supplied. A new key without it is a hole."""
        self.assertIn("$stagedPush -ne $pushUrl", BUILD)
        self.assertIn("staged push_bridge_url", BUILD, "the gate must still throw by name")

    def test_the_gate_compares_like_with_like(self):
        """It first failed with "staged push_bridge_url '' != intended ''" -- both empty,
        but not the same KIND of empty: $env:X and a missing hashtable key return $null,
        and in PowerShell '' -ne $null is TRUE. Normalise both sides or the gate rejects a
        perfectly correct build."""
        self.assertIn("if ($null -eq $pushUrl) { $pushUrl = \"\" }", BUILD)
        self.assertIn("ContainsKey('push_bridge_url')", BUILD)

    def test_the_release_workflow_actually_passes_the_value(self):
        """Baking the key into the build script does nothing if no caller supplies it --
        that is exactly how recorder-push came to be shipped-but-never-wired."""
        wf = (REPO / ".github" / "workflows" / "windows-release.yml").read_text(encoding="utf-8")
        self.assertIn("WATCHLOG_PUSH_BRIDGE_URL", wf)
        self.assertIn("$releaseArgs['PushBridgeUrl']", wf,
                      "must bind BY NAME: array splatting once shifted -SupabaseUrl into "
                      "-Code and baked an invalid backend URL into a customer release")

    def test_a_non_https_bridge_is_refused_at_build_time(self):
        self.assertIn("PushBridgeUrl is not an https URL", BUILD)


class UpgradesCanReceiveNewPublicKeysTests(unittest.TestCase):
    """NSIS writes watchlog.defaults.ini only when no watchlog.ini exists, which
    correctly preserves a site's recorder settings on upgrade -- and also means an
    ALREADY-INSTALLED site can never receive a new public key. Baking push_bridge_url
    into the build fixes new installs and does nothing for the existing fleet."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ini = self.tmp / "watchlog.ini"
        self.defaults = self.tmp / "watchlog.defaults.ini"

    def _write(self, path: Path, **kv):
        cp = configparser.ConfigParser()
        cp["watchlog"] = {k: v for k, v in kv.items()}
        with path.open("w", encoding="utf-8") as fh:
            cp.write(fh)

    def _read(self) -> dict:
        cp = configparser.ConfigParser()
        cp.read(self.ini, encoding="utf-8")
        return dict(cp.items("watchlog"))

    def test_a_missing_public_key_is_merged_in(self):
        self._write(self.ini, supabase_url="https://x", nvr_url="http://10.0.0.5")
        self._write(self.defaults, supabase_url="https://x",
                    push_bridge_url="https://push.example.io")
        added = sb.merge_public_defaults(self.ini, self.defaults)
        self.assertIn("push_bridge_url", added)
        self.assertEqual("https://push.example.io", self._read()["push_bridge_url"])

    def test_an_existing_value_is_never_overwritten(self):
        self._write(self.ini, push_bridge_url="https://operator-chose-this.io")
        self._write(self.defaults, push_bridge_url="https://build-default.io")
        sb.merge_public_defaults(self.ini, self.defaults)
        self.assertEqual("https://operator-chose-this.io", self._read()["push_bridge_url"])

    def test_recorder_settings_and_the_site_code_are_never_touched(self):
        """A consumed one-time code must not be resurrected, and recorder settings belong
        to the site, not the build."""
        self._write(self.ini, nvr_url="http://10.0.0.5", enrollment_code="")
        self._write(self.defaults, nvr_url="http://SHOULD-NOT-WIN",
                    enrollment_code="WL-SHOULD-NOT-WIN", nvr_driver="auto")
        sb.merge_public_defaults(self.ini, self.defaults)
        got = self._read()
        self.assertEqual("http://10.0.0.5", got["nvr_url"])
        self.assertEqual("", got.get("enrollment_code", ""))

    def test_it_never_raises_when_files_are_missing(self):
        self.assertEqual([], sb.merge_public_defaults(self.tmp / "nope.ini"))

    def test_the_installer_always_stages_the_defaults_for_the_merge(self):
        self.assertIn('File "watchlog.defaults.ini"', NSI,
                      "the defaults must ship unconditionally, not only on a fresh install")


class UpgradeAndUninstallLifecycleTests(unittest.TestCase):
    def test_the_onefile_bootloader_is_not_counted_as_a_duplicate_runtime(self):
        """A PyInstaller --onefile exe is ALWAYS two processes. Counting both made the
        commit guard ('more than one ... duplicate runtime') fire on every healthy
        install, so EVERY upgrade rolled back."""
        self.assertIn("ParentProcessId", UPGRADE)
        self.assertIn("$leaves", UPGRADE)

    def test_uninstall_stops_the_agent_process_before_deleting_files(self):
        """schtasks /End kills the launcher; the agent grandchild survives it, so the
        exe delete silently fails and a ghost agent keeps running."""
        un = NSI[NSI.find('Section "Uninstall"'):]
        stop = un.find("-Stage preflight")
        delete = un.find('Delete "$INSTDIR\\watchlog-agent.exe"')
        self.assertNotEqual(-1, stop, "uninstall must stop the agent, not just the task")
        self.assertLess(stop, delete, "the process must be stopped BEFORE files are deleted")

    def test_uninstall_removes_identity_bound_state(self):
        """Keeping agent_state.json and the spool meant a PC uninstalled at customer A
        and reinstalled at customer B drained A's queued events into B's site."""
        un = NSI[NSI.find('Section "Uninstall"'):]
        for leftover in ("agent_state.json", "spool.sqlite", "health.sqlite"):
            self.assertIn(leftover, un, f"{leftover} must not survive an uninstall")

    def test_uninstall_removes_the_files_that_kept_the_install_dir_alive(self):
        un = NSI[NSI.find('Section "Uninstall"'):]
        self.assertIn("wl-upgrade.ps1", un)
        self.assertIn("wlbak", un)


class WizardHonestyTests(unittest.TestCase):
    def test_the_ready_screen_actually_calls_the_status_lines(self):
        """_background_line was DEFINED in 0.4.8 and never invoked -- dead code. The
        earlier test only asserted the function existed, so it passed while the customer
        was shown nothing about background reporting at all."""
        self.assertIn("self._background_line()", GUI)
        self.assertIn("self._push_line()", GUI)
        summary = GUI[GUI.find("base = (f"):][:700]
        self.assertIn("_background_line()", summary,
                      "the status lines must be in the Ready summary, not merely defined")

    def test_a_setup_failure_offers_more_than_retry(self):
        """Retry was the ONLY control, so a technician had no way to export a support
        bundle and no way out except killing the window -- which aborted the install."""
        we = GUI[GUI.find("def _worker_error"):][:900]
        self.assertIn("incomplete_exit_btn.show()", we)
        self.assertIn("incomplete_bundle_btn.show()", we)

    def test_the_log_file_open_cannot_kill_a_windowed_build(self):
        """It runs at IMPORT, before any handler exists: a locked setup.log would kill a
        --windowed build with no window and no message."""
        head = GUI[:GUI.find("from PySide6")]
        self.assertIn("tempfile.gettempdir()", head, "needs a fallback log location")
        self.assertIn("except Exception", head, "logging must never prevent setup running")

    def test_a_multi_recorder_site_is_warned_not_silently_half_monitored(self):
        """cameras are unique per (site_id, channel), so a second recorder at one site
        overwrites the first's rows. The wizard used to say 'Found 2 possible recorders'
        and then silently monitor one."""
        fn = GUI[GUI.find("def show_recorders"):][:1800]
        self.assertIn("ONE recorder per installation", fn)
        self.assertIn("its own WatchLog site", fn)

    def test_the_push_outcome_is_logged(self):
        """Computed, returned, and never logged or shown -- the second reason nobody
        noticed the bridge was dead."""
        self.assertIn("recorder push: configured=", BACKEND)


class RecorderPushAgentSemanticsTests(unittest.TestCase):
    """A push agent's last_seen_at is ALARM-driven. Every heartbeat-shaped query treats
    silence as death, and the authority pick hands it control of the site."""

    def test_push_agents_cannot_win_the_authority_pick(self):
        self.assertIn("wl_current_site_agent", MIGRATION)
        fn = MIGRATION[MIGRATION.find("create or replace function public.wl_current_site_agent"):]
        newest = fn[fn.find("), newest as ("):fn.find("select coalesce((select holder_agent_id")]
        self.assertIn("<> 'recorder-push'", newest,
                      "a push agent would take authority and auto-resolve real faults")

    def test_the_live_lease_still_overrides_so_pc_failover_is_unaffected(self):
        fn = MIGRATION[MIGRATION.find("create or replace function public.wl_current_site_agent"):]
        self.assertIn("holder_agent_id from live_lease", fn)

    def test_push_agents_are_not_reported_offline(self):
        self.assertIn("'recorder push'", MIGRATION)

    def test_the_fleet_view_keeps_its_security_property_and_columns(self):
        """create-or-replace, NOT drop+create: the original carries
        `with (security_invoker = true)` and 0005_viewer_api selects from it."""
        self.assertIn("with (security_invoker = true)", MIGRATION)
        self.assertNotIn("drop view", MIGRATION.lower())
        for col in ("event_count", "agent_version", "platform"):
            self.assertIn(col, MIGRATION, f"column {col} must not disappear from the view")

    def test_liveness_records_reachability_without_inventing_an_event(self):
        fn = MIGRATION[MIGRATION.find("create or replace function public.wl_push_liveness"):]
        self.assertIn("update push_sources set last_push_at", fn)
        self.assertIn("update agents set last_seen_at", fn)
        self.assertNotIn("insert into events", fn,
                         "a keep-alive must never become an event")

    def test_liveness_is_token_authenticated(self):
        fn = MIGRATION[MIGRATION.find("create or replace function public.wl_push_liveness"):]
        self.assertIn("where token = p_token and enabled", fn)
        self.assertIn("28000", fn, "an unknown token must be refused, not silently ignored")


class DriverPushContractTests(unittest.TestCase):
    DAHUA = (ROOT / "agent" / "drivers" / "dahua.py").read_text(encoding="utf-8")
    HIK = (ROOT / "agent" / "drivers" / "hikvision.py").read_text(encoding="utf-8")

    def test_dahua_protocol_follows_the_bridge_scheme(self):
        """Hardcoding HTTP while computing port 443 told the recorder to open a PLAINTEXT
        connection to a TLS port: every alarm dropped at the handshake."""
        fn = self.DAHUA[self.DAHUA.find("def configure_push"):][:4200]
        self.assertNotIn('"AlarmServer.Protocol=HTTP"', fn)
        self.assertIn('scheme = "HTTPS" if u.scheme == "https" else "HTTP"', fn)

    def test_dahua_sends_config_keys_individually(self):
        """Dahua rejects an ENTIRE setConfig request when any single key is unknown to
        that firmware, so batching five keys meant one unsupported key discarded all."""
        fn = self.DAHUA[self.DAHUA.find("def configure_push"):][:4200]
        self.assertIn("for key, value in required", fn)
        self.assertIn("for key, value in optional", fn)

    def test_dahua_read_back_catches_a_protocol_mismatch(self):
        fn = self.DAHUA[self.DAHUA.find("def configure_push"):][:5200]
        self.assertIn("got_proto", fn)

    def test_hikvision_returns_the_same_contract_as_dahua(self):
        """It returned None, so provision_recorder_push read all-False and reported a
        working Hikvision push as failed."""
        fn = self.HIK[self.HIK.find("def configure_push"):][:4200]
        self.assertIn("-> dict", self.HIK[self.HIK.find("def configure_push"):][:120])
        self.assertIn('"applied": True', fn)
        self.assertIn('"verified": True', fn)


if __name__ == "__main__":
    unittest.main(verbosity=1)

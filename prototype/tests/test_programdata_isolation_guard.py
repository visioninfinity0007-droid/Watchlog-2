#!/usr/bin/env python3
r"""Guard: no test writes to the real %PROGRAMDATA%\WatchLog.

Setup and Agent code keep their state there (setup.log "[recorder-test]" lines,
Secrets\runtime-health.json, agent_state.json, DPAPI blobs). test_camera_enrollment,
test_recorder_setup and test_install_end_to_end wrote into the real directory of the PC that ran
them, which on a site PC is the live installation. Two mechanisms now keep every test out of it,
and this file keeps both in place:

  * conftest.py activates programdata_sandbox before any test module is collected and gives
    each test its own empty PROGRAMDATA. That covers every pytest run, including a file whose
    __main__ hands over to pytest.main.
  * A test file that runs as a plain script (unittest.main or its own runner) does not load
    conftest.py, so it must import programdata_sandbox itself. The script-style files listed in
    LEGACY_SCRIPT_TESTS were each run as a script with PROGRAMDATA pointed at an empty directory
    on 2026-10-05 and wrote nothing there; any other script-style test must import the sandbox
    (or hand its __main__ to pytest.main).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "agent"))

import programdata_sandbox  # noqa: E402

LEGACY_SCRIPT_TESTS = {
    "test_acceptance_hang_regression.py", "test_acceptance_selftest.py", "test_action_runtime.py",
    "test_agent_log_hygiene.py", "test_agent_run_dispatch.py", "test_agent_runtime_e2e.py",
    "test_agent_watchdog_contract.py", "test_ai_first_production_contract.py",
    "test_analytics_engine.py", "test_analytics_portal_contract.py",
    "test_analytics_reporting.py", "test_analytics_sampler.py", "test_apply_migrations_pg.py",
    "test_archive_driver_auth_backoff.py", "test_archive_proof_setup.py",
    "test_archive_runtime.py", "test_backfill.py", "test_billing_authz.py",
    "test_boot_persistence.py", "test_build_metadata.py", "test_camera_config.py",
    "test_camera_health_report_contract.py", "test_camera_preview_realtime_contract.py",
    "test_capabilities.py", "test_config_drift.py", "test_control_room_contract.py",
    "test_control_room_reporting_contract.py", "test_customer_lifecycle_contract.py",
    "test_customer_portal_language.py", "test_dahua_archive_clock_skew.py",
    "test_dahua_archive_close.py", "test_dahua_archive_paging.py",
    "test_dahua_archive_timezone.py", "test_dahua_archive_total_deadline.py",
    "test_device_knowledge_validator.py", "test_email_template.py", "test_entitlement.py",
    "test_existing_site_repair.py", "test_hardware_probe.py",
    "test_health_foundation_contract.py", "test_health_reconciliation_contract.py",
    "test_health_report_contract.py", "test_hikvision_archive.py",
    "test_installer_connectivity.py", "test_intelligence_monthly.py", "test_intelligence_pdf.py",
    "test_intelligence_report.py", "test_intelligence_whatsapp.py", "test_lease_client.py",
    "test_native_nvr_incident_evidence.py", "test_native_verification.py",
    "test_office_reporting.py", "test_old_agent_compat.py",
    "test_onvif_dahua_archive_channel_identity.py", "test_onvif_physical_channels.py",
    "test_operational_faults_contract.py", "test_ops_runtime.py", "test_pilot_hardening.py",
    "test_portal_alignment_contract.py", "test_pricing_alignment.py", "test_proc_util.py",
    "test_public_claims_contract.py", "test_push_bridge.py", "test_push_bridge_dahua.py",
    "test_recorder_probe.py", "test_recorder_push_live.py", "test_recorder_push_setup.py",
    "test_recording_storage_contract.py", "test_recovery.py", "test_recovery_ai.py",
    "test_recovery_dahua_archive_times.py", "test_recovery_hikvision_terminal.py",
    "test_recovery_rpc_contract.py", "test_recovery_wiring.py", "test_release_hardening.py",
    "test_report_pipeline_contract.py", "test_restaurant_reports_contract.py",
    "test_retention.py", "test_saas_operations_contract.py", "test_site_config_advisor.py",
    "test_site_control_portal_contract.py", "test_site_status.py",
    "test_site_type_intelligence_contract.py", "test_spool_offline.py",
    "test_spool_recovery_gap.py", "test_status_controller.py", "test_support_bundle.py",
    "test_team_and_trial.py", "test_tenant_isolation.py", "test_tenant_reporting_governance.py",
    "test_updater.py", "test_vendor_capability_matrix.py", "test_vision_filter.py",
    "test_vision_onnx.py", "test_visual_snapshot_pipeline_contract.py",
    "test_watch_ai_customer_harness.py", "test_watchlog_deployment_smoke.py",
    "test_windows_installer_product.py", "test_worker_exception_survival.py",
}

_MAIN = re.compile(r"^if __name__ == ['\"]__main__['\"]:", re.M)
_IMPORTS_SANDBOX = re.compile(r"^import programdata_sandbox\b", re.M)


def _runs_without_conftest(source: str) -> bool:
    """True for a test file that also runs as a plain script without pytest."""
    main = _MAIN.search(source)
    return bool(main) and "pytest.main" not in source[main.start():]


def test_conftest_activates_the_sandbox_for_every_pytest_run():
    conftest = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert _IMPORTS_SANDBOX.search(conftest), "conftest.py must import programdata_sandbox"
    assert "autouse=True" in conftest and 'setenv("PROGRAMDATA"' in conftest


def test_agent_and_setup_state_resolve_inside_the_sandbox():
    import credential_store
    import setup_backend
    import watchlog_agent

    real = Path(programdata_sandbox.ORIGINAL_PROGRAMDATA).resolve()
    current = Path(os.environ["PROGRAMDATA"]).resolve()
    assert current != real
    state = [setup_backend.programdata_dir(), credential_store.data_dir()]
    if os.name == "nt":                # elsewhere the Agent keeps its state under the home dir
        state += [watchlog_agent.default_state_dir(), watchlog_agent.runtime_health_path()]
    for path in state:
        assert Path(path).resolve().is_relative_to(current), path


def test_script_style_tests_isolate_programdata_themselves():
    missing = []
    for path in sorted(TESTS.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if (_runs_without_conftest(source) and path.name not in LEGACY_SCRIPT_TESTS
                and not _IMPORTS_SANDBOX.search(source)):
            missing.append(path.name)
    assert not missing, (
        "these tests run as plain scripts without conftest.py and do not import "
        f"programdata_sandbox, so they could write to the real ProgramData: {missing}")


def test_legacy_list_names_only_existing_script_style_tests():
    stale = []
    for name in sorted(LEGACY_SCRIPT_TESTS):
        path = TESTS / name
        if not path.exists() or not _runs_without_conftest(path.read_text(encoding="utf-8")):
            stale.append(name)
    assert not stale, f"remove from LEGACY_SCRIPT_TESTS: {stale}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))

#!/usr/bin/env python3
"""Guard: every test file in prototype/tests is run by a step of .github/workflows/ci.yml.

The 5.0.28 work added about thirty test files (ONVIF, incident evidence, worker survival,
recovery RPC contract, Hikvision terminal search errors, stream liveness, last_live, reconnect,
burst clocks, event time, targets, auth downgrade...) that no CI step ran, so a regression in
any of them could merge green, and their results were "locally proven" at best.

A test file counts as run when a path or glob in ci.yml (or in a shell script ci.yml runs)
matches it. Files that no CI step ran on origin/main 6488bab are listed below so this guard
could land without changing what CI covers for them; a NEW unreferenced file fails here. A
listed file that CI starts running must leave the list.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "prototype" / "tests"
CI = ROOT / ".github" / "workflows" / "ci.yml"

# test_*.py files no ci.yml step ran on origin/main 6488bab (live-cloud, Windows-only or manual
# tests among them). Do not add to this list: run a new test in CI instead.
UNREFERENCED_ON_MAIN = {
    "test_acceptance_hang_regression.py", "test_agent_coverage.py", "test_billing_authz.py",
    "test_boot_persistence.py", "test_entitlement.py", "test_install_end_to_end.py",
    "test_installer_connectivity.py", "test_office_reporting.py", "test_platform_admin_authz.py",
    "test_proc_util.py", "test_recorder_push_live.py", "test_recorder_setup.py",
    "test_release_hardening.py", "test_remote_maintenance_hikvision.py",
    "test_site_lifecycle_notifications_contract.py", "test_spool_recovery_gap.py",
    "test_team_and_trial.py", "test_tenant_isolation.py", "test_videoloss_reconciliation.py",
    "test_vision_worker_runtime.py", "test_watch_ai_customer_harness.py",
}
# e2e_*.py modules that are shared helpers, not runnable tests.
E2E_HELPERS = {"e2e_harness.py", "e2e_http.py"}

_PATH = re.compile(r"prototype/tests/[A-Za-z0-9_*?\[\]./-]+\.(?:py|sh)")


def _ci_patterns() -> set[str]:
    """Every prototype/tests path or glob named by ci.yml or by a test script it runs."""
    texts = [CI.read_text(encoding="utf-8")]
    patterns = set(_PATH.findall(texts[0]))
    for script in sorted(p for p in patterns if p.endswith(".sh")):
        path = ROOT / script
        if path.exists():
            patterns |= set(_PATH.findall(path.read_text(encoding="utf-8")))
    return patterns


def _referenced(name: str, patterns: set[str]) -> bool:
    return any(fnmatch.fnmatchcase(f"prototype/tests/{name}", pattern) for pattern in patterns)


def test_every_test_file_is_run_by_ci():
    patterns = _ci_patterns()
    missing = [p.name for p in sorted(TESTS.glob("test_*.py"))
               if p.name not in UNREFERENCED_ON_MAIN and not _referenced(p.name, patterns)]
    missing += [p.name for p in sorted(TESTS.glob("e2e_*.py"))
                if p.name not in E2E_HELPERS and not _referenced(p.name, patterns)]
    assert not missing, f"no step in .github/workflows/ci.yml runs: {missing}"


def test_the_unreferenced_list_only_shrinks():
    # A listed file that CI now runs (another branch added its step) is harmless and is not a
    # failure, so merge order between branches cannot break this guard. A listed file that no
    # longer exists must be removed from the list.
    gone = sorted(name for name in UNREFERENCED_ON_MAIN if not (TESTS / name).exists())
    assert not gone, f"remove from UNREFERENCED_ON_MAIN (file no longer exists): {gone}"


def test_every_ci_path_names_an_existing_file():
    """A typo in a ci.yml path would run nothing and fail only on GitHub."""
    dead = sorted(p for p in _ci_patterns()
                  if not any(ch in p for ch in "*?[") and not (ROOT / p).exists())
    assert not dead, f"ci.yml names files that do not exist: {dead}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))


_FFMPEG_SKIP = re.compile(r"skip(?:if|Unless)\([^\n]*ffmpeg", re.I)


def test_real_ffmpeg_tests_cannot_skip_silently_in_ci():
    """The real-FFmpeg tests (Hikvision whole-segment rejection and probe, the MPEG-PS -> MP4
    remux, frame decode) skip where no FFmpeg is found. The backend job installed no FFmpeg, so
    on a runner without one on PATH they showed as 's' and the step passed: a broken remux or
    duration check merged green. CI installs FFmpeg the way the release finds it
    (imageio-ffmpeg) and sets WATCHLOG_REQUIRE_FFMPEG, which turns the skip into a failure."""
    import yaml

    job = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["backend"]
    installs = " ".join(step.get("run", "") for step in job["steps"]
                        if "pip install" in step.get("run", ""))
    assert "imageio-ffmpeg" in installs, "the backend job does not install imageio-ffmpeg"
    assert str((job.get("env") or {}).get("WATCHLOG_REQUIRE_FFMPEG")) == "1", \
        "the backend job does not set WATCHLOG_REQUIRE_FFMPEG=1"
    skipping = sorted(p.name for p in TESTS.glob("test_*.py")
                      if _FFMPEG_SKIP.search(p.read_text(encoding="utf-8")))
    assert skipping, "no FFmpeg-gated test found: update this guard"
    blind = [name for name in skipping
             if "WATCHLOG_REQUIRE_FFMPEG" not in (TESTS / name).read_text(encoding="utf-8")]
    assert not blind, f"these FFmpeg tests still skip in CI: {blind}"

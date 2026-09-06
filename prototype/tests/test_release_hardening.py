#!/usr/bin/env python3
"""0.3.4 release-correctness contract gates.

Locks the P0 fixes that made 0.3.3 a broken/NO-GO artifact:
  * single version source (0.3.3 shipped runtime reporting 0.3.0)
  * NSIS uses a valid common-appdata path (0.3.3 used the non-existent
    $PROGRAMDATA constant -> NSIS warning 6000 -> broken path logic)
  * the release workflow uses a NAMED (hashtable) splat, not a positional
    array splat (0.3.3 baked an invalid backend URL and '-SupabaseUrl' as the
    site code)
  * the build script fails closed on leaked args, verifies the staged config,
    treats NSIS warnings as fatal, and passes the single version to NSIS
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(p):
    return (ROOT / p).read_text(encoding="utf-8")


def main():
    wl_version = text("prototype/agent/wl_version.py")
    agent = text("prototype/agent/watchlog_agent.py")
    analytics = text("prototype/agent/analytics_agent.py")
    backend = text("prototype/agent/setup_backend.py")
    nsis = text("prototype/installer/nsis/watchlog.nsi")
    build = text("tools/build_windows_release.ps1")
    build_exe = text("prototype/agent/build_exe.ps1")
    build_ui = text("prototype/agent/build_setup_gui.ps1")
    workflow = text(".github/workflows/windows-release.yml")

    version = re.search(r'^VERSION\s*=\s*"([^"]+)"', wl_version, re.M).group(1)
    nsis_ver = re.search(r'!define APPVERSION "([^"]+)"', nsis).group(1)

    checks = {
        # --- single version source ---
        "wl_version defines VERSION": bool(version),
        "agent derives version from wl_version": "from wl_version import VERSION as AGENT_VERSION" in agent,
        "analytics derives version from wl_version": "from wl_version import VERSION as AGENT_VERSION" in analytics,
        "backend derives version from wl_version": "from wl_version import VERSION as SETUP_AGENT_VERSION" in backend,
        "no stale hardcoded 0.3.0 in analytics": 'AGENT_VERSION = "0.3.0"' not in analytics,
        "NSIS fallback version matches wl_version": nsis_ver == version,
        "NSIS version is overridable by build": "!ifndef APPVERSION" in nsis,
        "build passes single version to NSIS": "/DAPPVERSION=$appVersion" in build,

        # --- NSIS ProgramData path (was invalid $PROGRAMDATA) ---
        "NSIS no longer uses invalid $PROGRAMDATA": "$PROGRAMDATA" not in nsis,
        "NSIS uses $COMMONPROGRAMDATA data root": "$COMMONPROGRAMDATA" in nsis and "${DATAROOT}" in nsis,
        "NSIS does not depend on SetShellVarContext for paths": "SetShellVarContext" not in nsis,

        # --- argument binding (named, not positional) ---
        "workflow uses named hashtable splat": "$releaseArgs = @{" in workflow,
        "workflow no longer uses positional array splat": "@('-SupabaseUrl'" not in workflow,

        # --- build fail-closed guards + verification ---
        "build rejects non-https SupabaseUrl": "SupabaseUrl is not an https URL" in build,
        "build rejects leaked enrollment code": "enrollment code is not a WL- code" in build,
        "build verifies staged supabase_url is https": "staged supabase_url is not https" in build,
        "build treats NSIS warnings as fatal": "NSIS emitted warnings" in build,

        # --- release governance corrections (review round 2) ---
        "tag (v*) builds run in production mode": "github.event_name == 'push'" in workflow,
        "production requires a PublisherUrl": "PRODUCTION release requires -PublisherUrl" in build,
        "staged config verified by EXACT equality": "!= intended" in build and "exact match" in build,
        "agent build embeds PE version metadata": "--version-file" in build_exe,
        "setup UI build embeds PE version metadata": "--version-file" in build_ui,
        "release asserts exe ProductVersion == wl_version": "ProductVersion" in workflow and "!= wl_version" in workflow,
    }

    failed = [k for k, ok in checks.items() if not ok]
    for k, ok in checks.items():
        print(("PASS" if ok else "FAIL") + "  " + k)
    if failed:
        raise SystemExit("release hardening contract failed: " + ", ".join(failed))
    print(f"\n{len(checks)}/{len(checks)} release-hardening checks passed (version {version})")


if __name__ == "__main__":
    main()

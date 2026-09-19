#!/usr/bin/env python3
"""Site Control portal capability-aware UX contract (items 4 & 5).

Static contract over portal/app/site-control/page.js + shell nav + the backing RPC grants.
Asserts the page is driven by the capability KB, gates controls on verdict x evidence, uses the
honest 'Not verified' wording for UNKNOWN, disables UNSUPPORTED with a reason, gates
Read/Recommend/Approve by role, and NEVER exposes a raw recorder command or credential.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OK = []
def check(cond, name):
    OK.append(bool(cond)); print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def main() -> int:
    page = (REPO / "portal" / "app" / "site-control" / "page.js").read_text(encoding="utf-8")
    shell = (REPO / "portal" / "app" / "shell.js").read_text(encoding="utf-8")

    # driven by the capability KB + diagnosis, with tenant guard
    check("requireTenant" in page, "page uses the requireTenant session gate")
    for rpc in ("wl_my_site_diagnosis", "wl_my_site_context", "wl_onboarding_status",
                "wl_upsert_site_context", "wl_sites"):
        check(rpc in page, f"page calls {rpc}")

    # capability-aware treatment of every verdict x evidence
    check('v==="supported"&&e==="FIELD_VERIFIED"' in page.replace(" ", "") and '"configure"' in page,
          "supported+FIELD_VERIFIED is treated as configurable")
    check('"Not verified"' in page or "Not verified" in page, "UNKNOWN capability is shown as 'Not verified'")
    check('v==="unsupported"' in page.replace(" ", "") and "disabled" in page,
          "UNSUPPORTED capability disables the control (with reason)")
    check('v==="by_camera"' in page.replace(" ", "") and "Camera-side" in page,
          "by_camera capability is shown as camera-side, not recorder-configurable")

    # Read -> Recommend -> Approve tiers gated by role
    check("tiers.approve" in page and "tiers.recommend" in page, "controls gate on Read/Recommend/Approve tiers")
    check("Read → Recommend → Approve" in page or "Recommend" in page and "approve" in page.lower(),
          "the Read/Recommend/Approve model is surfaced")

    # SAFETY: no raw recorder command or credential ever exposed in the browser
    low = page.lower()
    for banned in ("configmanager", "cgi-bin", ".cgi", "rtsp://", "nvr_password", "recorder_password", "admin123"):
        check(banned not in low, f"page never exposes '{banned}'")

    # nav registration
    check('"/site-control/"' in shell, "Site Control is registered in the portal nav")

    # backing RPCs are tenant-guarded (granted to authenticated, not anon)
    sql79 = (ROOT / "supabase" / "migrations" / "0079_site_diagnosis.sql").read_text(encoding="utf-8")
    check("wl_my_tenant()" in sql79 and "not authorized for this site" in sql79,
          "wl_my_site_diagnosis is tenant-guarded")
    check("from public, anon" in sql79 and "to authenticated" in sql79,
          "wl_my_site_diagnosis is revoked from anon, granted to authenticated")

    passed = sum(1 for x in OK if x)
    print(f"\n  {passed}/{len(OK)} checks passed")
    return 0 if passed == len(OK) else 1


if __name__ == "__main__":
    sys.exit(main())

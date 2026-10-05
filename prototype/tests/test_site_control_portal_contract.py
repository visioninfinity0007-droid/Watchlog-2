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
    # AI-first: site-control/page.js is a thin re-export of customer-workspace.js, which draws on the
    # use-customer-control hook. Read the real surface where the capability-aware Read/Recommend/Approve
    # UX and the tenant-guarded RPC calls are implemented. Nav registration lives in nav-config.js.
    sc = REPO / "portal" / "app" / "site-control"
    # recorder-control.js holds the capability labels and the per-recorder grouping (MNVR-048/049).
    workspace = (sc / "customer-workspace.js").read_text(encoding="utf-8")
    control = (sc / "recorder-control.js").read_text(encoding="utf-8") if (sc / "recorder-control.js").exists() else ""
    page = workspace + "\n" + control + "\n" + (sc / "use-customer-control.js").read_text(encoding="utf-8")
    nav = (REPO / "portal" / "app" / "nav-config.js").read_text(encoding="utf-8")

    # driven by the capability KB + diagnosis, with tenant guard
    check("requireTenant" in page, "page uses the requireTenant session gate")
    for rpc in ("wl_my_site_diagnosis", "wl_my_site_context", "wl_onboarding_status",
                "wl_upsert_site_context", "wl_sites"):
        check(rpc in page, f"page calls {rpc}")

    # capability-aware treatment of every verdict x evidence
    # MNVR-049: FIELD_VERIFIED evidence is recorded per recorder model; only evidence scoped to this
    # recorder makes a setting configurable.
    check('v==="supported"&&verifiedHere(cap)' in page.replace(" ", "") and '"configure"' in page,
          "supported + verified on this recorder is treated as configurable")
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
    check('"/site-control/"' in nav, "Site Control is registered in the portal nav")

    # backing RPCs are tenant-guarded (granted to authenticated, not anon)
    sql79 = (ROOT / "supabase" / "migrations" / "0079_site_diagnosis.sql").read_text(encoding="utf-8")
    check("wl_my_tenant()" in sql79 and "not authorized for this site" in sql79,
          "wl_my_site_diagnosis is tenant-guarded")
    check("from public, anon" in sql79 and "to authenticated" in sql79,
          "wl_my_site_diagnosis is revoked from anon, granted to authenticated")

    # Multi-recorder (MNVR-048/049): the page renders the recorder-aware diagnosis per recorder,
    # keyed by camera identity, and never offers a change on model-level evidence alone.
    check("recorderGroups(" in workspace and "key={cam.key}" in workspace,
          "camera channels are grouped by recorder and keyed by camera identity")
    check('(cam.channel??i)+"-"+(cam.name||"")' not in workspace,
          "camera rows are not keyed by channel+name (collides across recorders)")
    check("multiRecorder" in workspace and "caps.length&&!multiRecorder" in workspace.replace(" ", ""),
          "a multi-recorder site never shows one site-wide settings list")
    check('evidence_scope==="recorder"' in control.replace(" ", ""),
          "'Verified on your camera system' requires recorder-scoped evidence")
    # Per-recorder change flow: the Ask link carries the recorder and camera it is about.
    check('"&recorder="' in workspace and '"&camera="' in workspace,
          "the per-recorder change link sends recorder_id/camera_id")

    # The diagnosis the page calls is the recorder-aware one (latest definition, 0155).
    diag = ""
    for path in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        at = text.rfind("create or replace function public.wl_my_site_diagnosis(")
        if at >= 0:
            diag = text[at:text.find("$function$;", at)]
    for needle, name in (
        ("'recorders',v_recorders", "diagnosis returns one entry per configured recorder"),
        ("when v_recorder_count>1 then null", "diagnosis has no site-wide recorder identity on a multi-recorder site"),
        ("coalesce(c.is_canonical,true)", "diagnosis cameras are canonical cameras only"),
        ("'camera_id',o.camera_id", "diagnosis cameras carry camera identity"),
    ):
        check(needle in diag, name)
    check("max(vendor)" not in diag and "max(device_vendor)" not in diag,
          "recorder identity never mixes historical rows with max()")

    passed = sum(1 for x in OK if x)
    print(f"\n  {passed}/{len(OK)} checks passed")
    return 0 if passed == len(OK) else 1


if __name__ == "__main__":
    sys.exit(main())

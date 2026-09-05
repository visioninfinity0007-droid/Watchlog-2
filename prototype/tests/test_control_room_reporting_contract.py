#!/usr/bin/env python3
"""Static product/auth contract for Control Room operational reporting."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "portal/app/control-room/reports/page.js"


def main() -> int:
    src = PAGE.read_text(encoding="utf-8")
    lower = src.lower()

    assert 'requireTenant' in src, "report route must use the shared tenant/account guard"
    for rpc in ("wl_analytics_studio", "wl_portal_overview", "wl_site_health_details", "wl_analytics_overview"):
        assert rpc in src, f"missing tenant-safe data source: {rpc}"

    assert "Last 1 day" not in src, "window selector should be generated rather than hard-coded"
    for days in (1, 7, 30):
        assert str(days) in src, f"missing {days}-day report window"

    assert "Camera-wise report" in src
    assert "Site comparison" in src
    assert "Collective operations report" in src
    assert "Print / save PDF" in src

    # Camera reporting must be derived from the existing by_rule aggregate,
    # scoped by both camera and site so duplicate camera names cannot mix data.
    assert "by_rule" in src
    assert "item.camera === selectedCamera.name" in src
    assert "selectedCameraSite" in src
    assert "item.site === selectedCameraSite" in src
    assert "Analytics signals" in src

    # Product truth boundaries.
    assert "people presence, not sales" in lower
    assert "not a certified safety or transaction record" in lower
    forbidden = (
        "facial recognition",
        "live video wall",
        "completed transactions from cctv",
        "pos totals from cctv",
        "certified fire safety",
    )
    for phrase in forbidden:
        assert phrase not in lower, f"unsafe report claim found: {phrase}"

    # No direct table access or new mutation surface in the report page.
    for token in ('.from("', ".insert(", ".update(", ".delete("):
        assert token not in src, f"report page must remain RPC/read-only: {token}"

    print("OK: Control Room reporting contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

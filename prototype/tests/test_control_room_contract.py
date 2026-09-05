#!/usr/bin/env python3
"""Static product/security contract for the Control Room pilot."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "portal/app/control-room/page.js").read_text(encoding="utf-8")
MIG = (ROOT / "prototype/supabase/migrations/0039_control_room_layouts.sql").read_text(encoding="utf-8")


def require(text, needle, message):
    if needle not in text:
        raise AssertionError(message)


def main():
    # Product boundary: Control Room remains operational intelligence, not an
    # unvalidated cloud video product.
    require(PAGE, "Control Room Pilot", "pilot label missing")
    require(PAGE, "does not provide a live video wall", "live-video boundary missing")
    require(PAGE, "Exact transactions and till reconciliation are not inferred from CCTV alone.", "checkout truth boundary missing")
    for unsafe in ("rtsp://", "<video", "autoplay"):
        if unsafe.lower() in PAGE.lower():
            raise AssertionError(f"unvalidated live-video mechanism present: {unsafe}")

    # Shared account-status/tenant guard and existing tenant-safe data surfaces.
    require(PAGE, "requireTenant", "Control Room must use shared tenant/account guard")
    for rpc in ("wl_portal_overview", "wl_analytics_studio", "wl_analytics_overview", "wl_portal_snapshot"):
        require(PAGE, f'rpc("{rpc}"', f"Control Room must reuse {rpc}")

    # Saved layouts contain camera references only. No credentials/video URLs.
    require(MIG, "create table if not exists public.control_room_layouts", "layout table missing")
    require(MIG, "camera_ids  uuid[]", "layout camera references missing")
    require(MIG, "grid_size in (2,3,4)", "2x2/3x3/4x4 grid constraint missing")
    require(MIG, "cardinality(camera_ids) <= grid_size * grid_size", "layout capacity constraint missing")
    for forbidden in ("password", "rtsp", "stream_url", "video_url"):
        # comments may explain exclusions, so inspect only SQL column/function
        # body text after removing comment lines.
        sql = "\n".join(line for line in MIG.splitlines() if not line.lstrip().startswith("--"))
        if forbidden in sql.lower():
            raise AssertionError(f"layout storage must not contain {forbidden}")

    # Fail-closed storage: RLS on, no direct authenticated grants, RPC only.
    require(MIG, "alter table public.control_room_layouts enable row level security", "RLS missing")
    require(MIG, "revoke all on table public.control_room_layouts from public, anon, authenticated", "direct table access not revoked")
    if "create policy" in MIG.lower():
        raise AssertionError("Control Room layout table should remain RPC-only with no direct client policies")

    # Read is active-tenant scoped; writes are Owner/Admin scoped.
    require(MIG, "v_tenant uuid := wl_my_tenant()", "layout reads must use active tenant")
    require(MIG, "wl_require_role(array['owner','admin'])", "layout writes must require Owner/Admin")
    require(MIG, "c.tenant_id = v_tenant", "camera ownership validation missing")
    require(MIG, "layout not found in your account", "cross-tenant update/delete failure missing")
    require(MIG, "revoke all on function public.wl_control_room_layouts() from public, anon", "read RPC anon revoke missing")
    require(MIG, "revoke all on function public.wl_save_control_room_layout(text,int,uuid[],uuid) from public, anon", "save RPC anon revoke missing")
    require(MIG, "revoke all on function public.wl_delete_control_room_layout(uuid) from public, anon", "delete RPC anon revoke missing")

    print("Control Room contract: PASS")


if __name__ == "__main__":
    main()

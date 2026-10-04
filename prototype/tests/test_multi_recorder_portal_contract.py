#!/usr/bin/env python3
"""Static portal contract for customer-safe multi-recorder grouping."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HEALTH=(ROOT/"portal/app/site-health/health-workspace.js").read_text(encoding="utf-8")
CAMERAS=(ROOT/"portal/app/control-room/customer-workspace.js").read_text(encoding="utf-8")
MIG=(ROOT/"prototype/supabase/migrations/0152_multi_recorder_owner_read_model.sql").read_text(encoding="utf-8")


def require(text,needle,message):
    if needle not in text:
        raise AssertionError(message)


def main():
    # Both owner surfaces use the governed recorder read model.
    for surface,name in ((HEALTH,"System Health"),(CAMERAS,"Cameras & Evidence")):
        require(surface,'rpc("wl_my_site_recorders"',f"{name} must load recorder owner summary")

    # System Health groups recorder root causes instead of duplicating an NVR
    # failure as unrelated camera faults.
    for needle in (
        "recorderIssues",
        "recorderIssueCameraIds",
        "Recorders available",
        'title={multiRecorder?"Recorders":"Camera system"}',
    ):
        require(HEALTH,needle,f"System Health missing multi-recorder grouping: {needle}")
    for unsafe in ("system.vendor","system.model","recorderName=[system.vendor"):
        if unsafe in HEALTH:
            raise AssertionError(f"System Health leaks recorder vendor/model detail: {unsafe}")

    # Camera evidence identity is camera UUID first; channel-only lookup is
    # forbidden once recorders can both have Channel 1.
    require(CAMERAS,"latestEventByCamera","Camera View must index recent events by camera identity")
    require(CAMERAS,'e.camera_id ? "camera:"',"Camera View must prefer camera_id")
    require(CAMERAS,'"recorder:" + String(e.recorder_id)',"Camera View recorder+channel fallback missing")
    if "latestEventByChannel" in CAMERAS:
        raise AssertionError("Camera View must not use channel-only recent-event identity")
    require(CAMERAS,"multiRecorder","Camera View must detect multi-recorder sites")
    require(CAMERAS,'"recorder-" + row.id',"Camera View must group cameras by recorder")
    require(CAMERAS,"recorderIssues","Camera View lead must surface recorder-level root causes")

    # The DB read model is authenticated-only and deliberately excludes the
    # sensitive/internal recorder fields the browser does not need.
    require(MIG,"create or replace function public.wl_my_site_recorders","owner recorder RPC missing")
    require(MIG,"v_tenant := public.wl_assert_my_site(p_site_id)","tenant/site authorization missing")
    require(MIG,"grant execute on function public.wl_my_site_recorders(uuid)\n  to authenticated","owner RPC must be authenticated-only")
    for forbidden in (
        "'vendor'","'model'","'driver'","'local_key'","'identity_fingerprint'","'capabilities'"
    ):
        owner_part=MIG.split("create or replace function public.wl_ai_context",1)[0]
        if forbidden in owner_part:
            raise AssertionError(f"owner recorder read model exposes internal field {forbidden}")

    # Existing context stays additive but gains canonical provenance required to
    # prevent overlapping-channel evidence mixups.
    require(MIG,"'facts_version','watchlog-ai-context-v6'","context version must reflect recorder provenance")
    require(MIG,"e.camera_id","recent events must expose canonical camera identity")
    require(MIG,"e.recorder_id","recent events must expose recorder identity")
    require(MIG,"'recorder_id',c.recorder_id","camera rows must expose recorder identity")

    print("Multi-recorder portal contract: PASS")
    return 0


if __name__=="__main__":
    raise SystemExit(main())

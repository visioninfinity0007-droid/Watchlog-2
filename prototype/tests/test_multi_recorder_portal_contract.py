#!/usr/bin/env python3
"""Static portal contract for customer-safe multi-recorder grouping."""
import re
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HEALTH=(ROOT/"portal/app/site-health/health-workspace.js").read_text(encoding="utf-8")
CAMERAS=(ROOT/"portal/app/control-room/customer-workspace.js").read_text(encoding="utf-8")
MIG=(ROOT/"prototype/supabase/migrations/0152_multi_recorder_owner_read_model.sql").read_text(encoding="utf-8")
VISUAL_QA=(ROOT/"portal/tests/visual-qa.mjs").read_text(encoding="utf-8")
# Context v7 is the first owner/AI context with recorder provenance (recorder rows, camera and
# event recorder_id, multi-recorder masking). Later additive versions must keep those guards.
RECORDER_PROVENANCE_CONTEXT=7


def latest_definition(function,migrations=ROOT/"prototype/supabase/migrations"):
    """Body of the last migration that (re)defines public.<function>, i.e. what the DB runs."""
    marker=re.compile(rf"create\s+or\s+replace\s+function\s+public\.{re.escape(function)}\s*\(",re.I)
    body=None
    for path in sorted(Path(migrations).glob("*.sql")):
        text=path.read_text(encoding="utf-8")
        matches=list(marker.finditer(text))
        if not matches:
            continue
        start=matches[-1].start()
        end=text.find("$function$;",start)
        body=text[start:end if end>0 else len(text)]
    if body is None:
        raise AssertionError(f"no migration defines public.{function}")
    return body


def check_latest_definition_lookup():
    """The lookup must see a later redefinition whatever its keyword case (0126/0128 use upper case)."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp,"0001_a.sql").write_text(
            "create or replace function public.wl_probe(p uuid)\nas $function$ begin 'v1'; end $function$;\n",encoding="utf-8")
        Path(tmp,"0002_b.sql").write_text(
            "CREATE OR REPLACE FUNCTION public.wl_probe(p uuid)\nAS $function$ begin 'v2'; end $function$;\n",encoding="utf-8")
        if "'v2'" not in latest_definition("wl_probe",tmp):
            raise AssertionError("latest_definition skipped an upper-case redefinition")


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
    # Assert the version floor and the provenance/masking behaviour of the context the DB actually
    # serves (the latest wl_ai_context), not one version literal (MNVR-052).
    check_latest_definition_lookup()
    ctx=latest_definition("wl_ai_context")
    flat=re.sub(r"\s+"," ",ctx)
    versions=re.findall(r"'facts_version',\s*'watchlog-ai-context-v(\d+)'",ctx)
    if len(versions)!=1:
        raise AssertionError(f"wl_ai_context must emit exactly one facts_version, found {versions}")
    if int(versions[0])<RECORDER_PROVENANCE_CONTEXT:
        raise AssertionError("context version must reflect recorder provenance (v7 or later)")
    require(ctx,"e.camera_id","recent events must expose canonical camera identity")
    require(ctx,"e.recorder_id","recent events must expose recorder identity")
    require(ctx,"'recorder_id',c.recorder_id","camera rows must expose recorder identity")
    require(ctx,"public.wl_my_site_recorders(p_site_id)","context recorders must come from the owner recorder read model")
    for guard,message in (
        (r"'recorder',case when v_recorder_count<=1 then v_diag->'recorder' else null end",
         "a multi-recorder site must not get a site-wide recorder identity"),
        (r"'capabilities',case when v_recorder_count<=1 then v_diag->'capabilities' else '\{\}'::jsonb end",
         "a multi-recorder site must not get a site-wide capability profile"),
        (r"'capability_known',case when v_recorder_count<=1 then coalesce\(\(v_diag->>'capability_known'\)::boolean,false\) else false end",
         "capability_known must be false on a multi-recorder site"),
    ):
        if not re.search(guard.replace(" ",r"\s*"),flat):
            raise AssertionError(message)
    # The visual-QA fixture must model the context version the DB serves.
    fixture=re.findall(r'facts_version:\s*"watchlog-ai-context-v(\d+)"',VISUAL_QA)
    if fixture and set(fixture)!={versions[0]}:
        raise AssertionError(f"visual-qa fixture context version {sorted(set(fixture))} != DB v{versions[0]}")

    print("Multi-recorder portal contract: PASS")
    return 0


if __name__=="__main__":
    raise SystemExit(main())

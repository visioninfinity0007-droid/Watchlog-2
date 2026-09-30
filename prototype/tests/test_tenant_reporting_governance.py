from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
WATCHLOG=(ROOT/"ai-harness/WATCHLOG.md").read_text(encoding="utf-8")
CONTEXT=(ROOT/"ai-harness/CONTEXT.md").read_text(encoding="utf-8")
OFFICE=(ROOT/"ai-harness/site-types/office.yaml").read_text(encoding="utf-8")
RESTAURANT=(ROOT/"ai-harness/site-types/restaurant.yaml").read_text(encoding="utf-8")
CHAI=(ROOT/"ai-harness/tenants/chaiwala-chota-bukhari/context.yaml").read_text(encoding="utf-8")
ALK=(ROOT/"ai-harness/tenants/al-khalid-main-site/context.yaml").read_text(encoding="utf-8")
HASCO=(ROOT/"ai-harness/tenants/hasco-steel-head-office/context.yaml").read_text(encoding="utf-8")
SKILL=(ROOT/"ai-harness/skills/tenant-intelligence-setup.md").read_text(encoding="utf-8")
LANGUAGE=(ROOT/"ai-harness/core/customer-language.md").read_text(encoding="utf-8")
SUMMARY_TEMPLATE=(ROOT/"ai-harness/tenants/_template/reporting/daily-reports/_template/summary.md").read_text(encoding="utf-8")
DETAILED_TEMPLATE=(ROOT/"ai-harness/tenants/_template/reporting/daily-reports/_template/detailed-report.md").read_text(encoding="utf-8")
METHOD=(ROOT/"ai-harness/tenants/chaiwala-chota-bukhari/reporting/methods/visual-snapshot-analysis.md").read_text(encoding="utf-8")
HOOK=(ROOT/"portal/app/reports/use-report.js").read_text(encoding="utf-8")
REPORT=(ROOT/"portal/app/reports/customer-workspace.js").read_text(encoding="utf-8")
MIG=(ROOT/"prototype/supabase/migrations/0129_office_reporting_context.sql").read_text(encoding="utf-8")

def test_every_ai_is_routed_through_the_harness():
    agents=(ROOT/"AGENTS.md").read_text(encoding="utf-8")
    assert "ai-harness/WATCHLOG.md" in agents and "Never disclose WatchLog's internal workings" in agents
    for pointer in ("CLAUDE.md","GEMINI.md",".github/copilot-instructions.md",".cursorrules"):
        assert "AGENTS.md" in (ROOT/pointer).read_text(encoding="utf-8"), pointer
    assert "Start here: route yourself" in WATCHLOG and "Every runtime AI and how it receives the harness" in WATCHLOG
    for method in ("people-counting.md","person-recognition.md","incident-video-analysis.md"):
        text=(ROOT/"ai-harness/methods"/method).read_text(encoding="utf-8")
        assert f"methods/{method}" in WATCHLOG and "**Customer status:**" in text and "## Customer-facing claims" in text

def test_customer_vocabulary_is_enforced_in_every_runtime():
    import yaml
    vocab=yaml.safe_load((ROOT/"ai-harness/core/customer-vocabulary.yaml").read_text(encoding="utf-8"))
    mig=(ROOT/"prototype/supabase/migrations/0141_customer_language_guard.sql").read_text(encoding="utf-8")
    # 0141 created the guard with the first rule set; later vocabulary changes reach the DB through the
    # generated seed (applied after every change), which must match the harness, same order.
    seed=(ROOT/"prototype/supabase/sql/customer_vocabulary_seed.generated.sql").read_text(encoding="utf-8")
    for i,rule in enumerate(vocab["forbidden"], start=1):
        assert f"('{rule['id']}', {i}, '{rule['pattern'].replace(chr(92)+'b', chr(92)+'y')}'" in seed, rule["id"]
    assert "delete from public.customer_vocabulary_rules where id not in" in seed
    assert "before insert or update of payload on public.report_snapshots" in mig
    assert "before insert or update of summary on public.visual_day_summaries" in mig
    vision=(ROOT/"prototype/supabase/functions/watchlog-vision-worker/index.ts").read_text(encoding="utf-8")
    assert "OWNER_TEXT_RULES" in vision and "Visual review completed for" not in vision
    worker=(ROOT/"prototype/vision_worker/worker.py").read_text(encoding="utf-8")
    assert "harness_rules.generated.json" in worker and "Be explicit that periodic snapshots" not in worker
    assert "harness_rules.generated.json" in (ROOT/"prototype/vision_worker/Dockerfile").read_text(encoding="utf-8")
    chat=(ROOT/"prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
    assert "applyCustomerVocabulary(" in chat and "customerCardData(" in chat
    assert "available snapshots were reviewed" not in chat

def test_customer_facing_copy_and_templates_carry_no_internal_wording():
    import re, yaml, glob
    vocab=yaml.safe_load((ROOT/"ai-harness/core/customer-vocabulary.yaml").read_text(encoding="utf-8"))
    rx=re.compile("|".join(f"(?:{r['pattern']})" for r in vocab["forbidden"]), re.I)
    for f in glob.glob(str(ROOT/"ai-harness/tenants/*/reporting/daily-reports/*/*.md")):
        text=re.sub(r"visual-snapshot-analysis\.md","",open(f,encoding="utf-8").read())
        assert not rx.search(text), f"{f}: {rx.search(text).group(0)}"
    site=" ".join(open(f,encoding="utf-8").read() for f in glob.glob(str(ROOT/"deploy/wordpress/themes/watchlog/*.php")))
    for stale in ("On-site AI","on-site filtering","detector classes","production detector","one still per incident","Filtered on site"):
        assert stale.lower() not in site.lower(), stale

def test_site_types_and_tenant_folders_are_governed():
    assert "id: office" in OFFICE and "status: IMPLEMENTED" in OFFICE
    assert "id: restaurant" in RESTAURANT and "status: IMPLEMENTED" in RESTAURANT
    assert "latest completed configured working day" in OFFICE
    assert "al_khalid_main_site" in ALK
    assert "hasco_steel_head_office" in HASCO
    assert "chaiwala_chota_bukhari" in CHAI
    assert "site-types/office.yaml" in CONTEXT
    assert "tenant-intelligence-setup.md" in WATCHLOG

def test_all_active_tenants_have_reporting_tree():
    for tenant in ("chaiwala-chota-bukhari","al-khalid-main-site","hasco-steel-head-office"):
        base=ROOT/"ai-harness/tenants"/tenant/"reporting"
        assert (base/"README.md").exists()
        assert (base/"daily-reports/README.md").exists()
        assert (base/"methods/visual-snapshot-analysis.md").exists()

def test_tenant_inherits_paths_resolve():
    for tenant in ("chaiwala-chota-bukhari","al-khalid-main-site","hasco-steel-head-office"):
        folder=ROOT/"ai-harness/tenants"/tenant
        text=(folder/"context.yaml").read_text(encoding="utf-8")
        parents=[]
        for l in text.split("inherits:",1)[1].splitlines()[1:]:
            if not l.strip().startswith("- "): break
            parents.append(l.strip()[2:].strip())
        assert parents, tenant
        for rel in parents:
            assert (folder/rel).resolve().exists(), f"{tenant}: inherits {rel} does not resolve"

def test_tenant_registry_and_compliance_records():
    registry=(ROOT/"ai-harness/tenants/README.md").read_text(encoding="utf-8")
    for tenant in ("chaiwala-chota-bukhari","al-khalid-main-site","hasco-steel-head-office"):
        assert f"`{tenant}/`" in registry
        audits=list((ROOT/"ai-harness/tenants"/tenant/"reporting/internal-audits").glob("*-harness-compliance.md"))
        assert audits, f"{tenant} has no harness compliance record"
    assert "never analyse or report as active tenants" in registry
    assert "tenants/README.md" in CONTEXT

def test_ai_function_brief_is_compiled_from_current_harness():
    import subprocess, sys
    check=subprocess.run([sys.executable, str(ROOT/"prototype/scripts/compile_harness_brief.py"), "--check"],
                         capture_output=True, text=True)
    assert check.returncode==0, check.stdout+check.stderr
    fn=(ROOT/"prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
    assert "harnessMessage(context)" in fn, "model messages must carry the harness + tenant brief"
    assert "external_text_egress_allowed" in fn, "text-only tenant consent must be honoured"

def test_known_evidence_gaps_are_governed_not_hidden():
    alk_method=(ROOT/"ai-harness/tenants/al-khalid-main-site/reporting/methods/visual-snapshot-analysis.md").read_text(encoding="utf-8")
    assert "camera_attribution_evidence" in ALK
    assert "Enumerate by evidence, not only by `is_canonical`" in alk_method
    assert "Never assume \"Camera N\" is recorder channel N" in alk_method
    assert "monitoring start, not staff arrival" in alk_method
    assert "timing_resolution_rule" in RESTAURANT
    assert "sampling_interval_below_target" in CHAI
    assert "db_camera_purpose_conflicts" in CHAI
    assert "mapping_status: configured_names_not_yet_visually_verified" in HASCO

def test_visual_method_requires_full_visual_pass_before_report():
    assert "List every snapshot in the window" in METHOD
    assert "Analyze every snapshot camera-by-camera and chronologically" in METHOD
    assert "Build sequences before derived metrics" in METHOD
    assert "Missing coverage = unknown, not zero" in METHOD
    assert "Model confidence is not measured accuracy" in METHOD

def test_tenant_setup_skill_reproduces_context_and_reporting_setup():
    for phrase in (
        "Inspect canonical Git and production DB before planning",
        "Determine the site type",
        "Validate camera reality",
        "Create ai-harness/tenants/<tenant-site>/context.yaml",
        "Create reporting/README.md",
        "Yesterday always means the latest completed configured working/service day",
        "Compare any mirror/handoff repo against canonical",
    ):
        assert phrase in SKILL

def test_portal_uses_business_day_not_calendar_yesterday():
    assert 'wl_my_last_completed_business_date' in HOOK
    assert 'OFFICE_PROMPTS' in HOOK
    assert 'wl_office_period' in HOOK
    assert 'p_working_only:true' in HOOK
    assert 'p_working_only:false' in HOOK
    assert 'OFFICE_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]]' in REPORT

def test_office_report_keeps_detection_truth():
    assert "Activity detections" in REPORT
    assert "not unique people" in REPORT
    assert "OfficePeriodReport" in REPORT
    assert "OfficeDayReport" in REPORT
    assert "Missing monitoring is missing evidence" in REPORT

def test_office_runtime_source_is_reproducible():
    assert "wl_my_last_completed_business_date" in MIG
    assert "wl_office_brief" in MIG
    assert "wl_office_period" in MIG
    assert "office_ops_v1" in MIG
    assert "latest completed configured working day" in MIG.lower()


def test_daily_report_templates_include_summary_detail_and_recommendations():
    assert "## Recommended actions" in SUMMARY_TEMPLATE
    assert "Source: detailed-report.md" in SUMMARY_TEMPLATE
    assert "## 7. Recommendations" in DETAILED_TEMPLATE
    assert "## 10. Traceability" in DETAILED_TEMPLATE
    for tenant in (CHAI, ALK, HASCO):
        assert "archive: ./reporting/daily-reports/" in tenant
        assert "visual_method: ./reporting/methods/visual-snapshot-analysis.md" in tenant


def test_customer_language_is_natural_and_report_recommendations_are_mandatory():
    assert "trusted security/operations manager" in LANGUAGE
    assert "Do not sound like a template, log parser or engineer." in LANGUAGE
    assert "Recommendations must appear in management reporting as well as conversational answers" in LANGUAGE
    assert "Do not generate generic filler recommendations" in LANGUAGE
    assert "customer-language.md" in WATCHLOG
    assert "customer-language.md" in CONTEXT


def test_office_brief_uses_configured_working_window_not_midnight_day():
    assert "p_date::timestamp+s.open_time" in MIG
    assert "s.close_time" in MIG
    assert "monitoring_coverage" in MIG
    assert "wl_assert_my_site(p_site_id)" in MIG
    assert "revoke execute on function public.wl_office_brief(uuid,date) from public,anon,authenticated" in MIG

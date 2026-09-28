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

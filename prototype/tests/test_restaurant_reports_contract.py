from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
REPORT=(ROOT/"portal/app/reports/customer-workspace.js").read_text(encoding="utf-8")
HOOK=(ROOT/"portal/app/reports/use-report.js").read_text(encoding="utf-8")
MIGRATION=(ROOT/"prototype/supabase/migrations/0121_restaurant_visual_analytics.sql").read_text(encoding="utf-8")
CONFIG=(ROOT/"prototype/supabase/tenant-config/chaiwala_restaurant_analytics.sql").read_text(encoding="utf-8")
RUNTIME=(ROOT/"prototype/supabase/migrations/0122_vision_worker_runtime.sql").read_text(encoding="utf-8")
SCHEDULE=(ROOT/"prototype/supabase/migrations/0123_schedule_vision_worker.sql").read_text(encoding="utf-8")
PREOPEN=(ROOT/"prototype/supabase/migrations/0124_restaurant_preopen_service_day.sql").read_text(encoding="utf-8")
CAPTURE=(ROOT/"prototype/supabase/migrations/0125_restaurant_server_capture_scheduler.sql").read_text(encoding="utf-8")
ALIGN=(ROOT/"prototype/supabase/migrations/0126_chaiwala_ai_context_alignment.sql").read_text(encoding="utf-8")
PERIOD=(ROOT/"prototype/supabase/migrations/0127_chaiwala_report_windows.sql").read_text(encoding="utf-8")
QUALITY=(ROOT/"prototype/supabase/migrations/0128_chaiwala_analytics_quality.sql").read_text(encoding="utf-8")
RESTAURANT_HARNESS=(ROOT/"ai-harness/site-types/restaurant.yaml").read_text(encoding="utf-8")
CHAI_HARNESS=(ROOT/"ai-harness/tenants/chaiwala-chota-bukhari.yaml").read_text(encoding="utf-8")
AGENT=(ROOT/"prototype/agent/analytics_agent.py").read_text(encoding="utf-8")


def test_restaurant_report_uses_real_service_day_rpc():
    assert 'rpc("wl_restaurant_day"' in HOOK
    assert 'rpc("wl_restaurant_site_config"' in HOOK
    assert "service_date" in HOOK


def test_restaurant_report_labels_camera_derived_metrics_honestly():
    for text in (
        "Peak visible diners",
        "Peak occupied tables",
        "Estimated covers",
        "Served table sessions",
        "Median observed time to food",
        "not unique footfall",
        "not POS data",
    ):
        assert text in REPORT
    assert "No estimates are being fabricated from unprocessed snapshots." in REPORT
    assert ">Footfall<" not in REPORT
    assert "Peak footfall" not in REPORT


def test_restaurant_storage_is_private_and_tenant_guarded():
    for table in (
        "restaurant_camera_profiles",
        "restaurant_tables",
        "restaurant_visual_observations",
        "restaurant_table_observations",
    ):
        assert f"alter table public.{table} enable row level security" in MIGRATION
        assert f"revoke all on public.{table} from public,anon,authenticated" in MIGRATION
    assert "wl_assert_my_site(p_site_id)" in MIGRATION
    assert "grant execute on function public.wl_restaurant_day(uuid,date) to authenticated" in MIGRATION


def test_restaurant_visual_contract_preserves_truth_boundaries():
    for text in (
        "visible_customers means currently visible customers, not unique footfall.",
        "food_present means visible food on a table; do not infer order correctness or food quality.",
        "Do not infer sales, revenue, staff identity, health diagnosis, or customer demographics.",
        "Use null when a requested field is not visually defensible.",
    ):
        assert text in MIGRATION


def test_chaiwala_config_is_reproducible_and_role_specific():
    for role in (
        "dining_floor",
        "service_handoff",
        "cash_counter",
        "kitchen",
        "service_access",
        "office_security",
    ):
        assert f"'{role}'" in CONFIG
    assert CONFIG.count(",4,'anchor_match'") == 23
    assert "'F1-01'" in CONFIG and "'F1-13'" in CONFIG
    assert "'F2-01'" in CONFIG and "'F2-10'" in CONFIG
    assert '"customer_footfall_available":false' in CONFIG


def test_saved_historical_report_remains_authoritative():
    assert "payload?.restaurant||rest" in HOOK


def test_vision_worker_schedule_respects_current_provider_capacity():
    assert "'* * * * *'" in SCHEDULE
    assert """body := '{"limit":1}'::jsonb""" in SCHEDULE
    assert "wl_vision_worker_cron_secret" in SCHEDULE


def test_preopen_overnight_service_day_stays_on_previous_service():
    assert "v_local_now::time < v_ctx.open_time" in PREOPEN
    assert "v_local_now::time<v_ctx.open_time" in PREOPEN
    assert "v_local_now::time < v_ctx.close_time" not in PREOPEN


def test_restaurant_rpcs_are_not_anonymous():
    assert "revoke execute on function public.wl_restaurant_day(uuid,date) from anon,public" in PREOPEN
    assert "revoke execute on function public.wl_restaurant_site_config(uuid) from anon,public" in PREOPEN
    assert "grant execute on function public.wl_restaurant_day(uuid,date) to authenticated,service_role" in PREOPEN


def test_restaurant_server_capture_requires_explicit_agent_support():
    assert '"config_snapshot_requests"' in AGENT
    assert "? 'config_snapshot_requests'" in CAPTURE
    assert "a.last_seen_at>now()-interval '5 minutes'" in CAPTURE
    assert "request_source='restaurant_analytics'" in CAPTURE
    assert "'source','restaurant_requested_snapshot'" in CAPTURE


def test_restaurant_server_capture_respects_camera_mode_and_hours():
    assert "p.sampling_mode in ('interval','hybrid')" in CAPTURE
    assert "where rn<=2" in CAPTURE
    assert "'30 seconds'" in CAPTURE
    assert "rp.sampling_mode='event'" in CAPTURE
    assert "coalesce(ev.payload->>'source','')='periodic_snapshot'" in CAPTURE


def test_chaiwala_shared_intelligence_contract_is_structured():
    assert '"schema": "restaurant-intelligence-context-v2"' in ALIGN or '"schema":"restaurant-intelligence-context-v2"' in ALIGN
    for metric in (
        "visible_diners",
        "occupied_tables",
        "table_utilization_pct",
        "estimated_table_sessions",
        "estimated_covers",
        "served_table_sessions",
        "observed_time_to_food_minutes",
        "minimum_observed_dwell_minutes",
        "kitchen_load",
        "handoff_load",
        "counter_active",
        "customer_footfall",
    ):
        assert metric in ALIGN
    assert '"total_table_anchors": 23' in ALIGN or '"total_table_anchors":23' in ALIGN
    assert '"available": false' in ALIGN or '"available":false' in ALIGN
    assert "unique customer count" in ALIGN
    assert "POS order-to-serve" in ALIGN


def test_chaiwala_capture_cadence_matches_request_capacity():
    assert "'dining_floor' then 60" in ALIGN
    assert "'service_handoff' then 90" in ALIGN
    assert "'kitchen' then 120" in ALIGN
    assert "'cash_counter' then 180" in ALIGN
    assert "(v_kitchen,v_tenant,v_site,'kitchen','interval',120,true" in CONFIG
    assert "(v_front,v_tenant,v_site,'service_handoff','hybrid',90,true" in CONFIG
    assert "(v_cash,v_tenant,v_site,'cash_counter','hybrid',180,true" in CONFIG


def test_restaurant_day_has_floor_totals_and_coverage_truth():
    assert "'floors',coalesce(v_floors,'[]'::jsonb)" in ALIGN
    assert "'business_analytics_coverage_ratio',v_business_coverage" in ALIGN
    assert "'camera_coverage',coalesce(v_camera_coverage,'[]'::jsonb)" in ALIGN
    assert "site_total_requires_all_dining_floors" in ALIGN
    assert "having count(*)=(" in ALIGN
    assert "o.camera_role='dining_floor'" in ALIGN


def test_chaiwala_has_four_tenant_specific_report_windows():
    assert 'RESTAURANT_VIEWS=[["daily","Today"],["yesterday","Yesterday"],["week","Last 7 days"],["monthly","Last 30 days"]]' in REPORT
    assert 'report_layout_profile==="chaiwala_restaurant_ops_v1"' in HOOK
    assert 'wl_restaurant_period' in HOOK
    assert 'p_days:7' in HOOK
    assert 'p_days:30' in HOOK


def test_restaurant_period_contract_contains_management_dimensions():
    assert "CREATE OR REPLACE FUNCTION public.wl_restaurant_period" in PERIOD
    assert "'schema','restaurant-period-v1'" in PERIOD
    for key in (
        "'daily',v_days",
        "'hour_profile'",
        "'floor_profile'",
        "'table_profile'",
        "'weekday_profile'",
        "'weekly_trend'",
        "'service_time_distribution'",
        "'previous_period'",
        "'comparison'",
    ):
        assert key in PERIOD
    assert "p_days<2 or p_days>31" in PERIOD
    assert "wl_assert_my_site(p_site_id)" in PERIOD


def test_period_report_truth_and_previous_period_comparison():
    assert "Visible diners are concurrent visible people on dining-floor cameras, not unique footfall." in PERIOD
    assert "Estimated covers and table sessions are camera-derived estimates." in PERIOD
    assert "Observed time to food is seated/occupied to first food visible, not POS order-to-serve time." in PERIOD
    assert "Missing observation periods are missing coverage, not zero business activity." in PERIOD
    assert "'estimated_covers_pct'" in PERIOD
    assert "'served_sessions_pct'" in PERIOD
    assert "'median_time_to_food_delta_minutes'" in PERIOD
    assert "'coverage_delta_points'" in PERIOD


def test_chaiwala_report_layout_has_day_week_and_month_sections():
    for text in (
        "Peak visible diners",
        "Peak occupied tables",
        "Estimated covers",
        "Analytics coverage",
        "Floor comparison",
        "Table utilization",
        "7-day operations review",
        "30-day management review",
        "Demand by hour",
        "Weekday pattern",
        "Observed service-time distribution",
        "Most-used calibrated tables",
        "Lower-utilization tables",
    ):
        assert text in REPORT
    assert "Missing observation periods are missing coverage, not zero activity." in REPORT
    assert "Period-to-period changes should only be acted on when coverage is sufficiently comparable." in REPORT


def test_quality_summary_requires_repeated_evidence_and_no_fake_accuracy():
    assert "CREATE OR REPLACE FUNCTION public.wl_restaurant_quality_summary" in QUALITY
    assert "v_c.samples>=3" in QUALITY
    assert "glare_frames::numeric/v_c.samples>=0.20" in QUALITY
    assert "occlusion_frames::numeric/v_c.samples>=0.20" in QUALITY
    assert "obstruction_frames::numeric/v_c.samples>=0.15" in QUALITY
    assert "low_people_confidence_frames::numeric/v_c.samples>=0.25" in QUALITY
    assert "not_calibrated_against_human_ground_truth" in QUALITY
    assert "'can_publish_accuracy_percentage',false" in QUALITY
    assert "manual count validation sample" in QUALITY


def test_day_and_period_reports_include_analytics_quality():
    assert "restaurant-day-v3" in QUALITY
    assert "restaurant-period-v2" in QUALITY
    assert "'analytics_quality',public.wl_restaurant_quality_summary" in QUALITY
    assert "restaurant-analytics-quality-v1" in QUALITY
    assert "watchlog-ai-context-v5" in QUALITY


def test_report_ui_has_camera_quality_chart_and_improvement_recommendations():
    for text in (
        "Analytics quality & improvement recommendations",
        "People count",
        "Table tracking",
        "Glare risk",
        "Occlusion",
        "Angle quality",
        "Recommended improvement",
        "Customer-count accuracy:",
    ):
        assert text in REPORT
    assert "qualityTrack" in REPORT
    assert "improvementCard" in REPORT
    assert "Quality scoring is waiting for processed restaurant frames." in REPORT


def test_chaiwala_quality_context_is_tenant_specific_and_reproducible():
    assert '"schema": "restaurant-vision-v3"' in CONFIG or '"schema":"restaurant-vision-v3"' in CONFIG
    assert "analytics_quality and improvement recommendations" in QUALITY
    assert "Do not publish a customer-count accuracy percentage" in QUALITY
    assert "Repeated glare or overexposure" in QUALITY



def test_restaurant_harness_is_governed_and_reusable():
    assert "id: restaurant" in RESTAURANT_HARNESS
    assert "status: IMPLEMENTED" in RESTAURANT_HARNESS
    assert "semantic_policy: ai_harness" in RESTAURANT_HARNESS
    for metric in (
        "visible_diners",
        "occupied_tables",
        "estimated_table_sessions",
        "estimated_covers",
        "served_table_sessions",
        "observed_time_to_food_minutes",
        "customer_footfall",
    ):
        assert metric in RESTAURANT_HARNESS
    assert "Model confidence is not measured accuracy." in RESTAURANT_HARNESS
    assert "combined_group" in RESTAURANT_HARNESS


def test_chaiwala_harness_overlay_matches_runtime_contract():
    assert "id: chaiwala_chota_bukhari" in CHAI_HARNESS
    assert "inherits:" in CHAI_HARNESS and "../site-types/restaurant.yaml" in CHAI_HARNESS
    assert 'canonical_open: "16:00"' in CHAI_HARNESS
    assert 'canonical_close: "04:00"' in CHAI_HARNESS
    assert "total_calibrated_anchors: 23" in CHAI_HARNESS
    assert "unique_footfall_available: false" in CHAI_HARNESS
    assert "not_calibrated_against_human_ground_truth" in CHAI_HARNESS
    assert "owner_reported_unverified" in CHAI_HARNESS
    assert "chaiwala_restaurant_ops_v1" in CHAI_HARNESS
    assert "runtime_facts:" in CHAI_HARNESS and "source: watchlog_database" in CHAI_HARNESS


def test_runtime_migrations_preserve_vision_truth_and_egress_boundaries():
    assert "external_egress_allowed=true" in RUNTIME
    assert "wl_vision_worker_expected_secret" in RUNTIME
    assert "wl_vision_claim_snapshots_v2" in RUNTIME
    assert "p_provider_external" in RUNTIME
    assert "restaurant_intelligence_context" in ALIGN
    assert "v_profile.analytics_role <> 'dining_floor'" in ALIGN
    assert "v_profile.analytics_role <> 'kitchen'" in ALIGN
    assert "v_profile.analytics_role <> 'service_handoff'" in ALIGN
    assert "v_profile.analytics_role <> 'cash_counter'" in ALIGN

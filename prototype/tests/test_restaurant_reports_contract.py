from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
REPORT=(ROOT/"portal/app/reports/customer-workspace.js").read_text(encoding="utf-8")
HOOK=(ROOT/"portal/app/reports/use-report.js").read_text(encoding="utf-8")
MIGRATION=(ROOT/"prototype/supabase/migrations/0115_restaurant_visual_analytics.sql").read_text(encoding="utf-8")
CONFIG=(ROOT/"prototype/supabase/tenant-config/chaiwala_restaurant_analytics.sql").read_text(encoding="utf-8")


def test_restaurant_report_uses_real_service_day_rpc():
    assert 'rpc("wl_restaurant_day"' in HOOK
    assert 'rpc("wl_restaurant_site_config"' in HOOK
    assert "Asia/Karachi" not in HOOK
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
    assert CONFIG.count("'anchor_match'") == 23
    assert "'F1-01'" in CONFIG and "'F1-13'" in CONFIG
    assert "'F2-01'" in CONFIG and "'F2-10'" in CONFIG
    assert '"customer_footfall_available":false' in CONFIG

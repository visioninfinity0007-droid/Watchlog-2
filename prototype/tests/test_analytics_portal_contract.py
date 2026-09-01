#!/usr/bin/env python3
"""Static release contract for the Analytics / Site Health portal increment.

This complements runtime rule-engine tests. It catches the high-impact drift that
originally let a frontend ship ahead of its schema and let technical line
crossings lose their business meaning.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = (ROOT / "prototype/supabase/migrations/0030_analytics_semantics_authz.sql").read_text()
STUDIO = (ROOT / "portal/app/analytics/studio/page.js").read_text()
OVERVIEW = (ROOT / "portal/app/analytics/page.js").read_text()
SHELL = (ROOT / "portal/app/shell.js").read_text()
HEALTH = (ROOT / "portal/app/site-health/page.js").read_text()


def check() -> None:
    # Semantic identity lives on rules AND measurements and drives aggregation.
    assert "add column if not exists analytic_key text" in MIG
    assert "analytic_events_key_time_idx" in MIG
    assert "ae.analytic_key='visitor_flow'" in MIG
    assert "ae.analytic_key='vehicle_flow'" in MIG
    assert "ae.analytic_key='after_hours'" in MIG

    # Configuration is manager-only while read surfaces stay tenant-readable.
    assert "wl_analytics_require_manager" in MIG
    assert "m.role in ('owner','admin')" in MIG
    assert "'can_manage',public.wl_analytics_can_manage(v_tenant)" in MIG

    # Site Health is a first-class platform capability, not an AI/video rule.
    assert "'always_on',jsonb_build_array" in MIG
    assert "Site Health','note','Always on" in MIG
    assert "rule_type<>'health'" in MIG
    assert '["Site Health", "/site-health/"]' in SHELL
    assert 'Nav active="Site Health"' in HEALTH
    assert 'rpc("wl_portal_overview"' in HEALTH
    assert 'rpc("wl_sites"' in HEALTH

    # Studio explicitly sends business meaning; it does not make the DB infer it.
    assert 'rpc("wl_upsert_monitoring_rule_v2"' in STUDIO
    assert "p_analytic_key: rule.analyticKey" in STUDIO
    assert "purpose_recommendations" in STUDIO
    assert "studio?.can_manage !== false" in STUDIO

    # All Analytics screens expose one consistent secondary navigation.
    for src in (STUDIO, OVERVIEW):
        assert 'href="/analytics/schedules/"' in src


def main() -> int:
    check()
    print("Analytics portal contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Static release contract for the Analytics / Site Health portal increment.

This complements runtime rule-engine tests. It catches high-impact drift between
business-facing Analytics Setup and the schema semantics that actually power it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = (ROOT / "prototype/supabase/migrations/0030_analytics_semantics_authz.sql").read_text()
STUDIO = (ROOT / "portal/app/analytics/studio/page.js").read_text()
OVERVIEW = (ROOT / "portal/app/analytics/page.js").read_text()
# AI-first: the portal nav moved to nav-config.js (MAIN_TABS/MORE_TABS on the productRail), and each
# route's page.js is a thin re-export of its *-workspace.js. Read the nav source and the Site Health
# workspace where the capability is actually implemented. Navigability + guards are unchanged.
NAV = (ROOT / "portal/app/nav-config.js").read_text()
HEALTH = (ROOT / "portal/app/site-health/health-workspace.js").read_text()


def check() -> None:
    assert "add column if not exists analytic_key text" in MIG
    assert "analytic_events_key_time_idx" in MIG
    assert "ae.analytic_key='visitor_flow'" in MIG
    assert "ae.analytic_key='vehicle_flow'" in MIG
    assert "ae.analytic_key='after_hours'" in MIG

    assert "wl_analytics_require_manager" in MIG
    assert "m.role in ('owner','admin')" in MIG
    assert "'can_manage',public.wl_analytics_can_manage(v_tenant)" in MIG

    assert "'always_on',jsonb_build_array" in MIG
    assert "Site Health','note','Always on" in MIG
    assert "rule_type<>'health'" in MIG
    assert '"/site-health/"' in NAV and '"Site Health"' in NAV   # Site Health remains navigable (productRail)
    assert 'Nav active="Site Health"' in HEALTH
    # AI-first Site Health draws on the tenant-scoped AI context + sites (was wl_portal_overview).
    assert 'rpc("wl_ai_context"' in HEALTH
    assert 'rpc("wl_sites"' in HEALTH

    assert 'rpc("wl_upsert_monitoring_rule_v2"' in STUDIO
    assert "p_analytic_key:rule.analyticKey" in STUDIO or "p_analytic_key: rule.analyticKey" in STUDIO
    assert "purpose_recommendations" in STUDIO
    assert "studio?.can_manage!==false" in STUDIO or "studio?.can_manage !== false" in STUDIO

    # Geometry remains anchored to a fresh camera image even though the UI now
    # uses customer-facing words such as image, line and area instead of
    # implementation terms such as configuration still / geometry.
    assert "snapshotCapturedAt" in STUDIO
    assert "data.captured_at!==previous" in STUDIO or "data.captured_at !== previousCapturedAt" in STUDIO
    assert "Refresh camera image" in STUDIO
    assert "Request a current camera image before drawing a line or area." in STUDIO
    assert "Request a current camera image before adding this activity rule." in STUDIO   # renamed: "analytics setup" -> "activity rule"
    assert "setRule((r)=>r?({...r,points:[]}):r)" in STUDIO or "setRule((r) => r ? ({ ...r, points: [] }) : r)" in STUDIO
    assert "<polygon" in STUDIO
    assert "Save activity rule" in STUDIO   # renamed: "analytics setup" -> "activity rule"

    # Report scheduling is reachable: linked from the analytics setup surface (Studio), and the
    # read-only overview links through to Studio.
    assert 'href="/analytics/schedules/"' in STUDIO
    assert "analytics/studio" in OVERVIEW


def main() -> int:
    check()
    print("Analytics portal contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

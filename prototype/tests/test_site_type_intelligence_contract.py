"""Phase 28 - Site-Type Intelligence Profiles contract.

Tenant -> Site -> Site Type -> Intelligence Profile. The portal composes Home, Insights and Reports
from the site type, the configured camera purposes and governed evidence. Irrelevant modules are not
rendered, inactivity claims need verified coverage, and recommendations never assert a cause.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "portal" / "app"
PROFILES = (APP / "owner/site-profiles.js").read_text(encoding="utf-8")
MODULES = (APP / "owner/site-modules.js").read_text(encoding="utf-8")
HOME = (APP / "home/customer-workspace.js").read_text(encoding="utf-8")
INSIGHTS = (APP / "analytics/page.js").read_text(encoding="utf-8")
REPORTS = (APP / "reports/customer-workspace.js").read_text(encoding="utf-8")
HOOK = (APP / "reports/use-report.js").read_text(encoding="utf-8")
ASK = (APP / "ai/customer-prompts.js").read_text(encoding="utf-8")
SETUP = (APP / "setup/camera-setup.js").read_text(encoding="utf-8")
MIGRATION = (ROOT / "prototype/supabase/migrations/0143_site_period_facts.sql").read_text(encoding="utf-8")
EDGE = (ROOT / "prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
HARNESS = ROOT / "ai-harness/site-types"
COMPILER = (ROOT / "prototype/scripts/compile_harness_brief.py").read_text(encoding="utf-8")
MATRIX = ROOT / "docs/product/site-type-intelligence-matrix.md"


def test_derivation_unit_checks_pass_in_node():
    node = shutil.which("node")
    assert node, "node is required for the site-profile unit checks"
    with tempfile.TemporaryDirectory() as tmp:
        mod = Path(tmp) / "site-profiles.mjs"
        shutil.copy(APP / "owner/site-profiles.js", mod)
        r = subprocess.run([node, str(ROOT / "prototype/tests/site_profiles_unit.mjs"), mod.as_uri()],
                           capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checks passed" in r.stdout


def test_every_surface_composes_from_the_site_profile():
    for name, src in (("home", HOME), ("insights", INSIGHTS), ("reports", REPORTS)):
        assert "deriveSiteDay(" in src, name
        assert "SiteOperations" in src, name
        assert "SiteRailFacts" in src, name
    # One registry selects the profile and composer on every surface, restaurant included.
    for name, src in (("home", HOME), ("insights", INSIGHTS), ("reports hook", HOOK)):
        assert "selectSiteProfile(" in src, name
        assert 'includes(profile.key)' not in src and '"office","warehouse","factory","retail"]' not in src, name
    assert 'profile.composer==="restaurant"' in HOME.replace("const restaurantComposer=", "") or "restaurantComposer=profile.composer===\"restaurant\"" in HOME
    assert 'composer: "restaurant"' in PROFILES and 'requires: "restaurant_config"' in PROFILES
    assert 'selected.composer==="restaurant"' in INSIGHTS and 'selected.composer==="restaurant"' in HOOK
    # One governed day dataset feeds the derivation everywhere.
    assert 'wl_my_daily_intelligence' in INSIGHTS and 'wl_ai_context' in INSIGHTS and 'wl_ai_context' in HOOK


def test_reports_cover_all_business_site_types_without_breaking_office():
    assert 'const businessType=selected.composer==="business"?selected.key:null;' in HOOK
    assert "OFFICE_PROMPTS" in HOOK and "typePrompts(businessType)" in HOOK
    assert "}else if(r.isOffice){" in REPORTS  # office-model reporting branch, now shared by business types
    assert 'include={["timeline","zones","gaps","logistics","vehicles","checkout"]}' in REPORTS
    for phrase in ("Dock activity is not shipments or orders", "Activity is not production output",
                   "Activity is not sales, transactions or unique customers", "do not assert a cause"):
        assert phrase in HOOK, phrase


def test_modules_are_gated_by_type_and_evidence():
    assert 'profile.key === "retail" && <SiteCheckout' in MODULES
    assert '(profile.key === "warehouse" || profile.key === "factory") && <SiteVehicles' in MODULES
    assert 'profile.key === "office" && <SiteAfterHours' in MODULES
    assert 'if (!(profile.gapZones || []).length) return null;' in MODULES
    assert "if (!day.timeline.length) return null;" in MODULES
    # Irrelevant or empty modules are not rendered as zero.
    assert "if (!zones.some(z => z.episodes > 0)) return null;" in MODULES
    assert 'unknown: "Not available"' in MODULES


def test_recommendations_investigate_and_never_assert_a_cause():
    assert '"Cause not known"' in MODULES
    actions = " ".join(part.split("};")[0] for part in MODULES.split("const GAP_ACTION")[1:])
    lines = re.findall(r'[a-z_]+: "([^"]+)"', actions)
    assert len(lines) >= 5
    for line in lines:
        assert line.startswith("Check whether"), line
    banned = re.compile(r"\b(because|caused by|due to|staff were|workers were|production (fell|dropped)|sales (fell|rose))\b", re.I)
    # The profiles' own `prohibited:` lists name these phrases on purpose; everything else is customer copy.
    profiles_copy = re.sub(r"prohibited: \[[^\]]*\]", "", PROFILES)
    for name, src in (("modules", MODULES), ("profiles", profiles_copy)):
        visible = " ".join(x for line in src.splitlines() for x in re.findall(r'"([^"]{12,})"', line))
        assert not banned.search(visible), (name, banned.search(visible))


def test_vehicle_and_checkout_claims_are_scoped():
    assert "not separate vehicles, not time on site" in MODULES
    assert "no sales, transactions or conversion are inferred" in MODULES
    assert "NOT site time" in PROFILES


def test_inactivity_claims_require_verified_coverage():
    assert "coverage?.fullyVerified" in PROFILES
    assert 'out.gapsBlocked = { reason: "overlap" }' in PROFILES
    assert "return gs === null || ge === null ||" in PROFILES
    assert "daily.meta?.partial_day" in PROFILES
    assert "Quiet periods can't be confirmed for this day." in MODULES


def test_period_facts_are_read_only_by_their_own_profile():
    # Portal pages never call wl_office_period directly: the profile names its period source and schema.
    for name, src in (("home", HOME), ("insights", INSIGHTS), ("reports hook", HOOK)):
        assert 'sb.rpc("wl_office_period"' not in src, name
        assert ".period.rpc" in src, name
    for name, src in (("home", HOME), ("insights", INSIGHTS), ("reports", REPORTS)):
        assert "acceptPeriod(" in src and "periodMeasure(" in src, name
        assert ".activity_detections" not in src, name
    assert 'rpc: "wl_office_period", schema: "office-period-v1"' in PROFILES
    assert PROFILES.count('rpc: "wl_site_period", schema: "site-period-v1"') == 3
    # Non-office day reports never read the office brief.
    assert 'profile&&profile.key!=="office"?siteNeutralDay(data,siteDay):null' in REPORTS
    assert 'profile?.key === "office" ? (daily.office || {}) : {}' in PROFILES
    # Ask WatchLog (repo-side) gives warehouse/factory/retail the site-neutral period, offices keep theirs.
    assert 'rpcOptional(sb, "wl_site_period"' in EDGE and 'site_period: null' in EDGE


def test_starts_are_observations_unless_verified():
    assert '" began at " + day.opening.at' in MODULES and "day.opening.verified" in MODULES
    assert '"First observed " + noun.toLowerCase() + " was at "' in MODULES
    assert "Activity began at" not in MODULES.replace("// ", "")
    assert "out.opening.verified = !out.opening.low" in PROFILES
    assert "known.length > 0 && !before" in PROFILES
    assert '"First observed activity"' in REPORTS and "not proof of the operating start" in REPORTS


def test_quiet_periods_are_observations_with_an_explicit_unvalidated_threshold():
    assert "quietPeriod: { minutes: 45, validated: false }" in PROFILES
    assert PROFILES.count("quietPeriod: { minutes: 45, validated: false }") == 2
    assert 'title="Notable quiet periods"' in MODULES
    assert "an observation, not a finding of downtime or delay" in MODULES
    for banned in ("Operational gaps", "Inactive periods", "Longest inactive period", "operational interruption"):
        assert banned not in MODULES, banned
    allowed = ("not a finding of downtime or delay", "not proof of delay", "not downtime")
    for name, src in (("modules", MODULES), ("profiles", PROFILES), ("hook", HOOK), ("reports", REPORTS)):
        copy = re.sub(r"prohibited: \[[^\]]*\]", "", src)
        for line in copy.splitlines():
            if line.strip().startswith("//"):
                continue
            for a in allowed:
                line = line.replace(a, "")
            for text in re.findall(r'"([^"]*)"', line):
                assert not re.search(r"\b(downtime|delayed|lost productivity)\b", text, re.I), (name, text)


def test_ask_and_setup_follow_the_site_type():
    assert "configuredCapabilities" in ASK and "askQuestions" in ASK
    assert '"What happened overnight?"' in ASK  # fallback for unknown/general sites
    assert "purposeOptions" in SETUP
    assert '<option value="">Choose area</option>' in SETUP
    assert "Choose an area for every monitored camera before saving." in SETUP
    assert "p_purpose:c.purpose||null" in SETUP
    assert "never silently lost" in SETUP


def test_site_period_migration_is_site_neutral_and_tenant_scoped():
    sql = MIGRATION
    code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    # New site-neutral function; wl_office_period is not redefined (office clients unchanged).
    assert "create or replace function public.wl_site_period(" in code
    assert "function public.wl_office_period" not in code
    assert "'schema', 'site-period-v1'" in code and "office-period-v1" not in code
    # Never reads the office brief or emits office semantics.
    assert "->'office'" not in code and "'office'" in code  # 'office' only as an accepted site type
    assert "office activity" not in code.lower() and "office conclusions" not in code.lower()
    assert "if v_type not in ('office','warehouse','factory','retail') then" in code
    assert "'restaurant_period'" in code and "'site_type_not_profiled'" in code
    # Authorization preserved: tenant assertion, tenant-filtered reads, governed per-day reads.
    assert "v_tenant uuid := public.wl_assert_my_site(p_site_id);" in code
    assert "where id = p_site_id and tenant_id = v_tenant" in code
    assert "public.wl_my_daily_intelligence(p_site_id, v_cursor)" in code
    assert code.count("security definer") == 1 and "set search_path = public" in code
    # Narrowest ACL for the real callers: customer RPC -> authenticated only; helpers -> owner only.
    assert "revoke all on function public.wl_site_period(uuid,integer,boolean) from public, anon, service_role;" in code
    assert "grant execute on function public.wl_site_period(uuid,integer,boolean) to authenticated;" in code
    assert "revoke all on function public.wl_site_day_facts(jsonb) from public, anon, authenticated, service_role;" in code
    assert "revoke all on function public.wl_site_period_summary(jsonb) from public, anon, authenticated, service_role;" in code
    assert len(re.findall(r"grant execute on function", code)) == 1
    # wl_assert_my_site is not redefined here (its privileged service_role contract stays as in 0122).
    assert "function public.wl_assert_my_site" not in code
    assert not re.search(r"\b(insert into|update public\.|delete from|drop function|alter table)\b", code.lower())
    # LIVE / RECOVERED / UNVERIFIED stay separate at day and period level.
    assert code.count("'recovered_seconds'") >= 2 and code.count("'unverified_seconds'") >= 2
    # No unique-people / vehicles / sales / production / throughput claims in emitted text.
    notes = code.split("'measurement_notes'")[1].split(")\n  );")[0].lower()
    assert "not unique people or vehicles" in notes and "no business cause, production, throughput, sales or attendance" in notes
    # Real execution, exact ACL and tenant isolation run on the disposable Postgres in CI.
    assert "python prototype/tests/e2e_site_period_pg.py" in CI
    e2e = (ROOT / "prototype/tests/e2e_site_period_pg.py").read_text(encoding="utf-8")
    for proof in ("aclexplode(", 'set local role {role}', '"anon", "select wl_site_period', '"service_role", "select wl_site_period',
                  "tenant A CANNOT read tenant B's period facts", "tenant B CAN query its own", "warehouse gets site-period-v1",
                  'got == {"authenticated"}', "got == set()", "service_role contract unchanged"):
        assert proof in e2e, proof


def test_harness_profiles_exist_for_every_type_and_are_not_copies():
    texts = {}
    for t in ("office", "warehouse", "factory", "retail", "restaurant"):
        p = HARNESS / f"{t}.yaml"
        assert p.exists(), t
        texts[t] = p.read_text(encoding="utf-8")
        assert f"id: {t}" in texts[t]
        for section in ("owner_questions", "metric_status", "prohibited_interpretations"):
            assert section in texts[t], (t, section)
        assert f'"{t}"' in COMPILER or f"'{t}'" in COMPILER
    assert "loading_dock" in texts["warehouse"] and "checkout" not in texts["warehouse"].split("camera_roles")[1].split("\n\n")[0]
    assert "production_zone" in texts["factory"]
    assert "checkout" in texts["retail"]
    assert len({texts[t] for t in ("warehouse", "factory", "retail")}) == 3


def test_metric_matrix_is_documented():
    doc = MATRIX.read_text(encoding="utf-8")
    for state in ("Implemented", "Derivable", "Requires journey logic", "Field-gated", "External data required"):
        assert state in doc, state
    for t in ("Office", "Warehouse", "Factory", "Retail", "Restaurant"):
        assert "## " + t in doc, t


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print(f"site-type intelligence contract: {len(tests)} checks passed")
    sys.exit(0)

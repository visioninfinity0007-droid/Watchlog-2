#!/usr/bin/env python3
"""Static contract for the WatchLog owner-first portal design system."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "portal" / "app"

shell = (APP / "shell.js").read_text(encoding="utf-8")
nav = (APP / "nav-config.js").read_text(encoding="utf-8")
entry = (APP / "page.js").read_text(encoding="utf-8")
mobile = (APP / "mobile-launcher.js").read_text(encoding="utf-8")
layout = (APP / "layout.js").read_text(encoding="utf-8")
icons = (APP / "icons.js").read_text(encoding="utf-8")
system_css = (APP / "portal-system.css").read_text(encoding="utf-8")
home_css = (APP / "home" / "home.module.css").read_text(encoding="utf-8")
attention_css = (APP / "notifications" / "notifications.module.css").read_text(encoding="utf-8")
ai_css = (APP / "ai" / "customer.module.css").read_text(encoding="utf-8")
analytics = (APP / "analytics" / "page.js").read_text(encoding="utf-8")
ai_workspace = (APP / "ai" / "customer-workspace.js").read_text(encoding="utf-8")
evidence = (APP / "incidents" / "legacy.js").read_text(encoding="utf-8")
health = (APP / "site-health" / "health-workspace.js").read_text(encoding="utf-8")
home = (APP / "home" / "customer-workspace.js").read_text(encoding="utf-8")
attention = (APP / "notifications" / "customer-workspace.js").read_text(encoding="utf-8")
settings_hook = (APP / "settings" / "use-customer-settings.js").read_text(encoding="utf-8")
settings_sites = (APP / "settings" / "site-list.js").read_text(encoding="utf-8")
error_helper = (ROOT / "portal" / "lib" / "supabase.js").read_text(encoding="utf-8")

problems = []

for token in (
    '["Home","/home/","Home"]',
    '["Attention","/notifications/","Notifications"]',
    '["Insights","/analytics/","Analytics"]',
    '["Reports","/reports/?view=yesterday","Reports"]',
    '["Ask WatchLog","/ai/","WatchLog AI"]',
):
    if token.replace(" ", "") not in nav.replace(" ", "").replace("\n", ""):
        problems.append(f"owner primary navigation missing: {token}")

if 'location.replace(tenant ? "/home/" : "/onboarding/")' not in entry:
    problems.append("signed-in customer entry must resolve to /home/")

if "PortalIcon" not in shell or 'from "./icons"' not in shell:
    problems.append("portal navigation must use the canonical PortalIcon component")

for name in (
    "home","attention","insights","reports","ask","cameras & evidence",
    "system health","camera settings","saved video","team","setup & support","more"
):
    if f'key==="{name}"' not in icons:
        problems.append(f"canonical icon missing: {name}")

if "productMobileBottom" not in shell:
    problems.append("mobile owner navigation missing")
if "productMobileLauncher" in mobile or "☰" in mobile:
    problems.append("duplicate mobile hamburger must not return; bottom More is the secondary navigation entry")
if 'onClick={()=>setMobileOpen(true)}' not in shell:
    problems.append("mobile More must open the secondary navigation drawer")

for stylesheet in ('import "./portal-system.css";', 'import "./auth-system.css";'):
    if stylesheet not in layout:
        problems.append(f"root layout missing design layer: {stylesheet}")

for token in ("--portal-violet", "--portal-canvas", ".target-page-head", ".productMobileBottom"):
    if token not in system_css:
        problems.append(f"owner portal design-system token/rule missing: {token}")

for surface_name, source, token in (
    ("Home", home_css, ".primary,.secondary{flex:1;min-height:44px!important}"),
    ("Attention", attention_css, ".actions .markAll,.filterBar button,.openAction,.readAction{min-height:44px!important}"),
    ("Ask WatchLog", ai_css, ".suggestionRow a{min-height:44px;display:inline-flex;align-items:center}"),
    ("Ask WatchLog composer", ai_css, ".composer button{width:44px!important;height:44px!important;min-width:44px!important}"),
):
    if token not in source:
        problems.append(f"{surface_name} mobile touch-target contract missing: {token}")

for phrase in ("Activity insights are not configured yet", "missing measurements into zero activity", 'aria-label="Choose insight period"', "7-day pattern", "Reliable comparison not ready yet", "Previous period:"):
    if phrase not in analytics:
        problems.append(f"Insights truth/interaction contract missing: {phrase}")
for token in ("structured_restaurant_metrics", "observed_service_days", "estimated_covers_pct", "median_time_to_food_delta_minutes", "coverage_delta_points"):
    if token not in analytics:
        problems.append(f"restaurant Insights must use governed rolling-period data: {token}")
if "Chai Wala" in analytics:
    problems.append("Insights must not hard-code one tenant/site name into the reusable restaurant surface")
if 'Intl.DateTimeFormat("en-PK"' not in analytics or 'timeZone:"UTC"' not in analytics:
    problems.append("Insights completed service dates must use deterministic Pakistan-English date formatting")
for phrase in ('role="log"', 'role="alert"', 'aria-controls="watchlog-context-rail"', 'aria-expanded={!railCollapsed}'):
    if phrase not in ai_workspace:
        problems.append(f"Ask WatchLog interaction semantics missing: {phrase}")

for phrase in ("wl_ai_site_egress","wl_ai_set_site_text_egress","wl_ai_set_site_egress"):
    if phrase not in settings_hook:
        problems.append(f"owner privacy control missing governed backend contract: {phrase}")
for phrase in ("Ask WatchLog privacy","Written site information","Camera evidence"):
    if phrase not in settings_sites:
        problems.append(f"owner privacy control missing: {phrase}")
if 'kind==="text"&&privacy.evidence&&!allowed' not in settings_hook:
    problems.append("text-only consent must not appear independently disabled while broader evidence consent remains enabled")

if '["Incidents","/incidents/","Incidents"]' in nav:
    problems.append("Incidents must not compete as a primary/More destination; use Attention then drill down")

for phrase in ("Camera evidence", "Camera event history", "Available for review"):
    if phrase not in evidence:
        problems.append(f"camera evidence semantic copy missing: {phrase}")
for phrase in ("<strong>{rows.length}</strong>incident", "<small>Detection source</small>", "<small>Review state</small>"):
    if phrase in evidence:
        problems.append(f"camera event surface collapses semantic layers: {phrase}")

for phrase in ("Monitoring is not fully verified", "recording states are still unverified", "Unknown states are not treated as healthy"):
    if phrase not in health:
        problems.append(f"system-health truth language missing: {phrase}")

for phrase in ('rpc("wl_my_report_window"', "What changed", "A reliable comparison is not ready yet", "Missing periods are not treated as zero activity"):
    if phrase not in home:
        problems.append(f"owner Home governed-comparison contract missing: {phrase}")
for surface_name, source in (("Home", home), ("Attention", attention)):
    for phrase in ('Intl.DateTimeFormat("en-PK"', 'timeZone:timeZone||"Asia/Karachi"', "· site time"):
        if phrase not in source:
            problems.append(f"{surface_name} site-local timestamp contract missing: {phrase}")
if 'estimated_covers_pct' not in home or 'median_time_to_food_delta_minutes' not in home:
    problems.append("owner Home comparison must use governed period deltas, not raw detector counts")

for phrase in ("statement timeout", "schema cache", "WatchLog could not complete that request. Try again."):
    if phrase not in error_helper:
        problems.append(f"customer-safe error mapping missing: {phrase}")
if "return m" in error_helper:
    problems.append("shared customer error helper must not fall through to raw backend messages")

if problems:
    raise SystemExit("Portal design-system contract failed:\n- " + "\n- ".join(problems))
print("portal design-system contract: PASS")

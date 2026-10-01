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
activity_studio = (APP / "analytics" / "studio" / "page.js").read_text(encoding="utf-8")
control_legacy = (APP / "control-room" / "legacy.js").read_text(encoding="utf-8")
camera_view = (APP / "control-room" / "customer-workspace.js").read_text(encoding="utf-8")
setup_details = (APP / "setup" / "site-details.js").read_text(encoding="utf-8")
setup_cameras = (APP / "setup" / "camera-setup.js").read_text(encoding="utf-8")
setup_review = (APP / "setup" / "review-setup.js").read_text(encoding="utf-8")
setup_connect = (APP / "setup" / "connect-site.js").read_text(encoding="utf-8")
reports = (APP / "reports" / "customer-workspace.js").read_text(encoding="utf-8")
unified_report = (APP / "reports" / "unified-restaurant-report.js").read_text(encoding="utf-8")
incidents = (APP / "incidents" / "customer-workspace.js").read_text(encoding="utf-8")
archive_history = (APP / "archive" / "saved-video-history.js").read_text(encoding="utf-8")
archive_detail = (APP / "archive" / "saved-video-detail.js").read_text(encoding="utf-8")
archive_hook = (APP / "archive" / "use-saved-video.js").read_text(encoding="utf-8")
archive_form = (APP / "archive" / "saved-video-form.js").read_text(encoding="utf-8")
archive_history = (APP / "archive" / "saved-video-history.js").read_text(encoding="utf-8")
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
    ("Attention filter layout", attention_css, ".filterBar{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;overflow:visible}"),
    ("Ask WatchLog", ai_css, ".suggestionRow a{min-height:44px;display:inline-flex;align-items:center}"),
    ("Ask WatchLog composer", ai_css, ".composer button{width:44px!important;height:44px!important;min-width:44px!important}"),
    ("Ask WatchLog nav clearance", ai_css, ".composerWrap{bottom:calc(64px + env(safe-area-inset-bottom));padding:10px 12px 13px}"),
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

for phrase in ('Intl.DateTimeFormat("en-PK"', 'site?.timezone', "· site time"):
    if phrase not in activity_studio:
        problems.append(f"Activity Rules site-time contract missing: {phrase}")
if "snapshotCapturedAt).toLocaleString(" in activity_studio:
    problems.append("Activity Rules must not render camera-image timestamps in browser-local time")
if 'setStamp(new Date().toISOString())' not in control_legacy or 'updated ${ago(stamp)}' not in control_legacy:
    problems.append("multi-site advanced control refresh indicator must use timezone-neutral relative time")
if "toLocaleTimeString(" in control_legacy:
    problems.append("multi-site advanced control must not imply one browser-local clock across sites")
if 'c.purpose || "general"' in camera_view:
    problems.append("Cameras & Evidence must not display an unknown camera purpose as General")
for token in ('"Purpose not set"', 'type="button"', 'aria-busy={busy === c.id}'):
    if token not in camera_view:
        problems.append(f"Cameras & Evidence interaction/truth contract missing: {token}")

for forbidden in ('useState("08:00")','useState("19:00")','p_overnight:false'):
    if forbidden in setup_details:
        problems.append(f"Guided Setup must not invent customer business hours: {forbidden}")
for token in ('const overnight=close<open', 'p_overnight:overnight', 'WatchLog will not assume them.', 'aria-pressed={siteType===v}', 'aria-pressed={days.includes(n)}'):
    if token not in setup_details:
        problems.append(f"Guided Setup business-hours truth contract missing: {token}")
for forbidden in ('p_purpose:c.purpose||"general"', 'value={c.purpose||"general"}'):
    if forbidden in setup_cameras:
        problems.append(f"Guided Setup must not invent a camera purpose: {forbidden}")
for token in ('<option value="">Choose area</option>', 'Choose an area for every monitored camera before saving.', 'p_purpose:c.purpose||null'):
    if token not in setup_cameras:
        problems.append(f"Guided Setup camera-purpose truth contract missing: {token}")
for source_name, source in (("Camera setup", setup_cameras), ("Review setup", setup_review), ("Connect site", setup_connect)):
    if "<button onClick=" in source:
        problems.append(f"{source_name} action buttons must use explicit button types")

for phrase in ("wl_ai_site_egress","wl_ai_set_site_text_egress","wl_ai_set_site_egress"):
    if phrase not in settings_hook:
        problems.append(f"owner privacy control missing governed backend contract: {phrase}")
for phrase in ("Ask WatchLog privacy","Written site information","Camera evidence"):
    if phrase not in settings_sites:
        problems.append(f"owner privacy control missing: {phrase}")
if 'kind==="text"&&privacy.evidence&&!allowed' not in settings_hook:
    problems.append("text-only consent must not appear independently disabled while broader evidence consent remains enabled")

for source_name, source in (("Reports", reports), ("Unified restaurant report", unified_report)):
    if "Chai Wala" in source:
        problems.append(f"{source_name} must remain tenant-neutral")
    if 'Intl.DateTimeFormat("en-PK"' not in source or 'timeZone:"UTC"' not in source:
        problems.append(f"{source_name} must use deterministic Pakistan-English service-date formatting")
if "4 PM–4 AM" in reports or "4 PM-4 AM" in reports:
    problems.append("reusable Reports must not hard-code one restaurant service window")
for source_name, source in (("Incident review", incidents), ("Saved Video", archive_history), ("Settings sites", settings_sites)):
    if 'Intl.DateTimeFormat("en-PK"' not in source or "· site time" not in source:
        problems.append(f"{source_name} must render customer timestamps in site-local time")

if '["Incidents","/incidents/","Incidents"]' in nav:
    problems.append("Incidents must not compete as a primary/More destination; use Attention then drill down")

for phrase in ("Camera evidence", "Camera event history", "Available for review"):
    if phrase not in evidence:
        problems.append(f"camera evidence semantic copy missing: {phrase}")
for source_name, source in (("Camera evidence", evidence), ("Saved Video detail", archive_detail)):
    if 'Intl.DateTimeFormat("en-PK"' not in source or "· site time" not in source:
        problems.append(f"{source_name} must render event/evidence timestamps in site-local time")
if "zoneFor(row)" not in evidence:
    problems.append("multi-site camera evidence must resolve timezone per event/site")
for token in ("inputInZone", "zonedDate", 'timezone||"Asia/Karachi"'):
    if token not in archive_hook:
        problems.append(f"Saved Video request-window timezone conversion missing: {token}")
if "new Date(form.from)" in archive_hook or "new Date(form.to)" in archive_hook:
    problems.append("Saved Video must not interpret datetime-local inputs in the viewer browser timezone")
if "Times are interpreted in this site’s local time" not in archive_form:
    problems.append("Saved Video must explain that request windows use site-local time")
for token in ('role="button" tabIndex={0} aria-pressed={s.id===settings.siteId}', 'onKeyDown={e=>{if(e.key==="Enter"||e.key===" ")'):
    if token not in settings_sites:
        problems.append(f"Settings site selector keyboard contract missing: {token}")
for token in ('role="tablist" aria-label="Reporting period"', 'role="tab" aria-selected={r.view===k}', 'aria-pressed={choice===code}'):
    if token not in reports:
        problems.append(f"Reports selection semantics missing: {token}")
if '<button type="button" className="ghost small"' not in archive_history:
    problems.append("Saved Video history action must use an explicit button type")
if 'type="button" className={styles.searchButton}' not in archive_form or 'aria-busy={v.busy}' not in archive_form:
    problems.append("Saved Video search action must expose explicit button/busy semantics")
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

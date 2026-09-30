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
evidence = (APP / "incidents" / "legacy.js").read_text(encoding="utf-8")
health = (APP / "site-health" / "health-workspace.js").read_text(encoding="utf-8")
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

for phrase in ("statement timeout", "schema cache", "WatchLog could not complete that request. Try again."):
    if phrase not in error_helper:
        problems.append(f"customer-safe error mapping missing: {phrase}")
if "return m" in error_helper:
    problems.append("shared customer error helper must not fall through to raw backend messages")

if problems:
    raise SystemExit("Portal design-system contract failed:\n- " + "\n- ".join(problems))
print("portal design-system contract: PASS")

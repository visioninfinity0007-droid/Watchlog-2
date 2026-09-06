#!/usr/bin/env python3
"""Static contract for portal, reporting and Windows-installer alignment.

Customer wording has its own contract. This test therefore protects behavior,
authorization, structure and release integration without pinning old engineering
phrases into customer-facing pages.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS=(ROOT/"portal/app/reports/page.js").read_text()
SETTINGS=(ROOT/"portal/app/settings/page.js").read_text()
TEAM=(ROOT/"portal/app/team/page.js").read_text()
ONBOARD=(ROOT/"portal/app/onboarding/page.js").read_text()
DASH=(ROOT/"portal/app/dashboard/page.js").read_text()
INCIDENTS=(ROOT/"portal/app/incidents/page.js").read_text()
SITE_HEALTH=(ROOT/"portal/app/site-health/page.js").read_text()
LAYOUT=(ROOT/"portal/app/layout.js").read_text()
VISUAL=(ROOT/"portal/app/visual-target.css").read_text()
MODULE_VISUAL=(ROOT/"portal/app/module-target.css").read_text()
RECIP=(ROOT/"prototype/supabase/migrations/0031_report_recipient_destinations.sql").read_text()
AUTHZ=(ROOT/"prototype/supabase/migrations/0032_portal_operational_authz.sql").read_text()
HEALTH=(ROOT/"prototype/supabase/migrations/0033_site_health_details.sql").read_text()
ENROLL=(ROOT/"prototype/supabase/migrations/0034_enrollment_code_read_authz.sql").read_text()
BILLING_AUTH=(ROOT/"prototype/supabase/migrations/0035_billing_read_authz.sql").read_text()
BILLING_POLICY_FIX=(ROOT/"prototype/supabase/migrations/0036_billing_owner_policy_execution.sql").read_text()
NSIS=(ROOT/"prototype/installer/nsis/watchlog.nsi").read_text()
REGISTER=(ROOT/"prototype/installer/register-service.ps1").read_text()
BUILD=(ROOT/"tools/build_windows_release.ps1").read_text()
AGENT_BUILD=(ROOT/"prototype/agent/build_exe.ps1").read_text()
RELEASE_ENTRY=(ROOT/"prototype/agent/release_agent.py").read_text()
WRAPPER=(ROOT/"tools/make_installer.ps1").read_text()
SETUP=(ROOT/"prototype/agent/setup_wizard.py").read_text()
AN_SETUP=(ROOT/"prototype/agent/analytics_setup.py").read_text()
DAILY_REPORT=(ROOT/"prototype/reporter/daily_report.py").read_text()
REPORT_SERVICE=(ROOT/"prototype/reporter/serve.py").read_text()
SEED=(ROOT/"tools/seed_demo.py").read_text()


def check():
    # WhatsApp+Email remains one UX choice backed by two provider-specific rows.
    assert "wl_add_recipient_v2" in RECIP
    assert "p_channel in ('whatsapp','both')" in RECIP
    assert "p_channel in ('email','both')" in RECIP
    assert "'whatsapp',v_wa" in RECIP and "'email',v_email" in RECIP
    assert "requires separate destinations" in RECIP
    assert 'rpc("wl_add_recipient_v2"' in REPORTS
    assert "p_whatsapp:" in REPORTS and "p_email:" in REPORTS
    assert '[["whatsapp","WhatsApp"],["email","Email"],["both","WhatsApp + Email"]]' in REPORTS
    assert "Report recipients" in REPORTS
    assert "WatchLog daily report preview" in REPORTS
    assert "This preview intentionally shows no sample counts." in REPORTS

    # One canonical daily-report composition path includes Analytics intelligence.
    assert "def _compose_security" in DAILY_REPORT
    assert "return analytics_reporting.compose(_compose_security, report)" in DAILY_REPORT
    assert "return analytics_reporting.render_html(_render_base_html, report, portal_url)" in DAILY_REPORT
    assert "monkey-patch" in REPORT_SERVICE.lower() and "does not monkey-patch" in REPORT_SERVICE.lower()
    assert "analytics_reporting" not in REPORT_SERVICE.replace("does not monkey-patch", "")

    # Viewer UI and server write permissions remain aligned.
    assert "Account &amp; Plan" in SETTINGS and "Sites &amp; Setup" in SETTINGS
    assert "canOperate" in SETTINGS and "canBill" in SETTINGS
    assert "copyCode" in SETTINGS and "Setup code copied" in SETTINGS
    assert "canOperate&&s.open_code" in SETTINGS and "Code ready" in SETTINGS
    assert "wl_require_role(array['owner','admin'])" in AUTHZ
    assert "create or replace function public.wl_add_site" in AUTHZ
    assert "create or replace function public.wl_issue_code" in AUTHZ
    assert "wl_require_role(array['owner','admin'])" in RECIP
    assert "ROLE_COPY" in TEAM and "navigator.clipboard.writeText" in TEAM
    assert "Revoke this pending invitation" in TEAM
    assert "v_can_manage boolean" in ENROLL
    assert "'has_open_code', agg.open_code is not null" in ENROLL
    assert "case when v_can_manage then agg.open_code else null end" in ENROLL
    assert "ec.tenant_id = v_tenant" in ENROLL

    # Billing fails closed; detailed financial state remains Owner-only.
    assert 'NEXT_PUBLIC_BILLING_PROVIDER||""' in SETTINGS
    assert 'NEXT_PUBLIC_BILLING_PROVIDER||"mock"' not in SETTINGS
    assert "billingConfigured" in SETTINGS
    assert "Online plan changes are not available" in SETTINGS
    assert 'if(nextRole==="owner")' in SETTINGS
    assert "Invoices &amp; agreements" in SETTINGS
    assert "create or replace function public.wl_is_owner" in BILLING_AUTH
    assert "m.role = 'owner'" in BILLING_AUTH
    for table in ("billing_customers","subscriptions","payment_transactions","billing_checkouts"):
        assert table in BILLING_AUTH and table in BILLING_POLICY_FIX
    assert "using (public.wl_is_owner(tenant_id))" in BILLING_AUTH
    assert "tenant_id = public.wl_my_tenant()" in BILLING_POLICY_FIX
    assert "public.wl_my_role() = 'owner'" in BILLING_POLICY_FIX
    assert "grant execute on function public.wl_is_owner" not in BILLING_POLICY_FIX
    assert "v_tenant uuid := wl_require_role(array['owner'])" in BILLING_AUTH

    # Visual target remains wired into the actual portal.
    assert 'import "./visual-target.css"' in LAYOUT
    assert 'import "./module-target.css"' in LAYOUT
    assert ".navlink.active{background:var(--wl-blue)" in VISUAL
    assert "backdrop-filter:blur(18px)" in VISUAL
    assert ".overview-grid" in VISUAL and ".incident-workspace" in VISUAL
    assert ".report-layout" in VISUAL and ".health-ring" in VISUAL
    assert "analytics_chartVisitor" in MODULE_VISUAL and "var(--wl-ice)" in MODULE_VISUAL
    assert "portal_tabActive" in MODULE_VISUAL and "var(--wl-blue)" in MODULE_VISUAL

    # Core customer surfaces retain their real APIs and review hierarchy.
    assert 'rpc("wl_portal_overview"' in DASH and 'rpc("wl_portal_snapshot"' in DASH
    assert "overview-grid" in DASH and "overview-shots" in DASH and "overview-event-bars" in DASH
    assert "Review incidents" in DASH and "Site Health" in DASH
    assert 'rpc("wl_incidents"' in INCIDENTS and 'rpc("wl_portal_snapshot"' in INCIDENTS
    assert "incident-workspace" in INCIDENTS and "incident-detail" in INCIDENTS
    assert "aria-pressed" in INCIDENTS
    assert "modalBackdrop" not in INCIDENTS

    # Site Health uses tenant-scoped detail data and exposes the four useful signals.
    assert "create or replace function public.wl_site_health_details" in HEALTH
    assert "where c.tenant_id = v_tenant" in HEALTH
    assert "where e.tenant_id = v_tenant" in HEALTH
    assert "last_activity_at" in HEALTH
    assert "event_type in ('video_loss','tamper','disk_error','disk_full','offline')" in HEALTH
    assert 'rpc("wl_site_health_details"' in SITE_HEALTH
    assert "health-site-grid" in SITE_HEALTH and "health-ring" in SITE_HEALTH
    assert "Camera activity" in SITE_HEALTH and "Last reported activity" in SITE_HEALTH
    assert "Site connections" in SITE_HEALTH and "Camera-system issues" in SITE_HEALTH

    # Demo fixtures are explicitly demo data, never customer data.
    for name in ("Karachi Head Office","Korangi Warehouse","Landhi Factory Floor"):
        assert name in SEED
    assert '"demo":True' in SEED or '"demo": True' in SEED
    assert "Rear Perimeter intentionally has no event in the last 24h" in SEED
    assert '"video_loss"' in SEED
    assert "demo+sample@watchlog.test" in SEED
    assert '"report_deliveries"' in SEED

    # Onboarding and the Windows installer still describe the same real workflow.
    assert "Nothing to install" not in ONBOARD
    assert "Download WatchLog for Windows" in ONBOARD
    assert "same network" in ONBOARD
    assert "setup code" in ONBOARD.lower()
    assert "normal internet connection" in ONBOARD
    assert "setup-progress-row" in ONBOARD
    assert '"Done"' in ONBOARD and '"In progress"' in ONBOARD and '"Pending"' in ONBOARD
    assert '[1/4]' not in SETUP
    for title in ("Find the recorder","Verify the recorder login","Discover the cameras","Link this site to WatchLog"):
        assert title in SETUP
    assert "Monitoring context (optional, recommended)" in AN_SETUP

    # Background agent and branded GUI remain separate release surfaces.
    assert '$entry = "agent\\release_agent.py"' in AGENT_BUILD
    assert '_ORIGINAL_SETUP = app.analytics_setup.run' in RELEASE_ENTRY
    assert 'raise SystemExit(1)' in RELEASE_ENTRY
    assert 'app.enhanced_cmd_run = _setup_validation_complete' in RELEASE_ENTRY
    assert '"--setup" in sys.argv' in RELEASE_ENTRY
    assert 'File "watchlog-setup-ui.exe"' in NSIS
    gui_exec='ExecWait \'"$INSTDIR\\watchlog-setup-ui.exe" --config "$INSTDIR\\watchlog.ini"\''
    assert gui_exec in NSIS
    assert "WatchLog setup exited with code $0" in NSIS
    assert 'watchlog-agent.exe" --setup' not in NSIS

    # Installer completion is atomic before Add/Remove Programs registration.
    setup_pos=NSIS.index(gui_exec)
    task_exec='ExecWait \'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\\register-service.ps1"'
    task_pos=NSIS.index(task_exec)
    arp_pos=NSIS.index('WriteRegStr HKLM "${ARPKEY}" "DisplayName"')
    assert setup_pos < arp_pos and task_pos < arp_pos
    assert 'StrCpy $8 "0"' in NSIS and '/Query /TN "${TASKNAME}"' in NSIS and '/Run /TN "${TASKNAME}"' in NSIS
    assert 'watchlog.env' in NSIS and NSIS.index('watchlog.env') < task_pos
    assert "Register-ScheduledTask -TaskName $task" in REGISTER
    assert "-Force | Out-Null" in REGISTER
    assert "did not reach Running state" in REGISTER
    assert "schtasks /Delete" not in REGISTER
    assert "authoritative NSIS installer" in REGISTER

    version_match=re.search(r'!define APPVERSION "([0-9]+\.[0-9]+\.[0-9]+)"',NSIS)
    assert version_match
    app_version=version_match.group(1)
    assert f'VIProductVersion "{app_version}.0"' in NSIS
    assert '!define PUBLISHER "Vision Infinity"' in NSIS
    assert '!include "LogicLib.nsh"' in NSIS
    assert "watchlog.example" not in NSIS and "watchlog.pk" not in NSIS
    assert '[string]$PublisherUrl = ""' in BUILD and '[string]$PublisherUrl = ""' in WRAPPER
    assert "if ($PublisherUrl)" in BUILD and "/DPUBLISHER_URL=$PublisherUrl" in BUILD
    assert "NSIS only" in BUILD and "makensis" in BUILD.lower()
    assert "build_windows_release.ps1" in WRAPPER
    assert not (ROOT/"prototype/installer/watchlog.iss").exists()

    # Final release signing order remains correct.
    sign_agent=BUILD.index("Sign-WatchLogArtifact $agentExe")
    stage_agent=BUILD.index('Copy-Item $agentExe (Join-Path $stage "watchlog-agent.exe")')
    sign_ui=BUILD.index("Sign-WatchLogArtifact $setupUiExe")
    stage_ui=BUILD.index('Copy-Item $setupUiExe (Join-Path $stage "watchlog-setup-ui.exe")')
    sign_setup=BUILD.index("Sign-WatchLogArtifact $setup")
    final_hash=BUILD.index("Get-FileHash $setup -Algorithm SHA256")
    assert sign_agent < stage_agent and sign_ui < stage_ui and sign_setup < final_hash
    assert 'Get-AuthenticodeSignature -FilePath $Path' in BUILD
    assert 'signature verification failed' in BUILD
    assert "FINAL SHA256" in BUILD


def main():
    check()
    print("Portal alignment contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

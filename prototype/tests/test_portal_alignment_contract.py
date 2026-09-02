#!/usr/bin/env python3
"""Static contract for the portal / reporting / installer alignment release."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = (ROOT / "portal/app/reports/page.js").read_text()
SETTINGS = (ROOT / "portal/app/settings/page.js").read_text()
TEAM = (ROOT / "portal/app/team/page.js").read_text()
ONBOARD = (ROOT / "portal/app/onboarding/page.js").read_text()
DASH = (ROOT / "portal/app/dashboard/page.js").read_text()
INCIDENTS = (ROOT / "portal/app/incidents/page.js").read_text()
SITE_HEALTH = (ROOT / "portal/app/site-health/page.js").read_text()
RECIP = (ROOT / "prototype/supabase/migrations/0031_report_recipient_destinations.sql").read_text()
AUTHZ = (ROOT / "prototype/supabase/migrations/0032_portal_operational_authz.sql").read_text()
HEALTH = (ROOT / "prototype/supabase/migrations/0033_site_health_details.sql").read_text()
ENROLL = (ROOT / "prototype/supabase/migrations/0034_enrollment_code_read_authz.sql").read_text()
BILLING_AUTH = (ROOT / "prototype/supabase/migrations/0035_billing_read_authz.sql").read_text()
NSIS = (ROOT / "prototype/installer/nsis/watchlog.nsi").read_text()
REGISTER = (ROOT / "prototype/installer/register-service.ps1").read_text()
BUILD = (ROOT / "tools/build_windows_release.ps1").read_text()
AGENT_BUILD = (ROOT / "prototype/agent/build_exe.ps1").read_text()
RELEASE_ENTRY = (ROOT / "prototype/agent/release_agent.py").read_text()
WRAPPER = (ROOT / "tools/make_installer.ps1").read_text()
SETUP = (ROOT / "prototype/agent/setup_wizard.py").read_text()
AN_SETUP = (ROOT / "prototype/agent/analytics_setup.py").read_text()
DAILY_REPORT = (ROOT / "prototype/reporter/daily_report.py").read_text()
REPORT_SERVICE = (ROOT / "prototype/reporter/serve.py").read_text()
SEED = (ROOT / "tools/seed_demo.py").read_text()


def check() -> None:
    # Reports: BOTH is a UX convenience that becomes two provider-specific rows.
    assert "wl_add_recipient_v2" in RECIP
    assert "p_channel in ('whatsapp','both')" in RECIP
    assert "p_channel in ('email','both')" in RECIP
    assert "'whatsapp',v_wa" in RECIP
    assert "'email',v_email" in RECIP
    assert "requires separate destinations" in RECIP
    assert 'rpc("wl_add_recipient_v2"' in REPORTS
    assert "p_whatsapp:" in REPORTS and "p_email:" in REPORTS
    assert "Number, then edit for email" not in REPORTS
    assert "independent delivery endpoints" in REPORTS
    assert "07:00, site time" not in REPORTS
    assert "Actual dispatch time comes from the deployed reporting schedule" in REPORTS

    # Daily reporting has one canonical composition path. CLI/manual invocation
    # and the scheduled service must both include Analytics Site Intelligence.
    assert "def _compose_security" in DAILY_REPORT
    assert "return analytics_reporting.compose(_compose_security, report)" in DAILY_REPORT
    assert "return analytics_reporting.render_html(_render_base_html, report, portal_url)" in DAILY_REPORT
    assert "monkey-patch" in REPORT_SERVICE.lower() and "does not monkey-patch" in REPORT_SERVICE.lower()
    assert "analytics_reporting" not in REPORT_SERVICE.replace("does not monkey-patch", "")

    # Viewer UI and server write permissions tell the same story.
    assert "Account &amp; Plan" in SETTINGS and "Sites &amp; Setup" in SETTINGS
    assert "canOperate" in SETTINGS and "canBill" in SETTINGS
    assert "copyCode" in SETTINGS and "Enrollment code copied" in SETTINGS
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

    # Billing fails closed and detailed financial records are Owner-only.
    assert 'NEXT_PUBLIC_BILLING_PROVIDER||""' in SETTINGS
    assert 'NEXT_PUBLIC_BILLING_PROVIDER||"mock"' not in SETTINGS
    assert "billingConfigured" in SETTINGS
    assert "No plan change has been created" in SETTINGS
    assert 'if(nextRole==="owner")' in SETTINGS
    assert "Financial records are visible only to an Owner" in SETTINGS
    assert "create or replace function public.wl_is_owner" in BILLING_AUTH
    assert "m.role = 'owner'" in BILLING_AUTH
    for table in ("billing_customers", "subscriptions", "payment_transactions", "billing_checkouts"):
        assert table in BILLING_AUTH
    assert "using (public.wl_is_owner(tenant_id))" in BILLING_AUTH
    assert "v_tenant uuid := wl_require_role(array['owner'])" in BILLING_AUTH

    # Core portal surfaces expose the richer review hierarchy, not raw tables only.
    assert "Review Site Health" in DASH
    assert "humanType" in DASH and "loading still" in DASH
    assert "modalBackdrop" in INCIDENTS and "Review" in INCIDENTS

    # Site Health has a dedicated tenant-scoped detail API and exposes all four
    # signals sold by the product: site, agent, camera activity and faults.
    assert "create or replace function public.wl_site_health_details" in HEALTH
    assert "where c.tenant_id = v_tenant" in HEALTH
    assert "where e.tenant_id = v_tenant" in HEALTH
    assert "last_activity_at" in HEALTH and "event_type in ('video_loss','tamper','disk_error','disk_full','offline')" in HEALTH
    assert 'rpc("wl_site_health_details"' in SITE_HEALTH
    assert "Camera activity" in SITE_HEALTH and "Last reported activity" in SITE_HEALTH
    assert "Site Agents" in SITE_HEALTH and "Recorder & camera faults" in SITE_HEALTH

    # Dedicated demo tenant is useful but cannot masquerade as customer data.
    for name in ("Karachi Head Office", "Korangi Warehouse", "Landhi Factory Floor"):
        assert name in SEED
    assert '"demo":True' in SEED or '"demo": True' in SEED
    assert "Rear Perimeter intentionally has no event in the last 24h" in SEED
    assert '"video_loss"' in SEED
    assert "demo+sample@watchlog.test" in SEED
    assert '"report_deliveries"' in SEED

    # Onboarding and installer are one story and one release technology.
    assert "Nothing to install" not in ONBOARD
    assert "Windows installer" in ONBOARD
    assert "same network" in ONBOARD
    assert "outbound" in ONBOARD.lower()
    assert '[1/4]' not in SETUP  # step numbers are generated, not copied strings
    for title in ("Find the recorder", "Verify the recorder login", "Discover the cameras", "Link this site to WatchLog"):
        assert title in SETUP
    assert "Monitoring context (optional, recommended)" in AN_SETUP

    # The packaged explicit setup command is strict and finite. A cancelled
    # setup must fail NSIS; a successful one reaches enrollment/camera sync in
    # core.main and then returns instead of entering the infinite run loop.
    assert '$entry = "agent\\release_agent.py"' in AGENT_BUILD
    assert '_ORIGINAL_SETUP = app.analytics_setup.run' in RELEASE_ENTRY
    assert 'raise SystemExit(1)' in RELEASE_ENTRY
    assert 'app.enhanced_cmd_run = _setup_validation_complete' in RELEASE_ENTRY
    assert '"--setup" in sys.argv' in RELEASE_ENTRY
    assert 'ExecWait \'"$INSTDIR\\watchlog-agent.exe" --setup\'' in NSIS
    assert "Recorder/setup validation exited with code $0" in NSIS

    # Installer completion is atomic at the Windows registration layer: an
    # incomplete fresh install is not written into Add/Remove Programs, and an
    # upgrade stops/resumes the previous task rather than deleting it first.
    setup_pos = NSIS.index('ExecWait \'"$INSTDIR\\watchlog-agent.exe" --setup\'')
    task_pos = NSIS.index('register-service.ps1')
    arp_pos = NSIS.index('WriteRegStr HKLM "${ARPKEY}" "DisplayName"')
    assert setup_pos < arp_pos
    assert task_pos < arp_pos
    assert 'StrCpy $8 "0"' in NSIS and '/Query /TN "${TASKNAME}"' in NSIS
    assert 'attempting to resume the previous background task' in NSIS
    assert "Register-ScheduledTask -TaskName $task" in REGISTER
    assert "-Force | Out-Null" in REGISTER
    assert "did not reach Running state" in REGISTER
    assert "schtasks /Delete" not in REGISTER
    assert "authoritative NSIS installer" in REGISTER

    assert '!define APPVERSION "0.3.0"' in NSIS
    assert 'VIProductVersion "0.3.0.0"' in NSIS
    assert '!define PUBLISHER "Vision Infinity"' in NSIS
    assert '!include "LogicLib.nsh"' in NSIS
    assert "watchlog.example" not in NSIS and "watchlog.pk" not in NSIS
    assert '[string]$PublisherUrl = ""' in BUILD and '[string]$PublisherUrl = ""' in WRAPPER
    assert "if ($PublisherUrl)" in BUILD and "/DPUBLISHER_URL=$PublisherUrl" in BUILD
    assert "NSIS only" in BUILD
    assert "makensis" in BUILD.lower()
    assert "build_windows_release.ps1" in WRAPPER
    assert not (ROOT / "prototype/installer/watchlog.iss").exists()

    # Release signing is applied to BOTH executables and the checksum is of the
    # final distributable bytes, after Authenticode has changed them.
    sign_agent = BUILD.index("Sign-WatchLogArtifact $exe")
    stage_agent = BUILD.index('Copy-Item $exe (Join-Path $stage "watchlog-agent.exe")')
    sign_setup = BUILD.index("Sign-WatchLogArtifact $setup")
    final_hash = BUILD.index("Get-FileHash $setup -Algorithm SHA256")
    assert sign_agent < stage_agent
    assert sign_setup < final_hash
    assert 'Get-AuthenticodeSignature -FilePath $Path' in BUILD
    assert 'signature verification failed' in BUILD
    assert "FINAL SHA256" in BUILD


def main() -> int:
    check()
    print("Portal alignment contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

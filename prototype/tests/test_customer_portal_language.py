from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

# Every customer-facing route governed by the finished-product language contract.
CUSTOMER_FILES = [
    "portal/app/shell.js",
    "portal/app/ai/page.js",
    "portal/app/dashboard/page.js",
    "portal/app/control-room/page.js",
    "portal/app/control-room/reports/page.js",
    "portal/app/incidents/page.js",
    "portal/app/operations/page.js",
    "portal/app/executive/page.js",
    "portal/app/archive/page.js",
    "portal/app/site-health/page.js",
    "portal/app/analytics/page.js",
    "portal/app/analytics/studio/page.js",
    "portal/app/analytics/schedules/page.js",
    "portal/app/reports/page.js",
    "portal/app/team/page.js",
    "portal/app/settings/page.js",
    "portal/app/onboarding/page.js",
    "portal/app/account-suspended/page.js",
]

FORBIDDEN = [
    "Site Agent",
    "enrollment code",
    "Enrollment code",
    "release artifact",
    "billing provider",
    "payment webhook",
    "verified payment webhook",
    "service role",
    "service_role",
    "Supabase",
    "DPAPI",
    "monitoring geometry",
    "pull the new version",
    "delivery endpoint",
    "server-enforced",
]

FORBIDDEN_PRODUCT_LANGUAGE = [
    "Control Room Pilot",
    "Pilot boundary",
    "pilot scope",
    "pilot report",
    "Pilot report",
    "operational pilot report",
    "tenant-safe",
    "Latest tenant events",
    "field validation",
    "field-proven",
    "future WatchLog agent release",
    "future agent release",
    "Agent upgrade required",
    "agent upgrade required",
    "needs a compatible agent",
    "Advanced (operations governance)",
    "matching backend update",
    "finishing deployment",
    "QSR operating lens",
    "QSR camera roles",
    "QSR activity",
    "QSR analytics",
    "Operations Intelligence",
    "Operational awareness, not a surveillance wall",
    "Checkout-zone peak",
    "Native recorder events",
    "Native recorder event",
    "SOP violations",
    "Agent unreachable",
    "recorder_archive",
    "the site agent",
    "bounded footage",
    "live rule engine",
]


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


def main():
    problems = []
    for rel in CUSTOMER_FILES:
        path = ROOT / rel
        if not path.exists():
            problems.append(f"missing customer page: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        stripped = strip_comments(text)
        for phrase in FORBIDDEN:
            if phrase in text:
                problems.append(f"{rel}: customer-facing implementation phrase found: {phrase!r}")
        for phrase in FORBIDDEN_PRODUCT_LANGUAGE:
            if phrase in stripped:
                problems.append(f"{rel}: engineering/release language in customer copy: {phrase!r}")

    required = {
        "portal/app/settings/page.js": ["WatchLog Support", "setup code", "Download WatchLog for Windows"],
        "portal/app/site-health/page.js": ["Site connections", "WatchLog connection", "Operational health", "Not verified"],
        "portal/app/analytics/studio/page.js": ["Analytics Setup", "Save analytics setup", "Site update required", "Alert settings"],
        "portal/app/reports/page.js": ["Report recipients"],
        "portal/app/account-suspended/page.js": ["temporarily paused", "have not been deleted", "WatchLog support channel"],
        "portal/app/control-room/page.js": [
            "See what needs attention across every site.",
            "Common camera purposes",
            "Activity analytics",
            "Area occupancy peak",
            "requested still images",
        ],
        "portal/app/operations/page.js": [
            "Review operational exceptions that need attention.",
            "require a person to review them",
        ],
        "portal/app/archive/page.js": [
            "Recorded video search",
            "Recovered from recorder archive",
            "Site update required",
        ],
        "portal/app/executive/page.js": [
            "Site connection unavailable",
            "Recorder events",
            "SOP exceptions",
        ],
        "portal/app/ai/page.js": [
            "WatchLog AI",
            "How can I help with",
            "Ask WatchLog about this site",
            "Device changes always require the appropriate approval",
        ],
    }
    for rel, phrases in required.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel}: expected customer product phrase missing: {phrase!r}")

    control = (ROOT / "portal/app/control-room/page.js").read_text(encoding="utf-8")
    shell = (ROOT / "portal/app/shell.js").read_text(encoding="utf-8")
    home = (ROOT / "portal/app/page.js").read_text(encoding="utf-8")
    ai = (ROOT / "portal/app/ai/page.js").read_text(encoding="utf-8")
    setup_ai = (ROOT / "portal/app/setup/page.js").read_text(encoding="utf-8")
    ai_edge = (ROOT / "prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
    ai_migration = (ROOT / "prototype/supabase/migrations/0101_ai_workspace.sql").read_text(encoding="utf-8")

    # Control Room stays a truthful reuse of tenant-scoped contracts, not a new live-video promise.
    if 'rpc("wl_portal_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped portal overview contract")
    if 'requireTenant' not in control:
        problems.append("control room must use the shared tenant/account-status guard")
    if 'rpc("wl_analytics_studio"' not in control or 'rpc("wl_analytics_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped analytics contracts")
    if 'rpc("wl_portal_snapshot"' not in control:
        problems.append("control room incident stills must use the existing tenant-scoped snapshot contract")
    for unsafe in ("rtsp://", "<video", "autoplay", "continuous cloud video feed"):
        if unsafe.lower() in control.lower():
            problems.append(f"control room must not imply an unvalidated live-video surface: {unsafe!r}")

    # AI-first shell: four primary jobs; operational modules stay available as tools/deep links.
    for required_nav in ('["WatchLog AI", "/ai/"]','["Reports", "/reports/"]','["Setup", "/setup/"]','["Settings", "/settings/"]'):
        if required_nav not in shell:
            problems.append(f"AI-first shell missing primary navigation: {required_nav}")
    if '<a className="navlink" href="/control-room/">Camera View</a>' not in shell:
        problems.append("Control Room must remain available as the Camera View tool")
    if 'location.replace(tenant ? "/ai/" : "/onboarding/")' not in home:
        problems.append("signed-in tenants must land in WatchLog AI")

    # The browser invokes the authenticated Edge Function and never contains an AI secret.
    if 'functions.invoke("watchlog-ai"' not in ai:
        problems.append("AI workspace must invoke the server-side watchlog-ai function")
    if 'rpc("wl_ai_context"' not in ai:
        problems.append("AI workspace must use the tenant-scoped factual context")
    if "WATCHLOG_AI_API_KEY" in ai or "NEXT_PUBLIC_WATCHLOG_AI" in ai:
        problems.append("AI provider credentials/config must never be embedded in the browser")
    if 'req.headers.get("Authorization")' not in ai_edge or 'sb.auth.getUser()' not in ai_edge:
        problems.append("AI gateway must authenticate the Supabase user token")
    for invariant in (
        "Never invent a recorder capability",
        "UNKNOWN means unconfirmed",
        "Recorder writes are never silently executed",
        "Never ask for or expose recorder passwords/credentials",
    ):
        if invariant not in ai_edge:
            problems.append(f"AI safety prompt missing invariant: {invariant}")
    if 'WATCHLOG_AI_ENDPOINT' not in ai_edge or 'WATCHLOG_AI_API_KEY' not in ai_edge or 'WATCHLOG_AI_MODEL' not in ai_edge:
        problems.append("AI provider must be server-side and environment-configured")

    # AI context and setup mutations reuse exact WatchLog truth; no credential is returned.
    for rpc in ("wl_ai_new_conversation","wl_ai_conversations","wl_ai_messages","wl_ai_append_message","wl_ai_context","wl_ai_setup_camera"):
        if f"function public.{rpc}" not in ai_migration:
            problems.append(f"AI migration missing {rpc}")
    if "wl_my_site_diagnosis(p_site_id)" not in ai_migration or "wl_my_site_context(p_site_id)" not in ai_migration:
        problems.append("AI context must compose existing capability/diagnosis and business-context truth")
    if "recorder_credentials_leave_site',false" not in ai_migration:
        problems.append("AI context must explicitly preserve the local-credential boundary")
    if "nvr_password" in ai_migration.lower() or "recorder_password" in ai_migration.lower():
        problems.append("AI context migration must never include recorder password fields")

    # Guided setup must be real data mutation through guarded WatchLog RPCs, not a mock wizard.
    for rpc in ("wl_upsert_site_context","wl_ai_setup_camera","wl_onboarding_advance"):
        if f'rpc("{rpc}"' not in setup_ai:
            problems.append(f"guided setup must call {rpc}")
    if 'functions.invoke("watchlog-ai"' not in setup_ai:
        problems.append("guided setup must request capability-aware AI recommendations")
    if "NEXT_PUBLIC_INSTALLER_URL" not in setup_ai:
        problems.append("guided setup must use the configured canonical WatchLog installer URL")

    if problems:
        raise SystemExit("Customer portal language contract failed:\n- " + "\n- ".join(problems))
    print("customer portal language contract: PASS")


if __name__ == "__main__":
    main()

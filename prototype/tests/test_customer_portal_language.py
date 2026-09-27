from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

# AI-first refactor: each customer route's page.js is a thin re-export of its *-workspace.js (and, for
# the split surfaces, a legacy.js served at a subroute such as /settings/account/,
# /control-room/advanced/ and /incidents/evidence/). The finished-product language contract therefore
# scans the REAL customer copy across every customer route — not the thin stubs — so the forbidden
# -language guarantees remain meaningful after the split. Admin/platform and shared lib code are out of
# scope for customer-facing language.
_SKIP_DIR_SEGMENTS = {"admin", "api"}


def _customer_corpus():
    corpus = {}
    for p in sorted((ROOT / "portal/app").rglob("*.js")):
        rel_parts = p.relative_to(ROOT / "portal/app").parts
        if any(seg in _SKIP_DIR_SEGMENTS for seg in rel_parts):
            continue
        corpus[str(p.relative_to(ROOT)).replace("\\", "/")] = p.read_text(encoding="utf-8")
    return corpus


def _surface(rel_dir):
    parts = []
    for p in sorted((ROOT / "portal/app" / rel_dir).rglob("*.js")):
        if p.name == "legacy.js":
            continue
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


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
    # Customer-visible prose only: the internal event_source enum value "recorder_archive" is
    # legitimately compared in code to render the friendly "Recovered from saved video" copy, so forbid
    # the human-readable "recorder archive" wording rather than the internal identifier.
    "recorder archive",
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
    corpus = _customer_corpus()
    for rel, text in corpus.items():
        # Customer-facing language only: developer comments are stripped in the production build and are
        # never seen by customers, so both forbidden-language checks run against comment-stripped source.
        stripped = strip_comments(text)
        for phrase in FORBIDDEN:
            if phrase in stripped:
                problems.append(f"{rel}: customer-facing implementation phrase found: {phrase!r}")
        for phrase in FORBIDDEN_PRODUCT_LANGUAGE:
            if phrase in stripped:
                problems.append(f"{rel}: engineering/release language in customer copy: {phrase!r}")

    # Required customer product language, read from the surface where the AI-first UX actually renders
    # it. Wording that evolved with the AI-first product is asserted in its current form; the intent
    # (real, finished-product copy) is unchanged and, for split surfaces, points at the served subroute.
    required = {
        # Account/billing/plan/role surface served at /settings/account/ (settings/legacy.js).
        "portal/app/settings/legacy.js": ["WatchLog Support", "setup code", "Download WatchLog for Windows"],
        # AI-first Site Health (health-workspace.js): connection + camera-system + verifiability signals.
        "portal/app/site-health/health-workspace.js": ["Connection", "WatchLog", "Camera system", "Not verified"],
        # Activity Rules studio (renamed from "analytics setup").
        "portal/app/analytics/studio/page.js": ["Activity Rules", "Save activity rule", "WatchLog update required", "Review and evidence"],
        "portal/app/account-suspended/page.js": ["temporarily paused", "have not been deleted", "WatchLog support channel"],
        # Full Control Room served at /control-room/advanced/ (control-room/legacy.js).
        "portal/app/control-room/legacy.js": [
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
        "portal/app/executive/page.js": [
            "Site connection unavailable",
            "Recorder events",
            "SOP exceptions",
        ],
    }
    for rel, phrases in required.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel}: expected customer product phrase missing: {phrase!r}")

    # AI home (/ai/) copy lives across the ai workspace surface (customer-welcome/prompts/header/workspace).
    ai_home = _surface("ai")
    for phrase in ("WatchLog AI", "How can I help with", "Ask WatchLog about ", "Device changes require approval."):
        if phrase not in ai_home:
            problems.append(f"ai workspace: expected customer product phrase missing: {phrase!r}")

    # Reports delivery/recipients management, across the reports surface (workspace + recipient/delivery).
    reports_surface = _surface("reports")
    if "Recipient" not in reports_surface:
        problems.append("reports surface: recipient/delivery management copy missing")
    # Saved Video (was "recorder archive"), across the archive surface (workspace + saved-video-*).
    archive_surface = _surface("archive")
    for phrase in ("Search saved video", "Recovered from saved video", "Not available at this site"):
        if phrase not in archive_surface:
            problems.append(f"archive surface: expected saved-video phrase missing: {phrase!r}")

    control = (ROOT / "portal/app/control-room/legacy.js").read_text(encoding="utf-8")
    camera_view = (ROOT / "portal/app/control-room/customer-workspace.js").read_text(encoding="utf-8")
    shell = (ROOT / "portal/app/shell.js").read_text(encoding="utf-8")
    nav_config = (ROOT / "portal/app/nav-config.js").read_text(encoding="utf-8")
    home = (ROOT / "portal/app/page.js").read_text(encoding="utf-8")
    setup_surface = _surface("setup")
    ai_edge = (ROOT / "prototype/supabase/functions/watchlog-ai/index.ts").read_text(encoding="utf-8")
    ai_migration = (ROOT / "prototype/supabase/migrations/0101_ai_workspace.sql").read_text(encoding="utf-8")

    # Full Control Room (served at /control-room/advanced/) stays a truthful reuse of tenant-scoped
    # contracts, not a new live-video promise. The lean Camera View (/control-room/) must also stay safe.
    if 'rpc("wl_portal_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped portal overview contract")
    if 'requireTenant' not in control:
        problems.append("control room must use the shared tenant/account-status guard")
    if 'rpc("wl_analytics_studio"' not in control or 'rpc("wl_analytics_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped analytics contracts")
    if 'rpc("wl_portal_snapshot"' not in control:
        problems.append("control room incident stills must use the existing tenant-scoped snapshot contract")
    for unsafe in ("rtsp://", "<video", "autoplay", "continuous cloud video feed"):
        if unsafe.lower() in control.lower() or unsafe.lower() in camera_view.lower():
            problems.append(f"control room must not imply an unvalidated live-video surface: {unsafe!r}")
    if "requireTenant" not in camera_view:
        problems.append("Camera View must use the shared tenant/account guard")

    # AI-first shell: WatchLog AI is the home (brand, new chat, Ask WatchLog); Reports and Setup are the
    # primary jobs; Settings and the Camera View (Control Room) stay reachable under More. The nav is
    # data-driven from nav-config.js and rendered by shell.js.
    if '"WatchLog AI":"/ai/"' not in nav_config:
        problems.append("AI-first nav: WatchLog AI must be the home route")
    for token in ('["Reports","/reports/?view=yesterday","Reports"]', '["Setup","/setup/","Setup"]', '["Settings","/settings/","Settings"]'):
        if token not in nav_config:
            problems.append(f"AI-first nav missing primary job: {token}")
    if '["Cameras","/control-room/","Control Room"]' not in nav_config:
        problems.append("Control Room must remain reachable as the Camera View tool")
    if "MAIN_TABS" not in shell or "MORE_TABS" not in shell:
        problems.append("AI-first shell must render the data-driven nav from nav-config")
    if 'withSite("/ai/",siteId)' not in shell:
        problems.append("AI-first shell must route the home/brand to WatchLog AI")
    if 'location.replace(tenant ? "/ai/" : "/onboarding/")' not in home:
        problems.append("signed-in tenants must land in WatchLog AI")

    # The browser invokes the authenticated Edge Function and never contains an AI secret.
    if 'functions.invoke("watchlog-ai"' not in ai_home:
        problems.append("AI workspace must invoke the server-side watchlog-ai function")
    if 'rpc("wl_ai_context"' not in ai_home:
        problems.append("AI workspace must use the tenant-scoped factual context")
    if "WATCHLOG_AI_API_KEY" in ai_home or "NEXT_PUBLIC_WATCHLOG_AI" in ai_home:
        problems.append("AI provider credentials/config must never be embedded in the browser")
    if 'req.headers.get("Authorization")' not in ai_edge or 'sb.auth.getUser()' not in ai_edge:
        problems.append("AI gateway must authenticate the Supabase user token")
    for invariant in (
        "Never invent a recorder capability",
        "UNKNOWN means unconfirmed",
        "Recorder writes are never silently executed",
        # Recorder-credential protection invariant (present verbatim in the system prompt).
        "Recorder credentials stay on the on-site WatchLog service and must never be requested or exposed",
    ):
        if invariant not in ai_edge:
            problems.append(f"AI safety prompt missing invariant: {invariant}")
    # AI provider config is server-side. The governed provider router (migration 0105 ai_providers +
    # Vault, resolved by the service role via wl_ai_resolve_mode) is primary; the legacy WATCHLOG_AI_*
    # env bridge is preserved in the provider registry (providers/registry.ts) for existing deployments.
    # Read the whole watchlog-ai function surface, not just index.ts, so the relocated env bridge counts.
    ai_fn = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "prototype/supabase/functions/watchlog-ai").rglob("*.ts")))
    if 'WATCHLOG_AI_ENDPOINT' not in ai_fn or 'WATCHLOG_AI_API_KEY' not in ai_fn or 'WATCHLOG_AI_MODEL' not in ai_fn:
        problems.append("AI provider must be server-side and environment-configured")
    if 'wl_ai_resolve_mode' not in ai_edge:
        problems.append("AI provider must resolve server-side from the DB provider router (service-role only)")

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

    # Guided setup must be real data mutation through guarded WatchLog RPCs, not a mock wizard. The
    # AI-first setup is split across step surfaces (connect-site/site-details/camera-setup/review-setup).
    for rpc in ("wl_upsert_site_context","wl_ai_setup_camera","wl_onboarding_advance"):
        if f'rpc("{rpc}"' not in setup_surface:
            problems.append(f"guided setup must call {rpc}")
    if 'functions.invoke("watchlog-ai"' not in setup_surface:
        problems.append("guided setup must request capability-aware AI recommendations")
    if "NEXT_PUBLIC_INSTALLER_URL" not in setup_surface:
        problems.append("guided setup must use the configured canonical WatchLog installer URL")

    if problems:
        raise SystemExit("Customer portal language contract failed:\n- " + "\n- ".join(problems))
    print("customer portal language contract: PASS")


if __name__ == "__main__":
    main()

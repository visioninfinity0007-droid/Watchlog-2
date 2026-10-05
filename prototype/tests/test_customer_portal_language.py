from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

# Owner-first portal: customer routes may use thin page.js wrappers around workspace components (and, for
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


# The governed customer vocabulary (AGENTS.md section 2): every pattern in it is banned from customer copy
# on the portal too, in addition to the lists above. Parsed without a YAML dependency: each forbidden
# entry is "- id: ..." followed by "pattern: '<single-quoted regex>'".
VOCABULARY = ROOT / "ai-harness/core/customer-vocabulary.yaml"

# Strings that are not customer copy although they contain a vocabulary word, each with its reason.
VOCABULARY_EXEMPT = {
    # Instruction to the report writer listing the internal terms it must NOT use; never rendered.
    "portal/app/reports/use-report.js": ["Do not use internal terms such as canonical dataset"],
    # Error-message matcher for a missing server function; never rendered.
    "portal/app/control-room/legacy.js": ["|schema cache|function"],
}


def vocabulary_patterns():
    text = VOCABULARY.read_text(encoding="utf-8")
    forbidden = text[text.index("\nforbidden:"):]
    ids = re.findall(r"(?m)^\s*- id: (\S+)\s*$", forbidden)
    pats = [p.replace("''", "'") for p in re.findall(r"(?m)^\s*pattern: '((?:[^']|'')*)'\s*$", forbidden)]
    if not ids or len(ids) != len(pats):
        raise SystemExit(f"customer vocabulary: could not read every forbidden pattern ({len(ids)} ids, {len(pats)} patterns)")
    return [(i, re.compile(p, re.I)) for i, p in zip(ids, pats)]


def _js_strings(src):
    """String literals of comment-stripped JS: quoted strings, and template literals split at ${...}."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i + 1:j])
            i = j + 1
        elif c == "`":
            j, part = i + 1, []
            while j < n and src[j] != "`":
                if src[j] == "\\":
                    part.append(src[j:j + 2])
                    j += 2
                elif src.startswith("${", j):
                    out.append("".join(part))
                    part, depth, j = [], 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(src[j], 0)
                        j += 1
                else:
                    part.append(src[j])
                    j += 1
            out.append("".join(part))
            i = j + 1
        else:
            i += 1
    return out


def customer_strings(src):
    """Text a customer can see: string literals that read as words (at least one space and a letter;
    identifiers, RPC names, paths and keys have no space) and JSX text between tags."""
    texts = [t for t in _js_strings(src) if " " in t.strip() and re.search(r"[A-Za-z]", t)]
    # JSX text: after a tag's ">" (not an arrow "=>" or "->"), with no code punctuation in it.
    texts += [m.group(1) for m in re.finditer(r"(?<![=\-])>([^<>{}=;]*[A-Za-z][^<>{}=;]*)<", src)]
    return texts


def vocabulary_problems(rel, src, patterns):
    exempt = VOCABULARY_EXEMPT.get(rel, [])
    problems = []
    for text in customer_strings(src):
        if any(e in text for e in exempt):
            continue
        for rule, rx in patterns:
            m = rx.search(text)
            if m:
                problems.append(f"{rel}: customer vocabulary rule {rule!r} matched {m.group(0)!r} in {text.strip()[:80]!r}")
    return problems


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


def main():
    problems = []
    patterns = vocabulary_patterns()
    # Self-check: copy is caught, code identifiers and exempt strings are not.
    probe = 'const a=rpc("wl_portal_snapshot");<div>{shot?"Captured":"Still image is shown on request."}</div><p>Recorder offline</p>'
    if not vocabulary_problems("probe.js", probe, patterns) or len(vocabulary_problems("probe.js", probe, patterns)) != 1:
        problems.append("customer vocabulary check must flag customer copy and ignore code identifiers")
    if vocabulary_problems("probe.js", "<p>Recorder offline</p>", patterns):
        problems.append("customer vocabulary check flags plain customer copy")
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
        problems.extend(vocabulary_problems(rel, stripped, patterns))
    for rel, needles in VOCABULARY_EXEMPT.items():
        for needle in needles:
            if needle not in corpus.get(rel, ""):
                problems.append(f"{rel}: stale customer vocabulary exemption: {needle!r}")

    # Required customer product language, read from the surface where the AI-first UX actually renders
    # it. Wording that evolved with the AI-first product is asserted in its current form; the intent
    # (real, finished-product copy) is unchanged and, for split surfaces, points at the served subroute.
    required = {
        # Account/billing/plan/role surface served at /settings/account/ (settings/legacy.js).
        "portal/app/settings/legacy.js": ["WatchLog Support", "setup code", "Download WatchLog for Windows"],
        # AI-first Site Health (health-workspace.js): connection + camera-system + verifiability signals.
        "portal/app/site-health/health-workspace.js": ["Connection", "WatchLog", "Camera system", "Not verified", "Monitoring is not fully verified"],
        # Activity Rules studio (renamed from "analytics setup").
        "portal/app/analytics/studio/page.js": ["Activity Rules", "Save activity rule", "WatchLog update required", "Review and evidence"],
        "portal/app/account-suspended/page.js": ["temporarily paused", "have not been deleted", "WatchLog support channel"],
        # Full Control Room served at /control-room/advanced/ (control-room/legacy.js).
        "portal/app/control-room/legacy.js": [
            "See what needs attention across every site.",
            "Common camera purposes",
            "Activity analytics",
            "Area occupancy peak",
            "requested camera views",
        ],
    }
    for rel, phrases in required.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel}: expected customer product phrase missing: {phrase!r}")

    # Insights must remain tenant-neutral and must not translate missing configuration into zero activity.
    insights = (ROOT / "portal/app/analytics/page.js").read_text(encoding="utf-8")
    if "Chai Wala" in insights:
        problems.append("Insights surface must not hard-code a customer/site name")
    for phrase in ("Activity insights are not configured yet", "missing measurements into zero activity"):
        if phrase not in insights:
            problems.append(f"Insights surface missing configuration-truth copy: {phrase!r}")

    settings_hook = (ROOT / "portal/app/settings/use-customer-settings.js").read_text(encoding="utf-8")
    settings_sites = (ROOT / "portal/app/settings/site-list.js").read_text(encoding="utf-8")
    for rpc in ("wl_ai_site_egress","wl_ai_set_site_text_egress","wl_ai_set_site_egress"):
        if f'"{rpc}"' not in settings_hook:
            problems.append(f"Settings privacy control missing governed permission call: {rpc}")
    for phrase in ("Ask WatchLog privacy","Written site information","Camera evidence","off by default","Only an account owner or admin can change these permissions."):
        if phrase not in settings_sites:
            problems.append(f"Settings privacy control missing customer copy: {phrase!r}")

    # Ask WatchLog remains the governed question workspace, but it is no longer the signed-in homepage.
    ai_home = _surface("ai")
    for phrase in ("WatchLog AI", "Ask WatchLog about ", "Device changes require approval."):
        if phrase not in ai_home:
            problems.append(f"ai workspace: expected customer product phrase missing: {phrase!r}")

    # Reports delivery/recipients management, across the reports surface (workspace + recipient/delivery).
    reports_surface = _surface("reports")
    if "Recipient" not in reports_surface:
        problems.append("reports surface: recipient/delivery management copy missing")

    # Rendered Reports copy must never describe how WatchLog reviews/processes camera material.
    # Keep this focused on customer-workspace.js so internal variable names and hidden AI prompts
    # do not create false positives.
    reports_rendered = "\n".join([
        (ROOT / "portal/app/reports/customer-workspace.js").read_text(encoding="utf-8"),
        (ROOT / "portal/app/reports/unified-restaurant-report.js").read_text(encoding="utf-8"),
    ])
    if "Chai Wala" in reports_rendered:
        problems.append("reports surface must not hard-code a customer/site name")
    for phrase in (
        "scored frames",
        "analyzed frames",
        "analyzed samples",
        "camera-derived",
        "manual validation",
        "still being processed",
        "requested still images",
        "restaurant intelligence",
        "analytics quality",
    ):
        if phrase.lower() in reports_rendered.lower():
            problems.append(f"reports surface: customer-facing process language found: {phrase!r}")
    incident_surface = (ROOT / "portal/app/incidents/customer-workspace.js").read_text(encoding="utf-8")
    for phrase in ("Incident review", "Management action", "Acknowledge", "Resolve", "Supporting evidence"):
        if phrase not in incident_surface:
            problems.append(f"incident review missing consolidated customer workflow: {phrase!r}")
    operations_route = (ROOT / "portal/app/operations/page.js").read_text(encoding="utf-8")
    if 'location.replace("/incidents/"' not in operations_route:
        problems.append("legacy /operations/ route must resolve to the consolidated Incident Review")
    executive_route = (ROOT / "portal/app/executive/page.js").read_text(encoding="utf-8")
    if 'location.replace("/reports/?"' not in executive_route:
        problems.append("legacy /executive/ route must resolve to canonical Reports")

    evidence_surface = (ROOT / "portal/app/incidents/legacy.js").read_text(encoding="utf-8")
    for phrase in ("Camera evidence", "Camera event history", "Available for review"):
        if phrase not in evidence_surface:
            problems.append(f"camera-evidence surface missing semantic-layer copy: {phrase!r}")
    for phrase in ("<strong>{rows.length}</strong>incident", "<small>Detection source</small>", "<small>Review state</small>"):
        if phrase in evidence_surface:
            problems.append(f"camera-evidence surface collapses events into incidents: {phrase!r}")

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
    if 'c.purpose || "general"' in camera_view:
        problems.append("Camera View must not silently label an unknown purpose as General")
    if '"Purpose not set"' not in camera_view:
        problems.append("Camera View must preserve an unknown camera purpose as not set")

    # Owner-first shell: signed-in customers land on Home, where WatchLog proactively shows
    # attention, monitoring confidence and available business activity before asking the customer
    # to start a chat. Ask WatchLog remains a primary job, while advanced tools stay under More.
    owner_home = (ROOT / "portal/app/home/customer-workspace.js").read_text(encoding="utf-8")
    if 'Home:"/home/"' not in nav_config and '["Home","/home/","Home"]' not in nav_config.replace(" ", ""):
        problems.append("owner-first nav: Home must be the customer home route")
    for token in (
        '["Home","/home/","Home"]',
        '["Attention","/notifications/","Notifications"]',
        '["Insights","/analytics/","Analytics"]',
        '["Reports","/reports/?view=yesterday","Reports"]',
        '["Ask WatchLog","/ai/","WatchLog AI"]',
    ):
        if token not in nav_config.replace("\\n", "").replace(" ", ""):
            # The config is intentionally formatted for readability; compare without layout whitespace below.
            compact = re.sub(r"\\s+", "", nav_config)
            if re.sub(r"\\s+", "", token) not in compact:
                problems.append(f"owner-first nav missing primary job: {token}")
    for token in (
        '["Cameras & Evidence","/control-room/","Control Room"]',
        '["System Health","/site-health/","Site Health"]',
        '["Setup & Support","/setup/","Setup"]',
        '["Account","/settings/","Settings"]',
    ):
        compact = re.sub(r"\\s+", "", nav_config)
        if re.sub(r"\\s+", "", token) not in compact:
            problems.append(f"owner-first More menu missing tool: {token}")
    if "MAIN_TABS" not in shell or "MORE_TABS" not in shell:
        problems.append("owner-first shell must render the data-driven nav from nav-config")
    if 'withSite("/home/",siteId)' not in shell:
        problems.append("owner-first shell must route the brand and Home navigation to /home/")
    if 'location.replace(tenant ? "/home/" : "/onboarding/")' not in home:
        problems.append("signed-in tenants must land on owner Home")
    for phrase in (
        'title="Needs attention"',
        "What changed",
        "Monitoring coverage",
        "Nothing needs your attention right now.",
        "Business activity insights are not ready yet.",
        "Ask WatchLog",
    ):
        if phrase not in owner_home:
            problems.append(f"owner Home missing customer-facing foundation copy: {phrase!r}")
    for rpc in ("wl_ai_context", "wl_notifications", "wl_my_daily_intelligence", "wl_analytics_overview"):
        if f'rpc("{rpc}"' not in owner_home:
            problems.append(f"owner Home must reuse governed customer data: {rpc}")

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
    for phrase in ("WatchLog is connected", "Monitoring is active", "Approve current setup"):
        if phrase not in setup_surface:
            problems.append(f"connected-site setup experience missing: {phrase!r}")
    setup_base = (ROOT / "portal/app/setup/use-setup-base.js").read_text(encoding="utf-8")
    setup_details = (ROOT / "portal/app/setup/site-details.js").read_text(encoding="utf-8")
    if 's.key==="monitoring"' not in setup_base:
        problems.append("setup must distinguish a live monitoring site from pre-start onboarding")
    if "WatchLog will not assume them." not in setup_details:
        problems.append("Guided Setup must tell the customer that business hours are not assumed")
    if 'p_overnight:overnight' not in setup_details:
        problems.append("Guided Setup must preserve overnight business-day semantics")
    setup_cameras = (ROOT / "portal/app/setup/camera-setup.js").read_text(encoding="utf-8")
    setup_base = (ROOT / "portal/app/setup/use-setup-base.js").read_text(encoding="utf-8")
    if 'purpose:c.purpose||"general"' in setup_base:
        problems.append("Guided Setup must not silently rewrite an unknown camera purpose to General")
    if 'Choose an area for every monitored camera before saving.' not in setup_cameras:
        problems.append("Guided Setup must require an explicit purpose for each monitored camera")
    if 'p_purpose:c.purpose||"general"' in setup_cameras:
        problems.append("Guided Setup must not silently classify an unknown camera as General area")

    if problems:
        raise SystemExit("Customer portal language contract failed:\n- " + "\n- ".join(problems))
    print("customer portal language contract: PASS")


if __name__ == "__main__":
    main()

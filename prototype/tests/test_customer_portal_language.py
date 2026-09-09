from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

# Every customer-facing route. The customer experience must read like a finished
# commercial product, so engineering/release framing may not appear in any of these.
CUSTOMER_FILES = [
    "portal/app/shell.js",
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

# Implementation/process phrases that must never reach customer copy.
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

# Engineering / release / internal-architecture framing that was (or could be)
# visible to customers. WatchLog should read like a finished product, so these
# exact human-readable phrases must not appear in customer copy. They are checked
# against comment-stripped source: internal identifiers and code comments may
# still describe the implementation accurately (e.g. `runtime_capabilities`,
# "until a compatible agent reports support"), only *visible* copy is governed.
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
    """Remove JS block and line comments so we only govern visible copy, not
    internal identifiers/comments (which may keep accurate technical terms)."""
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

    # Approved product language that must be present (the finished-product wording
    # that replaced the engineering framing).
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
    }
    for rel, phrases in required.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel}: expected customer product phrase missing: {phrase!r}")

    control = (ROOT / "portal/app/control-room/page.js").read_text(encoding="utf-8")
    shell = (ROOT / "portal/app/shell.js").read_text(encoding="utf-8")
    # Control Room stays a truthful reuse of tenant-scoped contracts, not a new
    # unvalidated live-video surface.
    if 'rpc("wl_portal_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped portal overview contract")
    if 'requireTenant' not in control:
        problems.append("control room must use the shared tenant/account-status guard")
    if 'rpc("wl_analytics_studio"' not in control or 'rpc("wl_analytics_overview"' not in control:
        problems.append("control room must reuse the tenant-scoped analytics contracts")
    if 'rpc("wl_portal_snapshot"' not in control:
        problems.append("control room incident stills must use the existing tenant-scoped snapshot contract")
    if '["Control Room", "/control-room/"]' not in shell:
        problems.append("customer navigation must expose Control Room")
    for unsafe in ("rtsp://", "<video", "autoplay", "continuous cloud video feed"):
        if unsafe.lower() in control.lower():
            problems.append(f"control room must not imply an unvalidated live-video surface: {unsafe!r}")

    if problems:
        raise SystemExit("Customer portal language contract failed:\n- " + "\n- ".join(problems))
    print("customer portal language contract: PASS")


if __name__ == "__main__":
    main()

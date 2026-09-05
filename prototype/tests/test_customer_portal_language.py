from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CUSTOMER_FILES = [
    "portal/app/shell.js",
    "portal/app/dashboard/page.js",
    "portal/app/control-room/page.js",
    "portal/app/incidents/page.js",
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

# Implementation/process phrases are deliberately excluded from customer copy.
# Exact human-readable phrases are checked so internal identifiers can still
# describe the implementation accurately in code.
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


def main():
    problems=[]
    for rel in CUSTOMER_FILES:
        path=ROOT/rel
        if not path.exists():
            problems.append(f"missing customer page: {rel}")
            continue
        text=path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            if phrase in text:
                problems.append(f"{rel}: customer-facing implementation phrase found: {phrase!r}")

    required={
        "portal/app/settings/page.js":["WatchLog Support","setup code","Download WatchLog for Windows"],
        "portal/app/site-health/page.js":["Site connections","WatchLog connection"],
        "portal/app/analytics/studio/page.js":["Analytics Setup","Save analytics setup"],
        "portal/app/reports/page.js":["Report recipients"],
        "portal/app/account-suspended/page.js":["temporarily paused","have not been deleted","WatchLog support channel"],
        "portal/app/control-room/page.js":[
            "Control Room Pilot",
            "QSR operating lens",
            "does not provide a live video wall",
            "Exact transactions and till reconciliation are not inferred from CCTV alone.",
            "Configured analytics only",
            "Checkout-zone peak",
            "approved incident stills",
        ],
    }
    for rel,phrases in required.items():
        text=(ROOT/rel).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                problems.append(f"{rel}: expected customer product phrase missing: {phrase!r}")

    control=(ROOT/"portal/app/control-room/page.js").read_text(encoding="utf-8")
    shell=(ROOT/"portal/app/shell.js").read_text(encoding="utf-8")
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


if __name__=="__main__":
    main()

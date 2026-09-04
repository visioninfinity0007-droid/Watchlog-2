#!/usr/bin/env python3
"""Static contract for public product maturity and security claims.

The website may describe the broader WatchLog vision, but roadmap and custom
work must never silently read like current standard-product capability.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WP = ROOT / "deploy" / "wordpress" / "themes" / "watchlog"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def main() -> int:
    home = read(WP / "front-page.php")
    platform = read(WP / "page-platform.php")
    solutions = read(WP / "page-solutions.php")
    security = read(WP / "page-security.php")
    matrix = read(ROOT / "docs" / "design" / "PUBLIC_CLAIMS_MATRIX.md")
    public = "\n".join([home, platform, solutions, security])

    checks = {
        "claims matrix recognizes DPAPI protection":
            "Recorder credential protection | SUPPORTED" in matrix
            and "machine-scoped Windows DPAPI" in matrix,
        "security page describes protected local credential storage":
            "machine-scoped Windows DPAPI" in security
            and "SYSTEM and local Administrators" in security,
        "old plaintext-file credential wording is gone":
            "stays in a file on the site PC" not in public
            and "login lives on the site PC" not in public,
        "public pages do not overclaim release-gated isolation":
            "before every release" not in public,
        "analytics positioning includes supported business measurements":
            all(term in public for term in [
                "Visitor / people flow", "Vehicle flow", "Boundary activity",
                "Dwell / time in zone", "After-hours activity", "Checkout-zone activity",
            ]),
        "checkout wording denies transaction inference":
            "Not exact transactions, sales or till reconciliation" in home,
        "facial recognition is not implied":
            "No facial recognition" in home
            and "does not do facial recognition" in security,
        "roadmap has explicit maturity labels":
            "Coming Soon" in home and "Coming Soon" in platform and "Coming Soon" in solutions,
        "custom integrations are explicitly labelled":
            "Custom Solution" in home and "Custom Solution" in platform and "Custom Solution" in solutions,
        "control room is never labelled available":
            "Available</span><h3>Control Room" not in public,
        "fire and smoke are never labelled available":
            "Available</span><h3>Fire" not in public
            and "Available</span><h3>Smoke" not in public,
        "live video wall is described as absent":
            "does not currently offer a live video wall" in platform
            and "does not provide a live video wall" in solutions,
        "compatibility no longer says broad field validation":
            "Validated in the field" not in home,
        "security data boundary includes analytics and setup stills":
            "Configured Analytics Studio measurements" in security
            and "configuration stills" in security,
        "recorded video locality remains precise":
            "Recorded video stays on your recorder" in home
            and "Recorded video stays on your recorder" in security,
    }

    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL") + "  " + name)
    if failed:
        print("\nPublic claims contract failed:")
        for name in failed:
            print(" - " + name)
        return 1
    print(f"\n{len(checks)}/{len(checks)} public claims checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

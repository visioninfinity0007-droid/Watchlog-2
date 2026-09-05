#!/usr/bin/env python3
"""Static contract for public product maturity, merchant and security claims.

The website may describe the broader WatchLog vision, but roadmap and custom
work must never silently read like current standard-product capability. The
merchant structure must also be present without inventing missing business facts.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WP = ROOT / "deploy" / "wordpress" / "themes" / "watchlog"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def main() -> int:
    files = {
        "home": WP / "front-page.php",
        "platform": WP / "page-platform.php",
        "solutions": WP / "page-solutions.php",
        "security": WP / "page-security.php",
        "qsr": WP / "page-quick-service-restaurants.php",
        "integrations": WP / "page-integrations.php",
        "fuel": WP / "page-fuel-forecourt.php",
        "pricing": WP / "page-pricing.php",
        "contact": WP / "page-contact.php",
        "faq": WP / "page-faq.php",
        "privacy": WP / "page-privacy.php",
        "terms": WP / "page-terms.php",
        "policy": WP / "page-refund-cancellation-service-delivery.php",
        "footer": WP / "footer.php",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        print("FAIL: missing website files:", ", ".join(missing))
        return 1

    text = {name: read(path) for name, path in files.items()}
    matrix = read(ROOT / "docs" / "design" / "PUBLIC_CLAIMS_MATRIX.md")
    site_content = read(ROOT / "deploy" / "wordpress" / "site-content.sh")
    public = "\n".join(text.values())

    php_ok = True
    for name, path in files.items():
        proc = subprocess.run(["php", "-l", str(path)], capture_output=True, text=True)
        if proc.returncode != 0:
            php_ok = False
            print(f"PHP FAIL {name}: {proc.stdout}{proc.stderr}")

    checks = {
        "all changed public PHP parses": php_ok,
        "claims matrix recognizes DPAPI protection":
            "Recorder credential protection | SUPPORTED" in matrix
            and "machine-scoped Windows DPAPI" in matrix,
        "security page describes protected local credential storage":
            "machine-scoped Windows DPAPI" in text["security"]
            and "SYSTEM and local Administrators" in text["security"],
        "privacy no longer describes plaintext credential file":
            "Held in a file on your site PC" not in text["privacy"]
            and "machine-scoped Windows DPAPI" in text["privacy"],
        "old plaintext-file credential wording is gone from product claims":
            "stays in a file on the site PC" not in public
            and "login lives on the site PC" not in public,
        "public pages do not overclaim release-gated isolation":
            "before every release" not in public,
        "homepage carries video analytics SaaS positioning":
            "Video Analytics &amp; CCTV Intelligence Platform" in text["home"]
            and "Make your existing cameras useful every day." in text["home"],
        "analytics positioning includes supported business measurements":
            all(term in public for term in [
                "Visitor / people flow", "Vehicle flow", "Boundary activity",
                "Dwell / time in zone", "After-hours activity", "Checkout-zone activity",
            ]),
        "checkout wording denies transaction inference":
            "Not exact transactions, sales or till reconciliation" in text["home"],
        "facial recognition is not implied":
            "No facial recognition" in text["home"]
            and "does not do facial recognition" in text["security"],
        "roadmap has explicit maturity labels":
            "Coming Soon" in text["home"]
            and "Coming Soon" in text["platform"]
            and "Coming Soon" in text["solutions"],
        "custom integrations are explicitly labelled":
            "Custom Solution" in text["home"]
            and "Custom Solution" in text["platform"]
            and "Custom Solution" in text["solutions"]
            and "Custom Solution" in text["integrations"],
        "control room is never labelled available":
            "Available</span><h3>Control Room" not in public,
        "fire and smoke are never labelled available":
            "Available</span><h3>Fire" not in public
            and "Available</span><h3>Smoke" not in public,
        "live video wall is described as absent":
            "does not currently offer a live video wall" in text["platform"]
            and "does not provide a live video wall" in text["solutions"]
            and "does not provide a live video wall" in text["qsr"],
        "qsr page is pilot-safe and does not invent target-brand customers":
            "Pilot / Coming Soon" in text["qsr"]
            and all(brand not in text["qsr"] for brand in ["KFC", "McDonald", "McDonald's"]),
        "target companies are not published as customer endorsements":
            all(name not in public for name in ["Retex", "Shasan", "PSO", "KFC", "McDonald’s", "McDonald's"]),
        "cloud and camera ecosystem are not presented as approved partners without proof":
            "AWS Partner" not in public
            and "AWS partnership" not in public
            and "is a technology partner" not in public.lower()
            and "our technology partner" not in public.lower(),
        "integrations deny finished connector catalogue":
            "not advertised as finished plug-and-play connectors" in text["integrations"]
            and "No finished stock-sync connector" in text["integrations"],
        "compatibility no longer says broad field validation":
            "Validated in the field" not in text["home"],
        "security data boundary includes analytics and setup stills":
            "Configured Analytics Studio measurements" in text["security"]
            and "configuration stills" in text["security"],
        "recorded video locality remains precise":
            "Recorded video stays on your recorder" in text["home"]
            and "Recorded video stays on your recorder" in text["security"],
        "merchant policy page exists and does not invent refund rule":
            "Refund, cancellation and digital-service delivery" in text["policy"]
            and "Final refund rule awaiting business-owner approval" in text["policy"],
        "merchant legal identity is configuration-driven":
            all(key in site_content for key in [
                "WATCHLOG_LEGAL_NAME", "WATCHLOG_BUSINESS_ADDRESS",
                "WATCHLOG_CONTACT_PHONE_DISPLAY", "WATCHLOG_BILLING_EMAIL",
            ])
            and "Merchant onboarding notice" in text["contact"]
            and "Merchant onboarding notice" in text["footer"],
        "merchant navigation exposes required policy surfaces":
            all(slug in site_content for slug in [
                "faq", "privacy", "terms", "refund-cancellation-service-delivery",
            ])
            and "Refund, cancellation &amp; delivery" in text["footer"],
        "pricing remains PKR and merchant-visible":
            "PKR</span> 6,000" in text["pricing"]
            and "PKR</span> 12,000" in text["pricing"]
            and "Payment method" in text["pricing"],
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

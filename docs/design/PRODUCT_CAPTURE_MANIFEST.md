# WatchLog — Product Capture Manifest (design)

Real screenshots from the **demo tenant only**, captured after a portal visual audit so the real
mark, nav, typography and state are preserved. Never AI-generated. Mirrors the asset-pack manifest.

Capture rig: in-app Browser pane, viewport 1440–1920 wide, consistent zoom, demo login
(`PORTAL_DEMO_*` in `.env`). Output → `themes/watchlog/img/product-*.png` → run
`tools/build_site_images.py --src themes/watchlog/img --only product-` to pack WebP.

| File | Screen | Must show | Sanitise |
|---|---|---|---|
| product-platform-overview.png | Overview | sites, incidents, after-hours metric, site health, recent incidents | names/emails/phones |
| product-incidents.png | Incidents | filters + one person incident + snapshot | example imagery only |
| product-reports.png | Reports | daily report + delivery history + recipient/channel | recipient contacts |
| product-site-health.png | Site Health | ≥1 healthy + ≥1 fault state | — |
| product-team.png | Team | owner/admin/viewer roles, generic demo users | user names/emails |
| product-setup.png | Onboarding | enrollment→recorder→cameras→ready stepper | — |
| product-plan-billing.png | Plan & billing | trial/plan; sandbox clearly identified | no unapproved pricing |
| product-hero-composite.png | (assembled) | Overview dominant + incident detail layered + report/WhatsApp proof; real mark; midnight bg | derived from above |

## Portal audit before capture
Log into demo tenant, walk each surface, and fix only **safe, presentation-level** portal issues
that would make a screenshot look unfinished (spacing, missing mark, ugly demo data, debug text,
misalignment). Do not change product behaviour. Record fixes in the revamp log. Website + portal
must read as one brand.

## Composite
Assemble `product-hero-composite.png` from real captures on a midnight (`#07111F`) field:
Overview as the dominant plane, an incident-detail card layered front-right, a daily-report /
WhatsApp proof card to the right, faint site-health status. Export ~2000px wide, then pack WebP.
Built by `tools/build_hero_composite.py` (Overview + floating Reports card, baked rounded corners +
soft shadow on transparency). Shipped 1900×1240.

## Status (2026-09-01)
The client supplied **brand-aligned design-target** screens (1920×1200, real monogram geometry +
the exact palette) in `Downloads/watchlog_product_screens_brand_aligned/`, with the instruction:
use these as the visual target on the website now, **align the real portal to this system**, then
**replace with literal full-resolution captures from the demo tenant**. All 7 + the composite are
live on the site. These are feature-accurate (incidents, after-hours, cameras-online, faults,
Person/Vehicle/Motorcycle types, site health, per-recipient WhatsApp/email) and clearly marked
"DEMO TENANT — Sample data only". They are **not** literal screenshots.

**Follow-ups (documented, not yet done):**
1. Align the live portal UI (`portal/`) to this navy/blue/violet system (nav rail, stat cards, tables)
   so it matches both these targets and the website.
2. Once aligned, capture literal demo-tenant screenshots and swap them in (same filenames → drop in +
   `build_site_images.py` + `build_hero_composite.py`, no template changes).

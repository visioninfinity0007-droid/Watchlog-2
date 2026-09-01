# WatchLog — Website Revamp Master

Rebuild the public WatchLog site into a premium physical-security / video-intelligence SaaS
website that holds up beside Verkada / Rhombus / Spot AI / Solink — WatchLog-specific, not a copy,
and never contradicting the real product. The mark/logo and the working product do not change.

- **Branch:** `website/watchlog-platform-revamp` (off `main @ ae83473`).
- **Theme:** custom lightweight WordPress theme `deploy/wordpress/themes/watchlog/`. No builder.
- **Deploy:** Coolify app `watchlog-website-git` (`ez7677oub6mo1c2gwukaynuk`), compose from `/deploy`.
  Theme re-syncs into the volume on container start (`entrypoint.sh`); pages/menus via `site-content.sh`.
- **Demo URL (not production):** `https://watchlogsite.161.97.175.15.sslip.io`. Portal URL is
  config-driven (`watchlog_portal_base()` → `WATCHLOG_PORTAL_URL`); never hardcode sslip in components.

## Companion docs
- `VISUAL_SYSTEM.md` — tokens, type, rhythm, components, motion, a11y, perf.
- `SITEMAP.md` — IA, routes, templates, redirects.
- `PRODUCT_LANGUAGE.md` — canonical vocabulary (matches the portal).
- `PUBLIC_CLAIMS_MATRIX.md` — every claim classified; governs all copy.
- `COPY_DECK.md` — final public copy per page/section (editorial source of truth).
- `IMAGE_USAGE.md` — which asset appears where, and why; art-direction decisions.
- `PRODUCT_CAPTURE_MANIFEST.md` — the 7 real captures + hero composite spec.

## Positioning (see PRODUCT_LANGUAGE + PUBLIC_CLAIMS)
Category: **CCTV / video intelligence for businesses that already own cameras.**
Proposition: **The intelligence layer for the CCTV you already own** — keep your cameras and
recorder, add WatchLog, get validated incidents, site-health visibility and a daily report,
without exposing the recorder to the internet. Works **alongside** guards and security teams.

## Attribution decision (footer / company hierarchy)
The public site is **product-first**: the footer leads with **WatchLog** and a neutral
`© <year> WatchLog`. The previous prominent "Built by Vision Infinity" line is **removed** from the
visible footer because (a) WatchLog is commercially aligned with AKSS and customer-facing ownership
should not be undercut, and (b) §7 IP between AKSS and Vision Infinity is unresolved — the public
site should not unilaterally assert a corporate owner. `SoftwareApplication`/`Organization` schema
keeps a low-key publisher value. **Any AKSS co-branding or a definitive "a … product" line is
CLIENT CONFIRMATION REQUIRED** (a commercial decision for the principals), and is easy to add later
in one place (`footer.php` + `functions.php` schema).

## Build order (logical commits)
1. visual-system (CSS tokens/primitives) + design docs ✅ in progress
2. header + footer (mega-menu, premium dark footer)
3. home (14 sections)
4. product captures (real demo tenant) + hero composite
5. platform / incidents / reporting / site-health
6. solutions (index + 5)
7. how-it-works / compatibility / security
8. pricing / setup / contact / privacy / terms
9. routing (site-content.sh) + redirects
10. responsive + a11y + SEO/perf
11. deploy + full live QA → merge

## Gates before "complete"
Visual quality gate (§40), commercial/demo consistency (§41: 6000/12000/contact, 14-day, entitlement),
security consistency (§42), per-page PASS (§43), full live audit on real URLs (§44).

## Live-verified log
_(updated as pages ship; demo site = watchlogsite.161.97.175.15.sslip.io, app branch pointed at
`website/watchlog-platform-revamp` during the revamp; merge to `main` + repoint at the end)_
- **Homepage — LIVE VERIFIED** (commit ab44ec6): 14 sections render, correct dark/light rhythm, no
  console errors, WebP+srcset images 200, mega-menu, hero (product placeholder + dusk-camera bg),
  platform reveal, editorial capability rows, AI diagram blends into dark section, solutions photo
  tiles, pricing 6,000/12,000/Talk-to-us, product-first footer. Premium, on-message.
- **Inner pages — LIVE VERIFIED** (19 pages): all routes 200, old slugs 301, 0 dead CTAs, 27/27
  internal links resolve 200 directly (trailing-slash fix), no PHP errors, no console errors.
  Platform page shows the real Overview in an app-chrome frame; solutions show real photography.
- **Product imagery — LIVE** (client design-target set): homepage hero composite + all product
  sections/pages show real brand-aligned UI. Billing screen matches site pricing (6,000/12,000/
  Talk-to-us, 14-day trial, sandbox badge).
- **SEO — LIVE:** /sitemap.xml returns 200 (custom), robots points at it, OG card compliant.
- **Mobile — VERIFIED:** hamburger drawer + accordions work; hero/CTAs stack cleanly at 375px.
- **Merged to main (ea0d254 → 8b39f5c); Coolify app repointed to main.**
- **INFRA BLOCKER (recorded):** Coolify build queue on the box is flaky (Redis scan fails
  intermittently). One clean main-branch build succeeded; the final 2 one-line SEO/URL fixes were
  applied to the live container by direct sync (git main is the source of truth). See
  memory reference_coolify_queue_wedge. The next healthy Coolify deploy re-bakes main verbatim.
- **FOLLOW-UPS (client-directed):** align live portal UI to this navy/blue/violet system; then
  capture literal demo-tenant screenshots and swap in (same filenames + rerun the two build scripts).

# WatchLog — Sitemap (website revamp)

Reorganised around **Product · Solutions · Trust · Conversion** instead of a brochure list.
Each route has a bespoke slug-based template (`page-<slug>.php`); pages are created by
`deploy/wordpress/site-content.sh` (empty body — the template renders). Home = `front-page.php`.

## Primary navigation

- **Product** ▾ — Platform `/platform/`, Incidents `/incidents/`, Reporting `/reporting/`, Site Health `/site-health/`
- **Solutions** ▾ — Overview `/solutions/`, Warehouses & Logistics `/solutions/warehouses-logistics/`, Retail `/solutions/retail/`, Manufacturing `/solutions/manufacturing/`, Schools & Campuses `/solutions/schools-campuses/`, Offices `/solutions/offices/`
- **How it works** `/how-it-works/`
- **Compatibility** `/compatibility/`
- **Security** `/security/`
- **Pricing** `/pricing/`
- Right: **Sign in** (portal login), **Start free** (portal signup)

## Utility / footer

Setup `/setup/`, Contact `/contact/`, Privacy `/privacy/`, Terms `/terms/`.

## Full route table

| Route | Template | Purpose |
|---|---|---|
| `/` | front-page.php | Homepage, 14 sections |
| `/platform/` | page-platform.php | Flagship product page (Overview→security boundary) |
| `/incidents/` | page-incidents.php | Event → incident language + real Incidents UI |
| `/reporting/` | page-reporting.php | Daily report, channels, delivery history |
| `/site-health/` | page-site-health.php | Agent/camera health, silent-camera story |
| `/solutions/` | page-solutions.php | Editorial index, 5 solution cards |
| `/solutions/warehouses-logistics/` | page-warehouses-logistics.php | Solution |
| `/solutions/retail/` | page-retail.php | Solution |
| `/solutions/manufacturing/` | page-manufacturing.php | Solution |
| `/solutions/schools-campuses/` | page-schools-campuses.php | Solution |
| `/solutions/offices/` | page-offices.php | Solution |
| `/how-it-works/` | page-how-it-works.php | Technical/accessible architecture |
| `/compatibility/` | page-compatibility.php | Validated vs protocol-compatible vs unsupported |
| `/security/` | page-security.php | Serious security page |
| `/pricing/` | page-pricing.php | Verified pricing only |
| `/setup/` | page-setup.php | Prerequisites + install steps (conversion) |
| `/contact/` | page-contact.php | Real actions per path |
| `/privacy/` | page-privacy.php | Data held, retention |
| `/terms/` | page-terms.php | Plain-terms commitments |

## Redirects (old → new; `functions.php` `template_redirect`)

- `/features/` → `/platform/`
- `/who-its-for/` → `/solutions/`
- `/about/` → `/security/` (attribution/company content folds into Security + footer)

Nested solution slugs render by last-segment template (WP slug template hierarchy), created as
children of the `solutions` page so the URL path is `/solutions/<child>/`.

## SEO

`SoftwareApplication` + `Organization` JSON-LD; per-page title/description/canonical/OG; breadcrumbs
on inner pages; sitemap + robots verified; no thin duplicate pages. Solution pages are distinct
(different photo, different copy, different topics) — indexable, not templated filler.

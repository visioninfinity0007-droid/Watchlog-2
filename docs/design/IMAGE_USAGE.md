# WatchLog — Image Usage & Art Direction

Assets are **inputs**, art-directed — not all are used, and none is used where it weakens the page.
The **product leads**; photography sets atmosphere; diagrams carry the technical story.

Pipeline: `tools/build_site_images.py` → `<name>.webp` + `<name>-sm.webp` + `<name>.{jpg,png}` in
`themes/watchlog/img/`. Served via `watchlog_pic()` `<picture>` (WebP + mobile + fallback).

## Diagrams (branded, dark navy — placed ON dark sections so seams disappear)
| Asset | Where | Note |
|---|---|---|
| diagram-architecture.png | How it works (hero), Home §7 | Recorder→Agent→Outbound→Cloud→Portal |
| diagram-ai-filtering.png | Home §6 Local AI, Incidents, How-it-works | raw→local AI→kept; fail-open footnote |
| diagram-privacy.png | Security (hero), Home §11 | outbound-only privacy model |
| diagram-report-flow.png | Reporting (hero), Home §8 | incident→summary→delivery |
| diagram-multi-site.png | Home §9 Multi-site, Platform | one view across locations |
| diagram-compatibility.png | Compatibility (hero), Home §12 | keep cameras/recorder, add WatchLog |

Diagrams have an empty lower third by design → crop with `object-fit:cover; object-position:top`
or place full-bleed on a matching dark field. Each needs a descriptive caption/alt (they encode claims).

## Environment / atmosphere photography
| Asset | Where |
|---|---|
| hero-industry-atmosphere.jpg | Home hero — subtle right/edge layer behind the product composite only (not dominant) |
| environment-recorder.jpg | How it works, Setup, Home §3 (context) |
| environment-site-pc.jpg | How it works, Setup |

## Solutions photography (editorial tiles + solution-page heroes)
solution-warehouse / -retail / -manufacturing / -school-campus / -office-commercial `.jpg`
→ Home §10 tiles + each `/solutions/<x>/` hero. Gentle scale-on-hover (1.03) on tiles.

## CCTV example stills (the "raw event" texture — NOT product UI)
cctv-person / -vehicle / -motorcycle (kept examples) · cctv-empty-rain / -headlights (filtered noise)
· cctv-loading-activity / -gate-movement (activity) · cctv-camera-offline (Site Health).
Used in Incidents + Home §6 to show input footage. Always labelled as example CCTV, never as product UI.

## Real product captures (the star — from the demo tenant only)
product-platform-overview / -incidents / -reports / -site-health / -team / -setup / -plan-billing `.png`
+ **product-hero-composite.png** (assembled from the above). See `PRODUCT_CAPTURE_MANIFEST.md`.
Sanitise names/phones/emails; sandbox clearly identified on billing; real WatchLog mark only.

## Not used (art-direction cuts)
- Legacy `img/*.jpg` (hero-premises, still-*, seg-*, recorder-shelf, unwatched-monitor, close-dusk):
  superseded by the new pack; keep only if a page still references them, else prune at cleanup.

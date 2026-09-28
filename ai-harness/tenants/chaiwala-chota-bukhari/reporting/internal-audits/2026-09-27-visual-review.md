# INTERNAL ONLY — Chai Wala visual review audit — 2026-09-27

This file is for WatchLog operators / future AI sessions. It must not be rendered in the customer portal or copied into the owner-facing report.

## Scope
- Service date: 2026-09-27
- Configured service window: 16:00–04:00 Asia/Karachi
- Available image evidence reviewed: approximately 20:42–03:22 local
- All available images in that evidence set were visually reviewed camera-by-camera and chronologically.
- Internal inventory: 610 JPEG records across 8 canonical cameras.
- Per-camera inventory: Floor 1 77; Floor 2 76; Shop Front 77; Cash Counter 75; Kitchen 76; Back Entrance 76; Office View 77; Office Camera 76.

## Review rules used
- Read tenant business context before interpreting images.
- Treat Floor 1 / Floor 2 as overlapping dining views and de-duplicate simultaneous parties.
- Use party/table sessions rather than pretending camera observations equal unique visitors.
- Keep Shop Front as service handoff, Back Entrance as service access, Cash Counter as workstation activity, Kitchen as back-of-house operations, and office cameras as security only.
- Never infer sales, revenue, transaction count, unique footfall, order correctness, food quality, identity or demographics.
- Derive trends only from chronological sequences, not isolated frames.

## Internal synthesis
- Conservative peak de-duplicated visible diner range: 18–22.
- Conservative peak occupied table-group range: 6–8.
- Estimated observed-evening covers: approximately 45–60; not full-day and not POS.
- Two business demand waves: ~20:45–22:45 and ~00:10–01:15.
- Large after-midnight party used combined tables.
- Customer seating declined sharply after ~01:15–01:20.
- Furniture consolidation began ~01:40; most outdoor seating cleared ~02:40.
- Shop Front substantially shuttered ~02:45.
- Kitchen shifted into close-down/reset from ~02:40.
- Office views showed no visible occupancy/access.
- Back Entrance became a closing logistics route; staged chairs/materials narrowed the path.
- Repeated Floor 1 bulb glare materially obstructed the right-centre dining view.
- Floor 2 central hanging line/fixture partially obstructed the view.
- Office View is partly blocked by a large fan; Office Camera is the stronger complementary security view.
- Frontage/counter areas showed recurring loose paper/litter.

## Internal processing note
The production vision worker is active but the Chai Wala site is not currently eligible for the configured external-vision path because no site external-egress permission record is present. Automated structured restaurant rows therefore did not represent this service day. This technical condition is internal and must never be exposed in the owner-facing report.

## Public report authority for this service date
The saved report snapshot and the Summary/Detailed Report under `daily-reports/2026-09-27/` are the owner-facing business report. For this date, prefer the completed manual visual review over an empty automated-processing state.

## Follow-up engineering
- Preserve manual-reviewed report precedence in the portal.
- Decide whether manual batch reviews should be persisted into a dedicated structured manual-review table rather than overloading automated per-frame status.
- Keep technical evidence/audit separate from customer-facing reporting.

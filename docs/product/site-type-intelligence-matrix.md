# Site-Type Intelligence Matrix (Phase 28)

Tenant → Site → Site Type → Intelligence Profile. A metric is shown for a site only when **all four** hold:

1. **Site relevance**: the site type's profile includes it (no table utilisation in an office, no checkout in a warehouse).
2. **Configured camera purpose**: at least one monitored camera has the matching explicit purpose. "General area" and unset purposes never feed operational metrics.
3. **Governed evidence**: the value comes from one governed business-day dataset (`wl_my_daily_intelligence`) or from the profile's own period dataset. That is `wl_office_period` (`office-period-v1`) for offices, `wl_site_period` (`site-period-v1`) for warehouse, factory and retail, and the restaurant period for calibrated restaurants.
4. **Validated capability**: the underlying detection is validated (person or vehicle activity episodes, coverage classes).

If any condition fails, the module **is not rendered**. It is never shown as 0. A value that is unknown inside a rendered module reads "Not available" or "Not observed".

## Status legend

| Status | Meaning | Portal behaviour |
|---|---|---|
| **Implemented** | Governed backend metric, already in production data | Rendered when relevant and configured |
| **Derivable** | Deterministic from governed episodes plus camera purpose plus coverage; derived in `portal/app/owner/site-profiles.js` | Rendered by Phase 28 modules (marked ✓) or reserved for later (–) |
| **Requires journey logic** | Needs correlation across cameras (the same vehicle or person over time) | Not shown. Per-camera episodes are labelled "not time on site". |
| **Field-gated** | Needs a capability that has not been validated in the field (unique counting, PPE, queue length, staff separation) | Not shown, and Ask declines to estimate it |
| **External data required** | Needs WMS/ERP/POS/HR/MES data | Not shown. Explicitly prohibited as an interpretation of activity. |

The YAML `metric_status` blocks in `ai-harness/site-types/*.yaml` are the source of truth for Ask WatchLog. This table mirrors them and also shows what the portal renders.

## Architecture: generic facts, then the profile's interpretation

```
wl_my_daily_intelligence (one governed day)
   ├─ site-neutral keys: coverage · attention · after_hours · day_boundaries · access_windows
   │      └─ wl_site_day_facts → wl_site_period (site-period-v1): episodes, detections,
   │         vehicle episodes, by configured purpose, hourly buckets, first/last observed,
   │         coverage classes, deterministic comparison
   └─ office brief ('office' key) → wl_office_period (office-period-v1, unchanged)

selectSiteProfile()  (one registry for office · warehouse · factory · retail · restaurant · general)
   → composer (restaurant | business | general) + period source + measure + quiet-period config
   → the profile interprets only eligible facts (acceptPeriod: schema must match the profile)
```

- **Warehouse, factory and retail never receive office-period facts.** `acceptPeriod` rejects `office-period-v1` for them, and their day reports never read the office brief.
- **Restaurant** is selected through the same registry. Its behaviour is unchanged: calibrated restaurants get the restaurant composer and their own governed period. A restaurant without a calibrated configuration gets the general composer, never table or service modules.
- **Starts:**
  - "Activity began at X" is used only when the earlier part of the day was verified: full coverage, or partial coverage whose known unverified windows all start after X, with a high-confidence boundary.
  - Otherwise the page says "First observed activity was at X".
- **Quiet periods:** "Notable quiet period" means no observed activity for at least the profile's `quietPeriod.minutes` (45 for warehouse and factory) inside configured hours. **This threshold has not been field-validated.** A quiet period is an observation, never downtime, delay or lost productivity, and its cause is always "Cause not known".

## Shared (every site type)

| Metric | Status | Portal |
|---|---|---|
| Attention items, monitoring coverage (live / recovered / unverified) | Implemented | ✓ Home, Insights, Reports |
| Peak activity hour, first and last observed activity (start stated only when verified) | Implemented | ✓ |
| After-hours activity (verified only) | Implemented | ✓ |
| Activity Rules counts (line crossing, zone presence) | Implemented | ✓ Home "Activity rules · last 24 hours" |
| Hourly activity timeline (episodes per hour, not unique people) | Derivable | ✓ `SiteTimeline` |
| Area activity by configured purpose (active minutes, first/last) | Derivable | ✓ `SiteZones` |
| Attendance / hours worked | External data required | Prohibited |

## Office

| Metric | Status | Portal |
|---|---|---|
| Activity detections, 7/30 working-day comparison (`office-period-v1`) | Implemented | ✓ Insights trend, Reports week/month (unchanged source) |
| First observed activity vs configured opening | Implemented | ✓ Home lead: "First observed office activity was at …". "Began at …, later than the configured opening" is used only when the start is verified. |
| Reception, meeting-room, workspace, common-area activity | Derivable | ✓ Area activity |
| Restricted-area activity | Derivable | ✓ (security zone) |
| Parking vehicle dwell | Derivable | – (vehicle module is warehouse/factory only) |
| Quiet periods | Derivable | – (offices do not get gap claims) |
| Vehicle site time | Requires journey logic | Not shown |
| Visitor count, staff/visitor split | Field-gated | Not shown |
| Attendance, productivity, headcount | External data / prohibited | Not shown |

## Warehouse

| Metric | Status | Portal |
|---|---|---|
| Person activity, peak hour, opening/closing, after-hours | Implemented | ✓ |
| Receiving / loading dock / dispatch activity | Derivable | ✓ "Dock and area comparison" |
| Notable quiet periods at receiving/docks/dispatch (≥ profile threshold inside configured hours, verified coverage only, threshold not field-validated) | Derivable | ✓ "Notable quiet periods" with a suggested check per area and "Cause not known" |
| Vehicle episodes and per-camera dwell at gate/yard/dock | Derivable | ✓ "Vehicle movement" (not separate vehicles, not time on site) |
| Restricted storage after hours | Derivable | ✓ security zone |
| 7/30-day comparison, including by-area episode comparison (`site-period-v1`) | Derivable | ✓ once migration 0143 (`wl_site_period`) is applied; until then "comparison not available yet" |
| Vehicle site time, turnaround | Requires journey logic | Not shown |
| Forklift activity, PPE, staff/visitor, unique people/vehicles | Field-gated | Not shown |
| Shipments, orders, tonnes, throughput | External data required | Prohibited |

## Factory

| Metric | Status | Portal |
|---|---|---|
| Person activity, peak hour, first/last observed, after-hours | Implemented | ✓ ("First observed activity", or "Shift activity began" only when verified) |
| Production zone / assembly / packing activity | Derivable | ✓ "Production areas" |
| Notable quiet periods in production areas (verified coverage only, threshold not field-validated) | Derivable | ✓ "Notable quiet periods" + "Check whether this was a planned break, changeover or maintenance." |
| Raw materials / finished goods / dispatch movement | Derivable | ✓ "Material movement" |
| Dispatch vehicle dwell | Derivable | ✓ "Vehicle movement" when a gate or dispatch camera is configured |
| Maintenance after hours | Derivable | ✓ area activity |
| 7/30-day comparison, including by-area episode comparison (`site-period-v1`) | Derivable | ✓ once migration 0143 is applied; until then "comparison not available yet" |
| Vehicle site time | Requires journey logic | Not shown |
| Shift-level activity, PPE, staff per zone | Field-gated | Not shown |
| Production output, machine downtime/OEE, orders | External data required | Prohibited ("activity is not output") |

## Retail

| Metric | Status | Portal |
|---|---|---|
| Person activity, peak hour, opening/closing, after-hours | Implemented | ✓ Home lead "Store activity peaked h–h" |
| Entrance, sales floor, product and promotional zone activity | Derivable | ✓ "Store areas" |
| Checkout activity (active time, episodes; entrance for comparison) | Derivable | ✓ "Checkout activity" when a checkout / checkout_till / queue camera is configured |
| Stock room after hours | Derivable | ✓ area activity |
| 7/30-day comparison, including by-area episode comparison (`site-period-v1`) | Derivable | ✓ once migration 0143 is applied; until then "comparison not available yet" |
| Customer journey in store | Requires journey logic | Not shown |
| Footfall/unique visitors, queue length/wait, staff/customer split | Field-gated | Not shown |
| Sales, revenue, conversion, basket, till timing | External data required | Prohibited |
| Service-to-food timing | Not relevant | Never rendered |

## Restaurant (behaviour unchanged; selected through the same registry)

| Metric | Status | Portal |
|---|---|---|
| Visible diners, occupied tables, table utilisation, sessions, covers | Implemented (calibrated sites) | ✓ existing restaurant modules |
| Observed time to food, minimum dwell, kitchen/handoff load | Implemented (calibrated sites) | ✓ |
| Opening/closing, after-hours, peak hour, attention, coverage, rules | Implemented | ✓ |
| Period comparison | Derivable | ✓ restaurant period RPC |
| Customer footfall, counter queue, staff/customer split | Field-gated | Not shown |
| Sales, revenue, POS timing | External data required | Prohibited |
| Loading docks, production, checkout | Not relevant | Never rendered |

## Truth rules enforced in code

- **Quiet-period claims** ("Notable quiet period at loading docks: 2h 10m with no observed activity") need configured opening and closing hours and full verified coverage, or partial coverage with known gap windows that do not overlap. A coverage gap whose time cannot be placed blocks the claim. A day in progress is only scanned up to when its data was produced. Overnight sites (closing before opening) get no gap claims.
- **Recommendations** say what to check and are always labelled "Cause not known".
- **Vehicles** are counted as episodes at a camera: not separate vehicles, and not time on site.
- **Checkout**: no sales, transactions or conversion are inferred.
- **Ask starters** include only questions that the site's configuration can answer (`configuredCapabilities`).
- **Setup purposes** come from the site type. A purpose saved earlier stays selectable.

Tests:
- `prototype/tests/test_site_type_intelligence_contract.py` (14 checks);
- `prototype/tests/site_profiles_unit.mjs` (26 checks);
- `prototype/tests/e2e_site_period_pg.py`, which runs the migration and checks tenant isolation on the disposable Postgres in CI.

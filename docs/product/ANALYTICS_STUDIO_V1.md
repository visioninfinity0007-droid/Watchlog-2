# WatchLog Analytics Studio v1

Status: implementation branch `product/analytics-studio-v1`

## Product objective

Turn WatchLog from an incident-summary product into a configurable site-intelligence platform. Customers select a site type, classify what each camera watches, enable monitoring goals, draw lines/zones, assign schedules, and receive analytics that match the operational purpose of that site.

## Canonical model

Site type → Camera purpose → Monitoring rules → Geometry/schedule → Local tracking → Analytic events → Aggregates → Portal/reporting.

## v1 analytics

1. Visitor Flow
2. Vehicle Flow
3. Boundary Monitoring
4. Zone Activity
5. Schedule Monitoring
6. Site Health

Checkout/Till Activity is intentionally represented as an advanced/future module until queue tracking and/or POS integration is production-proven. v1 must not claim exact transaction counting from CCTV alone.

## Product principles

- No facial recognition.
- Current detector classes remain person, car and motorcycle.
- Tracking is local and ephemeral; WatchLog stores business events, not biometric identities.
- Measurement events are separate from incidents.
- Recorder credentials remain on site.
- Analytics configuration is pulled by the Site Agent over the existing outbound connection.
- Customer-facing configuration uses business language, not computer-vision jargon.

## Site types

- retail
- warehouse_logistics
- manufacturing
- office_commercial
- school_campus
- parking_yard
- residential_community
- custom

## Camera purposes

- entrance_exit
- main_gate
- reception
- checkout_till
- loading_bay
- warehouse_floor
- perimeter
- restricted_area
- parking
- office_floor
- school_gate
- corridor
- custom

## Monitoring rule types

- line_crossing
- zone_entry
- zone_dwell
- occupancy
- schedule_activity
- health

## Monitoring packs

### Retail Essentials
Visitor Flow, Checkout Activity (non-transactional v1), After-Hours Activity, Site Health.

### Warehouse Operations
Gate Flow, Vehicle Flow, Loading Bay Activity, Restricted Zones, Shift Monitoring, Site Health.

### Perimeter Security
Boundary Crossing, After-Hours Activity, Dwell, Site Health.

### Office Visibility
Visitor Flow, Reception Activity, After-Hours Activity, Site Health.

## UX split

### Installer
Hardware setup only:
- enrollment
- recorder discovery/test
- camera discovery
- site-type selection
- camera-purpose classification
- handoff to portal

### Portal
Business configuration:
- analytics studio
- line/polygon editor
- schedules
- monitoring packs
- analytics dashboards
- reporting integration

## Acceptance gates

1. Schema is tenant-isolated and migration-backed.
2. Site type and camera purpose are writable by authorized members only.
3. One camera can have multiple monitoring rules.
4. Rule geometry uses normalized 0..1 coordinates.
5. Agent can pull a versioned analytics configuration without inbound connectivity.
6. Agent can emit analytic events separately from incidents.
7. Line crossing, zone entry, dwell and schedule filtering have deterministic tests.
8. Portal provides Analytics navigation, site/camera configuration, rule editor and analytics overview.
9. Daily reports include analytics aggregates without turning every count into an incident.
10. Existing tenant-isolation, incident ingest and reporting tests still pass.

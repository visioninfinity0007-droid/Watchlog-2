# WatchLog — Public Claims Matrix

Every public claim on the website is classified. No sentence may contradict this file or the
product architecture. Status: **SUPPORTED** (ship as-is) · **QUALIFY** (ship only with the stated
precise wording) · **REMOVE** (never present as an available capability) · **CLIENT CONFIRMATION REQUIRED**
(omit until AKSS/VI confirm).

This file was re-baselined on **2026-09-04** against Analytics Studio v1, the graphical Windows
installer and Windows-local DPAPI credential protection merged in Sprint 2. A capability can exist
in code while still requiring field validation before stronger accuracy/production claims are made.

## Security / privacy

| Claim | Status | Exact wording to use |
|---|---|---|
| Recorder not on public internet | SUPPORTED | "Your recorder is never exposed to the public internet." |
| Connection direction | SUPPORTED | "The Agent connects outward only. No inbound connection to the recorder is required." |
| No port forwarding / firewall change | SUPPORTED | "No port forwarding. No inbound firewall changes. No VPN." |
| Recorded video location | QUALIFY | "Recorded video stays on your recorder." (NOT "footage never leaves the building".) |
| Credentials location | SUPPORTED | "Recorder credentials remain on the site PC and are never sent to WatchLog." |
| Recorder credential protection | SUPPORTED | "On supported Windows installs, the recorder password is stored using machine-scoped Windows DPAPI with file access restricted to SYSTEM and local Administrators." Do not call this end-to-end encryption. |
| What syncs outward | QUALIFY | WatchLog may receive validated event metadata, incident stills, configured Analytics Studio measurements, health/status data and on-demand camera configuration stills. Do not say "only one incident still" as a universal statement now that Analytics Studio is active. |
| No live camera access | SUPPORTED | "No live camera browsing through WatchLog. WatchLog cannot pan, zoom, or view live." |
| No public RTSP | SUPPORTED | "No public RTSP stream is opened." |
| Tenant isolation | QUALIFY | "Each customer's data is isolated in the database and covered by automated isolation tests." Do not say "before every release" unless the exact release gate ran against an appropriate target. |
| "Nothing of yours is on the internet" | REMOVE | Too absolute. Operational data and stills do go to the private cloud account. |
| "Your footage never leaves the building" | REMOVE | Replace with "Recorded video stays on your recorder." |
| Facial recognition | SUPPORTED (as a negative) | "WatchLog does not do facial recognition." Never imply it does. |
| Encryption specifics (E2E, "military-grade") | REMOVE | No unverified crypto claims. "In transit over HTTPS" and the exact DPAPI wording above are allowed. |
| SOC 2 / ISO / certifications | REMOVE | None held. Do not imply. |

## AI / analytics

| Claim | Status | Wording |
|---|---|---|
| On-site incident filtering | SUPPORTED | "False alarms are filtered on your own site PC before incident data is sent." |
| Detection scope | QUALIFY | "The current production detector recognizes person, car and motorcycle." Never "any object" / broad detection. |
| Fail-open incident filtering | SUPPORTED | "If the detector can't run, the event is kept rather than silently dropped." |
| Video analytics platform | QUALIFY | "WatchLog can turn configured cameras into business measurements such as visitor flow, vehicle flow, boundary activity, zone activity, dwell/time-in-zone and after-hours activity." Camera setup and field conditions affect measurement quality. |
| People counting | QUALIFY | Use "visitor flow" / "people flow" or "configured people counts" for supported line/occupancy use cases. Do not claim audited headcount accuracy across arbitrary camera placements until field validation is complete. |
| Vehicle counting | QUALIFY | Use "vehicle flow" / configured entries and exits. Do not imply ANPR, vehicle identity or universal class coverage. |
| Dwell / time in zone | SUPPORTED | Analytics Studio supports zone-dwell rules with configurable thresholds. |
| Loitering | QUALIFY | A configured dwell rule can support a loitering-style pilot. Public wording should remain "dwell / time in zone" until the specific loitering use case is field-validated. |
| Boundary / intrusion-style monitoring | QUALIFY | Analytics Studio supports line/zone boundary monitoring. Do not describe it as a certified security response or guaranteed intrusion detection. |
| After-hours activity | SUPPORTED | Analytics Studio supports schedule-based activity rules. |
| Checkout / till activity | QUALIFY | "Activity/occupancy around a configured checkout zone." Never exact transactions, sales, or till reconciliation from CCTV alone. |
| Live behavioural analytics | REMOVE | Current analytics are local measurements/events, not a guaranteed real-time cloud monitoring service. Do not say "live AI monitoring" or "instant behavioural alerts." |
| Fire detection | REMOVE | Not implemented on current `main`. May appear only as clearly labelled roadmap/pilot research, never as Available. |
| Smoke detection | REMOVE | Not implemented on current `main`. May appear only as clearly labelled roadmap/pilot research, never as Available. |
| Facial/visitor identity | REMOVE | Not implemented and contrary to current product principles. |
| Cross-camera re-identification | REMOVE | Not implemented. |
| Broad "detect/classify/track anything" | REMOVE | Current supported classes and analytics are intentionally bounded. |

## Control Room / video

| Claim | Status | Wording |
|---|---|---|
| Control Room | QUALIFY | May be presented as **Pilot / Coming Soon** until the dedicated Control Room module is implemented and validated. |
| Multi-camera saved layouts | REMOVE | Not implemented on the 2026-09-04 baseline. Do not present as Available. |
| Current/latest still | QUALIFY | A camera configuration still can be requested through the existing Site Agent path. Label it as a current/latest configuration still, not a live stream. |
| Live video wall | REMOVE | Not implemented. Do not call refreshed stills or event images "live video." |
| Recorded clip extraction | REMOVE | Requires recorder-specific field validation and clip/playback adapter work. |

## Reporting

| Claim | Status | Wording |
|---|---|---|
| Daily report | SUPPORTED | "A daily summary of what happened." |
| Channels | SUPPORTED | "Delivered on WhatsApp, email, or both." |
| Site timezone | SUPPORTED | "Counts are in each site's local time." |
| Delivery history | SUPPORTED | "Every report is kept in your portal history." |
| Idempotency (plain) | SUPPORTED | "The same day's report is never sent twice." |
| Analytics in reports | SUPPORTED | Current reporting code includes Analytics Studio aggregates. |
| Camera-wise / multi-site executive reports | QUALIFY | Planned extension of the reporting engine; do not present the full collective/QSR report suite as Available until the new views/templates ship. |
| Real-time alerting | REMOVE | Current commercial reporting path is daily, not a guaranteed real-time response service. No "instant alert" claims. |

## Integrations

| Claim | Status | Wording |
|---|---|---|
| POS integration | REMOVE | Not implemented on current `main`; may be described only as planned/custom integration work. |
| Shopify inventory updates | REMOVE | Not implemented and not validated. Never imply autonomous stock mutation from CCTV today. |
| Attendance-machine integration | REMOVE | Not implemented on current `main`; may be described as a custom integration direction. |
| CRM integration | REMOVE | No first-class integration layer exists yet. |
| Custom APIs/integrations | QUALIFY | "Custom integration work is available by scoped implementation." Do not imply a catalogue of finished connectors. |

## Compatibility (precise validation language)

| Category | Status | Members / wording |
|---|---|---|
| Driver implementation / simulator validation | QUALIFY | Hikvision (ISAPI) and Dahua (CGI) have implemented drivers and simulator coverage. Do not label both broadly "field validated." |
| Real-hardware experience | QUALIFY | Dahua has been seen on one real unit. Broad model/firmware acceptance is still ongoing. |
| Protocol-compatible / requires check | QUALIFY | "Protocol-compatible, confirm your unit: HiLook, Imou, CP Plus, Uniview, Tiandy, and most ONVIF recorders." Do NOT present as field-proven. |
| Unsupported | SUPPORTED | "Unbranded Xiongmai / Hisilicon boards are not supported." |
| "Works with every camera" | REMOVE | Never. |

## Commercial

| Claim | Status | Wording |
|---|---|---|
| Current published pricing | SUPPORTED | Starter PKR 6,000/mo · Growth PKR 12,000/mo · Enterprise "Talk to us". These are the current website/billing-aligned values. |
| Starter vs Standard naming | CLIENT CONFIRMATION REQUIRED | Latest meeting language used "Standard / Growth / Enterprise" while the live code/content uses "Starter / Growth / Enterprise". Do not rename until approved and updated consistently. |
| Trial | SUPPORTED | "14-day free trial. No card." |
| Trial expiry behaviour | SUPPORTED | "When a trial ends, reporting pauses — your recorded events and history are kept." |
| Cancellation | QUALIFY | "Cancel any time; reporting continues to the end of the paid month." (Matches entitlement grace/cancel behaviour.) |
| Retention 7/30/90 | SUPPORTED | Per-plan still retention is enforced (7/30/90 days). |
| Per-plan camera allowance (8/24/unlimited) | CLIENT CONFIRMATION REQUIRED | Present as positioning; exact enforced cap to be confirmed. Use "up to N" softly or omit hard numbers if unconfirmed. |
| SLA / uptime %, refund %, discounts, support response time | REMOVE | No such commitments are verified. |
| Per-transaction/mobile-launch charging | CLIENT CONFIRMATION REQUIRED | Discussed as a possible later commercial model, not current SaaS behavior. |

## Customers / partnerships

| Claim | Status | Wording |
|---|---|---|
| KFC / McDonald's | REMOVE as customer claim | They are target/example QSR use cases from the client meeting. Do not show logos or claim they use WatchLog without explicit evidence/permission. |
| AWS partnership | REMOVE until formally approved | Do not use AWS Partner badges or partnership wording without formal status. |
| Camera/manufacturer partnerships | REMOVE until formally approved | Compatibility is not the same thing as a commercial/technology partnership. |
| Customer case studies/logos | CLIENT CONFIRMATION REQUIRED | Publish only with verified customer status and permission. |

## Positioning (AKSS alignment)

| Claim | Status | Note |
|---|---|---|
| "No monthly guard" / anti-guard | REMOVE | WatchLog is commercially aligned with AKSS. Never position against guards, staff, security companies, or physical response. |
| Works alongside teams | SUPPORTED | "Gives guards, supervisors, control rooms and site managers better visibility." |
| Not a guarding service | SUPPORTED | "WatchLog observes and reports; it is not a guarding or monitoring service and does not dispatch a response." (Honest scope, not anti-guard.) |

## Website maturity labels

Use these labels when the broader client vision appears on the website:

- **Available** — implemented and demonstrable in the current product.
- **Pilot** — implemented enough for a controlled pilot but still requires field acceptance/calibration.
- **Coming Soon** — planned product module with no claim of present availability.
- **Custom Solution** — scoped implementation/integration for a specific customer; not a standard SaaS feature.

Do not use a maturity label to evade a false claim. The supporting copy must still describe the real capability precisely.

## Attribution

See `SITE_REVAMP_MASTER.md` §Attribution. Decision: customer-facing footer leads with **WatchLog**;
"Vision Infinity" credit is de-emphasised (small print), AKSS is not positioned against. Documented there.

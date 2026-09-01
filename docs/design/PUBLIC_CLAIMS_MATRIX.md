# WatchLog — Public Claims Matrix

Every public claim on the website is classified. No sentence may contradict this file or the
product architecture. Status: **SUPPORTED** (ship as-is) · **QUALIFY** (ship only with the stated
precise wording) · **REMOVE** (never say) · **CLIENT CONFIRMATION REQUIRED** (omit until AKSS/VI confirm).

## Security / privacy

| Claim | Status | Exact wording to use |
|---|---|---|
| Recorder not on public internet | SUPPORTED | "Your recorder is never exposed to the public internet." |
| Connection direction | SUPPORTED | "The Agent connects outward only. No inbound connection to the recorder is required." |
| No port forwarding / firewall change | SUPPORTED | "No port forwarding. No inbound firewall changes. No VPN." |
| Recorded video location | QUALIFY | "Recorded video stays on your recorder." (NOT "footage never leaves the building".) |
| Credentials location | SUPPORTED | "Recorder credentials remain on the site PC and are never sent to WatchLog." |
| What syncs outward | SUPPORTED | "Only validated event metadata and one incident still are sent to your private WatchLog account." |
| No live camera access | SUPPORTED | "No live camera browsing through WatchLog. WatchLog cannot pan, zoom, or view live." |
| No public RTSP | SUPPORTED | "No public RTSP stream is opened." |
| Tenant isolation | SUPPORTED | "Each customer's data is isolated in the database, verified by an automated test before every release." |
| "Nothing of yours is on the internet" | REMOVE | Too absolute — event metadata + one still DO go to the cloud account. |
| "Your footage never leaves the building" | REMOVE | Replace with "Recorded video stays on your recorder." |
| Facial recognition | SUPPORTED (as a negative) | "WatchLog does not do facial recognition." Never imply it does. |
| Encryption specifics (E2E, "military-grade") | REMOVE | No unverified crypto claims. "In transit over HTTPS" only. |
| SOC 2 / ISO / certifications | REMOVE | None held. Do not imply. |

## AI / detection

| Claim | Status | Wording |
|---|---|---|
| On-site filtering | SUPPORTED | "False alarms are filtered on your own site PC before anything is sent." |
| Detection scope | QUALIFY | "Detects person, car and motorcycle." Never "any object" / broad detection. |
| Fail-open | SUPPORTED | "If the detector can't run, the event is kept rather than silently dropped." |
| Live/behavioural analytics | REMOVE | Not built. No loitering/intrusion-AI/crowd claims. |

## Reporting

| Claim | Status | Wording |
|---|---|---|
| Daily report | SUPPORTED | "A daily summary of what happened." |
| Channels | SUPPORTED | "Delivered on WhatsApp, email, or both." |
| Site timezone | SUPPORTED | "Counts are in each site's local time." |
| Delivery history | SUPPORTED | "Every report is kept in your portal history." |
| Idempotency (plain) | SUPPORTED | "The same day's report is never sent twice." |
| Real-time alerting | REMOVE | Product is daily, not real-time. No "instant alert" claims. |

## Compatibility (precise validation language)

| Category | Status | Members / wording |
|---|---|---|
| Validated (driver exercised vs simulator; Dahua seen on real hardware once) | QUALIFY | "Validated: Hikvision (ISAPI), Dahua (CGI)." Label real-hardware status honestly — Dahua seen on one real unit; broad field validation is ongoing. |
| Protocol-compatible / requires check | QUALIFY | "Protocol-compatible, confirm your unit: HiLook, Imou, CP Plus, Uniview, Tiandy, and most ONVIF recorders." Do NOT present as field-proven. |
| Unsupported | SUPPORTED | "Unbranded Xiongmai / Hisilicon boards are not supported." |
| "Works with every camera" | REMOVE | Never. |

## Commercial

| Claim | Status | Wording |
|---|---|---|
| Pricing | SUPPORTED | Starter PKR 6,000/mo · Growth PKR 12,000/mo · Enterprise "Talk to us". No other numbers. |
| Trial | SUPPORTED | "14-day free trial. No card." |
| Trial expiry behaviour | SUPPORTED | "When a trial ends, reporting pauses — your recorded events and history are kept." |
| Cancellation | QUALIFY | "Cancel any time; reporting continues to the end of the paid month." (Matches entitlement grace/cancel behaviour.) |
| Retention 7/30/90 | SUPPORTED | Per-plan still retention is enforced (7/30/90 days). |
| Per-plan camera allowance (8/24/unlimited) | CLIENT CONFIRMATION REQUIRED | Present as positioning; exact enforced cap to be confirmed. Use "up to N" softly or omit hard numbers if unconfirmed. |
| SLA / uptime %, refund %, discounts, support response time | REMOVE | No such commitments are verified. |

## Positioning (AKSS alignment)

| Claim | Status | Note |
|---|---|---|
| "No monthly guard" / anti-guard | REMOVE | WatchLog is commercially aligned with AKSS. Never position against guards, staff, security companies, or physical response. |
| Works alongside teams | SUPPORTED | "Gives guards, supervisors, control rooms and site managers better visibility." |
| Not a guarding service | SUPPORTED | "WatchLog observes and reports; it is not a guarding or monitoring service and does not dispatch a response." (Honest scope, not anti-guard.) |

## Attribution

See `SITE_REVAMP_MASTER.md` §Attribution. Decision: customer-facing footer leads with **WatchLog**;
"Vision Infinity" credit is de-emphasised (small print), AKSS is not positioned against. Documented there.

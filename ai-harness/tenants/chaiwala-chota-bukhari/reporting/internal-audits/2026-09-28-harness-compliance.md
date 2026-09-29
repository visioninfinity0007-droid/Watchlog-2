# Harness Compliance Audit: Chai Wala - Chota Bukhari (2026-09-28)

Internal only. Checked against the `skills/tenant-intelligence-setup.md` completion gate,
`skills/restaurant-daily-business-report.md`, the restaurant site type and this tenant's visual method,
using production data read-only. Snapshot analysis ran on WatchLog-controlled compute only. No image was
sent to an external model.

| Gate / rule | Status | Evidence |
|---|---|---|
| Git and DB agree on site type, service day, report profile | ✅ | restaurant, 16:00-04:00 overnight, 7 days, `chaiwala_restaurant_ops_v1` in both |
| Reporting tree + visual method + archive | ✅ | Includes the 2026-09-27 report and internal audit |
| Canonical physical cameras only | ✅ | 8 canonical rows = 8 physical channels, names match `camera_role_map` |
| Camera roles agree between Git and DB | ❌ | Shop Front in the reception list, Back Entrance in the entrance list, Kitchen purpose `restricted` + critical. See `known_review_focus.db_camera_purpose_conflicts` |
| Capture cadence matches the context | ❌ | Actual ~300 s on all cameras vs targets of 60-180 s. Timing metrics must be coarse. Site-type rule `timing_resolution_rule` added |
| Coverage truth | ⚠️ | The PC is powered off after closing, so the closed period and the opening/closing edges are unmonitored |
| Recorder produces person/motion events | ❌ | None received. The DS-7608NI-Q1 does human/vehicle analysis on 4 channels only; no KB record yet |
| Customer AI answers with a model | ⚠️ → text-only consent recorded (see below) | Every request in the last 7 days fell back (egress not granted). Needs a local/private model path or an owner egress decision; do not change egress to clear this |
| Zones calibrated and confirmed | ⚠️ | A borrowed till zone gave a false "unattended" reading. Staff-side zones need owner/installer confirmation |
| Image quality | ⚠️ | Office View low light; Back Entrance soft focus (repeated) |

## Required changes outside the harness (not applied; need approval / owner)

1. `site_business_context` camera purposes/role lists: align with `camera_role_map`.
2. Recorder: enable human/vehicle analysis on the 4 most important channels. Add the model to the
   capability KB via migration.
3. Capture cadence: raise the floor cameras toward their 60 s target.
4. Operations: keep the site PC and agent running 24/7.
5. Customer AI: provide a private/local text path, or get an owner decision on text-only egress.

## Customer AI consent (2026-09-28)

The WatchLog operator (Awais) directed that customer questions for this tenant be answered by the cloud
AI with the harness + tenant context. It is recorded as **text-only external AI egress**
(`external_text_egress_allowed`, migration 0140):
- chat text may reach the configured cloud model;
- **camera images stay on WatchLog-controlled compute** (full `external_egress_allowed` stays false, so
  the cloud vision worker still skips this site).

This was set by the operator on the tenant's behalf, not by the tenant owner through
`wl_ai_set_site_text_egress`. Confirm with the tenant and move the record to the owner when a portal
consent switch exists.

It takes effect once the updated `watchlog-ai` function (reads the text-only flag) is deployed.

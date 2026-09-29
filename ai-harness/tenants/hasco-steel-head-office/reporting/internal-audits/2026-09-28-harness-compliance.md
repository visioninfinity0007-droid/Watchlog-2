# Harness Compliance Audit: HASCO Steel / Head Office (2026-09-28)

Internal only. Checked against the `skills/tenant-intelligence-setup.md` completion gate, the office
site type and this tenant's visual method, using production data read-only. No images were viewed.

| Gate / rule | Status | Evidence |
|---|---|---|
| Git and DB agree on site type, hours, days, report profile | ✅ | office, 08:00-19:00, Mon-Fri, `office_ops_v1` in both |
| Reporting tree + visual method exist | ✅ | `reporting/` complete |
| Canonical physical cameras only | ✅ | 8 canonical rows = 8 physical channels, names match `camera_role_map` and DB role lists |
| Mapping confirmed before role conclusions | ⚠️ | Names come from configuration and were not yet visually verified. `camera_identity.mapping_status` added |
| Working-day evidence exists | ❌ | 132 snapshots, all on Sat 26 Sep (non-working day). Recorder unreachable since 18:38 that day |
| Recorder produces person/motion events | ❌ | None received. The DS-7608NI-Q1 does human/vehicle analysis on 4 channels only; no KB record yet |
| Customer AI answers with a model | ⚠️ → text-only consent recorded (see below) | Every request in the last 7 days fell back (egress not granted). Needs a local/private model path or an owner egress decision |
| Daily report archive populated | ⚠️ | None possible until working-day evidence exists |

## Required changes outside the harness (not applied; need approval / owner)

1. Site: restore recorder connectivity from the site PC (power / IP / network).
2. Recorder: choose 4 channels for human/vehicle analysis. Add the model to the capability KB.
3. Visual setup pass on working-day images, under the site's egress policy.
4. Customer AI: private/local text path, or an owner decision on egress.

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

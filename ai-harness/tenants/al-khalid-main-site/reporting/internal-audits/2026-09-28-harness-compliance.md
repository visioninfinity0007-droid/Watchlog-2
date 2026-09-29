# Harness Compliance Audit: Al-Khalid Main site (2026-09-28)

Internal only. Checked against the `skills/tenant-intelligence-setup.md` completion gate, the office
site type and this tenant's visual method, using production data read-only.

| Gate / rule | Status | Evidence |
|---|---|---|
| Git and DB agree on site type, hours, days, report profile | ✅ | office, 08:00-19:00, Mon-Fri, `office_ops_v1` in both |
| Reporting tree + visual method exist | ✅ | `reporting/` complete |
| Canonical physical cameras only | ❌ **defect** | Event-to-camera attribution interleave. Reception + Armory Gate evidence sits only on hidden legacy rows. See `context.yaml` → `camera_attribution_evidence` |
| Mapping confirmed before role conclusions | ⚠️ | Recorder labels read from images (strong), physical views not confirmed. DB role lists correctly empty |
| Coverage truth | ⚠️ | ~39% of business hours monitored (11-25 Sep), agent offline nightly, 16 registrations. Now recorded in `known_review_focus` |
| Customer AI answers with a model | ✅ | Egress allowed; answers via the provider chain, mostly the fallback layer (22 vs 9 primary in 7 days) |
| Incident policies meaningful | ❌ | The only policy (`%armory%` purpose) matches no camera, so it cannot fire. 53 earlier incidents, 0 acknowledged |
| Recorder capability known | ✅ | `dh-xvr1b08-i.yaml`: SMD human/vehicle, FIELD_VERIFIED |
| Daily report archive populated | ⚠️ | No archived reports yet |

## Required changes outside the harness (not applied; need approval / owner)

1. **Agent:** fix event-to-camera attribution (installer/agent owner). Then re-attribute historical
   events, snapshots and incidents to the correct canonical cameras.
2. **Site visit:** physically confirm each view, then set roles in `site_business_context` and reassign
   the restricted-area purpose.
3. **Owner:** confirm Saturday working days and Friday hours.
4. **Operations:** keep the site PC and agent running 24/7.
5. **Camera:** fix the Armory Gate glare.

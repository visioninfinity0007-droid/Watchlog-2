# WatchLog SaaS Onboarding — self-serve site activation flow

**Status:** design spec (item 11) · **Author:** Vision Infinity engineering · **Date:** 2026-09-10

> A new customer must get from "signed up" to "WatchLog is watching my site and I understand what it
> will tell me" without ever seeing the letters SMD, CGI, ISAPI or NTP. This document specifies that
> flow, the business context it collects, and exactly which existing RPCs/tables it reuses versus which
> pieces are **NEW**. It is generic across tenants and verticals (office, retail, warehouse, clinic) —
> no site type, camera name or "armory" is hardcoded.

Design rule throughout: **speak business, resolve technical.** The customer describes their site in
plain language; WatchLog maps that to recorder configuration through the capability model
(`recorder_capabilities`, mig `0061`) and the Site Control plane (`0063`/`0064`). Recorder terminology
stays hidden behind an explicit **Advanced** view (§4).

---

## 1. The flow

```
Register --> Organization --> Site --> Connect Agent/Edge --> Recorder discovery -->
  Capability resolution --> Camera context --> AI recommendation --> Approve --> Monitoring begins
```

| Step | Customer sees | Backing product surface |
|---|---|---|
| 1. Register | Create login | Supabase auth (portal) |
| 2. Organization | Name your company; you're the owner | `tenants` + `memberships` (owner); `wl_my_tenant()` scopes everything after |
| 3. Site | Add a location (name, type, hours, timezone) | `sites` (existing) + **NEW** business-context capture (§2) |
| 4. Connect Agent/Edge | Download installer, run it, enter the code | `enrollment_codes` --consumed by--> `wl_enroll`; `agents` row appears; see `EDGE_DEPLOYMENT.md` |
| 5. Recorder discovery | "We found your recorder and 8 cameras" | Agent `driver.probe()`/`list_channels()` --> `wl_sync_cameras` --> `cameras`; vendor/model onto `agents.device_vendor/device_model/device_driver` |
| 6. Capability resolution | "This recorder can do X, not Y — here's what WatchLog will use" | `wl_sync_capabilities` (live probe) + `wl_recorder_profile()` / `wl_recorder_capability()` over `recorder_capabilities` (mig `0061`) |
| 7. Camera context | Label each camera's purpose; mark entrances & critical cameras | `cameras.purpose` (existing) + **NEW** context fields (§2) |
| 8. AI recommendation | "We recommend: disable vehicle alerts on indoor cameras; sync the clock; enable after-hours alerts" | Site Control RECOMMEND: `wl_site_command_propose_write(mode='recommend')` (mig `0064`), gated by the capability model |
| 9. Approve | One tap approves each recommendation | `wl_site_command_approve()` --> queued --> Agent applies transactionally (`site_control.execute_write`) |
| 10. Monitoring begins | "You're live. First daily brief tomorrow." | Events via `wl_ingest_events`; health via `wl_report_health`; brief via `wl_office_brief`; delivery via `report_recipients`/`report_deliveries` |

Steps 5-9 form one "we set it up for you" moment: discover, understand what the hardware can actually
do, learn what the cameras are *for*, then propose and apply only safe, field-verified settings.

## 2. Business context collected (plain language, per site)

Onboarding captures the operating context that powers alerts and the daily brief — never recorder settings:

| Context | Example | Drives |
|---|---|---|
| Site name & **type** | "Gulberg Office", office / retail / warehouse / clinic | Brief phrasing, default rule templates, per-vertical defaults |
| **Operating hours** (per day) | Mon-Sat 09:00-18:00, Sun closed | Opening/closing state machine, after-hours screening (replaces the hard-coded 08-19 window) |
| Timezone | Asia/Karachi | All local-time reporting (`sites.timezone`, existing) |
| **Entrances / exits** | which cameras cover the front door | Journey start/end anchoring, visitor vs internal logic |
| **Restricted areas** | store room, cash office, server room | Restricted-access episodes, after-hours-entry alerts |
| **Camera purpose** (per camera) | entrance / indoor / perimeter / restricted / parking | Analytics expectations (e.g. vehicles expected outdoors, not indoors) |
| **Critical cameras** | the two that must never go dark | Escalation priority in Needs-Attention and alerts |
| **Desired alerts** | after-hours person; restricted-area entry; camera offline | Rule templates (`monitoring_rules`) |
| **Reports & recipients** | daily WhatsApp to the manager; weekly email to the owner | `report_recipients` (channel + destination) |

Purpose and hours have existing homes (`cameras.purpose`, `sites.timezone`, `monitoring_schedules`).
Entrances/exits, restricted-area tagging, critical-camera flags and the guided site-type/hours capture
are **NEW** onboarding structure (§3).

## 3. Product / API / data model

### Reused as-is (existing)
- **Identity & tenancy:** `tenants`, `memberships`, `invitations`; `wl_my_tenant()`, `wl_platform_role()`.
- **Site & devices:** `sites`, `agents`, `enrollment_codes`, `cameras`; `wl_sites()`, `wl_enroll`,
  `wl_heartbeat`, `wl_sync_cameras`.
- **Capability resolution:** `recorder_capabilities` + `recorder_capability_sources` +
  `recorder_field_evidence` (mig `0061`); resolvers `wl_recorder_profile(vendor, model)` and
  `wl_recorder_capability(vendor, model, capability)`; live probe `wl_sync_capabilities`.
- **Recommend / approve / apply:** `site_commands`, `site_managed_actions` (mig `0063`/`0064`);
  `wl_site_command_propose_write`, `wl_site_command_approve`, `wl_agent_claim_command`,
  `wl_agent_complete_command`, `wl_site_command_result`; agent executor `prototype/agent/site_control.py`.
- **Camera purpose write:** the WatchLog-layer `set_camera_purpose` action
  (`SITE_CONTROL_API.md` §6) writing `cameras.purpose`.
- **Reporting:** `report_recipients`, `report_deliveries`, `wl_office_brief`; runner --> n8n -->
  Evolution WhatsApp / email.
- **Monitoring schedules & rules:** `monitoring_schedules`, `monitoring_rules` (used by the alert step).

### NEW (name them, mark clearly)
- **NEW `onboarding_state`** (per site): a resumable wizard checkpoint (`step`, `completed_at`,
  captured context) so a half-finished onboarding survives a refresh. (Related site setup-state exists
  from mig `0020`; this is the onboarding-specific superset.)
- **NEW site business-context fields/table** — `site_type`, structured `operating_hours`, and per-camera
  `is_entrance` / `is_critical` / `restricted` tags. Some map onto existing `cameras.purpose` +
  `monitoring_schedules`; the entrance/critical/restricted flags and `site_type` are additive.
- **NEW `wl_onboarding_submit_context(p_site_id, p_context jsonb)`** — one SECURITY DEFINER RPC that
  validates and persists the business context, tenant-scoped exactly like the `0064` write gate
  (`wl_platform_role()` or `site.tenant = wl_my_tenant()`), RLS-sealed, granted to `authenticated`.
- **NEW recommendation composer** (cloud-side): reads `wl_recorder_profile()` + captured camera purposes
  and emits the RECOMMEND-tier proposals of §1 step 8. It only ever calls the **existing**
  `wl_site_command_propose_write` — it invents no new write path, and every proposal is capability-gated.
- **NEW default rule/report seeding** — on "Monitoring begins", seed sensible `monitoring_rules` +
  `report_recipients` from the site type and desired-alerts answers (one row per template, editable).

All NEW RPCs follow the house pattern: `security definer set search_path = public`, RLS-sealed tables
with **no direct policies**, authorization inside the function body, granted to `authenticated` only.

## 4. Technical terms stay hidden (unless Advanced)

The customer-facing flow never surfaces SMD, CGI, ISAPI, NTP/DST, IVS, channel indexes or digest auth.
The capability model already stores WatchLog **generic keys** (`human_vehicle_classification`,
`video_loss`, `time_ntp_config`, `channel_title`) separate from the vendor mechanism — onboarding shows
only the generic, business-framed effect:

- "Tell people from vehicles" — not "SMD Human/Vehicle targets".
- "Keep the clock correct" — not "DST off + NTP on + GMT offset via `setCurrentTime`".
- "Camera has no usable image" — not "VideoLoss index on channel 5".

An explicit **Advanced / recorder detail** view (opt-in, per site, gated to Owner/Admin) may expose the
raw `wl_recorder_profile()` output — model, evidence class, constraints, per-capability read/write —
for an installer or a technical customer. It is never the default and never required to finish onboarding.

## 5. Honesty & safety guardrails (carried from the platform)

- **Capability-gated, field-verified writes only.** A recommendation can only be *approved into a write*
  if `wl_recorder_capability` reports, for that exact recorder model, `verdict=supported`, `write=true`,
  `safety_class=safe_write`, `evidence_class=FIELD_VERIFIED` (mig `0064`). Unknown/undocumented ⇒ the
  step is shown as "not available on your recorder", never silently attempted.
- **Nothing auto-applies during onboarding.** Every recommendation lands `proposed` and needs the human
  Approve tap; MANAGED auto-apply is off unless the tenant later opts a specific action into
  `site_managed_actions`.
- **Hard deny-list still holds.** Onboarding cannot express firmware/format/factory/reset/reboot/
  user/network/password/wipe — the `0063`/`0064` gate refuses them regardless of the wizard.
- **Honest capability copy.** If the recorder can't do something (e.g. line-crossing on a Cooper-I XVR),
  onboarding says so plainly and offers the software-defined alternative rather than a dead toggle.
- **Coverage honesty from day one.** The first brief states its coverage window; a partial first day
  says so (`wl_office_brief` `monitoring_coverage`), never a fabricated full-day summary.

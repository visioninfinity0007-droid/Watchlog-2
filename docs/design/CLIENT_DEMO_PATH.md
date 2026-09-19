# WatchLog Client Demo Path — the guided walkthrough

**Status:** design spec (item 26) · **Author:** Vision Infinity engineering · **Date:** 2026-09-10

> The demo tells one story end to end: *WatchLog proves your CCTV is working, finds what matters, lets
> you fix the recorder without touching it, and reports it in business language.* Every step below maps
> to a real, shipped feature and the RPC/table behind it — no slideware. Steps that need the **`SM-HP`
> `0.4.1 -> 0.4.2` field upgrade** to be *live* (not merely code-proven) are flagged, because those are
> the parts that only become truthful once `0.4.2` is running on the client's PC and back on the CCTV LAN.

**Fixtures rule (read first):** demo data (seeded sites, synthetic events, `drivers/mock.py`,
`prototype/sim/`) must run in a **dedicated demo tenant**, never in the client's production tenant
(Al-Khalid; live site `588cb40a`) and never written through `SM-HP`. The tenant-isolation gate
(`prototype/tests/test_tenant_isolation.py`) is what keeps a demo from leaking into or corrupting
production. Never seed fixtures into the live tenant to "make the demo look fuller".

Legend: **LIVE** = works in the deployed portal today · **NEEDS 0.4.2** = requires the `SM-HP` field
upgrade to be field-true · **PARTIAL/PENDING** = built but incomplete or unmerged (called out inline).

---

## The sequence

| # | Step | What you show | Backing feature / RPC | Field gate |
|---|---|---|---|---|
| 1 | **Login** | Role-scoped sign-in (owner/admin vs platform staff) | Supabase auth; `wl_my_tenant()` / `wl_platform_role()` scope every read | LIVE |
| 2 | **Site** | Pick the site from the fleet | `wl_sites()` (mig `0019`/`0034`) | LIVE |
| 3 | **Site Health** | Per-layer resolved state (agent / recorder / cameras / coverage %) — never one "Online" | `wl_site_health_details` (`0033`), `wl_portal_overview` (`0009`); layered health `wl_report_health` (`0044`) + `wl_report_camera_health` (`0045`/`0046`); coverage `wl_site_coverage_report` (`0062`) | LIVE for agent/coverage; **VideoLoss truth NEEDS 0.4.2** (below) |
| 4 | **Recorder** | Identity + what this exact model can/can't do, with evidence grade | `agents.device_vendor/device_model/device_driver`; `wl_recorder_profile(vendor, model)` over `recorder_capabilities` (`0061`) | LIVE (read-model) |
| 5 | **AI Diagnose** | One "Diagnose site" button fans out read-tier checks: reachable? clock drift? recording? video-loss? name/purpose mismatch? | Site Control READ: `wl_site_command_enqueue` --> `wl_agent_claim_command` --> `site_control.execute_read` (`inspect_recorder`, `get_video_loss_state`, ...) --> `wl_agent_complete_command` --> `wl_site_command_result` (`0063`) | **NEEDS 0.4.2** for the live cloud->agent->recorder round-trip |
| 6 | **Recommendation** | AI proposes a fix with reason + confidence ("Vehicle detection on an indoor camera -> disable") | `wl_site_command_propose_write(mode='recommend')` -> status `proposed`; capability-gated per model (`0064` + `0061`) | **NEEDS 0.4.2** to apply; the proposal itself is cloud-side |
| 7 | **Approval** | Human taps Approve; nothing reached the recorder before this | `wl_site_command_approve()` -> status `queued` | LIVE (cloud gate) |
| 8 | **Site Control** | Agent applies it transactionally: read -> backup -> diff -> apply -> read-back -> **verify** -> rollback on mismatch; before/after shown | `site_control.execute_write` (`configure_smd`/`configure_time`/`rename_channel`); audit folded by `wl_agent_complete_command` (`0064`) | **NEEDS 0.4.2** (live approved write) |
| 9 | **Activities / Journeys** | Per-area activity, access episodes, hourly traffic — Events -> Journeys -> Insights | `wl_office_brief` (`0060`/`0062`): `by_area`, `restricted`, `peak_hour`, episodes | LIVE for activities/episodes; **Journeys / unique-visitor = PENDING** (v1 model, not built — `ALKHALID_DAILY_BRIEF.md` §4) |
| 10 | **Incidents** | Filterable incident history with the still | `wl_incidents(...)` (`0020`, extended `0040`) | LIVE (read); **lifecycle PARTIAL** (promote-flag only); **on-demand footage PENDING-MERGE PR #36** |
| 11 | **Daily Intelligence** | The business-language brief: office day, activity, restricted-area, after-hours, CCTV health, coverage % | `wl_office_brief` (`0060`/`0062`) incl. `monitoring_coverage` | LIVE (computable parts); labels estimates honestly |
| 12 | **PDF** | "Print / save PDF" of the report page | Portal `control-room/reports` -> `window.print()` (browser print-to-PDF) — **there is no server-rendered PDF**; represent it as browser print | LIVE |
| 13 | **Alerts** | Daily summary to WhatsApp / email recipients | `report_recipients` / `report_deliveries` -> report runner -> n8n -> Evolution WhatsApp (email via SendGrid = unconfigured) | Engineering-ready; **0 WhatsApp sent to date — blocked on client recipient** (M1 item 7) |

## Why steps 5-8 (and VideoLoss in 3) need 0.4.2

`M1_PREDEPLOY_REVIEW.md` §2 and §7 are explicit: Site Control READ and SAFE-WRITE are **CODE/TEST
PROVEN, not field-proven** — the one live round-trip was attempted after the host left the CCTV LAN, and
no approved write has yet reached the recorder through the live Agent. VideoLoss truth
(`drivers/dahua.py current_faults()`) ships in **0.4.2**. So until the transactional `0.4.1 -> 0.4.2`
upgrade is done on `SM-HP` (installer SHA-256 in `M1_PREDEPLOY_REVIEW.md` §8) and the PC is back on the
`192.168.100.x` CCTV LAN:

- **Step 3 VideoLoss** ("Armory image unavailable") is honest only on 0.4.2 — before that the portal
  should not assert a camera-down truth it can't yet read.
- **Step 5 AI Diagnose** demonstrates the cloud plane (enqueue/claim/complete/result) but the live
  recorder read is unproven pre-0.4.2.
- **Steps 6-8 Recommend -> Approve -> Write** show the full permission model in the portal, but the
  *applied* write on the recorder needs 0.4.2.

**Demo guidance:** for a client demo *before* 0.4.2 is live, run steps 5-8 against a **demo-tenant mock
recorder** (`drivers/mock.py` / `prototype/sim/`) and clearly say "this is the sandbox recorder"; run
steps 1-4, 9-13 against the real tenant read-models. For the acceptance demo *after* 0.4.2, run 5-8
live on `SM-HP`/`DH-XVR1B08-I` and show the before/after/verified audit row (the `M1_PREDEPLOY_REVIEW.md`
§9 D-E-F live proofs). Never mix: demo fixtures stay in the demo tenant.

## Demo-safety checklist

1. Confirm you are in the **demo tenant** before any write/seed step (check `wl_my_tenant()` / the tenant
   badge). The isolation gate proves cross-tenant reads are refused — rely on it, don't defeat it.
2. Use `drivers/mock.py` / `prototype/sim/` for any pre-0.4.2 Site Control write; label it as sandbox.
3. Never enqueue a write against the client's live `site_id` / `SM-HP` during a rehearsal.
4. After a live acceptance demo, leave only the real audit rows; delete any demo-tenant scratch data
   from the demo tenant, not production.
5. If 0.4.2 is not yet live, present steps 5-8 as "the mechanism, proven in test; live proof is the
   0.4.2 field gate" — do not claim field-proven what `M1_PREDEPLOY_REVIEW.md` marks pending.

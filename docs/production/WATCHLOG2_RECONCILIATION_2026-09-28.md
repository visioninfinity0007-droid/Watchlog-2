# Watchlog-2 → Canonical Reconciliation — 2026-09-28

Canonical target: `Alkalid-security/Watchlog`
Mirror audited: `visioninfinity0007-droid/Watchlog-2`
Mirror head audited: `dfdabcbbe6172f62c253aecb1c0599e1627d1f5a`

## Audit scope

All paths changed on the Watchlog-2 build branch since 2026-09-26 were compared against canonical source using recursive Git tree blobs.

Initial audit:
- 72 changed paths
- 20 byte-identical in canonical
- 27 missing in canonical
- 25 present but intentionally different/newer

After reconciliation on `feat/tenant-reporting-governance`:
- 37 byte-identical paths
- virtual NVR/test-lab files and workflow ported
- discovery acceptance document ported
- restaurant DB/config/test stack ported
- snapshot-request capability merged into the newer canonical agent
- canonical portal kept newer/generic behavior and received restaurant + office reporting as semantic merges

## Restaurant migration renumbering

Canonical already owns `0120_recovered_snapshot_timestamps.sql`.
Therefore Watchlog-2 restaurant migrations were ported one number forward:

| Watchlog-2 | Canonical |
|---|---|
| 0120_restaurant_visual_analytics.sql | 0121_restaurant_visual_analytics.sql |
| 0121_vision_worker_runtime.sql | 0122_vision_worker_runtime.sql |
| 0122_schedule_vision_worker.sql | 0123_schedule_vision_worker.sql |
| 0123_restaurant_preopen_service_day.sql | 0124_restaurant_preopen_service_day.sql |
| 0124_restaurant_server_capture_scheduler.sql | 0125_restaurant_server_capture_scheduler.sql |
| 0125_chaiwala_ai_context_alignment.sql | 0126_chaiwala_ai_context_alignment.sql |
| 0126_chaiwala_report_windows.sql | 0127_chaiwala_report_windows.sql |
| 0127_chaiwala_analytics_quality.sql | 0128_chaiwala_analytics_quality.sql |

## Canonical-newer / intentionally different paths

The following categories remain byte-different because canonical contains newer production work and must not be downgraded to the mirror version:

- Windows installer/release pipeline;
- recorder discovery and driver handling;
- recovery / archive evidence handling;
- agent build/version metadata;
- related regression tests;
- canonical portal generic reporting behavior;
- canonical CI/windows release workflows.

Relevant canonical commits include the later Build 98/100, archive gap recovery and 5.0.23 production work. Mirror changes were reviewed for required semantics rather than copied backward.

One required mirror semantic was missing from the newer canonical agent and has been merged explicitly:
- `config_snapshot_requests` capability advertisement.

## Portal reconciliation

Restaurant reports from Watchlog-2 were merged into canonical without replacing canonical's existing office/security management report.

Canonical now has:
- Chai Wala Today / Yesterday / Last 7 / Last 30 reports;
- restaurant charts + analytics-quality recommendations;
- office Today / Yesterday / Last 7 / Last 30 reports;
- latest-completed-business-day semantics;
- office activity/coverage period charts;
- tenant reporting archive structure.

## AI/runtime source status

Live Supabase `watchlog-ai` version 21 and canonical checked-in `watchlog-ai` source are now synchronized for:
- last completed business-day resolution;
- business/service-day evidence windows;
- restaurant day/period tools;
- office period tools;
- office + restaurant intelligence contracts;
- natural customer-facing fallbacks;
- customer-boundary route auditing.

### Vision-worker source reconciliation

`prototype/supabase/functions/watchlog-vision-worker/index.ts`

Status: **RECONCILED.**

- deployed Supabase Edge Function version: 6;
- deployed source length: 18,842 bytes;
- Watchlog-2 build-branch source length: 18,842 bytes;
- deployed source and mirror source were byte-for-byte identical;
- source contains environment-variable references for `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, not hard-coded credentials;
- the exact deployed source has now been added to canonical Git.

There is no remaining file-level Watchlog-2 reconciliation exception from the audited 48-hour change set.


## Final blob-level audit on governance branch

Cumulative Watchlog-2 change set audited from Build 61 baseline `e7e8aa96` through mirror head `dfdabcb`:

- 72 changed paths total;
- 37 are byte-identical in `feat/tenant-reporting-governance`;
- 26 are present but byte-different because canonical is newer or the change was semantically merged;
- 9 mirror paths are absent by exact filename.

The 9 absent filenames resolve as:
- 8 restaurant migration paths intentionally renumbered from mirror `0120–0127` to canonical `0121–0128` because canonical already owns `0120_recovered_snapshot_timestamps.sql`;
- the former vision-worker source exception is now closed by adding the exact deployed v6 source to canonical Git.

No other cumulative Watchlog-2 path from this audit is unaccounted for.

The byte-different set was reviewed by category:
- canonical Windows installer/release/agent code is newer (Build 98/100 / 5.0.23 lineage) and must not be downgraded;
- portal report files are semantic merges that preserve canonical office/security behavior while adding restaurant + office reporting;
- `analytics_agent.py` retains newer canonical behavior plus the required `config_snapshot_requests` capability;
- test/CI files reflect newer canonical contracts or merged restaurant/reporting coverage;
- live `watchlog-ai` was previously ahead of checked-in source and is now synchronized; the deployed v6 vision-worker Edge Function source is also now synchronized into canonical Git.

## Rule going forward

Canonical `Alkalid-security/Watchlog/main` is the product source of truth.
Watchlog-2 is a handoff/mirror only and must not receive product work that is not reconciled back to canonical.


## Final continuation audit — post PR #71

A later Watchlog-2 main commit was created after the original 48-hour branch audit:

- mirror commit: `d3b2d0a10a8f4be490b98e9e28dcb208e6f6426b`
- file: `docs/release/CURRENT_WINDOWS_FIELD_CONTEXT_2026-09-28.md`

That mirror file described Build 76 / 5.0.21 as current field release authority. Canonical has newer 5.0.23 source/release context, so the file was **not copied as live authority**. Its field facts were reconciled into a canonical superseded-handoff document at the same path, with explicit links to the authoritative release ledger and current live context.

Additional final reconciliation completed:

- repaired `0129_office_reporting_context.sql` so the checked-in migration is actually executable;
- applied/recorded the repaired office reporting migration in production;
- added/applied `0130_business_day_evidence_window.sql`;
- synchronized live `watchlog-ai` back into canonical source;
- fixed the AI route-audit type for the customer-boundary route;
- upgraded live `watchlog-ai` to version 21;
- model-side evidence for “yesterday”/last-working-day/last-service-day now uses the same database-resolved business/service-day window as reports and visual summaries;
- added explicit summary + detailed-report templates inside every currently active tenant reporting tree.

Production migration history now includes:
- `office_reporting_context`
- `business_day_evidence_window`

The former vision-worker source exception is now closed. The exact deployed v6 source was copied only after verifying that it contains no literal credentials and matches the Watchlog-2 build-branch source byte-for-byte.

Final reconciliation status: **no known Watchlog-2 file-level change from the audited period remains unaccounted for.**


## Final mirror-head verification after PR #73

A fresh branch-head check was performed after the reporting/notification closure:

- `build/site-connector-v5-watchlog2` remains at `dfdabcbbe6172f62c253aecb1c0599e1627d1f5a`;
- mirror `main` remains at `d3b2d0a10a8f4be490b98e9e28dcb208e6f6426b`;
- the later mirror-main field handoff at `d3b2d0a` is already preserved in canonical as superseded context;
- no newer mirror head displaced either audited reference.

Canonical remains the only product source of truth.

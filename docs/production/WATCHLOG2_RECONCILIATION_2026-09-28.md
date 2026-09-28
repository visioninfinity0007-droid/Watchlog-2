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

Live Supabase `watchlog-ai` is ahead of the canonical checked-in source and currently includes:
- last completed business-day resolution;
- restaurant day/period tools;
- office period tools;
- office + restaurant intelligence contracts;
- natural customer-facing fallbacks.

The GitHub connector has repeatedly refused writes to provider/secret-sensitive Edge Function source.

### Outstanding source exception

`prototype/supabase/functions/watchlog-vision-worker/index.ts`

Status:
- live Edge Function exists and is deployed;
- Watchlog-2 source exists;
- connector blocks writing that provider/credential-handling source into canonical Git;
- no literal secret should be copied manually to bypass the safety control.

This is the only genuine file-level reconciliation exception remaining from the 48-hour Watchlog-2 audit.


## Final blob-level audit on governance branch

Cumulative Watchlog-2 change set audited from Build 61 baseline `e7e8aa96` through mirror head `dfdabcb`:

- 72 changed paths total;
- 37 are byte-identical in `feat/tenant-reporting-governance`;
- 26 are present but byte-different because canonical is newer or the change was semantically merged;
- 9 mirror paths are absent by exact filename.

The 9 absent filenames resolve as:
- 8 restaurant migration paths intentionally renumbered from mirror `0120–0127` to canonical `0121–0128` because canonical already owns `0120_recovered_snapshot_timestamps.sql`;
- 1 genuine source exception: `prototype/supabase/functions/watchlog-vision-worker/index.ts`.

No other cumulative Watchlog-2 path from this audit is unaccounted for.

The byte-different set was reviewed by category:
- canonical Windows installer/release/agent code is newer (Build 98/100 / 5.0.23 lineage) and must not be downgraded;
- portal report files are semantic merges that preserve canonical office/security behavior while adding restaurant + office reporting;
- `analytics_agent.py` retains newer canonical behavior plus the required `config_snapshot_requests` capability;
- test/CI files reflect newer canonical contracts or merged restaurant/reporting coverage;
- live `watchlog-ai` is ahead of checked-in provider-sensitive source and is documented as a source reconciliation exception alongside the vision worker.

## Rule going forward

Canonical `Alkalid-security/Watchlog/main` is the product source of truth.
Watchlog-2 is a handoff/mirror only and must not receive product work that is not reconciled back to canonical.

# Migration ledger reconciliation (2026-10-05)

Facts only. Production was read with read-only `SELECT` statements on 2026-10-05
(Supabase project `oyvgubyxmjlijiczjona`). Nothing in production was changed. The
exact queries are in the appendix. No secrets were selected: only migration names,
checksums, catalog metadata and function-body md5 values.

Refs: `origin/main` = `6488bab`; this branch = `chore/migration-ledger-alignment`
(adds `0142_stale_incident_clip_recovery.sql` and the recovered `0144`/`0145`).
Audit items: MNVR-064, WP-0 / PR-0 step 1.

## 1. Two ledgers

| Ledger | Written by | Key | Identity column |
|---|---|---|---|
| `public.schema_migrations` | `prototype/supabase/apply_migrations.py` | `filename` | `sha256` over the file with line endings normalized to LF (`normalized_sha`) |
| `supabase_migrations.schema_migrations` | Supabase migration API (MCP `apply_migration`) | `version` (timestamp) | `name`, `statements text[]` (the SQL text) |

`apply_migrations.py` reads only `public.schema_migrations`. A file whose name is
absent there is `PENDING`; a recorded file whose normalized sha256 differs is
`DRIFT` (`classify()`), and any `DRIFT` blocks the whole run (`build_plan()`).

## 2. `public.schema_migrations` (114 rows)

- Recorded: `0001`-`0110` except `0094`, then `0140`, `0141`, both `0142` files and
  `0143`. Latest row: `0143_site_period_facts.sql`, applied 2026-10-02 21:15:20 UTC.
- All 114 recorded rows match the repo bytes exactly. A digest over
  `filename:sha256` (query B3) is identical in production and in git, for the whole
  set and for each of the ranges `<0050`, `0050-0099`, `>=0100`
  (all: `7b5024031f9e3887e039427f6ea30f96`). This holds for `origin/main` plus
  `0142_stale_incident_clip_recovery.sql` and for this branch.
- Therefore **no recorded migration is in DRIFT**.
- On `origin/main` itself, `0142_stale_incident_clip_recovery.sql` is recorded in
  production but absent from the tree; this branch adds it (be2646d).

## 3. `supabase_migrations.schema_migrations` (77 rows)

- Versions `20260911091950` to `20261002224736`. The last three rows are
  `0143_site_period_facts`, `0144_portal_qa_truth_contracts` and
  `0145_camera_preview_performance`, applied in that order (21:15, 22:46 and 22:47 UTC
  on 2026-10-02), the same order as the repo numbering.
- 34 rows have text byte-identical (md5 after LF normalization) to a repo file
  (query B4): `0065`, `0067`-`0076`, `0078`-`0080`, `0082`-`0084`, `0111`-`0114`,
  `0115_context_aware_visual_review`, `0116`, `0129`-`0132`, `0134`-`0137`, `0143`,
  `0144`, `0145`.
- 43 rows match no repo file byte for byte:
  `0066_recorder_capability_kb_batch2`, `0077_recorder_capability_kb_batch3`,
  `0081_opening_closing_state_machine`, `camera_configuration_truth`,
  `recording_health_freshness`, `dahua_clip_export_truth`,
  `0088_current_agent_and_capability_truth`, `0089_recording_current_proof`,
  `0090_site_control_server_gate`, `0091_authority_aware_monitoring_coverage`,
  `0092_incident_lifecycle_schema`, `0093_incident_lifecycle_runtime`,
  `0095_incident_read_model`, `0096_people_intelligence_read_model`,
  `0097_incident_evidence_orchestrator`, `0098_recovery_and_coverage_classes`,
  `0099_daily_intelligence_coverage_classes`, `0100_sync_cameras_configured`,
  `0101_ai_workspace`, `0102_ai_message_integrity`,
  `0103_production_security_hardening`, `0104_ai_runtime_guardrails`,
  `temporary_alkhalid_sep17_snapshot_review_reader`,
  `remove_temporary_alkhalid_sep17_snapshot_review_reader`,
  `platform_ai_conversation_read`, `ai_chat_multimodal_admin`,
  `0111_recorder_push_status`, `restaurant_visual_analytics`,
  `restaurant_report_snapshot_integration`, `restaurant_context_site_type_compat`,
  `restaurant_visual_analytics_hardening`,
  `vision_worker_runtime_and_restaurant_service_day`, `schedule_vision_worker`,
  `vision_worker_safe_schedule`, `restaurant_preopen_service_day`,
  `restaurant_rpc_anon_revoke`, `restaurant_server_capture_scheduler`,
  `restaurant_capture_requires_agent_capability`, `recovered_snapshot_timestamps`,
  `existing_site_preflight_auth`, `watchlog_update_signing_vault`,
  `watchlog_update_signing_guard`, `rolling_saved_report_windows`.
  The numbered ones among them (`0066`-`0104`) are nonetheless recorded in
  `public.schema_migrations` with the repo sha256 (section 2).

## 4. Recovered production-only migrations 0144 and 0145

| File | supabase version | Bytes | md5 (production `md5(statements[1])` = file) | sha256 of the file (LF) |
|---|---|---|---|---|
| `0144_portal_qa_truth_contracts.sql` | `20261002224653` | 24004 | `e44730f590e1ecfd4fa82053b4867e7d` | `97afe9f5863f094134c30644a2972a4f1aa388f640c7ed97770535bef99a801d` |
| `0145_camera_preview_performance.sql` | `20261002224736` | 3144 | `2a2d4409ee216c8b741c7bdd9134253b` | `2b65b7c1c0022f12cd255acaaa149d16afbf2576248bc435230bda6d153e573a` |

- Method: one `statements[1]` element each, pure ASCII (no CR, tab, backslash or
  double quote; starts and ends with LF). 0145 was read in one `substr`; 0144 in
  eight line-aligned `substr` chunks whose md5 values were computed in SQL first
  (query A3). Every chunk md5, the whole-file md5 and the length matched on the first
  pass. The files carry no added header and no reformatting.
- Neither file is recorded in `public.schema_migrations`.
- Functions defined (md5 of each body equals production `md5(pg_proc.prosrc)`;
  SECURITY DEFINER, `proconfig`, anon / authenticated / PUBLIC execute equal
  production, query C1):

| Function | File | SECURITY DEFINER | search_path | anon | authenticated | PUBLIC | Earlier repo definitions |
|---|---|---|---|---|---|---|---|
| `wl_analytics_valid_purpose(text)` | 0144 | no | `public, pg_temp` | yes | yes | yes | 0024, 0103 |
| `wl_analytics_catalog()` | 0144 | no | `public` | no | yes | no | 0024, 0026, 0028, 0030 |
| `wl_notifications(uuid,integer)` | 0144 | yes | `public, pg_temp` | no | yes | no | 0117, 0131 |
| `wl_my_latest_report_snapshot(uuid)` | 0144 | yes | `public, pg_temp` | no | yes | no | none (new) |
| `wl_owner_site_truth(uuid)` | 0144 | yes | `public, pg_temp` | no | yes | no | none (new) |
| `wl_restaurant_day_truth(uuid,date)` | 0144 | yes | `public, pg_temp` | no | yes | no | none (new) |
| `wl_portal_overview(integer)` | 0144 | yes | `public` | no | yes | no | 0009 |
| `wl_camera_config_snapshot(uuid)` | 0145 | yes | `public` | no | yes | no | 0024, 0116 |
| `wl_camera_config_snapshots(uuid[])` | 0145 | yes | `public, pg_temp` | no | yes | no | none (new) |

- `service_role` has execute on all nine in production. On the disposable CI
  Postgres it has execute only where a migration grants it explicitly (the four new
  functions); that is a property of `ci_prelude.sql`, not of production.
- Applied after `0001`-`0143` on local postgres:16 with the CI runner: both files
  apply cleanly; the full `integration` job passes (section 8).
- Not covered by these two files: `wl_ai_context`, `wl_my_site_diagnosis`,
  `wl_agent_analytics_config`, `wl_upload_config_snapshot` are not redefined by
  0144 or 0145.

## 5. Repo migrations on `origin/main` not recorded in `public.schema_migrations`

30 files: `0111`-`0139` (two files carry `0115`). On this branch `0144` and `0145`
are also unrecorded there (section 4). Every one of the 30 would be `PENDING` for
`apply_migrations.py`.

Key objects were extracted from each file by pattern (functions; `create table`,
`create index`, `create trigger`, `create policy`; `alter table ... add column`;
`cron.schedule` job names; for the two data-only files the `reporting_prefs` JSON
path they set) and looked up in `pg_proc`, `pg_class`, `pg_trigger`, `pg_policies`,
`information_schema.columns`, `cron.job` and `public.site_business_context`
(query D3). "Present" means present by name. For functions the body md5 was also
compared with production `md5(prosrc)`; a body that is not live is either redefined
by a later repo file or differs from the repo's final definition.

| File | Byte-identical row in supabase ledger | Other key objects present | Functions present by name | Bodies from this file live | Not live, redefined later in repo | Not live, final repo body differs from production |
|---|---|---|---|---|---|---|
| `0111_visual_snapshot_pipeline` | `visual_snapshot_pipeline` | 6/6 | 7/7 | 3 | `wl_vision_claim_snapshots`, `wl_vision_complete_snapshot`, `wl_vision_day_for_worker`, `wl_vision_save_day_summary` | - |
| `0112_prioritize_recent_visual_review` | `prioritize_recent_visual_review` | 0/0 | 1/1 | 0 | `wl_vision_claim_snapshots` | - |
| `0113_private_media_mirror` | `private_media_mirror` | 6/6 | 3/3 | 2 | `wl_vision_claim_snapshots` | - |
| `0114_vision_worker_heartbeat` | `vision_worker_heartbeat` | 1/1 | 1/1 | 1 | - | - |
| `0115_context_aware_visual_review` | `context_aware_visual_review` | 0/0 | 2/2 | 0 | `wl_vision_claim_snapshots`, `wl_vision_day_for_worker` | - |
| `0115_recorder_push_status` | none (`0111_recorder_push_status` has different text) | 0/0 | 1/1 | 0 | - | `wl_agent_push_status` |
| `0116_camera_preview_realtime` | `camera_preview_realtime` | 6/6 | 2/2 | 1 | `wl_camera_config_snapshot` | - |
| `0117_site_lifecycle_notifications_camera_identity` | none | 4/4 | 8/8 | 6 | `wl_ai_context`, `wl_notifications` | - |
| `0118_visual_review_canonical_cameras` | none | 0/0 | 2/2 | 0 | `wl_vision_claim_snapshots`, `wl_vision_day_for_worker` | - |
| `0119_remote_agent_maintenance` | none (neither byte version) | 2/2 | 8/8 | 7 | - | `wl_agent_semver_triplet` |
| `0120_recovered_snapshot_timestamps` | none (`recovered_snapshot_timestamps` has different text) | 0/0 | 1/1 | 1 | - | - |
| `0121_restaurant_visual_analytics` | none (neither byte version; `restaurant_visual_analytics` has different text) | 21/21 | 5/5 | 1 | `wl_restaurant_site_config`, `wl_restaurant_day`, `wl_extract_restaurant_visual_observation` | `wl_vision_claim_snapshots` |
| `0122_vision_worker_runtime` | none (`vision_worker_runtime_and_restaurant_service_day` has different text) | 0/0 | 4/4 | 2 | `wl_vision_claim_snapshots_v2`, `wl_generate_daily_report` | - |
| `0123_schedule_vision_worker` | none (`schedule_vision_worker` has different text) | 1/1 (cron job) | 0/0 | - | - | - |
| `0124_restaurant_preopen_service_day` | none (`restaurant_preopen_service_day` has different text) | 0/0 | 2/2 | 0 | `wl_restaurant_day`, `wl_generate_daily_report` | - |
| `0125_restaurant_server_capture_scheduler` | none (`restaurant_server_capture_scheduler` has different text) | 3/3 | 3/3 | 2 | `wl_vision_claim_snapshots_v2` | - |
| `0126_chaiwala_ai_context_alignment` | none | 0/0 | 5/5 | 2 | `wl_restaurant_day`, `wl_generate_daily_report`, `wl_ai_context` | - |
| `0127_chaiwala_report_windows` | none | 0/0 | 2/2 | 1 | `wl_restaurant_period` | - |
| `0128_chaiwala_analytics_quality` | none | 0/0 | 5/5 | 5 | - | - |
| `0129_office_reporting_context` | `office_reporting_context` | 0/0 | 3/3 | 3 | - | - |
| `0130_business_day_evidence_window` | `business_day_evidence_window` | 0/0 | 1/1 | 1 | - | - |
| `0131_report_recommendation_notifications` | `report_recommendation_notifications` | 0/0 | 1/1 | 0 | `wl_notifications` | - |
| `0132_chaiwala_yesterday_service_day_rule` | `chaiwala_yesterday_service_day_rule` | 1/1 (JSON path) | 0/0 | - | - | - |
| `0133_existing_site_preflight_auth` | none (`existing_site_preflight_auth` has different text) | 0/0 | 1/1 | 0 | - | `wl_agent_preflight_auth` |
| `0134_report_recommendation_feedback` | `report_recommendation_feedback` | 3/3 | 4/4 | 4 | - | - |
| `0135_report_recommendation_feedback_indexes` | `report_recommendation_feedback_indexes` | 2/2 | 0/0 | - | - | - |
| `0136_service_day_monitoring_truth` | `service_day_monitoring_truth` | 0/0 | 2/2 | 2 | - | - |
| `0137_chaiwala_monitoring_context_truth` | `chaiwala_monitoring_context_truth` | 1/1 (JSON path) | 0/0 | - | - | - |
| `0138_rolling_saved_report_windows` | none (`rolling_saved_report_windows` has different text) | 0/0 | 1/1 | 1 | - | - |
| `0139_visual_service_day_windows` | none | 0/0 | 2/2 | 2 | - | - |

Summary: every extracted key object of all 30 files exists in production by name,
and every function they define exists by name. Four functions whose last repo
definition is in this range have a production body that differs from that
definition (query D2):

| Function | Repo final definition | Production `md5(prosrc)` / length | Repo `md5(prosrc)` / length (local chain) | Observed difference |
|---|---|---|---|---|
| `wl_agent_preflight_auth(uuid,text)` | 0133 | `5136b004c62e9374d3d2c335f337c10b` / 412 | `d5021bbc7617cc28785673bfa7d92c84` / 413 | production lacks one blank line; otherwise identical |
| `wl_agent_semver_triplet(text)` | 0119 | `8a4f5c64381d1080dbc565c409b84a27` / 205 | `54188d1204ba50bdc46968077191d09c` / 207 | regex literal: production `'^([0-9]+)\.([0-9]+)\.([0-9]+)'`, repo `'^([0-9]+)\\.([0-9]+)\\.([0-9]+)'` |
| `wl_agent_push_status(uuid,text)` | 0115_recorder_push_status | `547120b7cb00b80309dbb74fb72aa7b7` / 790 | `9081f7800c70c809ce2f4dbdab542edd` / 768 | not characterized (body not read) |
| `wl_vision_claim_snapshots(integer,text)` | 0121 | `ecc57aabda24344618051ffb5b49dffe` / 5141 | `a097503ce60a1b40d0e5a689e8fa0cc1` / 4793 | not characterized (body not read) |

`wl_agent_semver_triplet('5.0.27')` returns `{5,0,27}` in production and `{0,0,0}`
on the local repo chain; both servers run with `standard_conforming_strings = on`.
Its only caller in the repo, 0119 `wl_agent_report_capabilities`, keeps
`site_control_runtime` and `remote_update_v1` only when the parsed version is
`>= {5,0,22}`.

## 6. 0119 and 0121 (MNVR-064)

- 6488bab changed only dollar-quote delimiters in both files (`as $` to `as $$`,
  `$;` to `$$;`): five places in 0119, two in 0121. Function bodies (`prosrc`) are
  unchanged by that edit. `apply_migrations.py` applies the same rewrite to its
  execution copy when `WATCHLOG_CI_PLAIN_POSTGRES=1`.

| File | Version | Normalized sha256 | md5 |
|---|---|---|---|
| `0119_remote_agent_maintenance.sql` | 8aafb24 (= 6488bab^) | `86b2683926f750671d6fe37a4670ed8915becdf0c4cdea2549f4b9f5ffe4bd40` | `3d0f6896484ba9c328927f4e99342f69` |
| `0119_remote_agent_maintenance.sql` | 6488bab | `c544689df8ec78bfa0a80abdfaaa36bd4212e5ed57fff33e3ad57e9414bbb95c` | `6c3e2353099013e4794592d21a2ae90e` |
| `0121_restaurant_visual_analytics.sql` | 8aafb24 (= 6488bab^) | `d90c783782dcca8215691012dade0dc193e4610ba1cc2ed17ec8f75d76494679` | `1b712b33dfd32cd80aee97eddb551f63` |
| `0121_restaurant_visual_analytics.sql` | 6488bab | `49efa85d18aeaab9f84d2e08c1ee5f6b30566f9d539a58a1ab15fba4d20a82f0` | `c0d22ae5b5f4725503498f3a44264333` |

- Neither file is recorded in `public.schema_migrations` under any version, and no
  `supabase_migrations` row is byte-identical to either version of either file
  (query D1).
- `classify()` returns `PENDING` for a name absent from `public.schema_migrations`,
  so `apply_migrations.py` treats 0119 and 0121 as **PENDING, not DRIFT**, whichever
  bytes are committed. The DRIFT scenario in MNVR-064 requires a recorded row; there
  is none.
- Production objects: 0119's table, index and eight functions exist; seven of the
  eight bodies equal the repo; `wl_agent_semver_triplet` differs (section 5). 0121's
  4 tables, 12 indexes, trigger and 4 policies exist; 1 of its 5 function bodies is
  live, 3 are redefined later in the repo, `wl_vision_claim_snapshots` differs.
- Both byte versions of 0119 contain the same `\\.` regex literal.

## 7. What a bare `apply_migrations.py` run against production would do today

Derived from the code and the ledger above, not executed against production:

- Plan: 32 `PENDING` files on this branch (the 30 in section 5 plus 0144 and 0145),
  0 `DRIFT`, so no fail-closed block; it would execute them in filename order, each
  file sent as one multi-statement execution, and exit at the first error.
- Local rehearsal on the disposable Postgres: apply the full chain, delete the 32
  rows from `schema_migrations` to mirror production's ledger, rerun
  `apply_migrations.py`. Result: `0111`-`0120` re-executed without error (their
  function bodies were rewritten to the repo text, including 0119's
  `wl_agent_semver_triplet`); `0121` failed with
  `policy "restaurant_camera_profiles_no_direct" for table "restaurant_camera_profiles" already exists`
  and the run exited. That policy exists in production (section 5).

## 8. Local verification of this branch

- `python tools/lint_migrations.py`: ok, 146 files, `0001..0145`.
- CI `integration` job replayed locally on a disposable postgres:16 (operator's `wl-ci-local.py`):
  43 pass, 0 fail, 1 skip (package install). The new step
  `test_production_only_migrations.py --pg` passes; baseline without 0144/0145:
  42 pass.
- `backend` job: 70 pass, 1 fail, 2 skip. The failure is `Public website claims +
  maturity contract`, which needs PHP (absent on this machine); unrelated to this
  branch. `portal-contracts` job: 6 pass.
- `tools/reserved_migrations.json` on this branch never reserved 0144 or 0145
  (only 0094), so there was nothing to remove here. `mr/db-contracts` reserves both;
  that reservation has to be dropped there once this branch is merged.

## Appendix: exact queries

All run read-only against production on 2026-10-05, in the order listed. Where a
query carries a `VALUES` list, the list was generated from `git` blobs
(LF-normalized md5 or sha256) and is reproduced exactly.

### A1. Identity of the 0144/0145 rows

```sql
select version, name, array_length(statements,1) as n_statements, length(statements[1]) as len1, md5(statements[1]) as md5_1, octet_length(statements[1]) as octets1 from supabase_migrations.schema_migrations where name in ('0144_portal_qa_truth_contracts','0145_camera_preview_performance') order by version
```

### A2. Character profile of the statement text

```sql
select name,
 length(statements[1]) - length(replace(statements[1], chr(13), '')) as n_cr,
 length(statements[1]) - length(replace(statements[1], chr(10), '')) as n_lf,
 length(statements[1]) - length(replace(statements[1], chr(9), '')) as n_tab,
 length(statements[1]) - length(replace(statements[1], '\', '')) as n_backslash,
 length(statements[1]) - length(replace(statements[1], '"', '')) as n_dquote,
 right(statements[1], 1) = chr(10) as ends_lf,
 left(statements[1], 60) as head,
 statements[1] ~ '[^\x20-\x7E\n\t\r]' as has_nonprint
from supabase_migrations.schema_migrations where name in ('0144_portal_qa_truth_contracts','0145_camera_preview_performance') order by version
```

### A3. Line-aligned chunk map of 0144 with per-chunk md5

```sql
with s as (select statements[1] as t from supabase_migrations.schema_migrations where name='0144_portal_qa_truth_contracts'),
l as (select t2.line, t2.n from s, regexp_split_to_table(s.t, chr(10)) with ordinality as t2(line,n)),
c as (select n, line, sum(length(line)+1) over (order by n) - (length(line)+1) + 1 as start_pos from l),
g as (select ((n-1)/80) as chunk, min(start_pos) as start_pos, sum(length(line)+1) as len, min(n) as first_line, max(n) as last_line from c where n <= 611 group by 1)
select g.chunk, g.first_line, g.last_line, g.start_pos, g.len, md5(substr(s.t, g.start_pos::int, g.len::int)) as md5 from g, s order by chunk
```

### A4. Chunk reads (0144 used the same statement with each (start, length) pair)

```sql
-- 0145 in one read:
select substr(statements[1], 1, 3144) as chunk from supabase_migrations.schema_migrations where name = '0145_camera_preview_performance'
-- 0144 in eight reads, (start, length) from A3:
-- (1,5371) (5372,3411) (8783,2485) (11268,2948) (14216,2485) (16701,2601) (19302,2856) (22158,1847)
select substr(statements[1], 1, 5371) as chunk0 from supabase_migrations.schema_migrations where name='0144_portal_qa_truth_contracts'
```

### B1. supabase_migrations ledger listing

```sql
select version, name, md5(array_to_string(statements, E'\n')) as md5_all, array_length(statements,1) as n from supabase_migrations.schema_migrations order by version
```

### B2. public.schema_migrations listing

```sql
select filename, sha256, applied_at from public.schema_migrations order by filename
```

### B3. Ledger digest (compared with the same digest computed from git)

```sql
select count(*) as n,
       md5(string_agg(filename || ':' || sha256, E'\n' order by filename collate "C")) as digest_all,
       md5(string_agg(filename || ':' || sha256, E'\n' order by filename collate "C") filter (where filename < '0050')) as d_lt_0050,
       md5(string_agg(filename || ':' || sha256, E'\n' order by filename collate "C") filter (where filename >= '0050' and filename < '0100')) as d_0050_0099,
       md5(string_agg(filename || ':' || sha256, E'\n' order by filename collate "C") filter (where filename >= '0100')) as d_ge_0100
from public.schema_migrations
```

### B4. supabase_migrations rows byte-identical to a repo file (0065 onward)

```sql
with repo(f, md5) as (values ('0065','a99531a66779a75dcb9ae6e0f9df94d3'),('0066','f23dea39d17268cdc09215ca7ef6cf39'),('0067','eb9977990f2f8d6254c5ea3dbad7e4b7'),('0068','9c29d9424fa2d81329f096e699aea572'),('0069','dda9f7c3ce0ee0cc39aa424d00cb3169'),('0070','33b19178214675960481ef61c0b1a0f8'),('0071','ab035c7196b0ee192918687d9f23484f'),('0072','0949de45b424d2c904a88e2f04844636'),('0073','f35694e30587796236dae0636e635a8b'),('0074','59f4e52df2bb5debe285b9582812989d'),('0075','b73bd63587f1de7c54f8ff6502e95669'),('0076','827ea2799f45d7961e9156639ef58447'),('0077','96ca9a4b80505892ed37a21fd734421c'),('0078','0966ccab4e1775fddff910520a0fe7c2'),('0079','7631edb58b30537c8ec1bb928ceeecbd'),('0080','8d4ee6c23c5585a077dfb0591242ebd1'),('0081','b26b91515338e767cf8fc3523e14f3e5'),('0082','332fd9cb16b1be1ae8210b3ef96656b5'),('0083','1ebcd8c63af12b16274f71d9d8abb5e9'),('0084','661e5f48b0a6063b41d5c71a9511de6d'),('0085','ca9b965812b3efbcbdce2eb92c86729f'),('0086','3a9bd352b56d8a2adcdf5879763b7c02'),('0087','71d157bdf75f2dbb560faf597ad5d8dd'),('0088','9a29c0cfe162bdadd16591e02f77481d'),('0089','581440dbf946beb7099ce9dba9c24aaf'),('0090','5eb329b2ef2bfdfc76dc0fbca45522e9'),('0091','f1b80386d5b995cc354d39bd75239d90'),('0092','f77f4efa5e350bd0007272df05304ada'),('0093','2a5f407b699d6bfbb184dbd9b384f721'),('0095','aaa28e2dd49f5c83a590040af2f04732'),('0096','052269b3c4572a3b5a6690629cdae7ca'),('0097','28f92903fefc198fdacd984978682186'),('0098','6cb11c9295c09d781702138fb36a4254'),('0099','126c07fc71dbaa7c057b2245c6f3997a'),('0100','c6d7cd4fad8516e7b3bf1cc09cab1aa3'),('0101','522cf091d5e814aae36aac7bde88f6a4'),('0102','932fd5a3d21bc4ba51dc3cfeb7bab1cd'),('0103','b2904d4aec38ded23c1bda23a91e07e3'),('0104','02cf0a067da1f6515796c7fa2b1f997e'),('0105','084c523c0ad63efc8b1b878b2a16e848'),('0106','da137860f50140989ad0a4716b4cfe5f'),('0107','f56731805ea482e9c9f7aadf5097bd48'),('0108','796bf507ab2a690ec92a94bab1c23a8b'),('0109','0a3400aa673d56678aa759c8d90cd486'),('0110','c819e1736ab536dbb67199a4a06413a5'),('0111','8cf57fefc125010421b3b0c020735c95'),('0112','3639cc5d0bf62302babac3fbc4d36beb'),('0113','05cdae544c3e44ee4a594522651c26da'),('0114','da83fa249dcac5f3858c3f64eace8029'),('0115','58ae9d0ee4fd1f74d02cf000f1a82fd2'),('0115b','ac8e878bec6a1a5da3f79039ea21760b'),('0116','5bd9a9bc084a717f30cafc4ef9e8837d'),('0117','305b0b9c98b1217ac7caf694741c2beb'),('0118','707709c7de4924b16446f00446357052'),('0119','6c3e2353099013e4794592d21a2ae90e'),('0120','9ecf424f778a68df94d7173fe09c8b7b'),('0121','c0d22ae5b5f4725503498f3a44264333'),('0122','3b623cc751fb714bd70b0a3803caefa9'),('0123','2f26bc73a4263ebb43e1f627f1f12ffe'),('0124','a26fc68a848b228f3f254d82c30aea99'),('0125','01de2497544792dfb749743f895c5ad7'),('0126','dd5b51e4a6838e4847bd01e1de741f99'),('0127','d788913a8475fa0de68edfc975e3abf3'),('0128','7f969681d57d2b4765e02452cee2b4ff'),('0129','976bd97a4cf653b1e8d67350b1316dce'),('0130','1a34c14100675e7fabc877cd193734a4'),('0131','1906de44d0ea84d8ddbeb7ead7d6990a'),('0132','c474f511ff01835da72fdcc35b40c169'),('0133','7d50350525f45bbbd3a24045f9cf6825'),('0134','9b9c59ea42255fc2043fa286b3dfd87e'),('0135','b41e5204fab7923c9676d3488d7e8178'),('0136','bf95343c6b256e96cf99c738fd8aec23'),('0137','725c38b3c9ef2b8e89bfc824c4b54395'),('0138','e4727f6a4a291721e96368783fcd85b4'),('0139','256f088eee5f8b33d32b12af8ac18424'),('0140','14f5e840b6cef00e2959d01523a6f54f'),('0141','8b18591b04904f8a6ef172aa9c18f4be'),('0142','b1d24285b5ec878da717fc67589567c0'),('0142b','a3e75ed2d881c88cc1755201dac6d980'),('0143','de0b9a3da8e4c71afcb4c3901f05001c'),('0144','e44730f590e1ecfd4fa82053b4867e7d'),('0145','2a2d4409ee216c8b741c7bdd9134253b'),('0119@8aafb24','3d0f6896484ba9c328927f4e99342f69'),('0121@8aafb24','1b712b33dfd32cd80aee97eddb551f63'))
select s.version, s.name, string_agg(r.f, ',') as identical_repo_file
from supabase_migrations.schema_migrations s
left join repo r on r.md5 = md5(array_to_string(s.statements, E'
'))
group by s.version, s.name
order by s.version
```

### C1. Functions of 0144/0145: body md5, security, search_path, execute grants

```sql
select p.oid::regprocedure::text as sig, p.prosecdef as secdef, p.provolatile as vol,
       array_to_string(p.proconfig, ';') as cfg, md5(p.prosrc) as src_md5,
       has_function_privilege('anon', p.oid, 'execute') as anon_x,
       has_function_privilege('authenticated', p.oid, 'execute') as auth_x,
       has_function_privilege('service_role', p.oid, 'execute') as svc_x,
       coalesce(p.proacl::text, '(default)') ~ '(^\{|,)=X' or p.proacl is null as public_x
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('wl_analytics_valid_purpose','wl_analytics_catalog','wl_notifications',
                    'wl_my_latest_report_snapshot','wl_owner_site_truth','wl_restaurant_day_truth',
                    'wl_portal_overview','wl_camera_config_snapshot','wl_camera_config_snapshots')
order by 1
```

### D1. Unrecorded repo files (and both 0119/0121 versions) against both ledgers

```sql
with repo(filename, md5) as (values
('0111_visual_snapshot_pipeline.sql','8cf57fefc125010421b3b0c020735c95'),
('0112_prioritize_recent_visual_review.sql','3639cc5d0bf62302babac3fbc4d36beb'),
('0113_private_media_mirror.sql','05cdae544c3e44ee4a594522651c26da'),
('0114_vision_worker_heartbeat.sql','da83fa249dcac5f3858c3f64eace8029'),
('0115_context_aware_visual_review.sql','58ae9d0ee4fd1f74d02cf000f1a82fd2'),
('0115_recorder_push_status.sql','ac8e878bec6a1a5da3f79039ea21760b'),
('0116_camera_preview_realtime.sql','5bd9a9bc084a717f30cafc4ef9e8837d'),
('0117_site_lifecycle_notifications_camera_identity.sql','305b0b9c98b1217ac7caf694741c2beb'),
('0118_visual_review_canonical_cameras.sql','707709c7de4924b16446f00446357052'),
('0119_remote_agent_maintenance.sql','6c3e2353099013e4794592d21a2ae90e'),
('0119_remote_agent_maintenance.sql@8aafb24','3d0f6896484ba9c328927f4e99342f69'),
('0120_recovered_snapshot_timestamps.sql','9ecf424f778a68df94d7173fe09c8b7b'),
('0121_restaurant_visual_analytics.sql','c0d22ae5b5f4725503498f3a44264333'),
('0121_restaurant_visual_analytics.sql@8aafb24','1b712b33dfd32cd80aee97eddb551f63'),
('0122_vision_worker_runtime.sql','3b623cc751fb714bd70b0a3803caefa9'),
('0123_schedule_vision_worker.sql','2f26bc73a4263ebb43e1f627f1f12ffe'),
('0124_restaurant_preopen_service_day.sql','a26fc68a848b228f3f254d82c30aea99'),
('0125_restaurant_server_capture_scheduler.sql','01de2497544792dfb749743f895c5ad7'),
('0126_chaiwala_ai_context_alignment.sql','dd5b51e4a6838e4847bd01e1de741f99'),
('0127_chaiwala_report_windows.sql','d788913a8475fa0de68edfc975e3abf3'),
('0128_chaiwala_analytics_quality.sql','7f969681d57d2b4765e02452cee2b4ff'),
('0129_office_reporting_context.sql','976bd97a4cf653b1e8d67350b1316dce'),
('0130_business_day_evidence_window.sql','1a34c14100675e7fabc877cd193734a4'),
('0131_report_recommendation_notifications.sql','1906de44d0ea84d8ddbeb7ead7d6990a'),
('0132_chaiwala_yesterday_service_day_rule.sql','c474f511ff01835da72fdcc35b40c169'),
('0133_existing_site_preflight_auth.sql','7d50350525f45bbbd3a24045f9cf6825'),
('0134_report_recommendation_feedback.sql','9b9c59ea42255fc2043fa286b3dfd87e'),
('0135_report_recommendation_feedback_indexes.sql','b41e5204fab7923c9676d3488d7e8178'),
('0136_service_day_monitoring_truth.sql','bf95343c6b256e96cf99c738fd8aec23'),
('0137_chaiwala_monitoring_context_truth.sql','725c38b3c9ef2b8e89bfc824c4b54395'),
('0138_rolling_saved_report_windows.sql','e4727f6a4a291721e96368783fcd85b4'),
('0139_visual_service_day_windows.sql','256f088eee5f8b33d32b12af8ac18424'),
('0143_site_period_facts.sql','de0b9a3da8e4c71afcb4c3901f05001c'),
('0144_portal_qa_truth_contracts.sql','e44730f590e1ecfd4fa82053b4867e7d'),
('0145_camera_preview_performance.sql','2a2d4409ee216c8b741c7bdd9134253b'))
select r.filename, s.version, s.name as supabase_name,
       exists (select 1 from public.schema_migrations m where m.filename = split_part(r.filename, '@', 1)) as in_public_ledger
from repo r
left join supabase_migrations.schema_migrations s on md5(array_to_string(s.statements, E'
')) = r.md5
order by r.filename
```

### D3. Key objects of the unrecorded files

```sql
with o(f, kind, name, md5, superseded) as (values
('0111','function','wl_queue_snapshot_visual_review','32152dd0e5a553e94d5a348b37339a51',false),
('0111','function','wl_vision_claim_snapshots','4fc0aa0cfd678d633d9da555acab40aa',true),
('0111','function','wl_vision_complete_snapshot','e9f34c0716ac717adcd590d291345567',true),
('0111','function','wl_vision_fail_snapshot','d55946e0021749fa3f58e2c875985627',false),
('0111','function','wl_vision_day_for_worker','d6d38f13bd3772ec85898715d14e9ce0',true),
('0111','function','wl_vision_save_day_summary','c55c13ac5900f0ae9e3d1b6513e0c2cf',true),
('0111','function','wl_my_visual_day','c67deb819b09c5c7a35068e4551b9923',false),
('0111','table','snapshot_visual_reviews','',false),
('0111','table','visual_day_summaries','',false),
('0111','index','snapshot_visual_reviews_queue_idx','',false),
('0111','index','snapshot_visual_reviews_site_day_idx','',false),
('0111','index','visual_day_summaries_tenant_date_idx','',false),
('0111','trigger','trg_snapshot_visual_review_queue','',false),
('0112','function','wl_vision_claim_snapshots','2e798ab2650f05e9e7a6904faaca1c36',true),
('0113','function','wl_vision_mark_media','fec92aac88086151a20d662ce4903e22',false),
('0113','function','wl_vision_claim_snapshots','bca99ce4f4277bb7f49fcec5a1086102',true),
('0113','function','wl_vision_complete_snapshot','0abf726bea5a49b6bae7b0d63bf0f9bd',false),
('0113','index','snapshot_visual_reviews_media_idx','',false),
('0113','column','snapshot_visual_reviews.media_bucket','',false),
('0113','column','snapshot_visual_reviews.media_key','',false),
('0113','column','snapshot_visual_reviews.media_sha256','',false),
('0113','column','snapshot_visual_reviews.media_bytes','',false),
('0113','column','snapshot_visual_reviews.media_mirrored_at','',false),
('0114','function','wl_vision_worker_heartbeat','60c3b97d4a5c881f7de378f35d9d4195',false),
('0114','table','vision_worker_status','',false),
('0115','function','wl_vision_claim_snapshots','e3c10a702047092563cadeb5a879423c',true),
('0115','function','wl_vision_day_for_worker','a85602d5f551fb793cafe15d2dcfc37c',true),
('0115b','function','wl_agent_push_status','9081f7800c70c809ce2f4dbdab542edd',false),
('0116','function','wl_signal_camera_preview','9f9508634fa71a84cb941d2797201565',false),
('0116','function','wl_camera_config_snapshot','d7c272c3210933881517df16d73146c8',true),
('0116','table','camera_snapshot_signals','',false),
('0116','index','camera_snapshot_signals_site_idx','',false),
('0116','index','snapshots_camera_captured_idx','',false),
('0116','trigger','trg_camera_config_snapshot_realtime_signal','',false),
('0116','trigger','trg_event_snapshot_realtime_signal','',false),
('0116','policy','camera_snapshot_signals.portal_read_camera_snapshot_signals','',false),
('0117','function','wl_sync_cameras','b4bb075a2042b5e8a97902039f04cac9',false),
('0117','function','wl_sites','1bdffcd72c6b36131116e662f24389d9',false),
('0117','function','wl_ai_context','c52451b9fe310f3a72c8f14eff306012',true),
('0117','function','wl_agent_analytics_config','e3f6f1ae6510451e8e8484ef776ec64a',false),
('0117','function','wl_remove_site','c840eebdcd368c9f97018be6e9628142',false),
('0117','function','wl_notifications','51a5a10a139ef4cb792514a7ba066f10',true),
('0117','function','wl_notification_mark_read','7dc2870694f4316c4e9ec0d4725e54d7',false),
('0117','function','wl_notifications_mark_all_read','dff0d32e5def1bf596c91137fb7bb09d',false),
('0117','table','notification_reads','',false),
('0117','index','notification_reads_user_idx','',false),
('0117','column','cameras.is_canonical','',false),
('0117','column','cameras.physical_channel','',false),
('0118','function','wl_vision_claim_snapshots','fb90e04c9963be724eb9c6b7cd1a1624',true),
('0118','function','wl_vision_day_for_worker','09bd47fab9fec60477368a2fa112599c',true),
('0119','function','wl_known_capabilities','1d4e5e4acc69645dd01143cf90115b41',false),
('0119','function','wl_agent_semver_triplet','54188d1204ba50bdc46968077191d09c',false),
('0119','function','wl_agent_report_capabilities','53bf6b70407dd58fadf2b4e6b75c9329',false),
('0119','function','wl_platform_request_agent_update','1e33ec75c140af7711a826c5fa079c20',false),
('0119','function','wl_agent_claim_update_request','127858e34f479231a096137ec50ee1cc',false),
('0119','function','wl_agent_stage_update_request','6c4177f534f5f1b9d606d7604264bb48',false),
('0119','function','wl_agent_complete_update_request','80b02259b2ae13124d0b2237009ad0ab',false),
('0119','function','wl_platform_agent_update_status','59259303a5123372cf11ff5cca9f1d73',false),
('0119','table','agent_update_requests','',false),
('0119','index','agent_update_requests_site_status_idx','',false),
('0120','function','wl_ingest_events','8f4576d901fbe3783ce7836267be7a49',false),
('0121','function','wl_analytics_valid_site_type','83427ab8e30837c115327d1d7983d113',false),
('0121','function','wl_restaurant_site_config','2cc907d8c657b182ab63100f8812407a',true),
('0121','function','wl_restaurant_day','221b99cc7a3d8e3b4f3fd062ccf44f8a',true),
('0121','function','wl_extract_restaurant_visual_observation','0132ec198628fb8d851871d4667012f5',true),
('0121','function','wl_vision_claim_snapshots','a097503ce60a1b40d0e5a689e8fa0cc1',false),
('0121','table','restaurant_camera_profiles','',false),
('0121','table','restaurant_tables','',false),
('0121','table','restaurant_visual_observations','',false),
('0121','table','restaurant_table_observations','',false),
('0121','index','restaurant_camera_profiles_site_idx','',false),
('0121','index','restaurant_camera_profiles_tenant_idx','',false),
('0121','index','restaurant_tables_camera_idx','',false),
('0121','index','restaurant_tables_site_idx','',false),
('0121','index','restaurant_visual_observations_site_time_idx','',false),
('0121','index','restaurant_visual_observations_camera_time_idx','',false),
('0121','index','restaurant_table_observations_table_time_idx','',false),
('0121','index','restaurant_table_observations_site_time_idx','',false),
('0121','index','restaurant_tables_tenant_site_idx','',false),
('0121','index','restaurant_visual_observations_tenant_site_time_idx','',false),
('0121','index','restaurant_table_observations_tenant_site_time_idx','',false),
('0121','index','restaurant_table_observations_camera_time_idx','',false),
('0121','trigger','trg_extract_restaurant_visual_observation','',false),
('0121','policy','restaurant_camera_profiles.restaurant_camera_profiles_no_direct','',false),
('0121','policy','restaurant_tables.restaurant_tables_no_direct','',false),
('0121','policy','restaurant_visual_observations.restaurant_visual_observations_no_direct','',false),
('0121','policy','restaurant_table_observations.restaurant_table_observations_no_direct','',false),
('0122','function','wl_vision_worker_expected_secret','a812fd30df37bc547580e4c7a4ebc394',false),
('0122','function','wl_assert_my_site','c08e6d5d4b6029b106637a47bdd7a7f0',false),
('0122','function','wl_vision_claim_snapshots_v2','d90df3db19f923005ced71717f478083',true),
('0122','function','wl_generate_daily_report','8de8fde94ce2475e040567ab8a393d60',true),
('0123','cron','watchlog-vision-worker','',false),
('0124','function','wl_restaurant_day','1d1d943d0a098ccaa042ff2720b980b8',true),
('0124','function','wl_generate_daily_report','65faf971dec236363c020e1a5ea4f1d7',true),
('0125','function','wl_restaurant_schedule_snapshot_requests','354ec6ab8b0eb824038e783b761d6f88',false),
('0125','function','wl_upload_config_snapshot','4fb513cae459f0251eae9189e2e75974',false),
('0125','function','wl_vision_claim_snapshots_v2','f441ef025a076446d1a7fab7b466bbc5',true),
('0125','index','camera_snapshot_requests_restaurant_due_idx','',false),
('0125','column','camera_snapshot_requests.request_source','',false),
('0125','cron','watchlog-restaurant-snapshot-scheduler','',false),
('0126','function','wl_extract_restaurant_visual_observation','1c3c56754380bfd4724ef4884fe11388',false),
('0126','function','wl_vision_claim_snapshots_v2','25787ce940f1409f11c3d3ddcc3f79e6',false),
('0126','function','wl_restaurant_day','45456740068b371449696225f776f5b2',true),
('0126','function','wl_generate_daily_report','cc52ceed722f90953b9702ec6d5f3da3',true),
('0126','function','wl_ai_context','596e0b3e11a6b768ed8852148562c2a0',true),
('0127','function','wl_restaurant_period','0270ca80a1a4ccde2b55a48625c74ecf',true),
('0127','function','wl_restaurant_site_config','4fe6b81e46d47032450a135a4eed6782',false),
('0128','function','wl_restaurant_quality_summary','eba05035ab6c6a8beb7b43ae8d8bf85b',false),
('0128','function','wl_restaurant_day','097d9e10fa1acb74a2cf8db638f1ce4f',false),
('0128','function','wl_restaurant_period','6dcf1339ed1d775583b52a6aa022ad88',false),
('0128','function','wl_generate_daily_report','a2912540c3231b3574a12b3c90e2bb6b',false),
('0128','function','wl_ai_context','04986fe96914b01a22f2ee2d7ea298ec',false),
('0129','function','wl_my_last_completed_business_date','f2cec52944ea993c23da6a55d2359946',false),
('0129','function','wl_office_brief','358ee094a85ab2fb6583b41bc8927e1f',false),
('0129','function','wl_office_period','acf23f22ce3f586f46bafc4fa6c63064',false),
('0130','function','wl_my_business_day_window','17dd9c2a2b032f378b819d0a228ba7d0',false),
('0131','function','wl_notifications','85399c1a59cbbd90e78fc45ce3608e07',true),
('0133','function','wl_agent_preflight_auth','d5021bbc7617cc28785673bfa7d92c84',false),
('0134','function','wl_my_recommendation_feedback','440b93c42a6a149ad210620bc59c4ebe',false),
('0134','function','wl_save_recommendation_feedback','7cdd9f5c2ac182b52ca7b16336a970e3',false),
('0134','function','wl_platform_recommendation_feedback','f226268ba354e4e17c1a177324286f74',false),
('0134','function','wl_platform_set_recommendation_feedback_status','63872bb8cdc8f619298b054b65099e40',false),
('0134','table','report_recommendation_feedback','',false),
('0134','index','report_recommendation_feedback_tenant_status_idx','',false),
('0134','index','report_recommendation_feedback_site_report_idx','',false),
('0135','index','report_recommendation_feedback_client_user_idx','',false),
('0135','index','report_recommendation_feedback_team_updated_by_idx','',false),
('0136','function','wl_site_coverage_report','b621beb278b10f3199a2cc9010b801fa',false),
('0136','function','wl_my_business_day_monitoring','a1ec8634d4f4ab05dc6958c0a0a4adcd',false),
('0138','function','wl_my_report_window','c393c66cb04c1c55a285f1c2f01eb1c5',false),
('0139','function','wl_vision_day_for_worker','d9ff2ce512d23663a1c85fbeb22f5ae8',false),
('0139','function','wl_vision_save_day_summary','2aefcd789230c75bc528b90e02fbd250',false),
('0144','function','wl_analytics_valid_purpose','4832afe1918810a8cde78aa6e3d504a4',false),
('0144','function','wl_analytics_catalog','897286f1bd7b09fdcd592b4ecb7a269d',false),
('0144','function','wl_notifications','322f0133fe7da86741196018029a48aa',false),
('0144','function','wl_my_latest_report_snapshot','ec6d5c9c57607fc9bbc02b96a0883741',false),
('0144','function','wl_owner_site_truth','f9532f07a3a1f5bdabf19d8b0587f101',false),
('0144','function','wl_restaurant_day_truth','dece7cf3f15fc6d0484e24dddff91530',false),
('0144','function','wl_portal_overview','be09c52cab1ff2f987ad1c1d640a3347',false),
('0145','function','wl_camera_config_snapshot','dc9ae9ea90bb6e9730b8f74706ac07fd',false),
('0145','function','wl_camera_config_snapshots','a9a95d896e69bd105b97baa68631ab4e',false),
('0132','jsonkey','restaurant_intelligence_context,service_day,yesterday_rule','',false),
('0137','jsonkey','restaurant_intelligence_context,monitoring_truth','',false)),
chk as (
  select o.*,
    case o.kind
      when 'function' then exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = o.name)
      when 'table' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r','p') and c.relname = o.name)
      when 'index' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'i' and c.relname = o.name)
      when 'column' then exists (select 1 from information_schema.columns c where c.table_schema = 'public' and c.table_name = split_part(o.name, '.', 1) and c.column_name = split_part(o.name, '.', 2))
      when 'trigger' then exists (select 1 from pg_trigger t where not t.tgisinternal and t.tgname = o.name)
      when 'policy' then exists (select 1 from pg_policies p where p.schemaname = 'public' and p.tablename = split_part(o.name, '.', 1) and p.policyname = substr(o.name, strpos(o.name, '.') + 1))
      when 'cron' then exists (select 1 from cron.job j where j.jobname = o.name)
      when 'jsonkey' then exists (select 1 from public.site_business_context b where b.reporting_prefs #> string_to_array(o.name, ',') is not null)
    end as present,
    o.kind = 'function' and exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = o.name and md5(p.prosrc) = o.md5) as body_live
  from o)
select f,
  count(*) filter (where kind <> 'function') as n_obj,
  count(*) filter (where kind <> 'function' and present) as n_obj_present,
  string_agg(kind || ':' || name, ', ') filter (where kind <> 'function' and not present) as missing_objects,
  count(*) filter (where kind = 'function') as n_fn,
  count(*) filter (where kind = 'function' and present) as n_fn_present,
  count(*) filter (where kind = 'function' and body_live) as n_fn_body_live,
  string_agg(name, ', ') filter (where kind = 'function' and not body_live and superseded) as fn_superseded_in_repo,
  string_agg(name, ', ') filter (where kind = 'function' and not body_live and not superseded) as fn_body_differs_final
from chk group by f order by f
```

### D2. The four functions whose production body differs from the repo

```sql
select p.oid::regprocedure::text as sig, md5(p.prosrc) as src_md5, length(p.prosrc) as src_len, p.prosecdef as secdef, array_to_string(p.proconfig, ';') as cfg
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname in ('wl_agent_push_status','wl_agent_semver_triplet','wl_vision_claim_snapshots','wl_agent_preflight_auth')
order by 1;
-- body text of the two short pure-code functions (no secrets in either body):
select p.proname, p.prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname in ('wl_agent_semver_triplet','wl_agent_preflight_auth') order by 1;
-- immutable, side-effect-free call:
select public.wl_agent_semver_triplet('5.0.27') as triplet, current_setting('standard_conforming_strings') as scs, (select provolatile from pg_proc where oid = 'public.wl_agent_semver_triplet(text)'::regprocedure) as volatility
```

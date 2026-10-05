-- Production migration-ledger reconciliation (prepared 2026-10-06; NOT executed).
--
-- Status: reviewable proposal only. Running step 2 writes production's
-- public.schema_migrations and needs explicit owner approval first. Nothing in
-- this file executes migration SQL; it only records files whose effect is
-- already live, so that prototype/supabase/apply_migrations.py stops treating
-- them as PENDING.
-- Reference: docs/production/MIGRATION_LEDGER_RECONCILIATION_2026-10-05.md,
-- section 10.
--
-- Why. Production's public.schema_migrations (filename, sha256, applied_at) has
-- 114 rows and lacks 32 repo files whose objects are live:
--   0111..0139 (30 files, both 0115 files) - applied through the Supabase
--     migration API or the SQL editor, under other names or other text;
--   0144_portal_qa_truth_contracts, 0145_camera_preview_performance - applied
--     through the Supabase migration API on 2026-10-02 (recorded only in
--     supabase_migrations.schema_migrations).
-- A bare apply_migrations.py run would re-execute 0111..0120, each file committed
-- on its own, and then fail at 0121 (policy restaurant_camera_profiles_no_direct
-- already exists). Rehearsed locally on 2026-10-06 (chain with 0156, ledger rows
-- of these 32 files and 0156 deleted): it rewrote 8 function bodies to older
-- repo text - wl_agent_semver_triplet (broken regex), wl_known_capabilities
-- (0119 list, drops config_snapshot_requests once 0156 is live), wl_ai_context,
-- wl_camera_config_snapshot, wl_notifications, wl_vision_claim_snapshots,
-- wl_vision_day_for_worker, wl_vision_save_day_summary.
--
-- sha256 = apply_migrations.normalized_sha(): SHA-256 over the committed file
-- bytes with CRLF and CR folded to LF, exactly what the runner computes and
-- compares (prototype/tests/test_ledger_reconciliation.py pins every value).
--
-- Recorded repo files whose text differs from what production runs (recording
-- the row asserts "the effect of this file is live"; these are the exceptions):
--   * 0119_remote_agent_maintenance.sql - SEMANTIC difference.
--     wl_agent_semver_triplet(text): the repo regex '^([0-9]+)\\.([0-9]+)\\.([0-9]+)'
--     returns {0,0,0} under standard_conforming_strings=on; production runs
--     '^([0-9]+)\.([0-9]+)\.([0-9]+)' (md5 8a4f5c64381d1080dbc565c409b84a27) and
--     returns {5,0,27}. Production is correct. 0156 redefines the function to the
--     production body, so production keeps its behaviour and fresh chains are
--     fixed. Recording 0119 does not change production.
--   * 0119 and 0121 were edited in 6488bab (dollar-quote delimiters only:
--     'as $' -> 'as $$', '$;' -> '$$;'). Function bodies (prosrc) are unchanged.
--     The sha256 recorded here is that of the committed (post-6488bab) bytes,
--     which production never executed under this filename; that is intended:
--     it is what the runner compares.
--   * 0121_restaurant_visual_analytics.sql - formatting only.
--     wl_vision_claim_snapshots(integer,text): production md5
--     ecc57aabda24344618051ffb5b49dffe / 5141 chars, repo body md5
--     a097503ce60a1b40d0e5a689e8fa0cc1 / 4793 chars. Read back on 2026-10-06 and
--     compared token by token (identifiers, operators and all 100 string
--     literals): identical; only whitespace and line breaks differ. Recording
--     0121 hides no behavioural difference for this function.
--   * 0115_recorder_push_status.sql - formatting only. wl_agent_push_status:
--     production 547120b7... / 790, repo 9081f780... / 768; token-identical.
--   * 0133_existing_site_preflight_auth.sql - formatting only.
--     wl_agent_preflight_auth: production lacks one blank line.
-- Every other function whose final repo definition is in this set has the same
-- md5(prosrc), SECURITY DEFINER flag, proconfig and anon/authenticated execute
-- grant in production as on the local repo chain (compared 2026-10-06).
--
-- How to run (operator, after approval), against production only:
--   1. psql ... -v ON_ERROR_STOP=1 -c "<STEP 1 query>"   and review it;
--   2. psql ... -v ON_ERROR_STOP=1 -f this file          (steps 1, 1b and 2);
--   3. apply_migrations.py --status                      (step 3).
-- Order: run this BEFORE applying 0156, so that 0156 is then the only PENDING
-- file on chore/migration-ledger-alignment.

-- ============================================================================
-- STEP 1 (read-only). Run on its own first. Changes nothing.
-- Every row must show n_present = n_objects and missing = NULL. Any other
-- result: STOP; do not run step 2.
-- ============================================================================
with o(filename, kind, name) as (values
  ('0111_visual_snapshot_pipeline.sql','function','wl_queue_snapshot_visual_review'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_vision_claim_snapshots'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_vision_complete_snapshot'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_vision_fail_snapshot'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_vision_day_for_worker'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_vision_save_day_summary'),
  ('0111_visual_snapshot_pipeline.sql','function','wl_my_visual_day'),
  ('0111_visual_snapshot_pipeline.sql','table','snapshot_visual_reviews'),
  ('0111_visual_snapshot_pipeline.sql','table','visual_day_summaries'),
  ('0111_visual_snapshot_pipeline.sql','index','snapshot_visual_reviews_queue_idx'),
  ('0111_visual_snapshot_pipeline.sql','index','snapshot_visual_reviews_site_day_idx'),
  ('0111_visual_snapshot_pipeline.sql','index','visual_day_summaries_tenant_date_idx'),
  ('0111_visual_snapshot_pipeline.sql','trigger','trg_snapshot_visual_review_queue'),
  ('0112_prioritize_recent_visual_review.sql','function','wl_vision_claim_snapshots'),
  ('0113_private_media_mirror.sql','function','wl_vision_mark_media'),
  ('0113_private_media_mirror.sql','function','wl_vision_claim_snapshots'),
  ('0113_private_media_mirror.sql','function','wl_vision_complete_snapshot'),
  ('0113_private_media_mirror.sql','index','snapshot_visual_reviews_media_idx'),
  ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_bucket'),
  ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_key'),
  ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_sha256'),
  ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_bytes'),
  ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_mirrored_at'),
  ('0114_vision_worker_heartbeat.sql','function','wl_vision_worker_heartbeat'),
  ('0114_vision_worker_heartbeat.sql','table','vision_worker_status'),
  ('0115_context_aware_visual_review.sql','function','wl_vision_claim_snapshots'),
  ('0115_context_aware_visual_review.sql','function','wl_vision_day_for_worker'),
  ('0115_recorder_push_status.sql','function','wl_agent_push_status'),
  ('0116_camera_preview_realtime.sql','function','wl_signal_camera_preview'),
  ('0116_camera_preview_realtime.sql','function','wl_camera_config_snapshot'),
  ('0116_camera_preview_realtime.sql','table','camera_snapshot_signals'),
  ('0116_camera_preview_realtime.sql','index','camera_snapshot_signals_site_idx'),
  ('0116_camera_preview_realtime.sql','index','snapshots_camera_captured_idx'),
  ('0116_camera_preview_realtime.sql','trigger','trg_camera_config_snapshot_realtime_signal'),
  ('0116_camera_preview_realtime.sql','trigger','trg_event_snapshot_realtime_signal'),
  ('0116_camera_preview_realtime.sql','policy','camera_snapshot_signals.portal_read_camera_snapshot_signals'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_sync_cameras'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_sites'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_ai_context'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_agent_analytics_config'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_remove_site'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notifications'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notification_mark_read'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notifications_mark_all_read'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','table','notification_reads'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','index','notification_reads_user_idx'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','column','cameras.is_canonical'),
  ('0117_site_lifecycle_notifications_camera_identity.sql','column','cameras.physical_channel'),
  ('0118_visual_review_canonical_cameras.sql','function','wl_vision_claim_snapshots'),
  ('0118_visual_review_canonical_cameras.sql','function','wl_vision_day_for_worker'),
  ('0119_remote_agent_maintenance.sql','function','wl_known_capabilities'),
  ('0119_remote_agent_maintenance.sql','function','wl_agent_semver_triplet'),
  ('0119_remote_agent_maintenance.sql','function','wl_agent_report_capabilities'),
  ('0119_remote_agent_maintenance.sql','function','wl_platform_request_agent_update'),
  ('0119_remote_agent_maintenance.sql','function','wl_agent_claim_update_request'),
  ('0119_remote_agent_maintenance.sql','function','wl_agent_stage_update_request'),
  ('0119_remote_agent_maintenance.sql','function','wl_agent_complete_update_request'),
  ('0119_remote_agent_maintenance.sql','function','wl_platform_agent_update_status'),
  ('0119_remote_agent_maintenance.sql','table','agent_update_requests'),
  ('0119_remote_agent_maintenance.sql','index','agent_update_requests_site_status_idx'),
  ('0120_recovered_snapshot_timestamps.sql','function','wl_ingest_events'),
  ('0121_restaurant_visual_analytics.sql','function','wl_analytics_valid_site_type'),
  ('0121_restaurant_visual_analytics.sql','function','wl_restaurant_site_config'),
  ('0121_restaurant_visual_analytics.sql','function','wl_restaurant_day'),
  ('0121_restaurant_visual_analytics.sql','function','wl_extract_restaurant_visual_observation'),
  ('0121_restaurant_visual_analytics.sql','function','wl_vision_claim_snapshots'),
  ('0121_restaurant_visual_analytics.sql','table','restaurant_camera_profiles'),
  ('0121_restaurant_visual_analytics.sql','table','restaurant_tables'),
  ('0121_restaurant_visual_analytics.sql','table','restaurant_visual_observations'),
  ('0121_restaurant_visual_analytics.sql','table','restaurant_table_observations'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_camera_profiles_site_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_camera_profiles_tenant_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_camera_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_site_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_site_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_camera_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_table_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_site_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_tenant_site_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_tenant_site_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_tenant_site_time_idx'),
  ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_camera_time_idx'),
  ('0121_restaurant_visual_analytics.sql','trigger','trg_extract_restaurant_visual_observation'),
  ('0121_restaurant_visual_analytics.sql','policy','restaurant_camera_profiles.restaurant_camera_profiles_no_direct'),
  ('0121_restaurant_visual_analytics.sql','policy','restaurant_tables.restaurant_tables_no_direct'),
  ('0121_restaurant_visual_analytics.sql','policy','restaurant_visual_observations.restaurant_visual_observations_no_direct'),
  ('0121_restaurant_visual_analytics.sql','policy','restaurant_table_observations.restaurant_table_observations_no_direct'),
  ('0122_vision_worker_runtime.sql','function','wl_vision_worker_expected_secret'),
  ('0122_vision_worker_runtime.sql','function','wl_assert_my_site'),
  ('0122_vision_worker_runtime.sql','function','wl_vision_claim_snapshots_v2'),
  ('0122_vision_worker_runtime.sql','function','wl_generate_daily_report'),
  ('0123_schedule_vision_worker.sql','cron','watchlog-vision-worker'),
  ('0124_restaurant_preopen_service_day.sql','function','wl_restaurant_day'),
  ('0124_restaurant_preopen_service_day.sql','function','wl_generate_daily_report'),
  ('0125_restaurant_server_capture_scheduler.sql','function','wl_restaurant_schedule_snapshot_requests'),
  ('0125_restaurant_server_capture_scheduler.sql','function','wl_upload_config_snapshot'),
  ('0125_restaurant_server_capture_scheduler.sql','function','wl_vision_claim_snapshots_v2'),
  ('0125_restaurant_server_capture_scheduler.sql','index','camera_snapshot_requests_restaurant_due_idx'),
  ('0125_restaurant_server_capture_scheduler.sql','column','camera_snapshot_requests.request_source'),
  ('0125_restaurant_server_capture_scheduler.sql','cron','watchlog-restaurant-snapshot-scheduler'),
  ('0126_chaiwala_ai_context_alignment.sql','function','wl_extract_restaurant_visual_observation'),
  ('0126_chaiwala_ai_context_alignment.sql','function','wl_vision_claim_snapshots_v2'),
  ('0126_chaiwala_ai_context_alignment.sql','function','wl_restaurant_day'),
  ('0126_chaiwala_ai_context_alignment.sql','function','wl_generate_daily_report'),
  ('0126_chaiwala_ai_context_alignment.sql','function','wl_ai_context'),
  ('0127_chaiwala_report_windows.sql','function','wl_restaurant_period'),
  ('0127_chaiwala_report_windows.sql','function','wl_restaurant_site_config'),
  ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_quality_summary'),
  ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_day'),
  ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_period'),
  ('0128_chaiwala_analytics_quality.sql','function','wl_generate_daily_report'),
  ('0128_chaiwala_analytics_quality.sql','function','wl_ai_context'),
  ('0129_office_reporting_context.sql','function','wl_my_last_completed_business_date'),
  ('0129_office_reporting_context.sql','function','wl_office_brief'),
  ('0129_office_reporting_context.sql','function','wl_office_period'),
  ('0130_business_day_evidence_window.sql','function','wl_my_business_day_window'),
  ('0131_report_recommendation_notifications.sql','function','wl_notifications'),
  ('0133_existing_site_preflight_auth.sql','function','wl_agent_preflight_auth'),
  ('0134_report_recommendation_feedback.sql','function','wl_my_recommendation_feedback'),
  ('0134_report_recommendation_feedback.sql','function','wl_save_recommendation_feedback'),
  ('0134_report_recommendation_feedback.sql','function','wl_platform_recommendation_feedback'),
  ('0134_report_recommendation_feedback.sql','function','wl_platform_set_recommendation_feedback_status'),
  ('0134_report_recommendation_feedback.sql','table','report_recommendation_feedback'),
  ('0134_report_recommendation_feedback.sql','index','report_recommendation_feedback_tenant_status_idx'),
  ('0134_report_recommendation_feedback.sql','index','report_recommendation_feedback_site_report_idx'),
  ('0135_report_recommendation_feedback_indexes.sql','index','report_recommendation_feedback_client_user_idx'),
  ('0135_report_recommendation_feedback_indexes.sql','index','report_recommendation_feedback_team_updated_by_idx'),
  ('0136_service_day_monitoring_truth.sql','function','wl_site_coverage_report'),
  ('0136_service_day_monitoring_truth.sql','function','wl_my_business_day_monitoring'),
  ('0138_rolling_saved_report_windows.sql','function','wl_my_report_window'),
  ('0139_visual_service_day_windows.sql','function','wl_vision_day_for_worker'),
  ('0139_visual_service_day_windows.sql','function','wl_vision_save_day_summary'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_analytics_valid_purpose'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_analytics_catalog'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_notifications'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_my_latest_report_snapshot'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_owner_site_truth'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_restaurant_day_truth'),
  ('0144_portal_qa_truth_contracts.sql','function','wl_portal_overview'),
  ('0145_camera_preview_performance.sql','function','wl_camera_config_snapshot'),
  ('0145_camera_preview_performance.sql','function','wl_camera_config_snapshots'),
  ('0132_chaiwala_yesterday_service_day_rule.sql','jsonkey','restaurant_intelligence_context,service_day,yesterday_rule'),
  ('0137_chaiwala_monitoring_context_truth.sql','jsonkey','restaurant_intelligence_context,monitoring_truth')
),
chk as (
  select o.*, case o.kind
      when 'function' then exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = o.name)
      when 'table' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r','p') and c.relname = o.name)
      when 'index' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'i' and c.relname = o.name)
      when 'column' then exists (select 1 from information_schema.columns c where c.table_schema = 'public' and c.table_name = split_part(o.name, '.', 1) and c.column_name = split_part(o.name, '.', 2))
      when 'trigger' then exists (select 1 from pg_trigger t where not t.tgisinternal and t.tgname = o.name)
      when 'policy' then exists (select 1 from pg_policies p where p.schemaname = 'public' and p.tablename = split_part(o.name, '.', 1) and p.policyname = substr(o.name, strpos(o.name, '.') + 1))
      when 'cron' then exists (select 1 from cron.job j where j.jobname = o.name)
      when 'jsonkey' then exists (select 1 from public.site_business_context b where b.reporting_prefs #> string_to_array(o.name, ',') is not null)
    end as present
  from o
)
select filename,
       count(*) as n_objects,
       count(*) filter (where present) as n_present,
       string_agg(kind || ':' || name, ', ') filter (where not present) as missing,
       (select m.sha256 from public.schema_migrations m where m.filename = chk.filename) as already_recorded_sha256
from chk
group by filename
order by filename;

-- Step 1b (read-only, informational). The function bodies that production runs
-- for files in this set and that differ from the repo text (see the header).
-- Expected on 2026-10-06:
--   wl_agent_preflight_auth(uuid,text)       5136b004c62e9374d3d2c335f337c10b / 412
--   wl_agent_push_status(uuid,text)          547120b7cb00b80309dbb74fb72aa7b7 / 790
--   wl_agent_semver_triplet(text)            8a4f5c64381d1080dbc565c409b84a27 / 205
--   wl_vision_claim_snapshots(integer,text)  ecc57aabda24344618051ffb5b49dffe / 5141
--   semver_5_0_27 = {5,0,27}
select p.oid::regprocedure::text as sig, md5(p.prosrc) as prosrc_md5, length(p.prosrc) as prosrc_len,
       (select public.wl_agent_semver_triplet('5.0.27')) as semver_5_0_27
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('wl_agent_preflight_auth', 'wl_agent_push_status',
                    'wl_agent_semver_triplet', 'wl_vision_claim_snapshots')
order by 1;

-- ============================================================================
-- STEP 2 (writes public.schema_migrations only). One transaction. The DO block
-- repeats the step-1 check and aborts the whole transaction if any key object is
-- missing or if one of these files is already recorded with a different sha256.
-- No migration SQL is executed; only ledger rows are inserted.
-- ============================================================================
begin;

do $guard$
declare
  v_missing text;
  v_conflict text;
begin
  with o(filename, kind, name) as (values
    ('0111_visual_snapshot_pipeline.sql','function','wl_queue_snapshot_visual_review'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_vision_claim_snapshots'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_vision_complete_snapshot'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_vision_fail_snapshot'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_vision_day_for_worker'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_vision_save_day_summary'),
    ('0111_visual_snapshot_pipeline.sql','function','wl_my_visual_day'),
    ('0111_visual_snapshot_pipeline.sql','table','snapshot_visual_reviews'),
    ('0111_visual_snapshot_pipeline.sql','table','visual_day_summaries'),
    ('0111_visual_snapshot_pipeline.sql','index','snapshot_visual_reviews_queue_idx'),
    ('0111_visual_snapshot_pipeline.sql','index','snapshot_visual_reviews_site_day_idx'),
    ('0111_visual_snapshot_pipeline.sql','index','visual_day_summaries_tenant_date_idx'),
    ('0111_visual_snapshot_pipeline.sql','trigger','trg_snapshot_visual_review_queue'),
    ('0112_prioritize_recent_visual_review.sql','function','wl_vision_claim_snapshots'),
    ('0113_private_media_mirror.sql','function','wl_vision_mark_media'),
    ('0113_private_media_mirror.sql','function','wl_vision_claim_snapshots'),
    ('0113_private_media_mirror.sql','function','wl_vision_complete_snapshot'),
    ('0113_private_media_mirror.sql','index','snapshot_visual_reviews_media_idx'),
    ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_bucket'),
    ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_key'),
    ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_sha256'),
    ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_bytes'),
    ('0113_private_media_mirror.sql','column','snapshot_visual_reviews.media_mirrored_at'),
    ('0114_vision_worker_heartbeat.sql','function','wl_vision_worker_heartbeat'),
    ('0114_vision_worker_heartbeat.sql','table','vision_worker_status'),
    ('0115_context_aware_visual_review.sql','function','wl_vision_claim_snapshots'),
    ('0115_context_aware_visual_review.sql','function','wl_vision_day_for_worker'),
    ('0115_recorder_push_status.sql','function','wl_agent_push_status'),
    ('0116_camera_preview_realtime.sql','function','wl_signal_camera_preview'),
    ('0116_camera_preview_realtime.sql','function','wl_camera_config_snapshot'),
    ('0116_camera_preview_realtime.sql','table','camera_snapshot_signals'),
    ('0116_camera_preview_realtime.sql','index','camera_snapshot_signals_site_idx'),
    ('0116_camera_preview_realtime.sql','index','snapshots_camera_captured_idx'),
    ('0116_camera_preview_realtime.sql','trigger','trg_camera_config_snapshot_realtime_signal'),
    ('0116_camera_preview_realtime.sql','trigger','trg_event_snapshot_realtime_signal'),
    ('0116_camera_preview_realtime.sql','policy','camera_snapshot_signals.portal_read_camera_snapshot_signals'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_sync_cameras'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_sites'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_ai_context'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_agent_analytics_config'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_remove_site'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notifications'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notification_mark_read'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','function','wl_notifications_mark_all_read'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','table','notification_reads'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','index','notification_reads_user_idx'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','column','cameras.is_canonical'),
    ('0117_site_lifecycle_notifications_camera_identity.sql','column','cameras.physical_channel'),
    ('0118_visual_review_canonical_cameras.sql','function','wl_vision_claim_snapshots'),
    ('0118_visual_review_canonical_cameras.sql','function','wl_vision_day_for_worker'),
    ('0119_remote_agent_maintenance.sql','function','wl_known_capabilities'),
    ('0119_remote_agent_maintenance.sql','function','wl_agent_semver_triplet'),
    ('0119_remote_agent_maintenance.sql','function','wl_agent_report_capabilities'),
    ('0119_remote_agent_maintenance.sql','function','wl_platform_request_agent_update'),
    ('0119_remote_agent_maintenance.sql','function','wl_agent_claim_update_request'),
    ('0119_remote_agent_maintenance.sql','function','wl_agent_stage_update_request'),
    ('0119_remote_agent_maintenance.sql','function','wl_agent_complete_update_request'),
    ('0119_remote_agent_maintenance.sql','function','wl_platform_agent_update_status'),
    ('0119_remote_agent_maintenance.sql','table','agent_update_requests'),
    ('0119_remote_agent_maintenance.sql','index','agent_update_requests_site_status_idx'),
    ('0120_recovered_snapshot_timestamps.sql','function','wl_ingest_events'),
    ('0121_restaurant_visual_analytics.sql','function','wl_analytics_valid_site_type'),
    ('0121_restaurant_visual_analytics.sql','function','wl_restaurant_site_config'),
    ('0121_restaurant_visual_analytics.sql','function','wl_restaurant_day'),
    ('0121_restaurant_visual_analytics.sql','function','wl_extract_restaurant_visual_observation'),
    ('0121_restaurant_visual_analytics.sql','function','wl_vision_claim_snapshots'),
    ('0121_restaurant_visual_analytics.sql','table','restaurant_camera_profiles'),
    ('0121_restaurant_visual_analytics.sql','table','restaurant_tables'),
    ('0121_restaurant_visual_analytics.sql','table','restaurant_visual_observations'),
    ('0121_restaurant_visual_analytics.sql','table','restaurant_table_observations'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_camera_profiles_site_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_camera_profiles_tenant_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_camera_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_site_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_site_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_camera_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_table_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_site_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_tables_tenant_site_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_visual_observations_tenant_site_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_tenant_site_time_idx'),
    ('0121_restaurant_visual_analytics.sql','index','restaurant_table_observations_camera_time_idx'),
    ('0121_restaurant_visual_analytics.sql','trigger','trg_extract_restaurant_visual_observation'),
    ('0121_restaurant_visual_analytics.sql','policy','restaurant_camera_profiles.restaurant_camera_profiles_no_direct'),
    ('0121_restaurant_visual_analytics.sql','policy','restaurant_tables.restaurant_tables_no_direct'),
    ('0121_restaurant_visual_analytics.sql','policy','restaurant_visual_observations.restaurant_visual_observations_no_direct'),
    ('0121_restaurant_visual_analytics.sql','policy','restaurant_table_observations.restaurant_table_observations_no_direct'),
    ('0122_vision_worker_runtime.sql','function','wl_vision_worker_expected_secret'),
    ('0122_vision_worker_runtime.sql','function','wl_assert_my_site'),
    ('0122_vision_worker_runtime.sql','function','wl_vision_claim_snapshots_v2'),
    ('0122_vision_worker_runtime.sql','function','wl_generate_daily_report'),
    ('0123_schedule_vision_worker.sql','cron','watchlog-vision-worker'),
    ('0124_restaurant_preopen_service_day.sql','function','wl_restaurant_day'),
    ('0124_restaurant_preopen_service_day.sql','function','wl_generate_daily_report'),
    ('0125_restaurant_server_capture_scheduler.sql','function','wl_restaurant_schedule_snapshot_requests'),
    ('0125_restaurant_server_capture_scheduler.sql','function','wl_upload_config_snapshot'),
    ('0125_restaurant_server_capture_scheduler.sql','function','wl_vision_claim_snapshots_v2'),
    ('0125_restaurant_server_capture_scheduler.sql','index','camera_snapshot_requests_restaurant_due_idx'),
    ('0125_restaurant_server_capture_scheduler.sql','column','camera_snapshot_requests.request_source'),
    ('0125_restaurant_server_capture_scheduler.sql','cron','watchlog-restaurant-snapshot-scheduler'),
    ('0126_chaiwala_ai_context_alignment.sql','function','wl_extract_restaurant_visual_observation'),
    ('0126_chaiwala_ai_context_alignment.sql','function','wl_vision_claim_snapshots_v2'),
    ('0126_chaiwala_ai_context_alignment.sql','function','wl_restaurant_day'),
    ('0126_chaiwala_ai_context_alignment.sql','function','wl_generate_daily_report'),
    ('0126_chaiwala_ai_context_alignment.sql','function','wl_ai_context'),
    ('0127_chaiwala_report_windows.sql','function','wl_restaurant_period'),
    ('0127_chaiwala_report_windows.sql','function','wl_restaurant_site_config'),
    ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_quality_summary'),
    ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_day'),
    ('0128_chaiwala_analytics_quality.sql','function','wl_restaurant_period'),
    ('0128_chaiwala_analytics_quality.sql','function','wl_generate_daily_report'),
    ('0128_chaiwala_analytics_quality.sql','function','wl_ai_context'),
    ('0129_office_reporting_context.sql','function','wl_my_last_completed_business_date'),
    ('0129_office_reporting_context.sql','function','wl_office_brief'),
    ('0129_office_reporting_context.sql','function','wl_office_period'),
    ('0130_business_day_evidence_window.sql','function','wl_my_business_day_window'),
    ('0131_report_recommendation_notifications.sql','function','wl_notifications'),
    ('0133_existing_site_preflight_auth.sql','function','wl_agent_preflight_auth'),
    ('0134_report_recommendation_feedback.sql','function','wl_my_recommendation_feedback'),
    ('0134_report_recommendation_feedback.sql','function','wl_save_recommendation_feedback'),
    ('0134_report_recommendation_feedback.sql','function','wl_platform_recommendation_feedback'),
    ('0134_report_recommendation_feedback.sql','function','wl_platform_set_recommendation_feedback_status'),
    ('0134_report_recommendation_feedback.sql','table','report_recommendation_feedback'),
    ('0134_report_recommendation_feedback.sql','index','report_recommendation_feedback_tenant_status_idx'),
    ('0134_report_recommendation_feedback.sql','index','report_recommendation_feedback_site_report_idx'),
    ('0135_report_recommendation_feedback_indexes.sql','index','report_recommendation_feedback_client_user_idx'),
    ('0135_report_recommendation_feedback_indexes.sql','index','report_recommendation_feedback_team_updated_by_idx'),
    ('0136_service_day_monitoring_truth.sql','function','wl_site_coverage_report'),
    ('0136_service_day_monitoring_truth.sql','function','wl_my_business_day_monitoring'),
    ('0138_rolling_saved_report_windows.sql','function','wl_my_report_window'),
    ('0139_visual_service_day_windows.sql','function','wl_vision_day_for_worker'),
    ('0139_visual_service_day_windows.sql','function','wl_vision_save_day_summary'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_analytics_valid_purpose'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_analytics_catalog'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_notifications'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_my_latest_report_snapshot'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_owner_site_truth'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_restaurant_day_truth'),
    ('0144_portal_qa_truth_contracts.sql','function','wl_portal_overview'),
    ('0145_camera_preview_performance.sql','function','wl_camera_config_snapshot'),
    ('0145_camera_preview_performance.sql','function','wl_camera_config_snapshots'),
    ('0132_chaiwala_yesterday_service_day_rule.sql','jsonkey','restaurant_intelligence_context,service_day,yesterday_rule'),
    ('0137_chaiwala_monitoring_context_truth.sql','jsonkey','restaurant_intelligence_context,monitoring_truth')
  ),
  chk as (
    select o.*, case o.kind
      when 'function' then exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = o.name)
      when 'table' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind in ('r','p') and c.relname = o.name)
      when 'index' then exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'i' and c.relname = o.name)
      when 'column' then exists (select 1 from information_schema.columns c where c.table_schema = 'public' and c.table_name = split_part(o.name, '.', 1) and c.column_name = split_part(o.name, '.', 2))
      when 'trigger' then exists (select 1 from pg_trigger t where not t.tgisinternal and t.tgname = o.name)
      when 'policy' then exists (select 1 from pg_policies p where p.schemaname = 'public' and p.tablename = split_part(o.name, '.', 1) and p.policyname = substr(o.name, strpos(o.name, '.') + 1))
      when 'cron' then exists (select 1 from cron.job j where j.jobname = o.name)
      when 'jsonkey' then exists (select 1 from public.site_business_context b where b.reporting_prefs #> string_to_array(o.name, ',') is not null)
    end as present
    from o
  )
  select string_agg(filename || ' ' || kind || ':' || name, '; ' order by filename, kind, name)
    into v_missing
    from chk where not present;
  if v_missing is not null then
    raise exception 'ledger reconciliation aborted: key objects missing in this database: %', v_missing;
  end if;

  with ledger(filename, sha256) as (values
    ('0111_visual_snapshot_pipeline.sql', '967985cd198dff2721adb0944ea001e377e5a1b45056e31a533b56b700622d99'),
    ('0112_prioritize_recent_visual_review.sql', '0fe41396a22f55a792f019d991fe07b3c20d7f0807592b4845d660cc373a4e09'),
    ('0113_private_media_mirror.sql', '7ea0cbc3547c5ad5bc029b30b7c64aa135feced4c0bdf2e1bcdcb25e9158cc64'),
    ('0114_vision_worker_heartbeat.sql', 'e781700bdebf97e3ad36dc333999c13404391541113f26bd4bb2af6323af0dce'),
    ('0115_context_aware_visual_review.sql', 'ee244ca6e0f6a850abc7619042bcd44f62439ec583b3e3faf6a954a4974bfa19'),
    ('0115_recorder_push_status.sql', '0a1a6e00c1053b912742d6807275444dbc6e8e9befb872c9f5d66ea7572b2489'),
    ('0116_camera_preview_realtime.sql', '6679b877a2a2d9295125525ae09cfd0364539d2fa564f271fd9cec977464ed47'),
    ('0117_site_lifecycle_notifications_camera_identity.sql', '5b8d71059dc1a303b2257cc5e5d7165cae31443daf23bf46e683b6c93091e107'),
    ('0118_visual_review_canonical_cameras.sql', '02bf3c5545dd9048e808e647ea629e7664c1b054797a9fe865aa2a88bbd4f422'),
    ('0119_remote_agent_maintenance.sql', 'c544689df8ec78bfa0a80abdfaaa36bd4212e5ed57fff33e3ad57e9414bbb95c'),
    ('0120_recovered_snapshot_timestamps.sql', '02c16a36e96b82ba293c8500096e3285e82677f2c49a858dc1edd331ebe7c086'),
    ('0121_restaurant_visual_analytics.sql', '49efa85d18aeaab9f84d2e08c1ee5f6b30566f9d539a58a1ab15fba4d20a82f0'),
    ('0122_vision_worker_runtime.sql', '55f849f5113f20c90a14b0f524396b34e7ac3cc35b69927fca189b3710f1cce1'),
    ('0123_schedule_vision_worker.sql', 'bfa30ee7144233c986eb1a4002cd43abcabfc15e0771e13cdb9defab46a9116f'),
    ('0124_restaurant_preopen_service_day.sql', '0c7282b90cecd8d84e148b245723beda587b637b52d1be65acb7ca6080dea78c'),
    ('0125_restaurant_server_capture_scheduler.sql', '66f4fe05dad81da94b9da567b53af4b60043baa12aaff91564f5a97a228689bf'),
    ('0126_chaiwala_ai_context_alignment.sql', '87d6a207bc80fc643f9193783d23bccd5e58ce095c30366bd7e02d37ad8fbe63'),
    ('0127_chaiwala_report_windows.sql', '089e0d61d64684c47362dc682047284aec2789ceebfc95de8fa56d210a229441'),
    ('0128_chaiwala_analytics_quality.sql', '3118d7eed9c44c870486e55fa50aad4d3ebc9800fa17a0e1b07960f668166c2e'),
    ('0129_office_reporting_context.sql', 'de953f8e87be1af494c64cfb98fdff725083adea69c2c617693c2e46fcb99ee4'),
    ('0130_business_day_evidence_window.sql', '255221d2f5775e4a96553e488b23f58f4b3fd08c584c29f14519c5eadd5d987c'),
    ('0131_report_recommendation_notifications.sql', 'd71f44296bb9a17cdd71db1282269f79ce609653ab031fe34c7f7b0e12e323bd'),
    ('0132_chaiwala_yesterday_service_day_rule.sql', '084e3f04dd80fccbd9e08a816c147b1cd2b9d316c4ae3406b175c2337a1ad76d'),
    ('0133_existing_site_preflight_auth.sql', 'fba9c5593ef886668c250e6cb882893b54e28604254a0b9106e8640eb0d4155f'),
    ('0134_report_recommendation_feedback.sql', '3984d333d1bec0f32bf57a47ddf91cecead3b9201dce0161094313bda485ca56'),
    ('0135_report_recommendation_feedback_indexes.sql', 'b761f3edf6a74f9537e7f69a407a2cca9a4783045d586be274483f73e8bb322c'),
    ('0136_service_day_monitoring_truth.sql', 'd23feb0d74c840f3ea08b68d44ca5873a643da4821f675910662ed8531f4d2b4'),
    ('0137_chaiwala_monitoring_context_truth.sql', 'e4deb15303fb53132d6f9892b01b8647faae17b54db054a4f3588eb7ca6dabbd'),
    ('0138_rolling_saved_report_windows.sql', '606009003317aa41823045321606dad81b4e71d36b7e5571f56ebaf1bb02f2ba'),
    ('0139_visual_service_day_windows.sql', '915e1b3bc07e7f135f93663bce5e80aa04043e225cf07a72efd1728b4ff267cc'),
    ('0144_portal_qa_truth_contracts.sql', '97afe9f5863f094134c30644a2972a4f1aa388f640c7ed97770535bef99a801d'),
    ('0145_camera_preview_performance.sql', '2b65b7c1c0022f12cd255acaaa149d16afbf2576248bc435230bda6d153e573a')
  )
  select string_agg(m.filename || ' recorded ' || m.sha256 || ' expected ' || l.sha256, '; ' order by m.filename)
    into v_conflict
    from ledger l join public.schema_migrations m on m.filename = l.filename
   where m.sha256 <> l.sha256;
  if v_conflict is not null then
    raise exception 'ledger reconciliation aborted: already recorded with a different sha256: %', v_conflict;
  end if;
end
$guard$;

insert into public.schema_migrations (filename, sha256) values
  ('0111_visual_snapshot_pipeline.sql', '967985cd198dff2721adb0944ea001e377e5a1b45056e31a533b56b700622d99'),
  ('0112_prioritize_recent_visual_review.sql', '0fe41396a22f55a792f019d991fe07b3c20d7f0807592b4845d660cc373a4e09'),
  ('0113_private_media_mirror.sql', '7ea0cbc3547c5ad5bc029b30b7c64aa135feced4c0bdf2e1bcdcb25e9158cc64'),
  ('0114_vision_worker_heartbeat.sql', 'e781700bdebf97e3ad36dc333999c13404391541113f26bd4bb2af6323af0dce'),
  ('0115_context_aware_visual_review.sql', 'ee244ca6e0f6a850abc7619042bcd44f62439ec583b3e3faf6a954a4974bfa19'),
  ('0115_recorder_push_status.sql', '0a1a6e00c1053b912742d6807275444dbc6e8e9befb872c9f5d66ea7572b2489'),
  ('0116_camera_preview_realtime.sql', '6679b877a2a2d9295125525ae09cfd0364539d2fa564f271fd9cec977464ed47'),
  ('0117_site_lifecycle_notifications_camera_identity.sql', '5b8d71059dc1a303b2257cc5e5d7165cae31443daf23bf46e683b6c93091e107'),
  ('0118_visual_review_canonical_cameras.sql', '02bf3c5545dd9048e808e647ea629e7664c1b054797a9fe865aa2a88bbd4f422'),
  ('0119_remote_agent_maintenance.sql', 'c544689df8ec78bfa0a80abdfaaa36bd4212e5ed57fff33e3ad57e9414bbb95c'),
  ('0120_recovered_snapshot_timestamps.sql', '02c16a36e96b82ba293c8500096e3285e82677f2c49a858dc1edd331ebe7c086'),
  ('0121_restaurant_visual_analytics.sql', '49efa85d18aeaab9f84d2e08c1ee5f6b30566f9d539a58a1ab15fba4d20a82f0'),
  ('0122_vision_worker_runtime.sql', '55f849f5113f20c90a14b0f524396b34e7ac3cc35b69927fca189b3710f1cce1'),
  ('0123_schedule_vision_worker.sql', 'bfa30ee7144233c986eb1a4002cd43abcabfc15e0771e13cdb9defab46a9116f'),
  ('0124_restaurant_preopen_service_day.sql', '0c7282b90cecd8d84e148b245723beda587b637b52d1be65acb7ca6080dea78c'),
  ('0125_restaurant_server_capture_scheduler.sql', '66f4fe05dad81da94b9da567b53af4b60043baa12aaff91564f5a97a228689bf'),
  ('0126_chaiwala_ai_context_alignment.sql', '87d6a207bc80fc643f9193783d23bccd5e58ce095c30366bd7e02d37ad8fbe63'),
  ('0127_chaiwala_report_windows.sql', '089e0d61d64684c47362dc682047284aec2789ceebfc95de8fa56d210a229441'),
  ('0128_chaiwala_analytics_quality.sql', '3118d7eed9c44c870486e55fa50aad4d3ebc9800fa17a0e1b07960f668166c2e'),
  ('0129_office_reporting_context.sql', 'de953f8e87be1af494c64cfb98fdff725083adea69c2c617693c2e46fcb99ee4'),
  ('0130_business_day_evidence_window.sql', '255221d2f5775e4a96553e488b23f58f4b3fd08c584c29f14519c5eadd5d987c'),
  ('0131_report_recommendation_notifications.sql', 'd71f44296bb9a17cdd71db1282269f79ce609653ab031fe34c7f7b0e12e323bd'),
  ('0132_chaiwala_yesterday_service_day_rule.sql', '084e3f04dd80fccbd9e08a816c147b1cd2b9d316c4ae3406b175c2337a1ad76d'),
  ('0133_existing_site_preflight_auth.sql', 'fba9c5593ef886668c250e6cb882893b54e28604254a0b9106e8640eb0d4155f'),
  ('0134_report_recommendation_feedback.sql', '3984d333d1bec0f32bf57a47ddf91cecead3b9201dce0161094313bda485ca56'),
  ('0135_report_recommendation_feedback_indexes.sql', 'b761f3edf6a74f9537e7f69a407a2cca9a4783045d586be274483f73e8bb322c'),
  ('0136_service_day_monitoring_truth.sql', 'd23feb0d74c840f3ea08b68d44ca5873a643da4821f675910662ed8531f4d2b4'),
  ('0137_chaiwala_monitoring_context_truth.sql', 'e4deb15303fb53132d6f9892b01b8647faae17b54db054a4f3588eb7ca6dabbd'),
  ('0138_rolling_saved_report_windows.sql', '606009003317aa41823045321606dad81b4e71d36b7e5571f56ebaf1bb02f2ba'),
  ('0139_visual_service_day_windows.sql', '915e1b3bc07e7f135f93663bce5e80aa04043e225cf07a72efd1728b4ff267cc'),
  ('0144_portal_qa_truth_contracts.sql', '97afe9f5863f094134c30644a2972a4f1aa388f640c7ed97770535bef99a801d'),
  ('0145_camera_preview_performance.sql', '2b65b7c1c0022f12cd255acaaa149d16afbf2576248bc435230bda6d153e573a')
on conflict (filename) do nothing;

-- Post-check inside the transaction: all 32 rows recorded with the expected
-- sha256 (expect recorded = 32, mismatched = 0) and the ledger size (expect
-- 146 = 114 + 32 when run on production as of 2026-10-06).
with ledger(filename, sha256) as (values
  ('0111_visual_snapshot_pipeline.sql', '967985cd198dff2721adb0944ea001e377e5a1b45056e31a533b56b700622d99'),
  ('0112_prioritize_recent_visual_review.sql', '0fe41396a22f55a792f019d991fe07b3c20d7f0807592b4845d660cc373a4e09'),
  ('0113_private_media_mirror.sql', '7ea0cbc3547c5ad5bc029b30b7c64aa135feced4c0bdf2e1bcdcb25e9158cc64'),
  ('0114_vision_worker_heartbeat.sql', 'e781700bdebf97e3ad36dc333999c13404391541113f26bd4bb2af6323af0dce'),
  ('0115_context_aware_visual_review.sql', 'ee244ca6e0f6a850abc7619042bcd44f62439ec583b3e3faf6a954a4974bfa19'),
  ('0115_recorder_push_status.sql', '0a1a6e00c1053b912742d6807275444dbc6e8e9befb872c9f5d66ea7572b2489'),
  ('0116_camera_preview_realtime.sql', '6679b877a2a2d9295125525ae09cfd0364539d2fa564f271fd9cec977464ed47'),
  ('0117_site_lifecycle_notifications_camera_identity.sql', '5b8d71059dc1a303b2257cc5e5d7165cae31443daf23bf46e683b6c93091e107'),
  ('0118_visual_review_canonical_cameras.sql', '02bf3c5545dd9048e808e647ea629e7664c1b054797a9fe865aa2a88bbd4f422'),
  ('0119_remote_agent_maintenance.sql', 'c544689df8ec78bfa0a80abdfaaa36bd4212e5ed57fff33e3ad57e9414bbb95c'),
  ('0120_recovered_snapshot_timestamps.sql', '02c16a36e96b82ba293c8500096e3285e82677f2c49a858dc1edd331ebe7c086'),
  ('0121_restaurant_visual_analytics.sql', '49efa85d18aeaab9f84d2e08c1ee5f6b30566f9d539a58a1ab15fba4d20a82f0'),
  ('0122_vision_worker_runtime.sql', '55f849f5113f20c90a14b0f524396b34e7ac3cc35b69927fca189b3710f1cce1'),
  ('0123_schedule_vision_worker.sql', 'bfa30ee7144233c986eb1a4002cd43abcabfc15e0771e13cdb9defab46a9116f'),
  ('0124_restaurant_preopen_service_day.sql', '0c7282b90cecd8d84e148b245723beda587b637b52d1be65acb7ca6080dea78c'),
  ('0125_restaurant_server_capture_scheduler.sql', '66f4fe05dad81da94b9da567b53af4b60043baa12aaff91564f5a97a228689bf'),
  ('0126_chaiwala_ai_context_alignment.sql', '87d6a207bc80fc643f9193783d23bccd5e58ce095c30366bd7e02d37ad8fbe63'),
  ('0127_chaiwala_report_windows.sql', '089e0d61d64684c47362dc682047284aec2789ceebfc95de8fa56d210a229441'),
  ('0128_chaiwala_analytics_quality.sql', '3118d7eed9c44c870486e55fa50aad4d3ebc9800fa17a0e1b07960f668166c2e'),
  ('0129_office_reporting_context.sql', 'de953f8e87be1af494c64cfb98fdff725083adea69c2c617693c2e46fcb99ee4'),
  ('0130_business_day_evidence_window.sql', '255221d2f5775e4a96553e488b23f58f4b3fd08c584c29f14519c5eadd5d987c'),
  ('0131_report_recommendation_notifications.sql', 'd71f44296bb9a17cdd71db1282269f79ce609653ab031fe34c7f7b0e12e323bd'),
  ('0132_chaiwala_yesterday_service_day_rule.sql', '084e3f04dd80fccbd9e08a816c147b1cd2b9d316c4ae3406b175c2337a1ad76d'),
  ('0133_existing_site_preflight_auth.sql', 'fba9c5593ef886668c250e6cb882893b54e28604254a0b9106e8640eb0d4155f'),
  ('0134_report_recommendation_feedback.sql', '3984d333d1bec0f32bf57a47ddf91cecead3b9201dce0161094313bda485ca56'),
  ('0135_report_recommendation_feedback_indexes.sql', 'b761f3edf6a74f9537e7f69a407a2cca9a4783045d586be274483f73e8bb322c'),
  ('0136_service_day_monitoring_truth.sql', 'd23feb0d74c840f3ea08b68d44ca5873a643da4821f675910662ed8531f4d2b4'),
  ('0137_chaiwala_monitoring_context_truth.sql', 'e4deb15303fb53132d6f9892b01b8647faae17b54db054a4f3588eb7ca6dabbd'),
  ('0138_rolling_saved_report_windows.sql', '606009003317aa41823045321606dad81b4e71d36b7e5571f56ebaf1bb02f2ba'),
  ('0139_visual_service_day_windows.sql', '915e1b3bc07e7f135f93663bce5e80aa04043e225cf07a72efd1728b4ff267cc'),
  ('0144_portal_qa_truth_contracts.sql', '97afe9f5863f094134c30644a2972a4f1aa388f640c7ed97770535bef99a801d'),
  ('0145_camera_preview_performance.sql', '2b65b7c1c0022f12cd255acaaa149d16afbf2576248bc435230bda6d153e573a')
)
select count(m.filename) as recorded,
       count(*) filter (where m.sha256 is distinct from l.sha256) as mismatched,
       (select count(*) from public.schema_migrations) as ledger_rows
from ledger l left join public.schema_migrations m on m.filename = l.filename;

commit;

-- ============================================================================
-- STEP 3 (after commit, read-only). From a checkout of the branch being deployed:
--     python prototype/supabase/apply_migrations.py --status
-- Expected: 0 DRIFT and PENDING only for
--   * chore/migration-ledger-alignment: 0156_production_truth_hotfix.sql
--   * mr/db-contracts (and later):       0146..0155 and 0156
-- Anything else PENDING or any DRIFT: stop and investigate before any apply.
-- ============================================================================

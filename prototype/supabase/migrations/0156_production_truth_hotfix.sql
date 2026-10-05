-- 0156 - Production-truth hotfix (standalone; needs explicit approval before it is
-- applied to production; see docs/production/MIGRATION_LEDGER_RECONCILIATION_2026-10-05.md).
--
-- Numbers 0146-0155 belong to the in-flight multi-recorder chain (mr/db-contracts).
-- None of those files redefines an object below, so applying 0156 after that chain
-- never reverts it.
--
-- 1. wl_known_capabilities(): production's body (read with pg_get_functiondef on
--    2026-10-05) lacks 'config_snapshot_requests'. wl_agent_report_capabilities
--    keeps only known capabilities, so it stripped the one capability the 0125
--    restaurant scheduler requires, and wl_restaurant_schedule_snapshot_requests
--    could never select an Agent. The body below is the production body with that
--    one element appended; search_path and grants are unchanged (CREATE OR REPLACE
--    keeps the existing ACL).
--
-- 2. wl_agent_semver_triplet(text): 0119's regex literal '\\.' (two backslashes
--    under standard_conforming_strings=on) never matches, so a fresh chain parsed
--    every version as {0,0,0} and dropped site_control_runtime / remote_update_v1.
--    Production runs the single-backslash body; this redefinition is that body
--    exactly. No production behaviour changes.
--
-- 3. camera_snapshot_requests had no expiry: a request no Agent completed stayed
--    open forever, blocked new requests for that camera (one open request per
--    camera) and stayed at the head of the Agent's poll (oldest first, two per
--    poll). wl_expire_stale_snapshot_requests closes such requests after a bounded
--    window and records expired_at, so an expired request is never read as a
--    delivered image. Server-side only, in the same style as 0142_stale.
--    The manual window is 60 minutes (the clamp floor), not a day: the portal waits
--    15 s for a manual image, and two manual requests for cameras that never return
--    one would otherwise hold both Agent slots, so every newer restaurant request at
--    that site would expire unserved, for the whole window. A late upload still
--    stores the image. Fair ordering of the poll itself is not changed here.
--
-- 4. wl_vision_claim_snapshots_v2: production's filter excludes periodic stills of
--    event-sampled restaurant cameras with `not (rp.sampling_mode='event' and ...)`.
--    For a camera with no enabled restaurant profile (every office camera) the left
--    join leaves rp NULL, the predicate is NULL, and `not NULL` drops the row, so
--    those periodic stills were never claimed (all of HASCO Head Office's stayed
--    pending). The body below is production's body (prosrc md5
--    25787ce940f1409f11c3d3ddcc3f79e6, read 2026-10-05) with only that comparison
--    made null-safe. Signature, SECURITY DEFINER, search_path and grants unchanged.
--    The stranded rows kept their insert-time next_attempt_at, and the claim serves
--    the oldest next_attempt_at first (at most 4 per worker run), so releasing them
--    would put the whole backlog (bounded only by snapshot retention) ahead of every
--    site's fresh stills. 0156 therefore retires the rows stranded at apply time as
--    not reviewed, in the 0118 style (failed, attempts>=5, reason recorded). Stills
--    queued after the apply are claimed normally. Snapshots and events are untouched.

-- ---------------------------------------------------------------------------
-- 1. Known capabilities: production body + 'config_snapshot_requests'
-- ---------------------------------------------------------------------------
create or replace function public.wl_known_capabilities()
returns text[] language sql immutable
set search_path = public
as $$
  select array[
    'operations_runtime',
    'operations_extended_primitives',
    'operations_evidence_still',
    'operations_evidence_clip',
    'archive_processing',
    'multi_agent_fencing',
    'recorder_probe_v2',
    'site_control_runtime',
    'remote_update_v1',
    'config_snapshot_requests'
  ]
$$;

-- ---------------------------------------------------------------------------
-- 2. Semver triplet: the production body exactly
-- ---------------------------------------------------------------------------
create or replace function public.wl_agent_semver_triplet(p_version text)
returns int[]
language plpgsql
immutable
set search_path = public
as $$
declare
  m text[];
begin
  m := regexp_match(coalesce(p_version,''), '^([0-9]+)\.([0-9]+)\.([0-9]+)');
  if m is null then return array[0,0,0]; end if;
  return array[m[1]::int,m[2]::int,m[3]::int];
end
$$;

-- ---------------------------------------------------------------------------
-- 3. Bounded expiry of snapshot requests no Agent completed
-- ---------------------------------------------------------------------------
alter table public.camera_snapshot_requests
  add column if not exists expired_at timestamptz null;

comment on column public.camera_snapshot_requests.expired_at is
  'WatchLog 0156: set (with completed_at) when the request was closed because no Agent delivered an image within the expiry window. Null for delivered requests.';

create or replace function public.wl_expire_stale_snapshot_requests(
  p_manual_max_age_minutes integer default 60,
  p_analytics_max_age_minutes integer default 30
) returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_manual integer := least(greatest(coalesce(p_manual_max_age_minutes, 60), 60), 10080);
  v_analytics integer := least(greatest(coalesce(p_analytics_max_age_minutes, 30), 5), 1440);
  v_now timestamptz := now();
  v_count integer := 0;
begin
  with stale as (
    select q.id
      from public.camera_snapshot_requests q
     where q.completed_at is null
       and q.requested_at < v_now - make_interval(mins => case
             when q.request_source = 'restaurant_analytics' then v_analytics
             else v_manual
           end)
     for update skip locked
  ), expired as (
    update public.camera_snapshot_requests q
       set completed_at = v_now,
           expired_at = v_now
      from stale s
     where q.id = s.id
    returning q.id
  )
  select count(*) into v_count from expired;

  return v_count;
end
$$;

comment on function public.wl_expire_stale_snapshot_requests(integer, integer) is
  'WatchLog 0156: close snapshot requests no Agent completed (manual after 60 min, restaurant analytics after 30 min by default) and mark them expired';

revoke all on function public.wl_expire_stale_snapshot_requests(integer, integer)
  from public, anon, authenticated;
grant execute on function public.wl_expire_stale_snapshot_requests(integer, integer)
  to service_role;

-- Independent server-side cleanup: the Agent that should complete a request may be
-- the component that is offline or failing.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
  end if;
  if to_regclass('cron.job') is not null then
    if exists (select 1 from cron.job where jobname = 'watchlog-expire-stale-snapshot-requests') then
      perform cron.unschedule('watchlog-expire-stale-snapshot-requests');
    end if;
    perform cron.schedule(
      'watchlog-expire-stale-snapshot-requests',
      '*/5 * * * *',
      'select public.wl_expire_stale_snapshot_requests(60, 30)'
    );
  end if;
exception when others then
  raise notice 'stale snapshot request expiry scheduling skipped: %', sqlerrm;
end $$;

-- ---------------------------------------------------------------------------
-- 4. Visual-review claims: periodic stills of cameras without a restaurant profile
-- ---------------------------------------------------------------------------
create or replace function public.wl_vision_claim_snapshots_v2(
  p_limit integer default 2,
  p_worker_id text default null::text,
  p_provider_external boolean default true
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare v_out jsonb;
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;

  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
      join public.snapshots s on s.event_id=r.event_id
      join public.events ev on ev.id=s.event_id
      join public.cameras c on c.id=s.camera_id
      left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id and rp.enabled
     where coalesce(c.is_canonical,true)
       and (
         (r.status='pending' and r.next_attempt_at<=now())
         or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
         or (r.status='processing' and r.lease_until<now() and r.attempts<5)
       )
       and (
         not coalesce(p_provider_external,true)
         or exists (
           select 1 from public.ai_site_egress_policy ep
            where ep.site_id=s.site_id and ep.external_egress_allowed=true
         )
       )
       and not (
         coalesce(rp.sampling_mode,'')='event'
         and coalesce(ev.payload->>'source','')='periodic_snapshot'
       )
     order by r.next_attempt_at,r.captured_at
     for update of r skip locked
     limit least(greatest(coalesce(p_limit,2),1),4)
  ),
  claimed as (
    update public.snapshot_visual_reviews r
       set status='processing',
           attempts=r.attempts+1,
           lease_until=now()+interval '4 minutes',
           worker_id=left(coalesce(p_worker_id,'edge-vision-worker'),120),
           last_error=null,
           updated_at=now()
      from picked p
     where r.event_id=p.event_id
     returning r.event_id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'event_id',s.event_id,
      'tenant_id',s.tenant_id,
      'site_id',s.site_id,
      'camera_id',s.camera_id,
      'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
      'channel',coalesce(c.physical_channel,c.channel),
      'camera_purpose',coalesce(c.purpose,'general'),
      'captured_at',s.captured_at,
      'timezone',coalesce(si.timezone,'Asia/Karachi'),
      'site_type',coalesce(b.site_type,si.site_type,'other'),
      'business_context',jsonb_build_object(
        'site_type',coalesce(b.site_type,si.site_type,'other'),
        'open_time',b.open_time,
        'close_time',b.close_time,
        'overnight',coalesce(b.overnight,false),
        'working_days',coalesce(to_jsonb(b.working_days),'[]'::jsonb),
        'camera_context',coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
        'owner_insight_priorities',coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
        'ai_context_note',coalesce(b.reporting_prefs->>'ai_context_note',''),
        'restaurant_intelligence_context',coalesce(b.reporting_prefs->'restaurant_intelligence_context','{}'::jsonb),
        'restaurant_analytics',case
          when coalesce(b.site_type,si.site_type,'other')='restaurant' and rp.enabled then
            jsonb_build_object(
              'enabled',true,
              'camera_role',rp.analytics_role,
              'sampling_mode',rp.sampling_mode,
              'interval_seconds',rp.interval_seconds,
              'config',rp.config,
              'tables',coalesce((
                select jsonb_agg(jsonb_build_object(
                  'table_key',rt.table_key,'label',rt.label,'capacity',rt.capacity,
                  'tracking_mode',rt.tracking_mode,'anchor',rt.anchor,'roi',rt.roi,
                  'can_combine',rt.can_combine
                ) order by rt.sort_order,rt.table_key)
                from public.restaurant_tables rt
                where rt.camera_id=s.camera_id and rt.site_id=s.site_id and rt.active
              ),'[]'::jsonb),
              'output_contract',jsonb_build_object(
                'top_level_key','restaurant',
                'schema_version','restaurant-vision-v1',
                'truth_rules',jsonb_build_array(
                  'Count only visible people; do not infer unique identity.',
                  'visible_customers means currently visible customers, not unique footfall.',
                  'food_present means visible food on a table; do not infer order correctness or food quality.',
                  'Do not infer sales, revenue, staff identity, health diagnosis, or customer demographics.',
                  'Use null when a requested field is not visually defensible.'
                ),
                'fields',jsonb_build_array(
                  'visible_customers','staff_count','occupied_tables','served_tables',
                  'kitchen_load','handoff_load','counter_active','confidence','tables'
                ),
                'table_fields',jsonb_build_array(
                  'table_key','occupied','customer_count','food_present','drinks_present',
                  'staff_present','clearing_state','combined_group','visibility_quality','confidence'
                )
              )
            )
          else null
        end
      ),
      'content_type',s.content_type,
      'bytes',s.bytes,
      'image_b64',encode(s.image,'base64')
    ) order by s.captured_at),'[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshot_visual_reviews r on r.event_id=q.event_id
    join public.snapshots s on s.event_id=q.event_id
    join public.sites si on si.id=s.site_id
    left join public.site_business_context b on b.site_id=s.site_id
    left join public.cameras c on c.id=s.camera_id
    left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id
   where coalesce(c.is_canonical,true);

  return coalesce(v_out,'[]'::jsonb);
end $function$;

revoke all on function public.wl_vision_claim_snapshots_v2(integer, text, boolean)
  from public, anon, authenticated;
grant execute on function public.wl_vision_claim_snapshots_v2(integer, text, boolean)
  to service_role;

-- Retire the backlog the old filter stranded: every review row it would have claimed
-- but for that filter. They are marked not reviewed (no analysis) so they cannot
-- consume worker runs ahead of fresh work. A row already under a live lease is left
-- alone. Raw snapshots and events remain untouched for audit/history.
update public.snapshot_visual_reviews r
   set status='failed',
       attempts=greatest(r.attempts,5),
       lease_until=null,
       worker_id=null,
       last_error='Skipped: periodic still of a camera without a restaurant profile, stranded before 0156; not reviewed',
       updated_at=now()
  from public.snapshots s
  join public.events ev on ev.id=s.event_id
  join public.cameras c on c.id=s.camera_id
 where s.event_id=r.event_id
   and coalesce(c.is_canonical,true)
   and coalesce(ev.payload->>'source','')='periodic_snapshot'
   and not exists (
     select 1 from public.restaurant_camera_profiles rp
      where rp.camera_id=s.camera_id and rp.enabled
   )
   and (
     (r.status='pending' and r.next_attempt_at<=now())
     or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
     or (r.status='processing' and r.lease_until<now() and r.attempts<5)
   );

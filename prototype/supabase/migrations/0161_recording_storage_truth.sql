-- 0161 - Recording and storage truth for Dahua and Hikvision (Agent 5.1.2).
--
-- STACKED AFTER 0160. REPO ONLY until separately approved.
--
-- Field facts (Al-Khalid, DH-XVR1B08-I, Agent 5.1.1):
--   * recorder_health.recording_state was never written by any RPC after 0147's
--     one-time copy, so every recorder read 'unknown';
--   * storage was 'unknown' because the Agent could not parse the recorder's
--     disk reply, and no disk inventory or capacity ever reached the cloud;
--   * per-camera recording could only be 'recording' or 'unknown'.
--
-- Rule (owner-mandated): never silently convert UNKNOWN into OK. A state is
-- positive only with positive evidence; otherwise it stays 'unknown' with a
-- reason code.
--
-- What changes:
--   1. recorder_storage_disks: the current per-disk inventory of each recorder
--      (state, capacity, free space), keyed by (recorder_id, disk_id), written
--      only by wl_report_recorder_storage_disks (new, key-authenticated,
--      recorder-scoped, the 0147 auth pattern). recorder_health gains the
--      recorder's capacity totals.
--   2. camera_health.latest_recording_at: end of the newest archive segment the
--      Agent found for that camera (monotonic, never in the future).
--   3. wl_report_recorder_recording_storage_current_core (same signature) also
--      accepts the 5.1.2 reason codes, stores latest_recording_at, refuses a
--      'no_recent_recording' verdict that is not backed by an archive search,
--      and derives the recorder's recording rollup. Both deployed wrappers
--      (0147 recorder-scoped, 0089 site-scoped) call this core unchanged, so
--      5.0.x / 5.1.1 Agents keep their exact signatures and payloads.
--   4. wl_rollup_recorder_recording: recorder_health.recording_state from that
--      recorder's configured canonical cameras' fresh rec_current_state:
--        any configured live (not video-loss) camera not_recording -> not_recording
--        else any storage_fault                                  -> storage_fault
--        every camera recording (video-loss cameras excused,
--          at least one recording)                               -> recording
--        otherwise                                               -> unknown
--   5. wl_my_site_recorders (0152 body): additive recording_state,
--      storage_state, verified and capacity fields. 'healthy' keeps its 0152
--      meaning (the recorder is reachable and signed in, shown as "Available");
--      it now also needs no fresh storage problem, and 'verified' is true only
--      when storage is positively ok AND the recording rollup is positively
--      'recording'. Unknown recording/storage is reported as 'unknown', never
--      folded into a healthy value.

-- ---------------------------------------------------------------------
-- 1. Columns
-- ---------------------------------------------------------------------
alter table public.camera_health
  add column if not exists latest_recording_at timestamptz;

alter table public.recorder_health
  add column if not exists recording_reason_code text;
alter table public.recorder_health
  add column if not exists recording_state_at timestamptz;
alter table public.recorder_health
  add column if not exists storage_total_bytes bigint;
alter table public.recorder_health
  add column if not exists storage_free_bytes bigint;
alter table public.recorder_health
  add column if not exists storage_disk_count integer;
alter table public.recorder_health
  add column if not exists storage_capacity_at timestamptz;

-- ---------------------------------------------------------------------
-- 2. Per-disk inventory
-- ---------------------------------------------------------------------
create table if not exists public.recorder_storage_disks (
  recorder_id uuid not null,
  disk_id text not null check (length(disk_id) between 1 and 64),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  disk_path text null check (disk_path is null or length(disk_path) <= 128),
  disk_type text null check (disk_type is null or length(disk_type) <= 32),
  state text not null default 'unknown'
    check (state in ('ok','fault','unknown')),
  reason_code text null,
  total_bytes bigint null check (total_bytes is null or total_bytes >= 0),
  free_bytes bigint null check (free_bytes is null or free_bytes >= 0),
  present boolean not null default true,
  first_seen_at timestamptz not null default now(),
  observed_at timestamptz not null default now(),
  primary key (recorder_id, disk_id),
  constraint recorder_storage_disks_free_le_total
    check (free_bytes is null or total_bytes is null or free_bytes <= total_bytes),
  constraint recorder_storage_disks_recorder_lineage_fkey
    foreign key (recorder_id,tenant_id,site_id)
    references public.recorders(id,tenant_id,site_id)
    on delete cascade
);

create index if not exists recorder_storage_disks_site_idx
  on public.recorder_storage_disks(site_id, recorder_id);

alter table public.recorder_storage_disks enable row level security;
revoke all on table public.recorder_storage_disks from public,anon,authenticated;
grant select on table public.recorder_storage_disks to authenticated;

drop policy if exists portal_read_recorder_storage_disks on public.recorder_storage_disks;
create policy portal_read_recorder_storage_disks on public.recorder_storage_disks
  for select to authenticated
  using (public.wl_is_member(tenant_id));

-- ---------------------------------------------------------------------
-- 3. Recorder recording rollup (internal; caller has authorized the Agent)
-- ---------------------------------------------------------------------
create or replace function public.wl_rollup_recorder_recording(
  p_recorder_id uuid,
  p_agent_id uuid
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_rec public.recorders;
  v_now timestamptz := now();
  v_fresh_cut timestamptz := now() - interval '15 minutes';
  v_configured int := 0;
  v_recording int := 0;
  v_not int := 0;
  v_fault int := 0;
  v_excused int := 0;
  v_state text;
  v_reason text;
begin
  select * into v_rec from public.recorders where id = p_recorder_id;
  if v_rec.id is null then
    return null;
  end if;

  select count(*),
         count(*) filter (where x.fresh and x.rec = 'recording'),
         count(*) filter (where x.fresh and x.rec = 'not_recording' and not x.video_loss),
         count(*) filter (where x.fresh and x.rec = 'storage_fault'),
         count(*) filter (where not (x.fresh and x.rec = 'recording') and x.video_loss)
    into v_configured, v_recording, v_not, v_fault, v_excused
    from (
      select coalesce(ch.rec_current_at >= v_fresh_cut, false) as fresh,
             coalesce(ch.rec_current_state, 'unknown') as rec,
             coalesce(
               (ch.health_state::text = 'offline' and ch.reason_code::text = 'video_loss')
               or (ch.rec_current_at >= v_fresh_cut
                   and ch.rec_current_reason_code = 'video_loss'),
               false) as video_loss
        from public.cameras c
        left join public.camera_health ch on ch.camera_id = c.id
       where c.recorder_id = v_rec.id
         and c.tenant_id = v_rec.tenant_id
         and c.site_id = v_rec.site_id
         and c.is_configured
         and coalesce(c.is_canonical, true)
    ) x;

  if v_configured = 0 then
    v_state := 'unknown'; v_reason := 'no_cameras';
  elsif v_not > 0 then
    v_state := 'not_recording'; v_reason := 'camera_not_recording';
  elsif v_fault > 0 then
    v_state := 'storage_fault'; v_reason := 'storage_fault';
  elsif v_recording > 0 and v_recording + v_excused = v_configured then
    v_state := 'recording';
    v_reason := case when v_excused > 0 then 'video_loss_excluded' else 'ok' end;
  else
    v_state := 'unknown'; v_reason := 'not_verified';
  end if;

  -- updated_at stays the connectivity report's clock (0147); the rollup has
  -- its own recording_state_at.
  insert into public.recorder_health as rh(
    recorder_id, agent_id, tenant_id, site_id,
    recording_state, recording_reason_code, recording_state_at
  ) values (
    v_rec.id, p_agent_id, v_rec.tenant_id, v_rec.site_id,
    v_state, v_reason, v_now
  )
  on conflict (recorder_id, agent_id) do update
     set recording_state = excluded.recording_state,
         recording_reason_code = excluded.recording_reason_code,
         recording_state_at = excluded.recording_state_at;

  return jsonb_build_object('recording_state', v_state,
                            'recording_reason', v_reason,
                            'cameras', v_configured,
                            'recording', v_recording,
                            'not_recording', v_not,
                            'storage_fault', v_fault,
                            'video_loss_excused', v_excused);
end
$function$;

revoke all on function public.wl_rollup_recorder_recording(uuid,uuid)
  from public,anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- 4. Current-proof core (0147 signature and contract, extended)
-- ---------------------------------------------------------------------
create or replace function public.wl_report_recorder_recording_storage_current_core(
  p_agent_id uuid,
  p_tenant_id uuid,
  p_site_id uuid,
  p_recorder_id uuid,
  p_report jsonb
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_now timestamptz := now();
  v_storage_state text;
  v_storage_reason text;
  v_recording_evidence text := lower(coalesce(p_report->>'recording_evidence','unknown'));
  v_count int := 0;
  v_rollup jsonb;
begin
  if not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=p_tenant_id
       and r.site_id=p_site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  v_storage_state := lower(coalesce(p_report#>>'{storage,state}','unknown'));
  if v_storage_state not in ('ok','degraded','fault','unknown') then
    v_storage_state := 'unknown';
  end if;
  v_storage_reason := lower(coalesce(p_report#>>'{storage,reason}','unknown'));
  if v_storage_reason not in ('ok','unknown','storage_fault','disk_error','disk_full',
                              'nvr_unreachable','nvr_auth_failed','agent_unreachable',
                              'no_disks_reported','capacity_unknown','disk_state_unknown') then
    v_storage_reason := 'unknown';
  end if;

  -- updated_at stays the connectivity report's clock: storage proof alone
  -- must not make a stale reachability look fresh.
  insert into public.recorder_health as rh(
    recorder_id,agent_id,tenant_id,site_id,
    sto_current_state,sto_current_reason_code,sto_current_at,sto_current_evidence
  ) values (
    p_recorder_id,p_agent_id,p_tenant_id,p_site_id,
    v_storage_state,v_storage_reason,v_now,'vendor_status'
  )
  on conflict (recorder_id,agent_id) do update
     set sto_current_state=excluded.sto_current_state,
         sto_current_reason_code=excluded.sto_current_reason_code,
         sto_current_at=excluded.sto_current_at,
         sto_current_evidence=excluded.sto_current_evidence;

  with raw as (
    select r->>'channel' as channel,
           lower(coalesce(r->>'state','unknown')) as raw_state,
           lower(coalesce(r->>'reason','unknown')) as raw_reason,
           public.wl_try_timestamptz(r->>'latest_recording_at') as raw_latest
      from jsonb_array_elements(coalesce(p_report#>'{recording,channels}','[]'::jsonb)) r
     where jsonb_typeof(r) = 'object'
       and coalesce(r->>'channel','') <> ''
  ), clamped as (
    -- A 'recording' verdict needs archive evidence (0089). A 'no_recent_recording'
    -- verdict means "the archive search succeeded and found nothing": without
    -- that evidence it is unknown, never a recording fault.
    select raw.channel,
           case
             when raw.raw_state = 'recording' and v_recording_evidence <> 'archive_search'
               then 'unknown'
             when raw.raw_state = 'not_recording' and raw.raw_reason = 'no_recent_recording'
                  and v_recording_evidence <> 'archive_search'
               then 'unknown'
             when raw.raw_state in ('recording','not_recording','storage_fault','unknown')
               then raw.raw_state
             else 'unknown'
           end as state,
           raw.raw_reason,
           raw.raw_latest
      from raw
  ), normalized as (
    select c.id as camera_id,
           case when not c.is_configured then 'unknown' else k.state end as current_state,
           case
             when not c.is_configured then 'channel_disabled'
             when k.state = 'unknown' and k.raw_reason in ('ok','not_recording','no_recent_recording')
               then 'unknown'
             when k.raw_reason in ('ok','unknown','not_recording','storage_fault','channel_missing',
                                   'channel_disabled','nvr_unreachable','nvr_auth_failed',
                                   'agent_unreachable','disk_error','disk_full',
                                   'recording_disabled','no_recent_recording','video_loss',
                                   'archive_search_failed','no_recent_archive') then k.raw_reason
             else 'unknown'
           end as current_reason,
           -- The newest footage the archive search saw: only with a recording
           -- verdict from an archive search, never in the future.
           case
             when c.is_configured and k.state = 'recording'
                  and v_recording_evidence = 'archive_search'
                  and k.raw_latest <= v_now + interval '5 minutes'
               then k.raw_latest
           end as latest_recording_at,
           c.is_configured
      from clamped k
      join public.cameras c
        on c.site_id = p_site_id
       and c.tenant_id = p_tenant_id
       and c.recorder_id = p_recorder_id
       and c.channel = k.channel
  ), up as (
    insert into public.camera_health as ch
      (camera_id, tenant_id, site_id,
       rec_current_state, rec_current_reason_code, rec_current_at,
       rec_current_evidence, latest_recording_at, updated_at)
    select n.camera_id, p_tenant_id, p_site_id,
           n.current_state, n.current_reason, v_now,
           case when n.is_configured then v_recording_evidence else 'inventory' end,
           n.latest_recording_at,
           v_now
      from normalized n
    on conflict (camera_id) do update
       set rec_current_state = excluded.rec_current_state,
           rec_current_reason_code = excluded.rec_current_reason_code,
           rec_current_at = excluded.rec_current_at,
           rec_current_evidence = excluded.rec_current_evidence,
           -- greatest() ignores NULL: an empty search never erases older footage.
           latest_recording_at = greatest(ch.latest_recording_at, excluded.latest_recording_at),
           updated_at = v_now
    returning 1
  )
  select count(*) into v_count from up;

  v_rollup := public.wl_rollup_recorder_recording(p_recorder_id, p_agent_id);

  update public.agents set last_seen_at = v_now where id = p_agent_id;

  return jsonb_build_object('ok', true,
                            'agent_id', p_agent_id,
                            'site_id', p_site_id,
                            'recorder_id', p_recorder_id,
                            'storage_state', v_storage_state,
                            'storage_reason', v_storage_reason,
                            'recording_evidence', v_recording_evidence,
                            'cameras_refreshed', v_count,
                            'recording_state', v_rollup->>'recording_state',
                            'recording_reason', v_rollup->>'recording_reason',
                            'server_time', v_now);
end
$function$;

revoke all on function public.wl_report_recorder_recording_storage_current_core(
  uuid,uuid,uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- 5. Disk inventory report (key-authenticated, recorder-scoped)
--
-- Auth exactly as wl_report_recorder_recording_storage_current (0147):
-- wl_auth_agent (28000), current site Agent authority (42501), and the
-- recorder must be a configured recorder of the Agent's own site (42501).
--
-- p_report: {"disks": [{"id","path","type","state","reason",
--                       "total_bytes","free_bytes"}...],
--            "state","reason","total_bytes","free_bytes"}
-- Disks absent from a report stay as rows with present=false, state
-- 'unknown', reason 'disk_not_reported' (current inventory, not history).
-- A report without a 'disks' array changes nothing.
-- ---------------------------------------------------------------------
create or replace function public.wl_report_recorder_storage_disks(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_report jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_now timestamptz := now();
  v_norm jsonb;
  v_count int := 0;
  v_missing int := 0;
  v_total bigint;
  v_free bigint;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  if p_recorder_id is null or not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  if jsonb_typeof(p_report->'disks') is distinct from 'array' then
    return jsonb_build_object('ok', false, 'reason', 'no_disk_inventory',
                              'recorder_id', p_recorder_id);
  end if;
  if jsonb_array_length(p_report->'disks') > 64 then
    raise exception 'too many disks in one report' using errcode='22023';
  end if;

  with raw as (
    select left(nullif(btrim(d->>'id'),''),64) as disk_id,
           left(nullif(btrim(d->>'path'),''),128) as disk_path,
           left(nullif(btrim(d->>'type'),''),32) as disk_type,
           case when lower(coalesce(d->>'state','unknown')) in ('ok','fault','unknown')
                then lower(coalesce(d->>'state','unknown')) else 'unknown' end as state,
           case when lower(coalesce(d->>'reason','unknown')) in (
                  'ok','unknown','disk_error','disk_full','disk_state_unknown','capacity_unknown')
                then lower(coalesce(d->>'reason','unknown')) else 'unknown' end as reason_code,
           public.wl_try_bigint(d->>'total_bytes') as total_bytes,
           public.wl_try_bigint(d->>'free_bytes') as free_bytes,
           e.ord
      from jsonb_array_elements(p_report->'disks') with ordinality e(d,ord)
     where jsonb_typeof(d) = 'object'
  ), dedup as (
    select distinct on (disk_id) *
      from raw
     where disk_id is not null
     order by disk_id, ord
  ), sized as (
    -- Sizes are both present, non-negative and consistent, or both NULL.
    select disk_id, disk_path, disk_type, state, reason_code,
           case when total_bytes >= 0 and free_bytes >= 0 and free_bytes <= total_bytes
                then total_bytes end as total_bytes,
           case when total_bytes >= 0 and free_bytes >= 0 and free_bytes <= total_bytes
                then free_bytes end as free_bytes
      from dedup
  )
  select coalesce(jsonb_agg(to_jsonb(s)), '[]'::jsonb) into v_norm from sized s;

  with d as (
    select * from jsonb_to_recordset(v_norm) as x(
      disk_id text, disk_path text, disk_type text, state text, reason_code text,
      total_bytes bigint, free_bytes bigint)
  ), up as (
    insert into public.recorder_storage_disks as k(
      recorder_id, disk_id, tenant_id, site_id, agent_id,
      disk_path, disk_type, state, reason_code, total_bytes, free_bytes,
      present, first_seen_at, observed_at
    )
    select p_recorder_id, d.disk_id, v_agent.tenant_id, v_agent.site_id, v_agent.id,
           d.disk_path, d.disk_type, d.state, d.reason_code, d.total_bytes, d.free_bytes,
           true, v_now, v_now
      from d
    on conflict (recorder_id, disk_id) do update
       set agent_id = excluded.agent_id,
           disk_path = excluded.disk_path,
           disk_type = excluded.disk_type,
           state = excluded.state,
           reason_code = excluded.reason_code,
           total_bytes = excluded.total_bytes,
           free_bytes = excluded.free_bytes,
           present = true,
           observed_at = excluded.observed_at
    returning 1
  )
  select count(*) into v_count from up;

  with gone as (
    update public.recorder_storage_disks k
       set present = false,
           state = 'unknown',
           reason_code = 'disk_not_reported',
           observed_at = v_now
     where k.recorder_id = p_recorder_id
       and k.present
       and not exists (
         select 1 from jsonb_to_recordset(v_norm) as x(disk_id text)
          where x.disk_id = k.disk_id
       )
    returning 1
  )
  select count(*) into v_missing from gone;

  select sum(x.total_bytes), sum(x.free_bytes)
    into v_total, v_free
    from jsonb_to_recordset(v_norm) as x(total_bytes bigint, free_bytes bigint)
   where x.total_bytes is not null and x.free_bytes is not null;

  -- Capacity totals on the recorder's own health row; updated_at stays the
  -- connectivity clock (0147), so this never makes reachability look fresh.
  insert into public.recorder_health as rh(
    recorder_id, agent_id, tenant_id, site_id,
    storage_total_bytes, storage_free_bytes, storage_disk_count, storage_capacity_at
  ) values (
    p_recorder_id, v_agent.id, v_agent.tenant_id, v_agent.site_id,
    v_total, v_free, v_count, v_now
  )
  on conflict (recorder_id, agent_id) do update
     set storage_total_bytes = excluded.storage_total_bytes,
         storage_free_bytes = excluded.storage_free_bytes,
         storage_disk_count = excluded.storage_disk_count,
         storage_capacity_at = excluded.storage_capacity_at;

  update public.agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object('ok', true,
                            'recorder_id', p_recorder_id,
                            'disks_reported', v_count,
                            'disks_not_reported', v_missing,
                            'total_bytes', v_total,
                            'free_bytes', v_free,
                            'server_time', v_now);
end
$function$;

revoke all on function public.wl_report_recorder_storage_disks(uuid,text,uuid,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_recorder_storage_disks(uuid,text,uuid,jsonb)
  to anon;

-- ---------------------------------------------------------------------
-- 6. Owner recorder read model (0152 body; additive fields)
-- ---------------------------------------------------------------------
create or replace function public.wl_my_site_recorders(
  p_site_id uuid
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_current_agent uuid;
  v_recorders jsonb;
  v_fresh_cut timestamptz := now() - interval '15 minutes';
begin
  v_tenant := public.wl_assert_my_site(p_site_id);
  v_current_agent := public.wl_current_site_agent(p_site_id);

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id', q.id,
        'name', q.display_name,
        'state', q.owner_state,
        'issue', q.owner_issue,
        'checked_at', q.checked_at,
        'camera_count', q.camera_count,
        'camera_ids', q.camera_ids,
        -- 0161: positive-evidence-only recorder truth. 'unknown' is never
        -- shown as a healthy value; 'verified' needs storage ok AND every
        -- camera's recording positively confirmed.
        'recording_state', q.recording_now,
        'storage_state', q.storage_now,
        'verified', (q.owner_state = 'healthy'
                     and q.storage_now = 'ok'
                     and q.recording_now = 'recording'),
        'storage_total_bytes', q.capacity_total,
        'storage_free_bytes', q.capacity_free
      )
      order by q.is_primary desc, q.created_at, q.id
    ),
    '[]'::jsonb
  )
  into v_recorders
  from (
    select
      s.*,
      case
        when s.recorder_id_seen is null then 'unknown'
        when s.checked_at is null or s.checked_at < v_fresh_cut then 'unknown'
        when s.nvr_reachable is false then 'offline'
        when s.nvr_auth_ok is false then 'attention'
        when s.storage_any in ('fault','degraded') then 'attention'
        when s.nvr_reachable is true
         and s.nvr_auth_ok is true
          then 'healthy'
        else 'unknown'
      end as owner_state,
      case
        when s.recorder_id_seen is null then null
        when s.checked_at is null or s.checked_at < v_fresh_cut then null
        when s.nvr_reachable is false then 'connection'
        when s.nvr_auth_ok is false then 'sign_in'
        when s.storage_any in ('fault','degraded') then 'storage'
        else null
      end as owner_issue
    from (
      select
        r.id,
        r.display_name,
        r.is_primary,
        r.created_at,
        rh.recorder_id as recorder_id_seen,
        rh.updated_at as checked_at,
        rh.nvr_reachable,
        rh.nvr_auth_ok,
        -- Storage: the fresh present-tense proof wins; otherwise the durable
        -- ledger state (0148) as before.
        lower(case when rh.sto_current_at >= v_fresh_cut
                        and coalesce(rh.sto_current_state,'unknown') <> 'unknown'
                   then rh.sto_current_state
                   else coalesce(rh.storage_state,'unknown') end) as storage_any,
        case
          when rh.updated_at is null or rh.updated_at < v_fresh_cut then 'unknown'
          when rh.sto_current_at >= v_fresh_cut
            then lower(coalesce(rh.sto_current_state,'unknown'))
          else 'unknown'
        end as storage_now,
        case
          when rh.updated_at is null or rh.updated_at < v_fresh_cut then 'unknown'
          when rh.recording_state_at >= v_fresh_cut
            then lower(coalesce(rh.recording_state,'unknown'))
          else 'unknown'
        end as recording_now,
        case when rh.storage_capacity_at >= v_fresh_cut then rh.storage_total_bytes end
          as capacity_total,
        case when rh.storage_capacity_at >= v_fresh_cut then rh.storage_free_bytes end
          as capacity_free,
        coalesce(cam.camera_count,0) as camera_count,
        coalesce(cam.camera_ids,'[]'::jsonb) as camera_ids
      from public.recorders r
      left join lateral (
        select x.*
          from public.recorder_health x
         where x.recorder_id=r.id
           and x.tenant_id=v_tenant
           and x.site_id=p_site_id
         order by
           case
             when v_current_agent is not null and x.agent_id=v_current_agent then 0
             else 1
           end,
           x.updated_at desc
         limit 1
      ) rh on true
      left join lateral (
        select
          count(*)::int as camera_count,
          coalesce(
            jsonb_agg(c.id order by
              case when c.channel~'^[0-9]+$' then c.channel::int else 2147483647 end,
              c.channel,c.id
            ),
            '[]'::jsonb
          ) as camera_ids
        from public.cameras c
        where c.tenant_id=v_tenant
          and c.site_id=p_site_id
          and c.recorder_id=r.id
          and c.is_configured
          and coalesce(c.is_canonical,true)
      ) cam on true
      where r.tenant_id=v_tenant
        and r.site_id=p_site_id
        and r.is_configured
    ) s
  ) q;

  return jsonb_build_object(
    'enabled', true,
    'site_id', p_site_id,
    'recorders', v_recorders
  );
end
$function$;

revoke all on function public.wl_my_site_recorders(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_my_site_recorders(uuid)
  to authenticated;

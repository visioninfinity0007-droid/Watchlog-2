-- 0152 - Customer-safe multi-recorder owner read model.
--
-- STACKED AFTER 0151. REPO ONLY until separately approved.
--
-- Purpose:
--   * give the owner portal one governed recorder/root-cause view;
--   * expose only information an owner needs to understand monitoring impact;
--   * keep recorder credentials, local addresses, driver names, vendor/model and
--     capability internals out of the customer-facing read model.
--
-- This is a READ model only. It does not configure, disable or mutate recorders.

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
        'camera_ids', q.camera_ids
      )
      order by q.is_primary desc, q.created_at, q.id
    ),
    '[]'::jsonb
  )
  into v_recorders
  from (
    select
      r.id,
      r.display_name,
      r.is_primary,
      r.created_at,
      rh.updated_at as checked_at,
      case
        when rh.recorder_id is null then 'unknown'
        when rh.updated_at is null or rh.updated_at < v_fresh_cut then 'unknown'
        when rh.nvr_reachable is false then 'offline'
        when rh.nvr_auth_ok is false then 'attention'
        when lower(coalesce(rh.storage_state,'unknown')) in ('fault','degraded')
          then 'attention'
        when rh.nvr_reachable is true
         and rh.nvr_auth_ok is true
          then 'healthy'
        else 'unknown'
      end as owner_state,
      case
        when rh.recorder_id is null then null
        when rh.updated_at is null or rh.updated_at < v_fresh_cut then null
        when rh.nvr_reachable is false then 'connection'
        when rh.nvr_auth_ok is false then 'sign_in'
        when lower(coalesce(rh.storage_state,'unknown')) in ('fault','degraded')
          then 'storage'
        else null
      end as owner_issue,
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


-- Extend the existing governed owner/AI context with recorder provenance.
-- This is additive: all existing fields remain unchanged. UUIDs are used by the
-- portal for deterministic camera/event grouping and are not rendered as copy.
create or replace function public.wl_ai_context(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $function$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_diag jsonb;
  v_ctx jsonb;
  v_onboarding jsonb;
  v_recent jsonb;
  v_camera_rows jsonb;
  v_recorders jsonb;
  v_recorder_count int := 0;
begin
  select * into v_site
    from public.sites
   where id=p_site_id
     and tenant_id=v_tenant;

  v_diag := public.wl_my_site_diagnosis(p_site_id);
  v_ctx := public.wl_my_site_context(p_site_id);
  v_onboarding := public.wl_onboarding_status(p_site_id);
  v_recorders := public.wl_my_site_recorders(p_site_id);
  v_recorder_count := coalesce(jsonb_array_length(v_recorders->'recorders'),0);

  select coalesce(
    jsonb_agg(to_jsonb(r) order by r.device_ts desc),
    '[]'::jsonb
  )
  into v_recent
  from (
    select
      e.id as event_id,
      e.camera_id,
      e.recorder_id,
      e.event_type,
      e.device_ts,
      e.received_at,
      case
        when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
          then 'Camera '||coalesce(c.physical_channel,c.channel)
        else c.name
      end as camera,
      coalesce(c.physical_channel,c.channel) as channel,
      coalesce(e.payload->>'source','live') as source,
      case lower(trim(coalesce(e.payload->>'recovered','false')))
        when 'true' then true
        when 't' then true
        when '1' then true
        when 'yes' then true
        else false
      end as recovered
    from public.events e
    left join public.cameras c on c.id=e.camera_id
    where e.site_id=p_site_id
      and e.tenant_id=v_tenant
    order by e.device_ts desc
    limit 20
  ) r;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id',c.id,
        'recorder_id',c.recorder_id,
        'channel',coalesce(c.physical_channel,c.channel),
        'name',case
          when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
            then 'Camera '||coalesce(c.physical_channel,c.channel)
          else c.name
        end,
        'purpose',c.purpose,
        'monitor',c.is_configured,
        'analytics_enabled',coalesce(c.analytics_enabled,false),
        'health_state',coalesce(h.health_state::text,'unknown'),
        'recording_state',coalesce(h.recording_state::text,'unknown')
      )
      order by
        c.recorder_id,
        case
          when coalesce(c.physical_channel,c.channel) ~ '^[0-9]+$'
            then coalesce(c.physical_channel,c.channel)::int
          else 2147483647
        end,
        coalesce(c.physical_channel,c.channel)
    ),
    '[]'::jsonb
  )
  into v_camera_rows
  from public.cameras c
  join public.recorders cr
    on cr.id=c.recorder_id
   and cr.tenant_id=c.tenant_id
   and cr.site_id=c.site_id
   and cr.is_configured
  left join public.camera_health h on h.camera_id=c.id
  where c.site_id=p_site_id
    and c.tenant_id=v_tenant
    and coalesce(c.is_canonical,true);

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v7',
    'generated_at',now(),
    'site',jsonb_build_object(
      'id',v_site.id,
      'name',v_site.name,
      'timezone',v_site.timezone
    ),
    'business_context',v_ctx,
    'onboarding',v_onboarding,
    -- Legacy recorder/capability diagnosis is only valid while recorder
    -- identity is singleton. On a true multi-recorder site fail closed rather
    -- than projecting one recorder's capability truth onto the whole site.
    'recorder',case when v_recorder_count<=1 then v_diag->'recorder' else null end,
    'recorders',coalesce(v_recorders->'recorders','[]'::jsonb),
    'connectivity',v_diag->'connectivity',
    'capabilities',case
      when v_recorder_count<=1 then v_diag->'capabilities'
      else '{}'::jsonb
    end,
    'capability_known',case
      when v_recorder_count<=1 then coalesce((v_diag->>'capability_known')::boolean,false)
      else false
    end,
    'cameras',v_camera_rows,
    'faults',(
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'camera',c.name,
            'reason',coalesce(h.reason_code::text,'unknown')
          )
          order by c.name
        ),
        '[]'::jsonb
      )
      from public.camera_health h
      join public.cameras c on c.id=h.camera_id
      join public.recorders fr
        on fr.id=c.recorder_id
       and fr.tenant_id=c.tenant_id
       and fr.site_id=c.site_id
       and fr.is_configured
      where h.site_id=p_site_id
        and h.tenant_id=v_tenant
        and c.is_configured
        and coalesce(c.is_canonical,true)
        and h.health_state::text='offline'
    ),
    'coverage',v_diag->'coverage',
    'permissions',v_diag->'tiers',
    'recent_events',v_recent,
    'safety',jsonb_build_object(
      'recorder_credentials_leave_site',false,
      'recorder_writes_require_approval',true,
      'unknown_capability_must_not_be_assumed',true
    )
  );
end
$function$;


-- ---------------------------------------------------------------------
-- Recorder capability snapshot truth (NEW-L5).
--
-- sites.capabilities is the recorder analytics snapshot an Agent last synced
-- (wl_sync_capabilities). Nothing re-syncs it when the Agent changes driver
-- or a new Agent takes over, so at Al-Khalid a 2026-09-24 dahua-cgi snapshot
-- (native_ai, footage candidate) kept being served after the site moved to
-- ONVIF on 2026-09-26 with zero native events.
--
-- A snapshot is CURRENT only when all of these hold, otherwise it is UNKNOWN
-- and no capability claim is returned (fail closed, never promoted):
--   * it was recorded by the site's current authoritative Agent;
--   * under that Agent's current driver (case-insensitive; 'auto' or blank
--     is not a driver);
--   * it is not older than that Agent's enrollment;
--   * the site has at most one configured recorder (never project one
--     recorder's snapshot onto a multi-recorder site).
-- Provenance is stamped on every write of capabilities/capabilities_at and
-- names the Agent that WROTE it, never one inferred afterwards:
--   * wl_sync_capabilities passes its authenticated Agent to the trigger in a
--     transaction-local setting; a write with no Agent writer (direct or
--     service write), or by an Agent that is not the site's current Agent (a
--     stale PC left running), is stamped with no Agent and stays UNKNOWN;
--   * the driver is the writer's agents.device_driver at write time. The
--     Agent syncs capabilities at startup BEFORE that run's first heartbeat
--     records its driver, so that value can belong to the previous run; any
--     later change of the writer's device_driver therefore clears the stamp
--     (UNKNOWN until the Agent re-syncs), even if it changes back.
-- Rows synced before this migration carry no provenance and stay UNKNOWN
-- until the current Agent re-syncs.
--
-- The AI context (wl_ai_context), Site Control diagnosis
-- (wl_my_site_diagnosis) and owner recorder model (wl_my_site_recorders)
-- never read sites.capabilities; wl_capabilities() is the read model that
-- serves it, and it now applies this rule.
-- ---------------------------------------------------------------------
create table if not exists public.site_capability_provenance (
  site_id uuid primary key references public.sites(id) on delete cascade,
  capabilities_at timestamptz,
  agent_id uuid,
  driver text,
  recorded_at timestamptz not null default now()
);

alter table public.site_capability_provenance enable row level security;
revoke all on table public.site_capability_provenance
  from public, anon, authenticated;

create or replace function public.wl_stamp_site_capability_provenance()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_writer uuid := nullif(
    current_setting('watchlog.capability_writer_agent', true), '')::uuid;
  v_agent public.agents;
begin
  -- Only the site's current Agent, writing through wl_sync_capabilities, can
  -- record a snapshot that may later be served as current.
  if v_writer is not null
     and v_writer = public.wl_current_site_agent(new.id) then
    select a.* into v_agent
      from public.agents a
     where a.id = v_writer
       and a.site_id = new.id;
  end if;

  insert into public.site_capability_provenance as p(
    site_id, capabilities_at, agent_id, driver, recorded_at
  ) values (
    new.id, new.capabilities_at, v_agent.id, v_agent.device_driver, now()
  )
  on conflict (site_id) do update
     set capabilities_at = excluded.capabilities_at,
         agent_id = excluded.agent_id,
         driver = excluded.driver,
         recorded_at = excluded.recorded_at;
  return null;
end
$function$;

revoke all on function public.wl_stamp_site_capability_provenance()
  from public, anon, authenticated;

drop trigger if exists sites_capability_provenance on public.sites;
create trigger sites_capability_provenance
  after update of capabilities, capabilities_at on public.sites
  for each row
  execute function public.wl_stamp_site_capability_provenance();

-- The writer's driver changed after it recorded the snapshot: the stamp can
-- no longer say which driver took it, so clear it (fail closed).
create or replace function public.wl_clear_site_capability_provenance_on_driver()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  update public.site_capability_provenance p
     set agent_id = null,
         driver = null,
         recorded_at = now()
   where p.agent_id = new.id;
  return null;
end
$function$;

revoke all on function public.wl_clear_site_capability_provenance_on_driver()
  from public, anon, authenticated;

drop trigger if exists agents_capability_provenance_driver on public.agents;
create trigger agents_capability_provenance_driver
  after update of device_driver on public.agents
  for each row
  when (lower(btrim(coalesce(old.device_driver, '')))
        is distinct from lower(btrim(coalesce(new.device_driver, ''))))
  execute function public.wl_clear_site_capability_provenance_on_driver();

-- 0146 wl_sync_capabilities, unchanged except that it names itself as the
-- writer of the sites row for the provenance trigger above (transaction-local,
-- cleared right after the write).
create or replace function public.wl_sync_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_effective jsonb;
  v_recorder_id uuid;
begin
  v_agent := public.wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  v_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  v_effective := public.wl_overlay_camera_truth(v_agent.site_id, p_capabilities);

  perform set_config('watchlog.capability_writer_agent', v_agent.id::text, true);
  update public.sites
     set capabilities = v_effective,
         capabilities_at = now()
   where id = v_agent.site_id;
  perform set_config('watchlog.capability_writer_agent', '', true);

  update public.recorders
     set capabilities = v_effective,
         capabilities_at = now(),
         updated_at = now()
   where id = v_recorder_id
     and tenant_id = v_agent.tenant_id
     and site_id = v_agent.site_id;

  return jsonb_build_object(
    'ok', true,
    'recorder_id', v_recorder_id,
    'channels', coalesce(jsonb_array_length(v_effective->'channels'), 0)
  );
end
$function$;

-- Preserve the 0146 ACL exactly.
revoke all on function public.wl_sync_capabilities(uuid,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.wl_sync_capabilities(uuid,text,jsonb)
  to public, anon, authenticated, service_role;

create or replace function public.wl_site_capability_snapshot_current(
  p_site_id uuid
) returns boolean
language sql
stable
security definer
set search_path = public
as $function$
  select coalesce((
    select s.capabilities is not null
       and s.capabilities_at is not null
       and a.id is not null
       and p.agent_id = a.id
       and p.capabilities_at = s.capabilities_at
       and s.capabilities_at >= a.enrolled_at
       and lower(btrim(p.driver)) not in ('', 'auto')
       and lower(btrim(p.driver)) = lower(btrim(a.device_driver))
       and (select count(*)
              from public.recorders r
             where r.site_id = s.id
               and r.tenant_id = s.tenant_id
               and r.is_configured) <= 1
      from public.sites s
      left join public.agents a
        on a.id = public.wl_current_site_agent(s.id)
       and a.site_id = s.id
      left join public.site_capability_provenance p
        on p.site_id = s.id
     where s.id = p_site_id
  ), false);
$function$;

revoke all on function public.wl_site_capability_snapshot_current(uuid)
  from public, anon, authenticated;

-- Same shape as 0088 plus snapshot_state ('current' | 'unknown'). An unknown
-- snapshot returns capabilities null: no stale claim reaches a caller.
create or replace function public.wl_capabilities()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_tenant uuid := public.wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'site', s.name,
             'site_id', s.id,
             'reported_at', s.capabilities_at,
             'snapshot_state', case when x.is_current then 'current' else 'unknown' end,
             'capabilities', case when x.is_current
                                  then public.wl_overlay_camera_truth(s.id, s.capabilities)
                                  else null end)
             order by s.name)
      from public.sites s
      cross join lateral (
        select public.wl_site_capability_snapshot_current(s.id) as is_current
      ) x
     where s.tenant_id = v_tenant
       and s.capabilities is not null), '[]'::jsonb);
end
$function$;

-- Restate the 0015 ACL.
revoke all on function public.wl_capabilities() from public, anon;
grant execute on function public.wl_capabilities() to authenticated;

-- ---------------------------------------------------------------------
-- Recorder sections of the older owner read models (contract section 15).
-- wl_operations_report (0050) and wl_site_health_snapshot (0089) built
-- 'recorders' from the per-Agent nvr_health row, which the recorder-aware
-- RPCs never write: on a multi-recorder site it was missing or frozen at its
-- last singleton value and never named Recorder B. Both now list each
-- configured recorder from its own recorder_health row of the current Agent,
-- with connectivity older than 15 minutes reported unknown. The rest of each
-- body is unchanged from 0050 / 0089.
-- ---------------------------------------------------------------------

create or replace function public.wl_operations_report(
  p_site_id uuid,
  p_from    timestamptz default now() - interval '7 days',
  p_to      timestamptz default now()
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_site   public.sites;
  v_cov    jsonb;
  v_rel    jsonb;
  v_sec    jsonb;
  v_ops    jsonb;
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  select * into v_site from public.sites where id = p_site_id and tenant_id = v_tenant;
  if v_site.id is null then
    raise exception 'site not in your account' using errcode = '42501';
  end if;

  -- ---- completeness: coverage over MONITORED time; unverified excluded ------
  select jsonb_build_object(
    'wall_seconds', coalesce(sum(wall_seconds), 0),
    'monitored_seconds', coalesce(sum(monitored_seconds), 0),
    'unverified_seconds', coalesce(sum(unverified_seconds), 0),
    'coverage_pct', case when coalesce(sum(wall_seconds), 0) = 0 then null
        else round(100.0 * sum(monitored_seconds) / nullif(sum(wall_seconds), 0), 1) end,
    'unverified_pct', case when coalesce(sum(wall_seconds), 0) = 0 then null
        else round(100.0 * sum(unverified_seconds) / nullif(sum(wall_seconds), 0), 1) end,
    'note', 'Availability is measured over MONITORED time; unverified/UNKNOWN periods '
            || 'are surfaced, not counted as uptime or downtime.'
  ) into v_cov
  from public.monitoring_coverage
  where site_id = p_site_id
    and bucket_date between (p_from at time zone 'UTC')::date and (p_to at time zone 'UTC')::date;

  -- ---- reliability ----------------------------------------------------------
  with agent_un as (
    select id, started_at, coalesce(ended_at, now()) as endd
      from public.agent_unreachable_intervals
     where site_id = p_site_id and started_at < p_to and coalesce(ended_at, now()) > p_from
  ),
  unv as (
    select id, started_at, coalesce(ended_at, now()) as endd
      from public.unverified_intervals
     where site_id = p_site_id and started_at < p_to and coalesce(ended_at, now()) > p_from
  ),
  cam as (
    select count(*) as total,
           count(*) filter (where health_state = 'offline') as offline_now,
           count(*) filter (where health_state = 'unknown') as unknown_now
      from public.camera_health where site_id = p_site_id
  ),
  camf as (
    select count(*) as offline_faults,
           coalesce(jsonb_agg(id order by opened_at desc), '[]'::jsonb) as drill
      from public.operational_faults
     where site_id = p_site_id and fault_domain = 'camera' and opened_at between p_from and p_to
  )
  select jsonb_build_object(
    'agent_unreachable', jsonb_build_object(
       'intervals', (select count(*) from agent_un),
       'seconds', (select coalesce(round(sum(extract(epoch from (least(endd, p_to) - greatest(started_at, p_from)))))::bigint, 0) from agent_un),
       'drill', (select coalesce(jsonb_agg(id), '[]'::jsonb) from agent_un)),
    -- One entry per configured recorder from its recorder_health row of the
    -- current Agent (0152); connectivity older than 15 minutes is unknown.
    'recorders', (select coalesce(jsonb_agg(jsonb_build_object(
       'recorder_id', r.id, 'name', r.display_name, 'is_primary', r.is_primary,
       'agent_id', rh.agent_id,
       'reachable', case when rh.updated_at >= now() - interval '15 minutes' then rh.nvr_reachable end,
       'auth_ok', case when rh.updated_at >= now() - interval '15 minutes' then rh.nvr_auth_ok end,
       'fresh', coalesce(rh.updated_at >= now() - interval '15 minutes', false),
       'checked_at', rh.updated_at,
       'recording_state', case when rh.updated_at >= now() - interval '15 minutes'
                               then rh.recording_state else 'unknown' end,
       'storage_state', case when rh.updated_at >= now() - interval '15 minutes'
                             then rh.storage_state else 'unknown' end)
       order by r.is_primary desc, r.created_at, r.id), '[]'::jsonb)
       from public.recorders r
       left join public.recorder_health rh
         on rh.recorder_id = r.id
        and rh.agent_id = public.wl_current_site_agent(p_site_id)
      where r.site_id = p_site_id and r.tenant_id = v_tenant and r.is_configured),
    'cameras', jsonb_build_object(
       'total', (select total from cam), 'offline_now', (select offline_now from cam),
       'unknown_now', (select unknown_now from cam),
       'offline_faults_opened', (select offline_faults from camf), 'drill', (select drill from camf)),
    'unverified', jsonb_build_object(
       'intervals', (select count(*) from unv),
       'seconds', (select coalesce(round(sum(extract(epoch from (least(endd, p_to) - greatest(started_at, p_from)))))::bigint, 0) from unv))
  ) into v_rel;

  -- ---- security -------------------------------------------------------------
  with inc as (
    select * from public.operations_incidents
     where site_id = p_site_id and opened_at between p_from and p_to
  ),
  ev as (
    select event_type, count(*) c from public.events
     where site_id = p_site_id and received_at between p_from and p_to group by event_type
  ),
  clip as (
    select status, count(*) c from public.incident_clip_requests
     where site_id = p_site_id and requested_at between p_from and p_to group by status
  )
  select jsonb_build_object(
    'incidents', jsonb_build_object(
       'total', (select count(*) from inc),
       'by_status', (select coalesce(jsonb_object_agg(status, c), '{}'::jsonb)
                     from (select status, count(*) c from inc group by status) s),
       'by_severity', (select coalesce(jsonb_object_agg(severity, c), '{}'::jsonb)
                       from (select severity, count(*) c from inc group by severity) s),
       'review_required', (select count(*) from inc where review_required),
       'drill', (select coalesce(jsonb_agg(jsonb_build_object(
                   'id', id, 'type', incident_type, 'severity', severity, 'status', status,
                   'camera_id', camera_id, 'occurred_at', occurred_at,
                   'rule_id', rule_id, 'rule_version', rule_version) order by opened_at desc), '[]'::jsonb)
                 from inc)),
    'native_events', jsonb_build_object(
       'total', (select coalesce(sum(c), 0) from ev),
       'by_type', (select coalesce(jsonb_object_agg(event_type, c), '{}'::jsonb) from ev)),
    'evidence', jsonb_build_object(
       'clip_requests', (select coalesce(sum(c), 0) from clip),
       'by_status', (select coalesce(jsonb_object_agg(status, c), '{}'::jsonb) from clip))
  ) into v_sec;

  -- ---- operations (SOP violations by primitive) -----------------------------
  with inc as (
    select incident_type, id, opened_at from public.operations_incidents
     where site_id = p_site_id and opened_at between p_from and p_to
  )
  select jsonb_build_object(
    'sop_violations', jsonb_build_object(
       'total', (select count(*) from inc),
       'by_type', (select coalesce(jsonb_object_agg(incident_type, c), '{}'::jsonb)
                   from (select incident_type, count(*) c from inc group by incident_type) s),
       'drill', (select coalesce(jsonb_agg(id order by opened_at desc), '[]'::jsonb) from inc)),
    'dwell_wait', (select count(*) from inc where incident_type in ('zone_dwell', 'queue_wait')),
    'presence_absence', (select count(*) from inc where incident_type in ('zone_presence', 'zone_absence')),
    'occupancy', (select count(*) from inc where incident_type = 'occupancy'),
    'schedule', (select count(*) from inc where incident_type = 'schedule_activity')
  ) into v_ops;

  return jsonb_build_object(
    'site', jsonb_build_object('id', v_site.id, 'name', v_site.name),
    'period', jsonb_build_object('from', p_from, 'to', p_to),
    'completeness', coalesce(v_cov, jsonb_build_object(
       'wall_seconds', 0, 'monitored_seconds', 0, 'unverified_seconds', 0,
       'coverage_pct', null, 'unverified_pct', null,
       'note', 'No monitoring-coverage data recorded for this period; completeness is UNKNOWN.')),
    'reliability', v_rel,
    'security', v_sec,
    'operations', v_ops
  );
end $$;

revoke all on function public.wl_operations_report(uuid, timestamptz, timestamptz) from public, anon;
grant execute on function public.wl_operations_report(uuid, timestamptz, timestamptz) to authenticated;

create or replace function public.wl_site_health_snapshot(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_fresh_cut timestamptz := now() - interval '15 minutes';
  v_current_agent uuid := wl_current_site_agent(p_site_id);
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode='28000';
  end if;
  if not exists(select 1 from public.sites s
                where s.id=p_site_id and s.tenant_id=v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  return jsonb_build_object(
    'site_id', p_site_id,
    'current_agent_id', v_current_agent,
    'recorders', (
      -- One entry per configured recorder, from its own recorder_health row of
      -- the current Agent (0152). Connectivity older than 15 minutes is
      -- unknown, as in wl_my_site_recorders.
      select coalesce(jsonb_agg(jsonb_build_object(
        'recorder_id',r.id,
        'name',r.display_name,
        'is_primary',r.is_primary,
        'agent_id',v_current_agent,
        'nvr_reachable',case when rh.updated_at>=v_fresh_cut then rh.nvr_reachable end,
        'nvr_auth_ok',case when rh.updated_at>=v_fresh_cut then rh.nvr_auth_ok end,
        'health_fresh',coalesce(rh.updated_at>=v_fresh_cut,false),
        'recording_state',(
          select case
            when count(*) filter(where c.is_configured) = 0 then 'unknown'
            when count(*) filter(where c.is_configured and ch.rec_current_at>=v_fresh_cut
                                 and ch.rec_current_state='recording')
                 = count(*) filter(where c.is_configured) then 'recording'
            when count(*) filter(where c.is_configured and ch.rec_current_at>=v_fresh_cut
                                 and ch.rec_current_state='storage_fault') > 0 then 'storage_fault'
            when count(*) filter(where c.is_configured and ch.rec_current_at>=v_fresh_cut
                                 and ch.rec_current_state='not_recording') > 0 then 'not_recording'
            else 'unknown' end
            from public.cameras c
            left join public.camera_health ch on ch.camera_id=c.id
           where c.site_id=p_site_id and c.tenant_id=v_tenant
             and c.recorder_id=r.id),
        'storage_state',case when rh.sto_current_at>=v_fresh_cut
                             then rh.sto_current_state else 'unknown' end,
        'reason_code',case when rh.updated_at>=v_fresh_cut
                           then rh.reason_code else 'unknown' end,
        'storage_reason_code',case when rh.sto_current_at>=v_fresh_cut
                                   then rh.sto_current_reason_code else 'unknown' end,
        'storage_observed_at',rh.sto_current_at,
        'storage_fresh',coalesce(rh.sto_current_at>=v_fresh_cut,false),
        'storage_evidence',rh.sto_current_evidence,
        'updated_at',rh.updated_at)
        order by r.is_primary desc, r.created_at, r.id), '[]'::jsonb)
        from public.recorders r
        left join public.recorder_health rh
          on rh.recorder_id=r.id
         and rh.agent_id=v_current_agent
       where r.site_id=p_site_id and r.tenant_id=v_tenant
         and r.is_configured
    ),
    'cameras', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'camera_id',c.id,'channel',c.channel,'name',c.name,
        'is_configured',c.is_configured,
        'configuration_state',case when c.is_configured then 'configured' else 'no_camera_configured' end,
        'health_state',case when c.is_configured then coalesce(ch.health_state,'unknown') else 'unknown' end,
        'inventory_state',case when c.is_configured then coalesce(ci.inventory_state,'unknown') else 'disabled' end,
        'recording_state',case when not c.is_configured then 'unknown'
                               when ch.rec_current_at>=v_fresh_cut then coalesce(ch.rec_current_state,'unknown')
                               else 'unknown' end,
        'reason_code',case when c.is_configured then ch.reason_code else 'channel_disabled' end,
        'recording_reason_code',case when not c.is_configured then 'channel_disabled'
                                     when ch.rec_current_at>=v_fresh_cut then coalesce(ch.rec_current_reason_code,'unknown')
                                     else 'unknown' end,
        'recording_observed_at',ch.rec_current_at,
        'recording_fresh',case when c.is_configured then coalesce(ch.rec_current_at>=v_fresh_cut,false) else false end,
        'recording_evidence',ch.rec_current_evidence,
        'recording_transition_at',ch.rec_observed_at,
        'updated_at',greatest(ch.updated_at,ci.updated_at)) order by c.channel),'[]'::jsonb)
        from public.cameras c
        left join public.camera_health ch on ch.camera_id=c.id
        left join public.camera_inventory ci on ci.camera_id=c.id
       where c.site_id=p_site_id and c.tenant_id=v_tenant
    ),
    'faults', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id',f.id,'domain',f.fault_domain,'fault_type',f.fault_type,
        'severity',f.severity,'state',f.state,'reason_code',f.reason_code,
        'camera_id',f.camera_id,'agent_id',f.agent_id,
        'opened_at',f.opened_at,'acknowledged_at',f.acknowledged_at)
        order by case f.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                 f.opened_at desc),'[]'::jsonb)
        from public.operational_faults f
       where f.site_id=p_site_id and f.tenant_id=v_tenant and f.state<>'resolved'
         and (f.agent_id is null or f.agent_id=v_current_agent)
         and (f.camera_id is null or exists(select 1 from public.cameras fc
                                            where fc.id=f.camera_id and fc.is_configured))
    ),
    'summary', (
      select jsonb_build_object(
        'cameras_total',count(*) filter(where c.is_configured),
        'recorder_slots_total',count(*),
        'unconfigured_slots',count(*) filter(where not c.is_configured),
        'operational',count(*) filter(where c.is_configured and coalesce(ch.health_state,'unknown')='operational'),
        'degraded',count(*) filter(where c.is_configured and ch.health_state='degraded'),
        'offline',count(*) filter(where c.is_configured and ch.health_state='offline'),
        'unknown',count(*) filter(where c.is_configured and coalesce(ch.health_state,'unknown')='unknown'))
        from public.cameras c left join public.camera_health ch on ch.camera_id=c.id
       where c.site_id=p_site_id and c.tenant_id=v_tenant
    ),
    'faults_open', (
      select count(*) from public.operational_faults f
       where f.site_id=p_site_id and f.tenant_id=v_tenant and f.state<>'resolved'
         and (f.agent_id is null or f.agent_id=v_current_agent)
         and (f.camera_id is null or exists(select 1 from public.cameras fc
                                            where fc.id=f.camera_id and fc.is_configured))
    ),
    'server_time',now());
end $$;

revoke all on function public.wl_site_health_snapshot(uuid) from public, anon;
grant execute on function public.wl_site_health_snapshot(uuid) to authenticated;

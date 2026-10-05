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
-- Provenance is stamped on every write of capabilities/capabilities_at,
-- from the Agent that is current at that moment. Rows synced before this migration carry no
-- provenance and stay UNKNOWN until the current Agent re-syncs.
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
  v_agent public.agents;
begin
  select a.* into v_agent
    from public.agents a
   where a.id = public.wl_current_site_agent(new.id)
     and a.site_id = new.id;

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

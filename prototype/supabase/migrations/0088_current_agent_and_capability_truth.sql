-- =====================================================================
-- 0088 — Current-agent authority + canonical camera truth
--
-- A site may accumulate multiple historical Agent identities across upgrades /
-- re-enrolment. Those rows remain valuable history, but they must not continue
-- producing CURRENT site faults after a healthy replacement Agent has taken
-- over. Likewise recorder-reported channel titles are device metadata, not the
-- authoritative WatchLog camera inventory.
--
-- This migration:
--   * resolves a single current Agent identity per site;
--   * reconciles current faults only against that identity;
--   * returns only the current recorder in Site Health;
--   * overlays canonical cameras.name / cameras.is_configured on recorder
--     capability payloads without discarding the recorder's feature metadata;
--   * preserves historical Agent/NVR/fault rows unchanged.
-- =====================================================================

create or replace function public.wl_current_site_agent(p_site_id uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  with site_cfg as (
    select coalesce(multi_agent_enabled, false) as multi_agent_enabled
      from public.sites where id = p_site_id
  ), live_lease as (
    select l.holder_agent_id
      from public.site_agent_leases l, site_cfg s
     where s.multi_agent_enabled
       and l.site_id = p_site_id
       and l.holder_agent_id is not null
       and l.lease_expires_at > now()
     limit 1
  ), newest as (
    select a.id
      from public.agents a
     where a.site_id = p_site_id
     order by a.last_seen_at desc nulls last,
              a.enrolled_at desc nulls last,
              a.id desc
     limit 1
  )
  select coalesce((select holder_agent_id from live_lease),
                  (select id from newest));
$$;

create or replace function public.wl_overlay_camera_truth(
  p_site_id uuid,
  p_capabilities jsonb
) returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select case
    when p_capabilities is null then null
    when jsonb_typeof(p_capabilities) <> 'object' then p_capabilities
    when jsonb_typeof(p_capabilities->'channels') <> 'array' then p_capabilities
    else jsonb_set(
      p_capabilities,
      '{channels}',
      coalesce((
        select jsonb_agg(
          case
            when c.id is null then
              q.item || jsonb_build_object(
                'configuration_state', 'unknown',
                'configured', null)
            else
              q.item || jsonb_build_object(
                'name', c.name,
                'configured', c.is_configured,
                'configuration_state',
                  case when c.is_configured then 'configured'
                       else 'no_camera_configured' end)
          end
          order by q.ord)
          from jsonb_array_elements(p_capabilities->'channels')
               with ordinality as q(item, ord)
          left join public.cameras c
            on c.site_id = p_site_id
           and c.channel = q.item->>'channel'
      ), '[]'::jsonb),
      true)
  end;
$$;

create or replace function public.wl_sync_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_effective jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  v_effective := wl_overlay_camera_truth(v_agent.site_id, p_capabilities);

  update public.sites
     set capabilities = v_effective,
         capabilities_at = now()
   where id = v_agent.site_id;

  return jsonb_build_object(
    'ok', true,
    'channels', coalesce(jsonb_array_length(v_effective->'channels'), 0));
end $$;

create or replace function public.wl_capabilities()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'site', s.name,
             'site_id', s.id,
             'reported_at', s.capabilities_at,
             'capabilities', wl_overlay_camera_truth(s.id, s.capabilities))
             order by s.name)
      from public.sites s
     where s.tenant_id = v_tenant
       and s.capabilities is not null), '[]'::jsonb);
end $$;

create or replace function public.wl_reconcile_site_faults(p_site_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant     uuid;
  v_now        timestamptz := now();
  v_fresh_cut  timestamptz := v_now - interval '15 minutes';
  v_current_agent uuid := wl_current_site_agent(p_site_id);
  v_desired    jsonb;
  v_opened     int := 0;
  v_resolved   int := 0;
  v_open_total int := 0;
begin
  select tenant_id into v_tenant from public.sites where id = p_site_id;
  if v_tenant is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_site');
  end if;

  perform pg_advisory_xact_lock(hashtext('wl_reconcile_site_faults'), hashtext(p_site_id::text));

  with rec as (
    select nh.agent_id, nh.nvr_reachable, nh.nvr_auth_ok,
           nh.storage_state, nh.sto_observed_at,
           exists (select 1 from public.agent_unreachable_intervals aui
                    where aui.agent_id = nh.agent_id and aui.ended_at is null) as agent_down
      from public.nvr_health nh
     where nh.site_id = p_site_id
       and nh.agent_id = v_current_agent
  ),
  observable as (
    select exists (select 1 from rec
                    where not agent_down
                      and nvr_reachable is true
                      and nvr_auth_ok is true) as ok
  ),
  desired as (
    select 'agent:' || agent_id::text || ':unreachable' as dedupe_key,
           'agent' as fault_domain,
           'agent_unreachable' as fault_type,
           'critical' as severity,
           'agent_unreachable' as reason_code,
           null::uuid as camera_id, agent_id
      from rec where agent_down
    union all
    select 'nvr:' || agent_id::text || ':unreachable',
           'nvr_connectivity', 'nvr_unreachable', 'critical',
           'nvr_unreachable', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is false
    union all
    select 'nvr:' || agent_id::text || ':auth',
           'nvr_auth', 'nvr_auth_failed', 'critical',
           'nvr_auth_failed', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is true and nvr_auth_ok is false
    union all
    select 'nvr:' || agent_id::text || ':storage', 'storage',
           case when storage_state = 'fault' then 'storage_fault' else 'storage_degraded' end,
           case when storage_state = 'fault' then 'critical' else 'warning' end,
           case when storage_state = 'fault' then 'storage_fault' else 'disk_full' end,
           null::uuid, agent_id
      from rec
     where not agent_down
       and nvr_reachable is true
       and nvr_auth_ok is not false
       and sto_observed_at >= v_fresh_cut
       and storage_state in ('fault', 'degraded')
    union all
    select 'camera:' || ch.camera_id::text || ':offline',
           'camera', 'camera_offline', 'critical',
           'video_loss', ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.health_state = 'offline'
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
    union all
    select 'camera:' || ch.camera_id::text || ':recording',
           'recording',
           case when ch.recording_state = 'storage_fault'
                then 'recording_storage_fault' else 'not_recording' end,
           'warning',
           case when ch.recording_state = 'storage_fault'
                then 'storage_fault' else 'not_recording' end,
           ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.rec_observed_at >= v_fresh_cut
       and ch.recording_state in ('not_recording', 'storage_fault')
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'dedupe_key', dedupe_key,
           'fault_domain', fault_domain,
           'fault_type', fault_type,
           'severity', severity,
           'reason_code', reason_code,
           'camera_id', camera_id,
           'agent_id', agent_id)), '[]'::jsonb)
    into v_desired from desired;

  with d as (
    select * from jsonb_to_recordset(v_desired) as x(
      dedupe_key text, fault_domain text, fault_type text,
      severity text, reason_code text, camera_id uuid, agent_id uuid)
  ), ins as (
    insert into public.operational_faults
      (tenant_id, site_id, camera_id, agent_id, fault_domain, fault_type,
       severity, state, reason_code, dedupe_key, opened_at)
    select v_tenant, p_site_id, d.camera_id, d.agent_id,
           d.fault_domain, d.fault_type, d.severity,
           'open', d.reason_code, d.dedupe_key, v_now
      from d
    on conflict (dedupe_key) where state <> 'resolved' do nothing
    returning 1
  )
  select count(*) into v_opened from ins;

  with res as (
    update public.operational_faults f
       set state = 'resolved', resolved_at = v_now
     where f.site_id = p_site_id
       and f.state <> 'resolved'
       and not exists (
         select 1
           from jsonb_to_recordset(v_desired) as x(dedupe_key text)
          where x.dedupe_key = f.dedupe_key)
    returning 1
  )
  select count(*) into v_resolved from res;

  select count(*) into v_open_total
    from public.operational_faults
   where site_id = p_site_id and state <> 'resolved';

  return jsonb_build_object(
    'ok', true,
    'site_id', p_site_id,
    'current_agent_id', v_current_agent,
    'evaluated_at', v_now,
    'opened', v_opened,
    'resolved', v_resolved,
    'open_total', v_open_total);
end $$;

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
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists(select 1 from public.sites s
                where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  return jsonb_build_object(
    'site_id', p_site_id,
    'current_agent_id', v_current_agent,
    'recorders', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'agent_id', nh.agent_id,
        'nvr_reachable', nh.nvr_reachable,
        'nvr_auth_ok', nh.nvr_auth_ok,
        'recording_state', nh.recording_state,
        'storage_state', case when nh.sto_observed_at >= v_fresh_cut
                              then nh.storage_state else 'unknown' end,
        'reason_code', nh.reason_code,
        'storage_reason_code', case when nh.sto_observed_at >= v_fresh_cut
                                    then nh.storage_reason_code else 'unknown' end,
        'storage_observed_at', nh.sto_observed_at,
        'storage_fresh', coalesce(nh.sto_observed_at >= v_fresh_cut, false),
        'updated_at', nh.updated_at)), '[]'::jsonb)
        from public.nvr_health nh
       where nh.site_id = p_site_id
         and nh.tenant_id = v_tenant
         and nh.agent_id = v_current_agent
    ),
    'cameras', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'camera_id', c.id,
        'channel', c.channel,
        'name', c.name,
        'is_configured', c.is_configured,
        'configuration_state', case when c.is_configured
                                    then 'configured' else 'no_camera_configured' end,
        'health_state', case when c.is_configured
                             then coalesce(ch.health_state, 'unknown') else 'unknown' end,
        'inventory_state', case when c.is_configured
                                then coalesce(ci.inventory_state, 'unknown') else 'disabled' end,
        'recording_state', case
          when not c.is_configured then 'unknown'
          when ch.rec_observed_at >= v_fresh_cut then coalesce(ch.recording_state, 'unknown')
          else 'unknown' end,
        'reason_code', case when c.is_configured
                            then ch.reason_code else 'channel_disabled' end,
        'recording_reason_code', case
          when not c.is_configured then 'channel_disabled'
          when ch.rec_observed_at >= v_fresh_cut then ch.recording_reason_code
          else 'unknown' end,
        'recording_observed_at', ch.rec_observed_at,
        'recording_fresh', case when c.is_configured
                                then coalesce(ch.rec_observed_at >= v_fresh_cut, false)
                                else false end,
        'updated_at', greatest(ch.updated_at, ci.updated_at))
        order by c.channel), '[]'::jsonb)
        from public.cameras c
        left join public.camera_health ch on ch.camera_id = c.id
        left join public.camera_inventory ci on ci.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant
    ),
    'faults', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id', f.id,
        'domain', f.fault_domain,
        'fault_type', f.fault_type,
        'severity', f.severity,
        'state', f.state,
        'reason_code', f.reason_code,
        'camera_id', f.camera_id,
        'agent_id', f.agent_id,
        'opened_at', f.opened_at,
        'acknowledged_at', f.acknowledged_at)
        order by case f.severity when 'critical' then 0
                                 when 'warning' then 1 else 2 end,
                 f.opened_at desc), '[]'::jsonb)
        from public.operational_faults f
       where f.site_id = p_site_id
         and f.tenant_id = v_tenant
         and f.state <> 'resolved'
         and (f.agent_id is null or f.agent_id = v_current_agent)
         and (f.camera_id is null or exists(
              select 1 from public.cameras fc
               where fc.id = f.camera_id and fc.is_configured))
    ),
    'summary', (
      select jsonb_build_object(
        'cameras_total', count(*) filter(where c.is_configured),
        'recorder_slots_total', count(*),
        'unconfigured_slots', count(*) filter(where not c.is_configured),
        'operational', count(*) filter(where c.is_configured and coalesce(ch.health_state,'unknown')='operational'),
        'degraded', count(*) filter(where c.is_configured and ch.health_state='degraded'),
        'offline', count(*) filter(where c.is_configured and ch.health_state='offline'),
        'unknown', count(*) filter(where c.is_configured and coalesce(ch.health_state,'unknown')='unknown'))
        from public.cameras c
        left join public.camera_health ch on ch.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant
    ),
    'faults_open', (
      select count(*)
        from public.operational_faults f
       where f.site_id = p_site_id
         and f.tenant_id = v_tenant
         and f.state <> 'resolved'
         and (f.agent_id is null or f.agent_id = v_current_agent)
         and (f.camera_id is null or exists(
              select 1 from public.cameras fc
               where fc.id = f.camera_id and fc.is_configured))
    ),
    'server_time', now());
end $$;

-- Repair already-stored recorder channel labels immediately; future syncs are
-- canonicalized at the write boundary above.
update public.sites s
   set capabilities = public.wl_overlay_camera_truth(s.id, s.capabilities)
 where s.capabilities is not null;

-- Reconcile now so faults belonging only to retired Agent identities disappear
-- from CURRENT Site Health without deleting their historical rows.
select public.wl_sweep_faults();

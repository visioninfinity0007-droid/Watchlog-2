-- =====================================================================
-- 0086 - Recording/storage CURRENT-state freshness
--
-- The durable ledgers are transition-only by design. A steady state therefore
-- may have an old rec_observed_at / sto_observed_at even while newer Agent
-- heartbeats continue. That historical observation is valid evidence, but it
-- must not be presented forever as a CURRENT fault.
--
-- Health runs every 5 minutes. Current recording/storage claims are valid for
-- three cycles (15 minutes). Older evidence degrades to UNKNOWN in the read
-- model and cannot keep an operational fault open. No recorder setting is
-- changed and no historical transition is deleted.
-- =====================================================================

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
  v_desired    jsonb;
  v_opened     int := 0;
  v_resolved   int := 0;
  v_open_total int := 0;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_site');
  end if;

  perform pg_advisory_xact_lock(hashtext('wl_reconcile_site_faults'), hashtext(p_site_id::text));

  with rec as (
    select nh.agent_id, nh.nvr_reachable, nh.nvr_auth_ok,
           nh.storage_state, nh.sto_observed_at,
           exists (select 1 from agent_unreachable_intervals aui
                    where aui.agent_id = nh.agent_id and aui.ended_at is null) as agent_down
      from nvr_health nh
     where nh.site_id = p_site_id
  ),
  observable as (
    select exists (select 1 from rec
                    where not agent_down and nvr_reachable is true and nvr_auth_ok is true) as ok
  ),
  desired as (
    select 'agent:' || agent_id::text || ':unreachable' as dedupe_key, 'agent' as fault_domain,
           'agent_unreachable' as fault_type, 'critical' as severity,
           'agent_unreachable' as reason_code, null::uuid as camera_id, agent_id
      from rec where agent_down
    union all
    select 'nvr:' || agent_id::text || ':unreachable', 'nvr_connectivity', 'nvr_unreachable', 'critical',
           'nvr_unreachable', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is false
    union all
    select 'nvr:' || agent_id::text || ':auth', 'nvr_auth', 'nvr_auth_failed', 'critical',
           'nvr_auth_failed', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is true and nvr_auth_ok is false
    union all
    -- Storage is a CURRENT claim only while its own observation is fresh.
    select 'nvr:' || agent_id::text || ':storage', 'storage',
           case when storage_state = 'fault' then 'storage_fault' else 'storage_degraded' end,
           case when storage_state = 'fault' then 'critical' else 'warning' end,
           case when storage_state = 'fault' then 'storage_fault' else 'disk_full' end,
           null::uuid, agent_id
      from rec
     where not agent_down and nvr_reachable is true and nvr_auth_ok is not false
       and sto_observed_at >= v_fresh_cut
       and storage_state in ('fault', 'degraded')
    union all
    select 'camera:' || ch.camera_id::text || ':offline', 'camera', 'camera_offline', 'critical',
           'video_loss', ch.camera_id, null::uuid
      from camera_health ch
      left join camera_inventory ci on ci.camera_id = ch.camera_id
      join cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id and c.is_configured and ch.health_state = 'offline'
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
    union all
    -- Recording is a CURRENT claim only while its own observation is fresh.
    select 'camera:' || ch.camera_id::text || ':recording', 'recording',
           case when ch.recording_state = 'storage_fault' then 'recording_storage_fault' else 'not_recording' end,
           'warning',
           case when ch.recording_state = 'storage_fault' then 'storage_fault' else 'not_recording' end,
           ch.camera_id, null::uuid
      from camera_health ch
      left join camera_inventory ci on ci.camera_id = ch.camera_id
      join cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id and c.is_configured
       and ch.rec_observed_at >= v_fresh_cut
       and ch.recording_state in ('not_recording', 'storage_fault')
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'dedupe_key', dedupe_key, 'fault_domain', fault_domain, 'fault_type', fault_type,
           'severity', severity, 'reason_code', reason_code, 'camera_id', camera_id, 'agent_id', agent_id)),
         '[]'::jsonb)
    into v_desired from desired;

  with d as (
    select * from jsonb_to_recordset(v_desired) as x(dedupe_key text, fault_domain text,
             fault_type text, severity text, reason_code text, camera_id uuid, agent_id uuid)
  ),
  ins as (
    insert into operational_faults (tenant_id, site_id, camera_id, agent_id, fault_domain, fault_type,
                                    severity, state, reason_code, dedupe_key, opened_at)
    select v_tenant, p_site_id, d.camera_id, d.agent_id, d.fault_domain, d.fault_type, d.severity,
           'open', d.reason_code, d.dedupe_key, v_now
      from d
    on conflict (dedupe_key) where state <> 'resolved' do nothing
    returning 1
  )
  select count(*) into v_opened from ins;

  with res as (
    update operational_faults f
       set state = 'resolved', resolved_at = v_now
     where f.site_id = p_site_id and f.state <> 'resolved'
       and not exists (select 1 from jsonb_to_recordset(v_desired) as x(dedupe_key text)
                        where x.dedupe_key = f.dedupe_key)
    returning 1
  )
  select count(*) into v_resolved from res;

  select count(*) into v_open_total
    from operational_faults where site_id = p_site_id and state <> 'resolved';

  return jsonb_build_object('ok', true, 'site_id', p_site_id, 'evaluated_at', v_now,
    'opened', v_opened, 'resolved', v_resolved, 'open_total', v_open_total);
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
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists(select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  return jsonb_build_object(
    'site_id', p_site_id,
    'recorders', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'agent_id', nh.agent_id,
        'nvr_reachable', nh.nvr_reachable,
        'nvr_auth_ok', nh.nvr_auth_ok,
        'recording_state', nh.recording_state,
        'storage_state', case when nh.sto_observed_at >= v_fresh_cut then nh.storage_state else 'unknown' end,
        'reason_code', nh.reason_code,
        'storage_reason_code', case when nh.sto_observed_at >= v_fresh_cut then nh.storage_reason_code else 'unknown' end,
        'storage_observed_at', nh.sto_observed_at,
        'storage_fresh', coalesce(nh.sto_observed_at >= v_fresh_cut, false),
        'updated_at', nh.updated_at
      )), '[]'::jsonb)
      from nvr_health nh
      where nh.site_id = p_site_id and nh.tenant_id = v_tenant
    ),
    'cameras', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'camera_id', c.id,
        'channel', c.channel,
        'name', c.name,
        'is_configured', c.is_configured,
        'configuration_state', case when c.is_configured then 'configured' else 'no_camera_configured' end,
        'health_state', case when c.is_configured then coalesce(ch.health_state, 'unknown') else 'unknown' end,
        'inventory_state', case when c.is_configured then coalesce(ci.inventory_state, 'unknown') else 'disabled' end,
        'recording_state', case
          when not c.is_configured then 'unknown'
          when ch.rec_observed_at >= v_fresh_cut then coalesce(ch.recording_state, 'unknown')
          else 'unknown' end,
        'reason_code', case when c.is_configured then ch.reason_code else 'channel_disabled' end,
        'recording_reason_code', case
          when not c.is_configured then 'channel_disabled'
          when ch.rec_observed_at >= v_fresh_cut then ch.recording_reason_code
          else 'unknown' end,
        'recording_observed_at', ch.rec_observed_at,
        'recording_fresh', case when c.is_configured then coalesce(ch.rec_observed_at >= v_fresh_cut, false) else false end,
        'updated_at', greatest(ch.updated_at, ci.updated_at)
      ) order by c.channel), '[]'::jsonb)
      from cameras c
      left join camera_health ch on ch.camera_id = c.id
      left join camera_inventory ci on ci.camera_id = c.id
      where c.site_id = p_site_id and c.tenant_id = v_tenant
    ),
    'faults', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id', f.id, 'domain', f.fault_domain, 'fault_type', f.fault_type,
        'severity', f.severity, 'state', f.state, 'reason_code', f.reason_code,
        'camera_id', f.camera_id, 'agent_id', f.agent_id,
        'opened_at', f.opened_at, 'acknowledged_at', f.acknowledged_at)
        order by case f.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                 f.opened_at desc), '[]'::jsonb)
      from operational_faults f
      where f.site_id = p_site_id and f.tenant_id = v_tenant and f.state <> 'resolved'
        and (f.camera_id is null or exists(select 1 from cameras fc where fc.id=f.camera_id and fc.is_configured))
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
      from cameras c left join camera_health ch on ch.camera_id=c.id
      where c.site_id=p_site_id and c.tenant_id=v_tenant
    ),
    'faults_open', (
      select count(*) from operational_faults f
      where f.site_id=p_site_id and f.tenant_id=v_tenant and f.state<>'resolved'
        and (f.camera_id is null or exists(select 1 from cameras fc where fc.id=f.camera_id and fc.is_configured))
    ),
    'server_time', now()
  );
end $$;

-- Reconcile immediately so stale recording/storage faults do not wait for cron.
select public.wl_sweep_faults();

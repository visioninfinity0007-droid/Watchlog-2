-- =====================================================================
-- 0089 — Fresh recording/storage proof, separate from transition watermarks
--
-- The transition ledger is immutable/change-only and its rec_observed_*/
-- sto_observed_* columns are ordering watermarks. They must NOT be repurposed as
-- heartbeat/freshness clocks. Present-tense evidence therefore gets independent
-- current-state columns, refreshed by the current authoritative Agent.
--
-- For Dahua, the packaged Agent only emits state=recording from a successful
-- recent archive search. A missing/ambiguous file search is UNKNOWN, never a
-- fabricated not_recording alarm.
-- =====================================================================

alter table public.camera_health
  add column if not exists rec_current_state text not null default 'unknown',
  add column if not exists rec_current_reason_code text,
  add column if not exists rec_current_at timestamptz,
  add column if not exists rec_current_evidence text;

alter table public.nvr_health
  add column if not exists sto_current_state text not null default 'unknown',
  add column if not exists sto_current_reason_code text,
  add column if not exists sto_current_at timestamptz,
  add column if not exists sto_current_evidence text;

create or replace function public.wl_report_recording_storage_current(
  p_agent_id uuid,
  p_agent_key text,
  p_report jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_now timestamptz := now();
  v_current uuid;
  v_storage_state text;
  v_storage_reason text;
  v_recording_evidence text := lower(coalesce(p_report->>'recording_evidence','unknown'));
  v_count int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  v_current := wl_current_site_agent(v_agent.site_id);
  if v_current is distinct from v_agent.id then
    return jsonb_build_object('ok', false, 'reason', 'not_current_agent',
                              'current_agent_id', v_current);
  end if;

  v_storage_state := lower(coalesce(p_report#>>'{storage,state}','unknown'));
  if v_storage_state not in ('ok','degraded','fault','unknown') then
    v_storage_state := 'unknown';
  end if;
  v_storage_reason := lower(coalesce(p_report#>>'{storage,reason}','unknown'));
  if v_storage_reason not in ('ok','unknown','storage_fault','disk_error','disk_full',
                              'nvr_unreachable','nvr_auth_failed','agent_unreachable') then
    v_storage_reason := 'unknown';
  end if;

  insert into public.nvr_health as nh
    (agent_id, tenant_id, site_id,
     sto_current_state, sto_current_reason_code, sto_current_at,
     sto_current_evidence, updated_at)
  values
    (v_agent.id, v_agent.tenant_id, v_agent.site_id,
     v_storage_state, v_storage_reason, v_now, 'vendor_status', v_now)
  on conflict (agent_id) do update
     set sto_current_state = excluded.sto_current_state,
         sto_current_reason_code = excluded.sto_current_reason_code,
         sto_current_at = excluded.sto_current_at,
         sto_current_evidence = excluded.sto_current_evidence,
         updated_at = v_now;

  with raw as (
    select r->>'channel' as channel,
           lower(coalesce(r->>'state','unknown')) as raw_state,
           lower(coalesce(r->>'reason','unknown')) as raw_reason
      from jsonb_array_elements(coalesce(p_report#>'{recording,channels}','[]'::jsonb)) r
     where coalesce(r->>'channel','') <> ''
  ), normalized as (
    select c.id as camera_id,
           case
             when not c.is_configured then 'unknown'
             when raw.raw_state = 'recording' and v_recording_evidence <> 'archive_search' then 'unknown'
             when raw.raw_state in ('recording','not_recording','storage_fault','unknown') then raw.raw_state
             else 'unknown'
           end as current_state,
           case
             when not c.is_configured then 'channel_disabled'
             when raw.raw_state = 'recording' and v_recording_evidence <> 'archive_search' then 'unknown'
             when raw.raw_reason in ('ok','unknown','not_recording','storage_fault','channel_missing',
                                     'channel_disabled','nvr_unreachable','nvr_auth_failed',
                                     'agent_unreachable','disk_error','disk_full') then raw.raw_reason
             else 'unknown'
           end as current_reason,
           c.is_configured
      from raw
      join public.cameras c
        on c.site_id = v_agent.site_id
       and c.channel = raw.channel
  ), up as (
    insert into public.camera_health as ch
      (camera_id, tenant_id, site_id,
       rec_current_state, rec_current_reason_code, rec_current_at,
       rec_current_evidence, updated_at)
    select n.camera_id, v_agent.tenant_id, v_agent.site_id,
           n.current_state, n.current_reason, v_now,
           case when n.is_configured then v_recording_evidence else 'inventory' end,
           v_now
      from normalized n
    on conflict (camera_id) do update
       set rec_current_state = excluded.rec_current_state,
           rec_current_reason_code = excluded.rec_current_reason_code,
           rec_current_at = excluded.rec_current_at,
           rec_current_evidence = excluded.rec_current_evidence,
           updated_at = v_now
    returning 1
  )
  select count(*) into v_count from up;

  update public.agents set last_seen_at = v_now where id = v_agent.id;
  perform public.wl_reconcile_site_faults(v_agent.site_id);

  return jsonb_build_object('ok', true,
                            'agent_id', v_agent.id,
                            'site_id', v_agent.site_id,
                            'recording_evidence', v_recording_evidence,
                            'cameras_refreshed', v_count,
                            'server_time', v_now);
end $$;

create or replace function public.wl_reconcile_site_faults(p_site_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v_now timestamptz := now();
  v_fresh_cut timestamptz := v_now - interval '15 minutes';
  v_current_agent uuid := wl_current_site_agent(p_site_id);
  v_desired jsonb;
  v_opened int := 0;
  v_resolved int := 0;
  v_open_total int := 0;
begin
  select tenant_id into v_tenant from public.sites where id = p_site_id;
  if v_tenant is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_site');
  end if;

  perform pg_advisory_xact_lock(hashtext('wl_reconcile_site_faults'), hashtext(p_site_id::text));

  with rec as (
    select nh.agent_id, nh.nvr_reachable, nh.nvr_auth_ok,
           nh.sto_current_state as storage_state,
           nh.sto_current_at as storage_observed_at,
           exists (select 1 from public.agent_unreachable_intervals aui
                    where aui.agent_id = nh.agent_id and aui.ended_at is null) as agent_down
      from public.nvr_health nh
     where nh.site_id = p_site_id
       and nh.agent_id = v_current_agent
  ), observable as (
    select exists (select 1 from rec
                    where not agent_down
                      and nvr_reachable is true
                      and nvr_auth_ok is true) as ok
  ), desired as (
    select 'agent:' || agent_id::text || ':unreachable' as dedupe_key,
           'agent' as fault_domain, 'agent_unreachable' as fault_type,
           'critical' as severity, 'agent_unreachable' as reason_code,
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
       and storage_observed_at >= v_fresh_cut
       and storage_state in ('fault','degraded')
    union all
    select 'camera:' || ch.camera_id::text || ':offline',
           'camera', 'camera_offline', 'critical', 'video_loss',
           ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.health_state = 'offline'
       and coalesce(ci.inventory_state,'present') not in ('missing','disabled')
       and (select ok from observable)
    union all
    select 'camera:' || ch.camera_id::text || ':recording',
           'recording',
           case when ch.rec_current_state = 'storage_fault'
                then 'recording_storage_fault' else 'not_recording' end,
           'warning',
           case when ch.rec_current_state = 'storage_fault'
                then 'storage_fault' else 'not_recording' end,
           ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.rec_current_at >= v_fresh_cut
       and ch.rec_current_state in ('not_recording','storage_fault')
       and coalesce(ci.inventory_state,'present') not in ('missing','disabled')
       and (select ok from observable)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'dedupe_key',dedupe_key,'fault_domain',fault_domain,
           'fault_type',fault_type,'severity',severity,'reason_code',reason_code,
           'camera_id',camera_id,'agent_id',agent_id)),'[]'::jsonb)
    into v_desired from desired;

  with d as (
    select * from jsonb_to_recordset(v_desired) as x(
      dedupe_key text, fault_domain text, fault_type text,
      severity text, reason_code text, camera_id uuid, agent_id uuid)
  ), ins as (
    insert into public.operational_faults
      (tenant_id,site_id,camera_id,agent_id,fault_domain,fault_type,
       severity,state,reason_code,dedupe_key,opened_at)
    select v_tenant,p_site_id,d.camera_id,d.agent_id,d.fault_domain,d.fault_type,
           d.severity,'open',d.reason_code,d.dedupe_key,v_now from d
    on conflict (dedupe_key) where state <> 'resolved' do nothing
    returning 1
  ) select count(*) into v_opened from ins;

  with res as (
    update public.operational_faults f
       set state='resolved', resolved_at=v_now
     where f.site_id=p_site_id and f.state<>'resolved'
       and not exists(select 1 from jsonb_to_recordset(v_desired) as x(dedupe_key text)
                      where x.dedupe_key=f.dedupe_key)
    returning 1
  ) select count(*) into v_resolved from res;

  select count(*) into v_open_total
    from public.operational_faults where site_id=p_site_id and state<>'resolved';

  return jsonb_build_object('ok',true,'site_id',p_site_id,
                            'current_agent_id',v_current_agent,
                            'evaluated_at',v_now,'opened',v_opened,
                            'resolved',v_resolved,'open_total',v_open_total);
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
      select coalesce(jsonb_agg(jsonb_build_object(
        'agent_id',nh.agent_id,
        'nvr_reachable',nh.nvr_reachable,
        'nvr_auth_ok',nh.nvr_auth_ok,
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
           where c.site_id=p_site_id and c.tenant_id=v_tenant),
        'storage_state',case when nh.sto_current_at>=v_fresh_cut
                             then nh.sto_current_state else 'unknown' end,
        'reason_code',nh.reason_code,
        'storage_reason_code',case when nh.sto_current_at>=v_fresh_cut
                                   then nh.sto_current_reason_code else 'unknown' end,
        'storage_observed_at',nh.sto_current_at,
        'storage_fresh',coalesce(nh.sto_current_at>=v_fresh_cut,false),
        'storage_evidence',nh.sto_current_evidence,
        'updated_at',nh.updated_at)), '[]'::jsonb)
        from public.nvr_health nh
       where nh.site_id=p_site_id and nh.tenant_id=v_tenant
         and nh.agent_id=v_current_agent
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

select public.wl_sweep_faults();

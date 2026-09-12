-- =====================================================================
-- 0085 - Camera configuration truth: recorder slot != installed camera
--
-- XVRs/DVRs enumerate logical recorder slots even when no physical camera
-- is connected. WatchLog must never turn an empty slot + VideoLoss into a
-- customer-facing "camera offline" fault.
--
-- Cloud truth is explicit: cameras.is_configured says whether a physical
-- camera is expected on that recorder channel. Recorder enumeration may
-- create a slot row, but it can never auto-promote that slot to configured.
-- =====================================================================

alter table public.cameras add column if not exists is_configured boolean;

-- Backwards compatibility: all cameras that existed before this migration
-- were historically treated as configured. Site-specific corrections are
-- data operations, not hard-coded into this reusable schema migration.
update public.cameras set is_configured = true where is_configured is null;
alter table public.cameras alter column is_configured set default false;
alter table public.cameras alter column is_configured set not null;

comment on column public.cameras.is_configured is
  'Cloud-authoritative physical camera expectation. False means recorder slot exists but no camera is configured/expected; it must not produce camera health faults.';

-- Keep current health/fault materialization honest whenever an operator changes
-- whether a physical camera is expected on a slot.
create or replace function public.wl_camera_configuration_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.is_configured is false then
    insert into camera_inventory as ci
      (camera_id, tenant_id, site_id, inventory_state, reason_code, updated_at)
    values
      (new.id, new.tenant_id, new.site_id, 'disabled', 'channel_disabled', now())
    on conflict (camera_id) do update
       set inventory_state = 'disabled',
           reason_code = 'channel_disabled',
           updated_at = now();

    insert into camera_health as ch
      (camera_id, tenant_id, site_id, health_state, recording_state,
       reason_code, recording_reason_code, updated_at)
    values
      (new.id, new.tenant_id, new.site_id, 'unknown', 'unknown',
       'channel_disabled', 'channel_disabled', now())
    on conflict (camera_id) do update
       set health_state = 'unknown',
           recording_state = 'unknown',
           reason_code = 'channel_disabled',
           recording_reason_code = 'channel_disabled',
           consecutive_fail = 0,
           consecutive_ok = 0,
           observed_at = null,
           observed_epoch = null,
           observed_seq = null,
           observed_ingest = null,
           rec_observed_at = null,
           rec_observed_epoch = null,
           rec_observed_seq = null,
           rec_observed_ingest = null,
           updated_at = now();

    -- Any previously derived fault for an empty recorder slot is false by
    -- definition. Resolve it immediately; normal reconciliation remains the
    -- authority for configured cameras.
    update operational_faults
       set state = 'resolved', resolved_at = coalesce(resolved_at, now())
     where camera_id = new.id and state <> 'resolved';
  elsif tg_op = 'UPDATE' and old.is_configured is false and new.is_configured is true then
    -- A newly configured camera starts UNKNOWN. Fresh agent evidence must prove
    -- presence/health/recording; never resurrect stale state from when the slot
    -- was intentionally unused.
    insert into camera_inventory as ci
      (camera_id, tenant_id, site_id, inventory_state, reason_code, updated_at)
    values
      (new.id, new.tenant_id, new.site_id, 'unknown', 'unknown', now())
    on conflict (camera_id) do update
       set inventory_state = 'unknown', reason_code = 'unknown', updated_at = now();

    insert into camera_health as ch
      (camera_id, tenant_id, site_id, health_state, recording_state,
       reason_code, recording_reason_code, updated_at)
    values
      (new.id, new.tenant_id, new.site_id, 'unknown', 'unknown',
       'unknown', 'unknown', now())
    on conflict (camera_id) do update
       set health_state = 'unknown', recording_state = 'unknown',
           reason_code = 'unknown', recording_reason_code = 'unknown',
           consecutive_fail = 0, consecutive_ok = 0,
           observed_at = null, observed_epoch = null, observed_seq = null, observed_ingest = null,
           rec_observed_at = null, rec_observed_epoch = null, rec_observed_seq = null, rec_observed_ingest = null,
           updated_at = now();
  end if;
  return new;
end $$;

revoke all on function public.wl_camera_configuration_guard() from public, anon, authenticated;

drop trigger if exists cameras_configuration_truth on public.cameras;
create trigger cameras_configuration_truth
after insert or update of is_configured on public.cameras
for each row execute function public.wl_camera_configuration_guard();

-- Operator/admin mutation surface. Recorder enumeration is discovery only;
-- this RPC records the human/system-of-record decision that a camera is (or
-- is not) physically expected on the channel.
create or replace function public.wl_set_camera_configured(
  p_site_id uuid,
  p_channel text,
  p_configured boolean,
  p_name text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_row cameras;
begin
  update cameras c
     set is_configured = coalesce(p_configured, false),
         name = case when nullif(trim(coalesce(p_name,'')), '') is not null
                     then trim(p_name) else c.name end,
         analytics_enabled = case when coalesce(p_configured,false) then c.analytics_enabled else false end
   where c.tenant_id = v_tenant
     and c.site_id = p_site_id
     and c.channel = p_channel
  returning * into v_row;

  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_channel');
  end if;

  return jsonb_build_object(
    'ok', true, 'camera_id', v_row.id, 'site_id', v_row.site_id,
    'channel', v_row.channel, 'name', v_row.name,
    'is_configured', v_row.is_configured);
end $$;

revoke all on function public.wl_set_camera_configured(uuid,text,boolean,text) from public, anon;
grant execute on function public.wl_set_camera_configured(uuid,text,boolean,text) to authenticated;

-- ---------------------------------------------------------------------
-- Recorder discovery/sync: enumerate slots, but never infer installation.
-- Existing configuration truth and human-friendly names are preserved.
-- ---------------------------------------------------------------------
create or replace function public.wl_sync_cameras(
  p_agent_id uuid,
  p_agent_key text,
  p_cameras jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_out   jsonb := '{}'::jsonb;
  v_row   record;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  insert into cameras (tenant_id, site_id, channel, name, is_configured)
  select v_agent.tenant_id, v_agent.site_id,
         c->>'channel',
         coalesce(nullif(c->>'name',''), 'Channel ' || (c->>'channel')),
         false
    from jsonb_array_elements(coalesce(p_cameras, '[]'::jsonb)) c
   where coalesce(c->>'channel','') <> ''
  on conflict (site_id, channel) do update
     set name = case when cameras.is_configured then cameras.name else excluded.name end;

  for v_row in
    select channel, id from cameras where site_id = v_agent.site_id
  loop
    v_out := v_out || jsonb_build_object(v_row.channel, v_row.id);
  end loop;

  return v_out;
end $$;

revoke all on function public.wl_sync_cameras(uuid,text,jsonb) from public;
grant execute on function public.wl_sync_cameras(uuid,text,jsonb) to anon, authenticated;

-- ---------------------------------------------------------------------
-- Inventory: an unconfigured slot is intentionally non-operational scope.
-- Internally it uses the existing DISABLED vocabulary so every older read
-- model/fault suppressor remains safe; customer copy uses is_configured=false
-- and says "No camera configured".
-- ---------------------------------------------------------------------
create or replace function public.wl_report_health(
  p_agent_id uuid,
  p_agent_key text,
  p_report jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent      agents;
  v_now        timestamptz := now();
  v_nvr        jsonb := coalesce(p_report->'nvr', '{}'::jsonb);
  v_reachable  boolean := (v_nvr->>'reachable')::boolean;
  v_auth_ok    boolean;
  v_reason     text := coalesce(v_nvr->>'reason', 'unknown');
  v_enumerated boolean := coalesce((p_report#>>'{channels,enumerated}')::boolean, false);
  v_prev       nvr_health;
  v_present int := 0; v_missing int := 0; v_disabled int := 0; v_unknown int := 0;
  v_unconfigured int := 0;
  v_unmapped   jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  if v_nvr ? 'auth_ok' and jsonb_typeof(v_nvr->'auth_ok') = 'boolean' then
    v_auth_ok := (v_nvr->>'auth_ok')::boolean;
  else
    v_auth_ok := null;
  end if;

  if v_reason not in ('ok','unknown','nvr_unreachable','nvr_auth_failed','agent_unreachable',
                      'probe_timeout','stale_frame','video_loss','channel_missing',
                      'channel_disabled','storage_fault','not_recording','tamper',
                      'disk_error','disk_full') then
    v_reason := 'unknown';
  end if;

  select * into v_prev from nvr_health where agent_id = v_agent.id;

  insert into nvr_health as nh (agent_id, tenant_id, site_id, nvr_reachable, nvr_auth_ok,
                                reason_code, last_ok_at, last_change_at, updated_at)
  values (v_agent.id, v_agent.tenant_id, v_agent.site_id, v_reachable, v_auth_ok, v_reason,
          case when v_reachable and coalesce(v_auth_ok, true) then v_now else null end,
          v_now, v_now)
  on conflict (agent_id) do update
     set nvr_reachable  = excluded.nvr_reachable,
         nvr_auth_ok    = excluded.nvr_auth_ok,
         reason_code    = excluded.reason_code,
         last_ok_at     = case when excluded.nvr_reachable and coalesce(excluded.nvr_auth_ok, true)
                               then v_now else nh.last_ok_at end,
         last_change_at = case when nh.nvr_reachable is distinct from excluded.nvr_reachable
                                 or nh.nvr_auth_ok is distinct from excluded.nvr_auth_ok
                               then v_now else nh.last_change_at end,
         updated_at     = v_now;

  if v_prev.agent_id is null
     or v_prev.nvr_reachable is distinct from v_reachable
     or v_prev.nvr_auth_ok   is distinct from v_auth_ok then
    insert into nvr_health_transitions (tenant_id, site_id, agent_id, layer,
                                        from_state, to_state, reason_code)
    values (v_agent.tenant_id, v_agent.site_id, v_agent.id,
            case when v_auth_ok is false then 'auth' else 'connectivity' end,
            coalesce(v_prev.reason_code, 'unknown'), v_reason, v_reason);
  end if;

  with rep as (
    select (c->>'channel') as channel,
           coalesce((c->>'enabled')::boolean, true) as enabled
      from jsonb_array_elements(coalesce(p_report#>'{channels,reported}', '[]'::jsonb)) c
     where coalesce(c->>'channel','') <> ''
  ),
  det as ( select (v_reachable and coalesce(v_auth_ok, true) and v_enumerated) as ok ),
  derived as (
    select cm.id as camera_id, cm.channel, cm.is_configured,
           case
             when not cm.is_configured then 'disabled'
             when not (select ok from det) then 'unknown'
             when exists (select 1 from rep r where r.channel = cm.channel and r.enabled = false) then 'disabled'
             when exists (select 1 from rep r where r.channel = cm.channel) then 'present'
             else 'missing'
           end as state
      from cameras cm
     where cm.site_id = v_agent.site_id
  ),
  prev_inv as (
    select camera_id, inventory_state from camera_inventory where site_id = v_agent.site_id
  ),
  tx as (
    insert into camera_inventory_transitions (tenant_id, site_id, camera_id,
                                              from_state, to_state, reason_code)
    select v_agent.tenant_id, v_agent.site_id, d.camera_id,
           coalesce(p.inventory_state, 'unknown'), d.state,
           case d.state when 'missing'  then 'channel_missing'
                        when 'disabled' then 'channel_disabled'
                        when 'unknown'  then v_reason else 'ok' end
      from derived d
      left join prev_inv p on p.camera_id = d.camera_id
     where p.inventory_state is distinct from d.state
    returning 1
  ),
  up as (
    insert into camera_inventory as ci (camera_id, tenant_id, site_id, inventory_state,
                                        reason_code, first_seen_at, last_present_at, updated_at)
    select d.camera_id, v_agent.tenant_id, v_agent.site_id, d.state,
           case d.state when 'missing'  then 'channel_missing'
                        when 'disabled' then 'channel_disabled'
                        when 'unknown'  then v_reason else 'ok' end,
           case when d.state = 'present' then v_now else null end,
           case when d.state = 'present' then v_now else null end,
           v_now
      from derived d
    on conflict (camera_id) do update
       set inventory_state = excluded.inventory_state,
           reason_code     = excluded.reason_code,
           first_seen_at   = coalesce(ci.first_seen_at, excluded.first_seen_at),
           last_present_at = case when excluded.inventory_state = 'present' then v_now
                                  else ci.last_present_at end,
           updated_at      = v_now
    returning 1
  )
  select count(*) filter (where state = 'present'),
         count(*) filter (where state = 'missing'),
         count(*) filter (where state = 'disabled'),
         count(*) filter (where state = 'unknown'),
         count(*) filter (where not is_configured)
    into v_present, v_missing, v_disabled, v_unknown, v_unconfigured
    from derived;

  select coalesce(jsonb_agg(r.channel), '[]'::jsonb) into v_unmapped
    from (
      select (c->>'channel') as channel
        from jsonb_array_elements(coalesce(p_report#>'{channels,reported}', '[]'::jsonb)) c
       where coalesce(c->>'channel','') <> ''
         and not exists (select 1 from cameras cm
                          where cm.site_id = v_agent.site_id and cm.channel = c->>'channel')
    ) r;

  update agents
     set last_seen_at  = v_now,
         device_vendor = coalesce(v_nvr->>'vendor', device_vendor),
         device_model  = coalesce(v_nvr->>'model',  device_model)
   where id = v_agent.id;

  return jsonb_build_object(
    'ok', true,
    'nvr_state', coalesce(v_nvr->>'state', 'unknown'),
    'present', v_present, 'missing', v_missing,
    'disabled', v_disabled, 'unknown', v_unknown,
    'not_configured', v_unconfigured,
    'unmapped', v_unmapped, 'server_time', v_now);
end $$;

revoke all on function public.wl_report_health(uuid,text,jsonb) from public;
grant execute on function public.wl_report_health(uuid,text,jsonb) to anon, authenticated;

-- Camera liveness from the agent is accepted ONLY for physically expected
-- cameras. 0.4.3 may still probe every XVR slot; the cloud rejects empty-slot
-- health claims rather than allowing VideoLoss to become a false camera fault.
create or replace function public.wl_report_camera_health(
  p_agent_id uuid,
  p_agent_key text,
  p_report jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_now   timestamptz := now();
  v_op int := 0; v_deg int := 0; v_off int := 0; v_unk int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  with rep as (
    select (c->>'channel') as channel,
           lower(coalesce(c->>'health', 'unknown')) as health,
           lower(coalesce(c->>'reason', 'unknown')) as reason
      from jsonb_array_elements(coalesce(p_report->'cameras', '[]'::jsonb)) c
     where coalesce(c->>'channel', '') <> ''
  ),
  mapped as (
    select cm.id as camera_id,
           case when r.health in ('operational','degraded','offline','unknown')
                then r.health else 'unknown' end as health,
           case when r.reason in ('ok','unknown','probe_timeout','stale_frame','video_loss',
                                  'channel_missing','channel_disabled','nvr_unreachable',
                                  'nvr_auth_failed','agent_unreachable','storage_fault',
                                  'not_recording','tamper','disk_error','disk_full')
                then r.reason else 'unknown' end as reason
      from rep r
      join cameras cm on cm.site_id = v_agent.site_id
                     and cm.channel = r.channel
                     and cm.is_configured
  ),
  up as (
    insert into camera_health as chh (camera_id, tenant_id, site_id, health_state, reason_code,
                                      last_probe_at, last_ok_at, last_change_at, observed_at,
                                      last_offline_at, last_recovery_at, updated_at)
    select m.camera_id, v_agent.tenant_id, v_agent.site_id, m.health, m.reason,
           v_now,
           case when m.health = 'operational' then v_now else null end,
           v_now, v_now,
           case when m.health = 'offline' then v_now else null end,
           case when m.health = 'operational' then v_now else null end,
           v_now
      from mapped m
    on conflict (camera_id) do update
       set health_state    = excluded.health_state,
           reason_code      = excluded.reason_code,
           last_probe_at    = v_now,
           last_ok_at       = case when excluded.health_state = 'operational' then v_now
                                   else chh.last_ok_at end,
           last_change_at   = case when chh.health_state is distinct from excluded.health_state
                                   then v_now else chh.last_change_at end,
           observed_at      = v_now,
           last_offline_at  = case when excluded.health_state = 'offline'
                                     and chh.health_state is distinct from 'offline'
                                   then v_now else chh.last_offline_at end,
           last_recovery_at = case when excluded.health_state = 'operational'
                                     and chh.health_state = 'offline'
                                   then v_now else chh.last_recovery_at end,
           updated_at       = v_now
       where v_now >= coalesce(chh.observed_at, '-infinity'::timestamptz)
    returning 1
  )
  select count(*) filter (where m.health = 'operational'),
         count(*) filter (where m.health = 'degraded'),
         count(*) filter (where m.health = 'offline'),
         count(*) filter (where m.health = 'unknown')
    into v_op, v_deg, v_off, v_unk
    from mapped m;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true, 'operational', v_op, 'degraded', v_deg, 'offline', v_off,
    'unknown', v_unk, 'server_time', v_now);
end $$;

revoke all on function public.wl_report_camera_health(uuid,text,jsonb) from public;
grant execute on function public.wl_report_camera_health(uuid,text,jsonb) to anon, authenticated;

-- Legacy site-health detail read: empty recorder slots are not cameras and
-- must not inflate camera totals / quiet-camera lists / fault lists.
create or replace function public.wl_site_health_details(p_days integer default 7)
returns jsonb
language plpgsql
stable security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days int := least(greatest(coalesce(p_days, 7), 1), 90);
begin
  if v_tenant is null then
    return jsonb_build_object('cameras','[]'::jsonb,'faults','[]'::jsonb);
  end if;

  return jsonb_build_object(
    'cameras', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', c.id,
        'site_id', s.id,
        'site', s.name,
        'channel', c.channel,
        'camera', coalesce(nullif(c.name,''), 'Camera ' || c.channel),
        'last_activity_at', q.last_activity_at,
        'hours_since_activity', case
          when q.last_activity_at is null then null
          else round(extract(epoch from (now() - q.last_activity_at)) / 3600.0, 1)
        end,
        'activity_state', case
          when q.last_activity_at is null then 'never'
          when q.last_activity_at < now() - interval '24 hours' then 'silent'
          else 'active'
        end
      ) order by s.name, c.channel)
      from cameras c
      join sites s on s.id = c.site_id
      left join lateral (
        select max(e.device_ts) as last_activity_at
          from events e
         where e.camera_id = c.id
           and e.tenant_id = v_tenant
      ) q on true
      where c.tenant_id = v_tenant and c.is_configured
    ), '[]'::jsonb),

    'faults', coalesce((
      select jsonb_agg(jsonb_build_object(
        'event_id', e.id,
        'site_id', s.id,
        'site', s.name,
        'camera_id', c.id,
        'camera', coalesce(nullif(c.name,''), 'Unassigned camera'),
        'event_type', e.event_type,
        'device_ts', e.device_ts
      ) order by e.device_ts desc)
      from events e
      join sites s on s.id = e.site_id
      left join cameras c on c.id = e.camera_id
      where e.tenant_id = v_tenant
        and e.device_ts >= now() - make_interval(days => v_days)
        and e.event_type in ('video_loss','tamper','disk_error','disk_full','offline')
        and (c.id is null or c.is_configured)
    ), '[]'::jsonb)
  );
end $$;

revoke all on function public.wl_site_health_details(integer) from public, anon;
grant execute on function public.wl_site_health_details(integer) to authenticated;

-- Truthful operational snapshot. Keep recorder slots visible, explicitly mark
-- unused ones, but count health only across configured physical cameras.
create or replace function public.wl_site_health_snapshot(p_site_id uuid)
returns jsonb
language plpgsql
stable security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists (select 1 from sites s where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'that site does not belong to your account';
  end if;

  return jsonb_build_object(
    'site_id', p_site_id,
    'recorders', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'agent_id', nh.agent_id, 'nvr_reachable', nh.nvr_reachable, 'nvr_auth_ok', nh.nvr_auth_ok,
               'recording_state', nh.recording_state, 'storage_state', nh.storage_state,
               'reason_code', nh.reason_code, 'storage_reason_code', nh.storage_reason_code,
               'updated_at', nh.updated_at)), '[]'::jsonb)
        from nvr_health nh where nh.site_id = p_site_id and nh.tenant_id = v_tenant),
    'cameras', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'camera_id', c.id, 'channel', c.channel, 'name', c.name,
               'is_configured', c.is_configured,
               'configuration_state', case when c.is_configured then 'configured' else 'no_camera_configured' end,
               'health_state', case when c.is_configured then coalesce(ch.health_state, 'unknown') else 'unknown' end,
               'inventory_state', case when c.is_configured then coalesce(ci.inventory_state, 'unknown') else 'disabled' end,
               'recording_state', case when c.is_configured then coalesce(ch.recording_state, 'unknown') else 'unknown' end,
               'reason_code', case when c.is_configured then ch.reason_code else 'channel_disabled' end,
               'recording_reason_code', case when c.is_configured then ch.recording_reason_code else 'channel_disabled' end,
               'updated_at', greatest(ch.updated_at, ci.updated_at)) order by c.channel), '[]'::jsonb)
        from cameras c
        left join camera_health ch on ch.camera_id = c.id
        left join camera_inventory ci on ci.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant),
    'faults', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'id', f.id, 'domain', f.fault_domain, 'fault_type', f.fault_type, 'severity', f.severity,
               'state', f.state, 'reason_code', f.reason_code, 'camera_id', f.camera_id,
               'agent_id', f.agent_id, 'opened_at', f.opened_at, 'acknowledged_at', f.acknowledged_at)
               order by case f.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                        f.opened_at desc), '[]'::jsonb)
        from operational_faults f
       where f.site_id = p_site_id and f.tenant_id = v_tenant and f.state <> 'resolved'
         and (f.camera_id is null or exists (
               select 1 from cameras fc where fc.id = f.camera_id and fc.is_configured))),
    'summary', (
      select jsonb_build_object(
               'cameras_total', count(*) filter (where c.is_configured),
               'recorder_slots_total', count(*),
               'unconfigured_slots', count(*) filter (where not c.is_configured),
               'operational', count(*) filter (where c.is_configured and coalesce(ch.health_state, 'unknown') = 'operational'),
               'degraded', count(*) filter (where c.is_configured and ch.health_state = 'degraded'),
               'offline', count(*) filter (where c.is_configured and ch.health_state = 'offline'),
               'unknown', count(*) filter (where c.is_configured and coalesce(ch.health_state, 'unknown') = 'unknown'))
        from cameras c left join camera_health ch on ch.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant),
    'faults_open', (
      select count(*) from operational_faults f
       where f.site_id = p_site_id and f.tenant_id = v_tenant and f.state <> 'resolved'
         and (f.camera_id is null or exists (
               select 1 from cameras fc where fc.id = f.camera_id and fc.is_configured))),
    'server_time', now());
end $$;

revoke all on function public.wl_site_health_snapshot(uuid) from public, anon;
grant execute on function public.wl_site_health_snapshot(uuid) to authenticated;

-- =====================================================================
-- 0045 - Phase A increment 4: camera health report ingest
--
-- wl_report_camera_health() persists the agent's per-channel camera health (the output of
-- prototype/agent/camera_health.py's hybrid native-fault + bounded-probe monitor) into
-- camera_health + camera_health_transitions.
--
-- Same contract as the other agent writes: (agent_id, agent_key) authenticate; tenant_id and
-- site_id come from the AGENT ROW, never the payload; each reported channel binds to THIS
-- site's camera by channel number. States/reasons are clamped to their domains so a buggy or
-- hostile agent cannot poison a column. Transitions are written only when a camera's health
-- state actually changes (idempotent under steady reporting).
--
-- Camera health is inherently AGENT-computed (the agent runs the probes + hysteresis machine),
-- so unlike inventory the cloud trusts the reported state and binds/persists it. Its authority
-- is the (tenant, site, camera) binding, the domain clamp, and the transition-on-change.
--
-- Out of scope here (later increments): recording_state / storage_state are left untouched;
-- no snapshot bytes are accepted or stored (the report carries only channel/health/reason);
-- no events/snapshots are written.
-- =====================================================================

create or replace function public.wl_report_camera_health(
  p_agent_id  uuid,
  p_agent_key text,
  p_report    jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent  agents;
  v_now    timestamptz := now();
  v_op int := 0; v_deg int := 0; v_off int := 0; v_unk int := 0; v_changed int := 0;
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
    -- bind to THIS site's cameras only; clamp state + reason to their domains
    select cm.id as camera_id, cm.channel,
           case when r.health in ('operational','degraded','offline','unknown')
                then r.health else 'unknown' end as health,
           case when r.reason in ('ok','unknown','probe_timeout','stale_frame','video_loss',
                                  'channel_missing','channel_disabled','nvr_unreachable',
                                  'nvr_auth_failed','agent_unreachable','storage_fault',
                                  'not_recording','tamper','disk_error','disk_full')
                then r.reason else 'unknown' end as reason
      from rep r
      join cameras cm on cm.site_id = v_agent.site_id and cm.channel = r.channel
  ),
  prev as (
    select camera_id, health_state from camera_health where site_id = v_agent.site_id
  ),
  tx as (
    insert into camera_health_transitions (tenant_id, site_id, camera_id,
                                           from_state, to_state, reason_code)
    select v_agent.tenant_id, v_agent.site_id, m.camera_id,
           coalesce(p.health_state, 'unknown'), m.health, m.reason
      from mapped m
      left join prev p on p.camera_id = m.camera_id
     where p.health_state is distinct from m.health
    returning 1
  ),
  up as (
    insert into camera_health as chh (camera_id, tenant_id, site_id, health_state, reason_code,
                                      last_probe_at, last_ok_at, last_change_at,
                                      last_offline_at, last_recovery_at, updated_at)
    select m.camera_id, v_agent.tenant_id, v_agent.site_id, m.health, m.reason,
           v_now,
           case when m.health = 'operational' then v_now else null end,
           v_now,
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
           last_offline_at  = case when excluded.health_state = 'offline'
                                     and chh.health_state is distinct from 'offline'
                                   then v_now else chh.last_offline_at end,
           last_recovery_at = case when excluded.health_state = 'operational'
                                     and chh.health_state = 'offline'
                                   then v_now else chh.last_recovery_at end,
           updated_at       = v_now
    returning 1
  )
  select count(*) filter (where m.health = 'operational'),
         count(*) filter (where m.health = 'degraded'),
         count(*) filter (where m.health = 'offline'),
         count(*) filter (where m.health = 'unknown'),
         (select count(*) from tx)
    into v_op, v_deg, v_off, v_unk, v_changed
    from mapped m;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object(
    'ok', true, 'operational', v_op, 'degraded', v_deg, 'offline', v_off,
    'unknown', v_unk, 'transitions', v_changed, 'server_time', v_now);
end $$;

-- Agent-facing (publishable/anon key), authenticated inside the body.
revoke all on function public.wl_report_camera_health(uuid, text, jsonb) from public;
grant execute on function public.wl_report_camera_health(uuid, text, jsonb) to anon, authenticated;

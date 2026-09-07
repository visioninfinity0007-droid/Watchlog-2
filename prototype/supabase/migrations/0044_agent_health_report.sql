-- =====================================================================
-- 0044 - Phase A increment 3: agent NVR-health + inventory report ingest
--
-- wl_report_health() is the agent-facing write for recorder connectivity/auth health and
-- channel inventory. Same auth pattern as wl_heartbeat/wl_sync_cameras: the agent presents
-- (agent_id, agent_key); tenant_id and site_id come from the AUTHENTICATED AGENT ROW, never
-- from the payload — so a report can only ever touch its own site's cameras.
--
-- What it persists (from prototype/agent/nvr_health.py's report):
--   * nvr_health.nvr_reachable / nvr_auth_ok / reason_code  (+ connectivity/auth transitions)
--   * camera_inventory PRESENT/MISSING/DISABLED/UNKNOWN     (+ inventory transitions)
--
-- Inventory is derived HERE, not trusted from the agent: the cloud owns the "should-have"
-- set (its cameras), so MISSING = a known camera the recorder no longer reports. This mirrors
-- prototype/server/inventory_model.classify_inventory (test_health_report_contract.py pins it).
--
-- Deliberately NOT touched here (later increments): recording_state / storage_state stay
-- 'unknown' (recording/HDD health is a separate increment); no snapshot probing; no event
-- backfill. And nothing sensitive is stored — no URL, no credential; only vendor/model
-- identity onto the existing agents columns.
--
-- Transitions are written only when a state actually changes, so a steady site that reports
-- every few minutes produces no transition churn (idempotent).
-- =====================================================================

create or replace function public.wl_report_health(
  p_agent_id  uuid,
  p_agent_key text,
  p_report    jsonb
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
  v_unmapped   jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  -- auth_ok is tri-state: true / false / JSON-null (unknown -> SQL null).
  if v_nvr ? 'auth_ok' and jsonb_typeof(v_nvr->'auth_ok') = 'boolean' then
    v_auth_ok := (v_nvr->>'auth_ok')::boolean;
  else
    v_auth_ok := null;
  end if;

  -- Defensive: a buggy/hostile agent cannot poison the reason column beyond the domain.
  if v_reason not in ('ok','unknown','nvr_unreachable','nvr_auth_failed','agent_unreachable',
                      'probe_timeout','stale_frame','video_loss','channel_missing',
                      'channel_disabled','storage_fault','not_recording','tamper',
                      'disk_error','disk_full') then
    v_reason := 'unknown';
  end if;

  -- ---- NVR connectivity/auth health (recording/storage left 'unknown' — later increment) ----
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

  -- ---- Camera inventory (cloud-authoritative derivation; transitions only on change) ----
  with rep as (
    select (c->>'channel') as channel,
           coalesce((c->>'enabled')::boolean, true) as enabled
      from jsonb_array_elements(coalesce(p_report#>'{channels,reported}', '[]'::jsonb)) c
     where coalesce(c->>'channel','') <> ''
  ),
  det as ( select (v_reachable and coalesce(v_auth_ok, true) and v_enumerated) as ok ),
  derived as (
    select cm.id as camera_id, cm.channel,
           case
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
         count(*) filter (where state = 'unknown')
    into v_present, v_missing, v_disabled, v_unknown
    from derived;

  -- Channels the recorder reports that are not cameras yet (info only; not persisted here).
  select coalesce(jsonb_agg(r.channel), '[]'::jsonb) into v_unmapped
    from (
      select (c->>'channel') as channel
        from jsonb_array_elements(coalesce(p_report#>'{channels,reported}', '[]'::jsonb)) c
       where coalesce(c->>'channel','') <> ''
         and not exists (select 1 from cameras cm
                          where cm.site_id = v_agent.site_id and cm.channel = c->>'channel')
    ) r;

  -- Liveness + non-sensitive identity onto the agent row (never a URL or credential).
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
    'unmapped', v_unmapped, 'server_time', v_now);
end $$;

-- Agent-facing (publishable/anon key), authenticated inside the body. Not the portal's.
revoke all on function public.wl_report_health(uuid, text, jsonb) from public;
grant execute on function public.wl_report_health(uuid, text, jsonb) to anon, authenticated;

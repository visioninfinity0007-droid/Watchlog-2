-- =====================================================================
-- 0047 - Phase A increment 6: NVR recording + storage health report ingest
--
-- wl_report_recording_storage() persists the agent's recording/storage assessment (the output of
-- prototype/agent/recording_health.py) into the columns 0042 already defined:
--   * nvr_health.storage_state      — HDD/storage health (ok|degraded|fault|unknown)
--   * nvr_health.recording_state    — an NVR-level recording rollup
--   * camera_health.recording_state — per channel (recording|not_recording|storage_fault|unknown)
-- plus nvr_health_transitions on the storage and recording layers (change-only, idempotent).
--
-- Same agent-auth contract as the other writes: (agent_id, agent_key) authenticate; tenant_id and
-- site_id come from the AGENT ROW, never the payload; states clamped to their domains so a buggy
-- agent cannot poison a column. Recording is NOT inferred from a snapshot — the agent already
-- resolved UNKNOWN where the vendor API is unreadable, and this only stores what it reported.
--
-- Deliberately left alone: camera_health.health_state (video health, increment 4), the connectivity
-- reason on nvr_health (increment 3), the transition LEDGER dedupe/reconciliation (increment 5). No
-- per-channel recording transition ledger yet — the operational-fault lifecycle is increment 7.
-- =====================================================================

create or replace function public.wl_report_recording_storage(
  p_agent_id  uuid,
  p_agent_key text,
  p_report    jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent    agents;
  v_now      timestamptz := now();
  v_prev     nvr_health;
  v_storage  text;
  v_rollup   text;
  v_rec_rows int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  -- storage_state, clamped to the wl_storage_state domain.
  v_storage := lower(coalesce(p_report#>>'{storage,state}', 'unknown'));
  if v_storage not in ('ok','degraded','fault','unknown') then v_storage := 'unknown'; end if;

  -- NVR-level recording ROLLUP from the per-channel states (priority: storage fault > not_recording
  -- > all-recording > unknown). A quick "is this recorder recording?" answer for the read model.
  with rec as (
    select case when lower(c->>'state') in ('recording','not_recording','storage_fault','unknown')
                then lower(c->>'state') else 'unknown' end as s
      from jsonb_array_elements(coalesce(p_report#>'{recording,channels}', '[]'::jsonb)) c
     where coalesce(c->>'channel','') <> ''
  )
  select case
           when v_storage = 'fault' then 'storage_fault'
           when exists (select 1 from rec where s = 'storage_fault') then 'storage_fault'
           when exists (select 1 from rec where s = 'not_recording') then 'not_recording'
           when count(*) > 0 and count(*) filter (where s = 'recording') = count(*) then 'recording'
           else 'unknown'
         end
    into v_rollup
    from rec;
  v_rollup := coalesce(v_rollup, 'unknown');

  -- ---- NVR storage + recording (upsert current; connectivity/auth reason left untouched) ----
  select * into v_prev from nvr_health where agent_id = v_agent.id;

  insert into nvr_health as nh (agent_id, tenant_id, site_id, storage_state, recording_state,
                                last_change_at, updated_at)
  values (v_agent.id, v_agent.tenant_id, v_agent.site_id, v_storage, v_rollup, v_now, v_now)
  on conflict (agent_id) do update
     set storage_state   = excluded.storage_state,
         recording_state  = excluded.recording_state,
         last_change_at   = case when nh.storage_state is distinct from excluded.storage_state
                                   or nh.recording_state is distinct from excluded.recording_state
                                 then v_now else nh.last_change_at end,
         updated_at       = v_now;

  -- storage-layer transition (change-only)
  if v_prev.agent_id is null or v_prev.storage_state is distinct from v_storage then
    insert into nvr_health_transitions (tenant_id, site_id, agent_id, layer, from_state, to_state,
                                        reason_code)
    values (v_agent.tenant_id, v_agent.site_id, v_agent.id, 'storage',
            coalesce(v_prev.storage_state, 'unknown'), v_storage,
            case when v_storage = 'fault' then 'storage_fault'
                 when v_storage = 'degraded' then 'disk_full' else 'ok' end);
  end if;
  -- recording-layer transition (change-only)
  if v_prev.agent_id is null or v_prev.recording_state is distinct from v_rollup then
    insert into nvr_health_transitions (tenant_id, site_id, agent_id, layer, from_state, to_state,
                                        reason_code)
    values (v_agent.tenant_id, v_agent.site_id, v_agent.id, 'recording',
            coalesce(v_prev.recording_state, 'unknown'), v_rollup,
            case when v_rollup = 'not_recording' then 'not_recording'
                 when v_rollup = 'storage_fault' then 'storage_fault' else 'ok' end);
  end if;

  -- ---- per-channel camera_health.recording_state (bound to THIS site's cameras) ----
  with rep as (
    select (c->>'channel') as channel,
           case when lower(c->>'state') in ('recording','not_recording','storage_fault','unknown')
                then lower(c->>'state') else 'unknown' end as rec
      from jsonb_array_elements(coalesce(p_report#>'{recording,channels}', '[]'::jsonb)) c
     where coalesce(c->>'channel','') <> ''
  ),
  mapped as (
    select cm.id as camera_id, r.rec
      from rep r join cameras cm on cm.site_id = v_agent.site_id and cm.channel = r.channel
  ),
  up as (
    insert into camera_health as chh (camera_id, tenant_id, site_id, recording_state, updated_at)
    select m.camera_id, v_agent.tenant_id, v_agent.site_id, m.rec, v_now
      from mapped m
    on conflict (camera_id) do update
       set recording_state = excluded.recording_state,
           updated_at      = v_now
    returning 1
  )
  select count(*) into v_rec_rows from up;

  update agents set last_seen_at = v_now where id = v_agent.id;

  return jsonb_build_object('ok', true, 'storage_state', v_storage, 'recording_state', v_rollup,
                            'channels_updated', v_rec_rows, 'server_time', v_now);
end $$;

revoke all on function public.wl_report_recording_storage(uuid, text, jsonb) from public;
grant execute on function public.wl_report_recording_storage(uuid, text, jsonb) to anon, authenticated;

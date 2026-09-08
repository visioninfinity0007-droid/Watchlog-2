-- =====================================================================
-- 0048 - Phase A increment 7: operational fault lifecycle + customer read model
--
-- The health ingest (0044-0047) records WHAT each layer's current state is. This increment turns
-- that settled state into the operator-facing RELIABILITY signal: operational_faults (the table +
-- dedupe index already exist from 0042) and a tenant-scoped read model the portal renders.
--
-- A fault is "your CCTV needs attention" (camera offline, recorder unreachable, storage fault,
-- recording stopped) — NOT a security incident. The pure spec is prototype/server/fault_model.py;
-- test_operational_faults_contract.py pins this SQL to it. Two invariants carried from that model:
--
--   * LAYERED SUPPRESSION: an observer that is DOWN never fabricates faults beneath it. Agent
--     unreachable -> the ONLY fault is agent_unreachable (cameras/NVR/storage are now UNKNOWN/stale).
--     A down or unauthenticated NVR likewise suppresses the camera/recording/storage faults under it.
--   * UNKNOWN IS NEVER A FAULT: only a POSITIVELY CONFIRMED bad state opens one; a removed/disabled
--     channel is inventory (MISSING/DISABLED), not a fault.
--
-- Faults DEDUPE on operational_faults.dedupe_key (partial-unique where state <> 'resolved'), so a
-- sustained condition is ONE row. Reconciliation opens the newly-confirmed, resolves the cleared
-- (freeing the key for a future recurrence), and leaves a persistent/acknowledged fault untouched.
-- Server-derived: a per-minute pg_cron sweep, never fabricated at the portal.
-- =====================================================================

-- =====================================================================
-- wl_reconcile_site_faults — derive the desired open-fault set for ONE site from confirmed current
-- state and reconcile operational_faults to match. SECURITY DEFINER, INTERNAL (cron/definer only).
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
  v_desired    jsonb;
  v_opened     int := 0;
  v_resolved   int := 0;
  v_open_total int := 0;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_site');
  end if;

  -- Serialize per-site so a manual run cannot race the cron sweep into duplicate opens.
  perform pg_advisory_xact_lock(hashtext('wl_reconcile_site_faults'), hashtext(p_site_id::text));

  -- DESIRED open-fault set, derived ONCE with the layered-observer suppression (mirrors
  -- fault_model.desired_faults). dedupe_key literals match the model EXACTLY.
  with rec as (
    -- one row per recorder/agent on the site: is the agent observable, and the NVR layer state
    select nh.agent_id, nh.nvr_reachable, nh.nvr_auth_ok, nh.storage_state,
           exists (select 1 from agent_unreachable_intervals aui
                    where aui.agent_id = nh.agent_id and aui.ended_at is null) as agent_down
      from nvr_health nh
     where nh.site_id = p_site_id
  ),
  observable as (   -- the site can be seen iff some recorder is up, reachable AND authenticated
    select exists (select 1 from rec
                    where not agent_down and nvr_reachable is true and nvr_auth_ok is true) as ok
  ),
  desired as (
    -- (A) AGENT unreachable — the only assertable fault for a down agent (suppresses everything below)
    select 'agent:' || agent_id || ':unreachable' as dedupe_key, 'agent' as fault_domain,
           'agent_unreachable' as fault_type, 'critical' as severity,
           'agent_unreachable' as reason_code, null::uuid as camera_id, agent_id
      from rec where agent_down
    union all
    -- (B) NVR unreachable (agent up, recorder down) — suppresses cameras/storage
    select 'nvr:' || agent_id || ':unreachable', 'nvr_connectivity', 'nvr_unreachable', 'critical',
           'nvr_unreachable', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is false
    union all
    -- (C) NVR auth failed (agent up, reachable, auth explicitly false)
    select 'nvr:' || agent_id || ':auth', 'nvr_auth', 'nvr_auth_failed', 'critical',
           'nvr_auth_failed', null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is true and nvr_auth_ok is false
    union all
    -- (D) STORAGE fault (critical) / degraded (warning) — only when the recorder is readable
    select 'nvr:' || agent_id || ':storage', 'storage',
           case when storage_state = 'fault' then 'storage_fault' else 'storage_degraded' end,
           case when storage_state = 'fault' then 'critical' else 'warning' end,
           case when storage_state = 'fault' then 'storage_fault' else 'disk_full' end,
           null::uuid, agent_id
      from rec where not agent_down and nvr_reachable is true and nvr_auth_ok is not false
       and storage_state in ('fault', 'degraded')
    union all
    -- (E) CAMERA offline — only if the site is OBSERVABLE; MISSING/DISABLED is inventory, not a fault
    select 'camera:' || ch.camera_id || ':offline', 'camera', 'camera_offline', 'critical',
           'video_loss', ch.camera_id, null::uuid
      from camera_health ch
      left join camera_inventory ci on ci.camera_id = ch.camera_id
     where ch.site_id = p_site_id and ch.health_state = 'offline'
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
    union all
    -- (E) RECORDING stopped / channel storage fault — same observability + inventory gating
    select 'camera:' || ch.camera_id || ':recording', 'recording',
           case when ch.recording_state = 'storage_fault' then 'recording_storage_fault' else 'not_recording' end,
           'warning',
           case when ch.recording_state = 'storage_fault' then 'storage_fault' else 'not_recording' end,
           ch.camera_id, null::uuid
      from camera_health ch
      left join camera_inventory ci on ci.camera_id = ch.camera_id
     where ch.site_id = p_site_id and ch.recording_state in ('not_recording', 'storage_fault')
       and coalesce(ci.inventory_state, 'present') not in ('missing', 'disabled')
       and (select ok from observable)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'dedupe_key', dedupe_key, 'fault_domain', fault_domain, 'fault_type', fault_type,
           'severity', severity, 'reason_code', reason_code, 'camera_id', camera_id, 'agent_id', agent_id)),
         '[]'::jsonb)
    into v_desired from desired;

  -- OPEN the desired faults not already live. The partial-unique dedupe (state <> 'resolved') means a
  -- sustained condition — even an acknowledged one — is never duplicated or restarted.
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

  -- RESOLVE live faults for this site no longer desired (the condition cleared -> frees the key).
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

revoke all on function public.wl_reconcile_site_faults(uuid) from public, anon, authenticated;

-- =====================================================================
-- wl_sweep_faults — reconcile faults for every site with health data. Cron/superuser only.
-- =====================================================================
create or replace function public.wl_sweep_faults()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  r record;
  res jsonb;
  v_sites int := 0; v_opened int := 0; v_resolved int := 0;
begin
  perform pg_advisory_xact_lock(hashtext('wl_sweep_faults'));
  for r in select distinct site_id from (
             select site_id from nvr_health
             union
             select site_id from camera_health) s loop
    res := wl_reconcile_site_faults(r.site_id);
    v_sites := v_sites + 1;
    v_opened := v_opened + coalesce((res->>'opened')::int, 0);
    v_resolved := v_resolved + coalesce((res->>'resolved')::int, 0);
  end loop;
  return jsonb_build_object('ok', true, 'evaluated_at', now(),
    'sites', v_sites, 'opened', v_opened, 'resolved', v_resolved);
end $$;

revoke all on function public.wl_sweep_faults() from public, anon, authenticated;

-- =====================================================================
-- wl_ack_fault — an operator acknowledges an open fault (owner/admin). Tenant-scoped even though the
-- function is definer: the caller can only touch a fault in their OWN tenant.
-- =====================================================================
create or replace function public.wl_ack_fault(p_fault_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v_state  text;
begin
  v_tenant := wl_require_role(array['owner', 'admin']);   -- resolves tenant + enforces role (else 42501)
  update operational_faults
     set state = 'acknowledged', acknowledged_at = now()
   where id = p_fault_id and tenant_id = v_tenant and state = 'open'
   returning state into v_state;
  if v_state is null then
    return jsonb_build_object('ok', false, 'reason', 'not_open_or_not_yours');
  end if;
  return jsonb_build_object('ok', true, 'fault_id', p_fault_id, 'state', v_state);
end $$;

revoke all on function public.wl_ack_fault(bigint) from public, anon;
grant execute on function public.wl_ack_fault(bigint) to authenticated;

-- =====================================================================
-- wl_site_health_snapshot — the customer READ MODEL: current per-layer health + open faults for a
-- site the caller owns. Tenant-scoped, read-only. This is what the portal health view renders.
-- =====================================================================
create or replace function public.wl_site_health_snapshot(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
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
    -- recorder layer (connectivity / auth / recording rollup / storage) — one row per agent/recorder
    'recorders', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'agent_id', nh.agent_id, 'nvr_reachable', nh.nvr_reachable, 'nvr_auth_ok', nh.nvr_auth_ok,
               'recording_state', nh.recording_state, 'storage_state', nh.storage_state,
               'reason_code', nh.reason_code, 'storage_reason_code', nh.storage_reason_code,
               'updated_at', nh.updated_at)), '[]'::jsonb)
        from nvr_health nh where nh.site_id = p_site_id and nh.tenant_id = v_tenant),
    -- camera layer: inventory (present/missing/disabled/unknown) kept DISTINCT from health
    'cameras', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'camera_id', c.id, 'channel', c.channel, 'name', c.name,
               'health_state', coalesce(ch.health_state, 'unknown'),
               'inventory_state', coalesce(ci.inventory_state, 'unknown'),
               'recording_state', coalesce(ch.recording_state, 'unknown'),
               'reason_code', ch.reason_code, 'recording_reason_code', ch.recording_reason_code,
               'updated_at', ch.updated_at) order by c.channel), '[]'::jsonb)
        from cameras c
        left join camera_health ch on ch.camera_id = c.id
        left join camera_inventory ci on ci.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant),
    -- open operational faults (critical first), the "needs attention" list
    'faults', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'id', f.id, 'domain', f.fault_domain, 'fault_type', f.fault_type, 'severity', f.severity,
               'state', f.state, 'reason_code', f.reason_code, 'camera_id', f.camera_id,
               'agent_id', f.agent_id, 'opened_at', f.opened_at, 'acknowledged_at', f.acknowledged_at)
               order by case f.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                        f.opened_at desc), '[]'::jsonb)
        from operational_faults f
       where f.site_id = p_site_id and f.tenant_id = v_tenant and f.state <> 'resolved'),
    'summary', (
      select jsonb_build_object(
               'cameras_total', count(*),
               'operational', count(*) filter (where coalesce(ch.health_state, 'unknown') = 'operational'),
               'degraded', count(*) filter (where ch.health_state = 'degraded'),
               'offline', count(*) filter (where ch.health_state = 'offline'),
               'unknown', count(*) filter (where coalesce(ch.health_state, 'unknown') = 'unknown'))
        from cameras c left join camera_health ch on ch.camera_id = c.id
       where c.site_id = p_site_id and c.tenant_id = v_tenant),
    'faults_open', (
      select count(*) from operational_faults f
       where f.site_id = p_site_id and f.tenant_id = v_tenant and f.state <> 'resolved'),
    'server_time', now());
end $$;

revoke all on function public.wl_site_health_snapshot(uuid) from public, anon;
grant execute on function public.wl_site_health_snapshot(uuid) to authenticated;

-- =====================================================================
-- Schedule the fault sweep every minute via pg_cron, if available (same guard as 0014/0043 — the
-- migration still applies cleanly where pg_cron is absent, e.g. a disposable test Postgres).
-- =====================================================================
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'watchlog-fault-sweep';
    perform cron.schedule('watchlog-fault-sweep', '* * * * *', 'select public.wl_sweep_faults()');
  end if;
exception when others then
  raise notice 'pg_cron scheduling skipped: %', sqlerrm;
end $$;

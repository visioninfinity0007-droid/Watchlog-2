-- 0162 - Agent runtime status: per-worker liveness and health reported by the Agent (5.1.2).
--
-- REPO ONLY until separately approved for production.
--
-- Before 5.1.2 the cloud could only infer an Agent's health from other tables: wl_heartbeat
-- (0004) carries version and device, and a worker thread that died inside a heartbeating
-- process (collector, health, Site Control, remote update ...) was invisible. The 5.1.2
-- Agent supervises every long-running worker (agent/worker_supervisor.py) and reports each
-- one's state here every 60 s and immediately on a change, from its main loop.
--
-- 1. wl_known_capabilities(): the 0156 body with one element appended,
--    'agent_runtime_status_v1', so wl_agent_report_capabilities keeps the capability a
--    5.1.2 Agent advertises. Nothing else changes: the body is otherwise the 0156 body,
--    search_path stays exactly as 0156 set it (public) and CREATE OR REPLACE keeps the ACL.
--
-- 2. Tables (RLS on, no client access; read only through wl_site_agent_runtime):
--    agent_runtime_status   one row per (agent, recorder or none, worker):
--                           state in starting|running|stalled|restarting|failed|completed|
--                           disabled, enabled, healthy, critical, last_success_at,
--                           last_error (redacted by the Agent, capped here), restart_count,
--                           detail jsonb, reported_at.
--    agent_runtime_recorders one row per (agent, recorder or none): event stream
--                           up|down|unknown, credential_unavailable, queue depth, queue
--                           overflow, upload degraded.
--    agent_runtime_summary  one row per agent: queue depth/capacity/overflow, last
--                           successful event upload and heartbeat, worker counts and the
--                           agent-level healthy verdict (computed here from the stored rows:
--                           every enabled critical worker healthy).
--    The nullable recorder is keyed through a generated recorder_key (the nil UUID when
--    NULL), so the primary keys are (agent_id, recorder_key, worker) and
--    (agent_id, recorder_key).
--
-- 3. wl_report_agent_runtime(p_agent_id uuid, p_agent_key text, p_status jsonb) -> jsonb.
--    Agent-key authenticated through wl_auth_agent (0004), exactly like the other Agent
--    RPCs; EXECUTE for anon only (Agents call with the publishable key). The payload is
--    bounded (64 KiB, at most 200 worker and 64 recorder rows; larger raises 54000). Each
--    report is the Agent's complete current set: rows are upserted and this Agent's rows not
--    in the report are removed. An unknown worker name or state, a malformed or foreign
--    recorder id (not a recorder of the Agent's own site) or a duplicate row is skipped and
--    listed under "rejected"; the rest of the report is still stored. A row reported
--    healthy in any state other than running/completed is stored unhealthy.
--
-- 4. wl_site_agent_runtime(p_site_id uuid) -> jsonb. Tenant-scoped read for the portal and
--    acceptance tooling: any active member of the site's tenant (wl_is_member), else 42501.
--    EXECUTE for authenticated only. "healthy" is true only for a row reported within the
--    last 180 seconds (fresh) and reported healthy: no evidence, no health. The rows name
--    internal workers; owner-facing surfaces must translate them into customer language
--    (ai-harness/core/customer-vocabulary.yaml) and never show them raw.

-- ---------------------------------------------------------------------------
-- 1. Known capabilities: 0156 body + 'agent_runtime_status_v1'
-- ---------------------------------------------------------------------------
create or replace function public.wl_known_capabilities()
returns text[] language sql immutable
set search_path = public
as $$
  select array[
    'operations_runtime',
    'operations_extended_primitives',
    'operations_evidence_still',
    'operations_evidence_clip',
    'archive_processing',
    'multi_agent_fencing',
    'recorder_probe_v2',
    'site_control_runtime',
    'remote_update_v1',
    'config_snapshot_requests',
    'agent_runtime_status_v1'
  ]
$$;

-- ---------------------------------------------------------------------------
-- 2. Tables
-- ---------------------------------------------------------------------------
create table if not exists public.agent_runtime_status (
  agent_id uuid not null references public.agents(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  recorder_id uuid null references public.recorders(id) on delete cascade,
  recorder_key uuid generated always as
    (coalesce(recorder_id, '00000000-0000-0000-0000-000000000000'::uuid)) stored,
  worker text not null,
  state text not null,
  enabled boolean not null default true,
  healthy boolean not null default false,
  critical boolean not null default false,
  last_success_at timestamptz null,
  last_error text null,
  restart_count integer not null default 0,
  detail jsonb not null default '{}'::jsonb,
  reported_at timestamptz not null default now(),
  constraint agent_runtime_status_pk primary key (agent_id, recorder_key, worker),
  constraint agent_runtime_status_state_chk check (state in
    ('starting','running','stalled','restarting','failed','completed','disabled')),
  constraint agent_runtime_status_error_len_chk check (char_length(last_error) <= 500),
  constraint agent_runtime_status_restart_chk check (restart_count >= 0),
  constraint agent_runtime_status_detail_chk check (jsonb_typeof(detail) = 'object')
);

create index if not exists agent_runtime_status_site_idx
  on public.agent_runtime_status(site_id, agent_id);

create table if not exists public.agent_runtime_recorders (
  agent_id uuid not null references public.agents(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  recorder_id uuid null references public.recorders(id) on delete cascade,
  recorder_key uuid generated always as
    (coalesce(recorder_id, '00000000-0000-0000-0000-000000000000'::uuid)) stored,
  event_stream text not null default 'unknown',
  credential_unavailable boolean not null default false,
  spool_depth integer null,
  spool_overflow boolean null,
  upload_degraded boolean not null default false,
  detail jsonb not null default '{}'::jsonb,
  reported_at timestamptz not null default now(),
  constraint agent_runtime_recorders_pk primary key (agent_id, recorder_key),
  constraint agent_runtime_recorders_stream_chk check (event_stream in ('up','down','unknown')),
  constraint agent_runtime_recorders_spool_chk check (spool_depth is null or spool_depth >= 0),
  constraint agent_runtime_recorders_detail_chk check (jsonb_typeof(detail) = 'object')
);

create index if not exists agent_runtime_recorders_site_idx
  on public.agent_runtime_recorders(site_id, agent_id);

create table if not exists public.agent_runtime_summary (
  agent_id uuid primary key references public.agents(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  agent_version text null,
  spool_depth integer null,
  spool_capacity integer null,
  spool_overflow boolean not null default false,
  cloud_upload_last_success_at timestamptz null,
  heartbeat_last_success_at timestamptz null,
  workers_total integer not null default 0,
  workers_unhealthy integer not null default 0,
  critical_unhealthy integer not null default 0,
  healthy boolean not null default false,
  reported_at timestamptz not null default now(),
  constraint agent_runtime_summary_spool_chk check (spool_depth is null or spool_depth >= 0)
);

create index if not exists agent_runtime_summary_site_idx
  on public.agent_runtime_summary(site_id, reported_at desc);

alter table public.agent_runtime_status enable row level security;
alter table public.agent_runtime_recorders enable row level security;
alter table public.agent_runtime_summary enable row level security;
revoke all on table public.agent_runtime_status from public, anon, authenticated;
revoke all on table public.agent_runtime_recorders from public, anon, authenticated;
revoke all on table public.agent_runtime_summary from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Internal helpers (no client role may execute them)
-- ---------------------------------------------------------------------------
-- The worker names a 5.1.2 Agent reports (agent/worker_supervisor.py KNOWN_WORKERS).
create or replace function public.wl_agent_runtime_workers()
returns text[] language sql immutable
set search_path = public
as $$
  select array[
    'collector',
    'health',
    'recovery',
    'periodic_stills',
    'credential_watch',
    'analytics',
    'archive',
    'site_control',
    'recorder_check',
    'incident_footage',
    'incident_stills',
    'remote_update',
    'capability_sync'
  ]
$$;

-- A reported timestamp, or NULL when it does not parse; never later than now(), so an
-- Agent clock running ahead cannot make a success look fresh.
create or replace function public.wl_agent_runtime_ts(p_value text)
returns timestamptz
language plpgsql
stable
set search_path = public
as $$
declare
  v timestamptz;
begin
  if p_value is null or btrim(p_value) = '' or char_length(p_value) > 64 then
    return null;
  end if;
  begin
    v := p_value::timestamptz;
  exception when others then
    return null;
  end;
  return least(v, now());
end
$$;

create or replace function public.wl_agent_runtime_bool(p_value jsonb, p_default boolean)
returns boolean language sql immutable
set search_path = public
as $$
  select case when jsonb_typeof(p_value) = 'boolean' then (p_value)::boolean else p_default end
$$;

create or replace function public.wl_agent_runtime_int(p_value jsonb)
returns integer language sql immutable
set search_path = public
as $$
  select case when jsonb_typeof(p_value) = 'number'
              then least(greatest(floor((p_value)::numeric), 0), 2147483647)::integer
              else null end
$$;

revoke all on function public.wl_agent_runtime_workers() from public, anon, authenticated;
revoke all on function public.wl_agent_runtime_ts(text) from public, anon, authenticated;
revoke all on function public.wl_agent_runtime_bool(jsonb, boolean) from public, anon, authenticated;
revoke all on function public.wl_agent_runtime_int(jsonb) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. wl_report_agent_runtime: the Agent's report (agent-key authenticated)
-- ---------------------------------------------------------------------------
create or replace function public.wl_report_agent_runtime(
  p_agent_id uuid,
  p_agent_key text,
  p_status jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  c_none constant uuid := '00000000-0000-0000-0000-000000000000';
  c_states constant text[] := array['starting','running','stalled','restarting','failed',
                                    'completed','disabled'];
  c_uuid constant text := '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
  v_agent public.agents;
  v_workers jsonb;
  v_recorders jsonb;
  v_fields jsonb;
  v_item jsonb;
  v_idx integer := 0;
  v_rejected jsonb := '[]'::jsonb;
  v_worker text;
  v_state text;
  v_stream text;
  v_rec_text text;
  v_rec uuid;
  v_detail jsonb;
  v_healthy boolean;
  v_seen_workers text[] := '{}';
  v_seen_recorders uuid[] := '{}';
  v_workers_ok integer := 0;
  v_recorders_ok integer := 0;
  v_now timestamptz := now();
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  if p_status is null or jsonb_typeof(p_status) <> 'object' then
    raise exception 'runtime status must be a JSON object' using errcode = '22023';
  end if;
  if octet_length(p_status::text) > 65536 then
    raise exception 'runtime status payload too large' using errcode = '54000';
  end if;

  v_workers := case when jsonb_typeof(p_status->'workers') = 'array'
                    then p_status->'workers' else '[]'::jsonb end;
  v_recorders := case when jsonb_typeof(p_status->'recorders') = 'array'
                      then p_status->'recorders' else '[]'::jsonb end;
  v_fields := case when jsonb_typeof(p_status->'agent') = 'object'
                   then p_status->'agent' else '{}'::jsonb end;
  if jsonb_array_length(v_workers) > 200 or jsonb_array_length(v_recorders) > 64 then
    raise exception 'runtime status has too many rows' using errcode = '54000';
  end if;

  -- Workers -----------------------------------------------------------------
  for v_item in select value from jsonb_array_elements(v_workers) loop
    v_idx := v_idx + 1;
    if jsonb_typeof(v_item) <> 'object' then
      v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                     'reason','not an object');
      continue;
    end if;
    v_worker := v_item->>'worker';
    v_state := v_item->>'state';
    if v_worker is null or not (v_worker = any(public.wl_agent_runtime_workers())) then
      v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                     'worker',left(v_worker, 64),
                                                     'reason','unknown worker');
      continue;
    end if;
    if v_state is null or not (v_state = any(c_states)) then
      v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                     'worker',v_worker,
                                                     'reason','unknown state');
      continue;
    end if;
    v_rec := null;
    v_rec_text := nullif(v_item->>'recorder_id', '');
    if v_rec_text is not null then
      if v_rec_text !~ c_uuid then
        v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                       'worker',v_worker,
                                                       'reason','invalid recorder_id');
        continue;
      end if;
      v_rec := v_rec_text::uuid;
      if not exists (select 1 from public.recorders r
                      where r.id = v_rec and r.site_id = v_agent.site_id) then
        v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                       'worker',v_worker,
                                                       'reason','recorder is not on this site');
        continue;
      end if;
    end if;
    if (coalesce(v_rec, c_none)::text || '|' || v_worker) = any(v_seen_workers) then
      v_rejected := v_rejected || jsonb_build_object('kind','worker','index',v_idx,
                                                     'worker',v_worker,
                                                     'reason','duplicate row');
      continue;
    end if;
    v_seen_workers := v_seen_workers || (coalesce(v_rec, c_none)::text || '|' || v_worker);

    v_detail := case
      when jsonb_typeof(v_item->'detail') = 'object'
           and octet_length((v_item->'detail')::text) <= 2048 then v_item->'detail'
      when jsonb_typeof(v_item->'detail') = 'object' then jsonb_build_object('truncated', true)
      else '{}'::jsonb end;
    -- No evidence, no health: only a running (or successfully completed) worker is healthy.
    v_healthy := public.wl_agent_runtime_bool(v_item->'healthy', false)
                 and v_state in ('running', 'completed');

    insert into public.agent_runtime_status as s
      (agent_id, tenant_id, site_id, recorder_id, worker, state, enabled, healthy, critical,
       last_success_at, last_error, restart_count, detail, reported_at)
    values
      (v_agent.id, v_agent.tenant_id, v_agent.site_id, v_rec, v_worker, v_state,
       public.wl_agent_runtime_bool(v_item->'enabled', true) and v_state <> 'disabled',
       v_healthy,
       public.wl_agent_runtime_bool(v_item->'critical', false),
       public.wl_agent_runtime_ts(v_item->>'last_success_at'),
       left(nullif(v_item->>'last_error', ''), 500),
       coalesce(public.wl_agent_runtime_int(v_item->'restart_count'), 0),
       v_detail, v_now)
    on conflict (agent_id, recorder_key, worker) do update
       set tenant_id = excluded.tenant_id,
           site_id = excluded.site_id,
           state = excluded.state,
           enabled = excluded.enabled,
           healthy = excluded.healthy,
           critical = excluded.critical,
           last_success_at = excluded.last_success_at,
           last_error = excluded.last_error,
           restart_count = excluded.restart_count,
           detail = excluded.detail,
           reported_at = excluded.reported_at;
    v_workers_ok := v_workers_ok + 1;
  end loop;

  -- The report is the Agent's complete set: a worker it no longer runs is not kept.
  delete from public.agent_runtime_status s
   where s.agent_id = v_agent.id
     and not ((s.recorder_key::text || '|' || s.worker) = any(v_seen_workers));

  -- Recorders ---------------------------------------------------------------
  v_idx := 0;
  for v_item in select value from jsonb_array_elements(v_recorders) loop
    v_idx := v_idx + 1;
    if jsonb_typeof(v_item) <> 'object' then
      v_rejected := v_rejected || jsonb_build_object('kind','recorder','index',v_idx,
                                                     'reason','not an object');
      continue;
    end if;
    v_stream := coalesce(v_item->>'event_stream', 'unknown');
    if v_stream not in ('up', 'down', 'unknown') then
      v_rejected := v_rejected || jsonb_build_object('kind','recorder','index',v_idx,
                                                     'reason','unknown event_stream');
      continue;
    end if;
    v_rec := null;
    v_rec_text := nullif(v_item->>'recorder_id', '');
    if v_rec_text is not null then
      if v_rec_text !~ c_uuid then
        v_rejected := v_rejected || jsonb_build_object('kind','recorder','index',v_idx,
                                                       'reason','invalid recorder_id');
        continue;
      end if;
      v_rec := v_rec_text::uuid;
      if not exists (select 1 from public.recorders r
                      where r.id = v_rec and r.site_id = v_agent.site_id) then
        v_rejected := v_rejected || jsonb_build_object('kind','recorder','index',v_idx,
                                                       'reason','recorder is not on this site');
        continue;
      end if;
    end if;
    if coalesce(v_rec, c_none) = any(v_seen_recorders) then
      v_rejected := v_rejected || jsonb_build_object('kind','recorder','index',v_idx,
                                                     'reason','duplicate row');
      continue;
    end if;
    v_seen_recorders := v_seen_recorders || coalesce(v_rec, c_none);
    v_detail := case
      when jsonb_typeof(v_item->'detail') = 'object'
           and octet_length((v_item->'detail')::text) <= 2048 then v_item->'detail'
      when jsonb_typeof(v_item->'detail') = 'object' then jsonb_build_object('truncated', true)
      else '{}'::jsonb end;

    insert into public.agent_runtime_recorders as r
      (agent_id, tenant_id, site_id, recorder_id, event_stream, credential_unavailable,
       spool_depth, spool_overflow, upload_degraded, detail, reported_at)
    values
      (v_agent.id, v_agent.tenant_id, v_agent.site_id, v_rec, v_stream,
       public.wl_agent_runtime_bool(v_item->'credential_unavailable', false),
       public.wl_agent_runtime_int(v_item->'spool_depth'),
       case when jsonb_typeof(v_item->'spool_overflow') = 'boolean'
            then (v_item->'spool_overflow')::boolean else null end,
       public.wl_agent_runtime_bool(v_item->'upload_degraded', false),
       v_detail, v_now)
    on conflict (agent_id, recorder_key) do update
       set tenant_id = excluded.tenant_id,
           site_id = excluded.site_id,
           event_stream = excluded.event_stream,
           credential_unavailable = excluded.credential_unavailable,
           spool_depth = excluded.spool_depth,
           spool_overflow = excluded.spool_overflow,
           upload_degraded = excluded.upload_degraded,
           detail = excluded.detail,
           reported_at = excluded.reported_at;
    v_recorders_ok := v_recorders_ok + 1;
  end loop;

  delete from public.agent_runtime_recorders r
   where r.agent_id = v_agent.id
     and not (r.recorder_key = any(v_seen_recorders));

  -- Agent-level fields; counts and the verdict come from the stored rows, not the payload.
  insert into public.agent_runtime_summary as m
    (agent_id, tenant_id, site_id, agent_version, spool_depth, spool_capacity, spool_overflow,
     cloud_upload_last_success_at, heartbeat_last_success_at, workers_total,
     workers_unhealthy, critical_unhealthy, healthy, reported_at)
  select v_agent.id, v_agent.tenant_id, v_agent.site_id,
         left(nullif(coalesce(p_status->>'agent_version', ''), ''), 40),
         public.wl_agent_runtime_int(v_fields->'spool_depth'),
         public.wl_agent_runtime_int(v_fields->'spool_capacity'),
         public.wl_agent_runtime_bool(v_fields->'spool_overflow', false),
         public.wl_agent_runtime_ts(v_fields->>'cloud_upload_last_success_at'),
         public.wl_agent_runtime_ts(v_fields->>'heartbeat_last_success_at'),
         count(s.worker)::integer,
         (count(s.worker) filter (where s.enabled and not s.healthy
                                   and s.state <> 'completed'))::integer,
         (count(s.worker) filter (where s.critical and s.enabled and not s.healthy))::integer,
         (count(s.worker) filter (where s.critical and s.enabled)) > 0
           and (count(s.worker) filter (where s.critical and s.enabled and not s.healthy)) = 0,
         v_now
    from (select 1) one
    left join public.agent_runtime_status s on s.agent_id = v_agent.id
  on conflict (agent_id) do update
     set tenant_id = excluded.tenant_id,
         site_id = excluded.site_id,
         agent_version = excluded.agent_version,
         spool_depth = excluded.spool_depth,
         spool_capacity = excluded.spool_capacity,
         spool_overflow = excluded.spool_overflow,
         cloud_upload_last_success_at = excluded.cloud_upload_last_success_at,
         heartbeat_last_success_at = excluded.heartbeat_last_success_at,
         workers_total = excluded.workers_total,
         workers_unhealthy = excluded.workers_unhealthy,
         critical_unhealthy = excluded.critical_unhealthy,
         healthy = excluded.healthy,
         reported_at = excluded.reported_at;

  return jsonb_build_object(
    'ok', true,
    'workers_accepted', v_workers_ok,
    'recorders_accepted', v_recorders_ok,
    'rejected', v_rejected,
    'reported_at', v_now);
end
$$;

-- ---------------------------------------------------------------------------
-- 4. wl_site_agent_runtime: tenant-scoped read (portal / acceptance tooling)
-- ---------------------------------------------------------------------------
create or replace function public.wl_site_agent_runtime(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  c_fresh constant interval := interval '180 seconds';
  v_site public.sites;
begin
  select * into v_site from public.sites where id = p_site_id;
  if v_site.id is null or not public.wl_is_member(v_site.tenant_id) then
    raise exception 'not authorized' using errcode = '42501';
  end if;

  return jsonb_build_object(
    'site_id', v_site.id,
    'server_time', now(),
    'fresh_within_seconds', extract(epoch from c_fresh)::integer,
    'agents', coalesce((
      select jsonb_agg(jsonb_build_object(
               'agent_id', m.agent_id,
               'agent_version', m.agent_version,
               'reported_at', m.reported_at,
               'fresh', m.reported_at >= now() - c_fresh,
               'healthy', m.healthy and m.reported_at >= now() - c_fresh,
               'reported_healthy', m.healthy,
               'spool_depth', m.spool_depth,
               'spool_capacity', m.spool_capacity,
               'spool_overflow', m.spool_overflow,
               'cloud_upload_last_success_at', m.cloud_upload_last_success_at,
               'heartbeat_last_success_at', m.heartbeat_last_success_at,
               'workers_total', m.workers_total,
               'workers_unhealthy', m.workers_unhealthy,
               'critical_unhealthy', m.critical_unhealthy,
               'workers', coalesce((
                 select jsonb_agg(jsonb_build_object(
                          'worker', s.worker,
                          'recorder_id', s.recorder_id,
                          'state', s.state,
                          'enabled', s.enabled,
                          'critical', s.critical,
                          'healthy', s.healthy and s.reported_at >= now() - c_fresh,
                          'reported_healthy', s.healthy,
                          'last_success_at', s.last_success_at,
                          'last_error', s.last_error,
                          'restart_count', s.restart_count,
                          'detail', s.detail,
                          'reported_at', s.reported_at)
                        order by s.recorder_key, s.worker)
                   from public.agent_runtime_status s
                  where s.agent_id = m.agent_id), '[]'::jsonb),
               'recorders', coalesce((
                 select jsonb_agg(jsonb_build_object(
                          'recorder_id', r.recorder_id,
                          'event_stream', case when r.reported_at >= now() - c_fresh
                                               then r.event_stream else 'unknown' end,
                          'reported_event_stream', r.event_stream,
                          'credential_unavailable', r.credential_unavailable,
                          'spool_depth', r.spool_depth,
                          'spool_overflow', r.spool_overflow,
                          'upload_degraded', r.upload_degraded,
                          'detail', r.detail,
                          'reported_at', r.reported_at)
                        order by r.recorder_key)
                   from public.agent_runtime_recorders r
                  where r.agent_id = m.agent_id), '[]'::jsonb))
             order by m.reported_at desc)
        from public.agent_runtime_summary m
       where m.site_id = v_site.id), '[]'::jsonb));
end
$$;

-- ---------------------------------------------------------------------------
-- Grants
-- ---------------------------------------------------------------------------
revoke all on function public.wl_report_agent_runtime(uuid, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.wl_report_agent_runtime(uuid, text, jsonb) to anon;

revoke all on function public.wl_site_agent_runtime(uuid) from public, anon;
grant execute on function public.wl_site_agent_runtime(uuid) to authenticated;

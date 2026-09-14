-- ---------------------------------------------------------------------
-- Touch an ongoing physical incident.
-- ---------------------------------------------------------------------
create or replace function public.wl_touch_operations_incident(
  p_incident_id bigint,
  p_activity_at timestamptz,
  p_confidence numeric default null,
  p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare v public.operations_incidents;
begin
  update public.operations_incidents i
     set incident_started_at = least(
           coalesce(i.incident_started_at,p_activity_at), p_activity_at),
         last_activity_at = greatest(
           coalesce(i.last_activity_at,p_activity_at), p_activity_at),
         lifecycle_confidence = case
           when p_confidence is null then i.lifecycle_confidence
           else greatest(coalesce(i.lifecycle_confidence,0),p_confidence)
         end,
         detail = coalesce(i.detail,'{}'::jsonb) || coalesce(p_detail,'{}'::jsonb)
   where i.id = p_incident_id
     and i.lifecycle_state in ('active','escalated')
  returning * into v;

  -- Do not append one lifecycle-history row per detector firing. The current
  -- `last_activity_at` timestamp is the authoritative ongoing-episode signal;
  -- lifecycle history is reserved for meaningful state transitions.
  return case when v.id is null then null else to_jsonb(v) end;
end $$;

revoke all on function public.wl_touch_operations_incident(bigint,timestamptz,numeric,jsonb)
  from public, anon, authenticated;
grant execute on function public.wl_touch_operations_incident(bigint,timestamptz,numeric,jsonb)
  to service_role;

-- ---------------------------------------------------------------------
-- End physical activity. Review `status` is intentionally untouched.
-- 0098 augments this function to enqueue complete bounded evidence windows.
-- ---------------------------------------------------------------------
create or replace function public.wl_end_operations_incident(
  p_incident_id bigint,
  p_ended_at timestamptz,
  p_post_seconds int default 30,
  p_reason text default 'activity_ended'
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v public.operations_incidents;
  v_post int := least(greatest(coalesce(p_post_seconds,30),0),120);
begin
  update public.operations_incidents i
     set lifecycle_state = 'ended',
         incident_ended_at = greatest(
           coalesce(i.incident_started_at,p_ended_at),
           coalesce(p_ended_at,i.last_activity_at,now())
         ),
         last_activity_at = greatest(
           coalesce(i.last_activity_at,p_ended_at),
           coalesce(p_ended_at,i.last_activity_at)
         ),
         evidence_window_start = coalesce(
           i.evidence_window_start,
           coalesce(i.incident_started_at,i.occurred_at) - interval '15 seconds'
         ),
         evidence_window_end = greatest(
           coalesce(i.evidence_window_end,'-infinity'::timestamptz),
           coalesce(p_ended_at,i.last_activity_at,now())
             + make_interval(secs=>v_post)
         ),
         detail = coalesce(i.detail,'{}'::jsonb)
           || jsonb_build_object(
                'lifecycle_end_reason',coalesce(p_reason,'activity_ended'))
   where i.id = p_incident_id
     and i.lifecycle_state in ('active','escalated')
  returning * into v;

  if v.id is not null then
    insert into public.operations_incident_lifecycle_events
      (incident_id,tenant_id,event_type,occurred_at,detail)
    values
      (v.id,v.tenant_id,'ended',v.incident_ended_at,
       jsonb_build_object('reason',coalesce(p_reason,'activity_ended')));
  end if;

  return case when v.id is null then null else to_jsonb(v) end;
end $$;

revoke all on function public.wl_end_operations_incident(bigint,timestamptz,int,text)
  from public, anon, authenticated;
grant execute on function public.wl_end_operations_incident(bigint,timestamptz,int,text)
  to service_role;

-- ---------------------------------------------------------------------
-- End stale physical episodes using each rule's own quiet threshold.
-- ---------------------------------------------------------------------
create or replace function public.wl_finalize_stale_operations_incidents(
  p_site_id uuid,
  p_limit int default 100
) returns integer
language plpgsql
volatile
security definer
set search_path = public
as $$
declare v_count int := 0; r record; v_current uuid;
begin
  v_current := public.wl_current_site_agent(p_site_id);
  if v_current is null then return 0; end if;

  for r in
    select i.id, i.last_activity_at,
           coalesce(mr.episode_quiet_seconds,60) as quiet_seconds
      from public.operations_incidents i
      left join public.monitoring_rules mr on mr.id = i.rule_id
      join public.agents a on a.id = v_current and a.site_id = i.site_id
     where i.site_id = p_site_id
       and i.lifecycle_state in ('active','escalated')
       and coalesce(i.last_activity_at,i.opened_at)
           < now() - make_interval(
               secs=>coalesce(mr.episode_quiet_seconds,60))
       -- Critical truth guard: do not interpret silence as "activity ended"
       -- unless the CURRENT Agent remained observable through the entire quiet
       -- interval after the last activity. If the Agent disappeared, coverage
       -- becomes uncertain and the incident remains active until evidence returns.
       and a.last_seen_at >= coalesce(i.last_activity_at,i.opened_at)
                              + make_interval(
                                  secs=>coalesce(mr.episode_quiet_seconds,60))
       and coalesce(a.capabilities,'[]'::jsonb) ? 'operations_runtime'
     order by coalesce(i.last_activity_at,i.opened_at)
     limit least(greatest(coalesce(p_limit,100),1),500)
     for update of i skip locked
  loop
    perform public.wl_end_operations_incident(
      r.id,
      coalesce(r.last_activity_at,now()),
      30,
      'inactivity_timeout'
    );
    v_count := v_count + 1;
  end loop;

  return v_count;
end $$;

revoke all on function public.wl_finalize_stale_operations_incidents(uuid,int)
  from public, anon, authenticated;
grant execute on function public.wl_finalize_stale_operations_incidents(uuid,int)
  to service_role;

-- Server timer entrypoint. Physical incident timing is cloud-authoritative and
-- does not depend on a later detector event. Only sites that explicitly enabled
-- Operations are considered; each site still has to satisfy the current-Agent
-- heartbeat truth guard inside wl_finalize_stale_operations_incidents().
create or replace function public.wl_finalize_all_stale_operations_incidents()
returns integer
language plpgsql
volatile
security definer
set search_path = public
as $$
declare v_total int := 0; v_site uuid; v_n int;
begin
  for v_site in
    select s.id
      from public.sites s
     where coalesce(s.operations_runtime_enabled,false)
  loop
    v_n := public.wl_finalize_stale_operations_incidents(v_site,100);
    v_total := v_total + coalesce(v_n,0);
  end loop;
  return v_total;
end $$;

revoke all on function public.wl_finalize_all_stale_operations_incidents()
  from public, anon, authenticated;
grant execute on function public.wl_finalize_all_stale_operations_incidents()
  to service_role;

-- Supabase production has pg_cron. Keep this conditional so disposable CI
-- Postgres without pg_cron can still apply/test the migration.
do $$
declare v_jobid bigint;
begin
  if exists(select 1 from pg_extension where extname='pg_cron') then
    begin
      select jobid into v_jobid
        from cron.job
       where jobname='watchlog-finalize-operations-incidents'
       order by jobid desc
       limit 1;
      if v_jobid is not null then
        perform cron.unschedule(v_jobid);
      end if;
    exception when undefined_table or invalid_schema_name then
      v_jobid := null;
    end;

    perform cron.schedule(
      'watchlog-finalize-operations-incidents',
      '* * * * *',
      'select public.wl_finalize_all_stale_operations_incidents();'
    );
  end if;
end $$;

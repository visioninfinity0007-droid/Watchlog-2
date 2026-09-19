-- =====================================================================
-- 0097 — Full-incident evidence over the existing bounded clip transport
--
-- No new clip schema and no relaxed limits. WatchLog advances one bounded
-- recorder request at a time across the final incident evidence window.
-- Existing 60-second and 32-MiB request limits remain authoritative.
-- =====================================================================

create or replace function public.wl_advance_operations_incident_evidence(
  p_incident_id bigint
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_inc public.operations_incidents;
  v_rule public.monitoring_rules;
  v_wants_clip boolean := false;
  v_active uuid;
  v_latest public.incident_clip_requests;
  v_next_start timestamptz;
  v_next_end timestamptz;
  v_mid timestamptz;
  v_seconds int;
  v_pre int;
  v_post int;
  v_request uuid;
  v_old_state text;
begin
  select * into v_inc
    from public.operations_incidents
   where id=p_incident_id
   for update;
  if v_inc.id is null then
    return jsonb_build_object('ok',false,'reason','incident_not_found');
  end if;
  if v_inc.lifecycle_state in ('active','escalated')
     or v_inc.evidence_window_end is null then
    return jsonb_build_object('ok',true,'state',v_inc.lifecycle_state,'queued',false);
  end if;

  select * into v_rule from public.monitoring_rules where id=v_inc.rule_id;
  if v_rule.id is not null then
    select exists(
      select 1 from jsonb_array_elements(coalesce(v_rule.actions,'[]'::jsonb)) a
       where a->>'type'='request_footage'
    ) into v_wants_clip;
  end if;

  if not v_wants_clip then
    update public.operations_incidents
       set lifecycle_state='report_ready',
           summary_json=coalesce(summary_json,'{}'::jsonb)
             ||jsonb_build_object('footage_status','not_requested')
     where id=v_inc.id;
    return jsonb_build_object('ok',true,'state','report_ready','queued',false);
  end if;

  if v_inc.camera_id is null
     or not public.wl_site_has_capability(v_inc.site_id,'operations_evidence_clip') then
    update public.operations_incidents
       set lifecycle_state='report_ready',
           summary_json=coalesce(summary_json,'{}'::jsonb)
             ||jsonb_build_object('footage_status','not_available')
     where id=v_inc.id;
    return jsonb_build_object('ok',true,'state','report_ready','queued',false,'reason','clip_capability_not_available');
  end if;

  select r.id into v_active
    from public.incident_clip_requests r
   where r.operations_incident_id=v_inc.id
     and r.status in ('pending','processing')
   order by r.requested_at
   limit 1;
  if v_active is not null then
    update public.operations_incidents set lifecycle_state='evidence_processing' where id=v_inc.id;
    return jsonb_build_object('ok',true,'state','evidence_processing','queued',false,'active_request_id',v_active);
  end if;

  select r.* into v_latest
    from public.incident_clip_requests r
    join public.operations_incident_actions a
      on a.incident_id=v_inc.id
     and a.action_type='request_footage'
     and a.detail->>'window_kind'='incident_window'
     and a.detail->>'clip_request_id'=r.id::text
   where r.operations_incident_id=v_inc.id
   order by r.end_at desc,r.requested_at desc
   limit 1;

  if v_latest.id is not null and v_latest.status in ('failed','unsupported','expired') then
    update public.operations_incidents
       set lifecycle_state='report_ready',
           summary_json=coalesce(summary_json,'{}'::jsonb)
             ||jsonb_build_object('footage_status','incomplete','footage_error',coalesce(v_latest.error_message,v_latest.status))
     where id=v_inc.id;
    return jsonb_build_object('ok',true,'state','report_ready','queued',false,'reason','footage_incomplete');
  end if;

  v_next_start := coalesce(v_latest.end_at,v_inc.evidence_window_start);
  if v_next_start >= v_inc.evidence_window_end then
    v_old_state := v_inc.lifecycle_state;
    update public.operations_incidents
       set lifecycle_state='report_ready',
           summary_json=coalesce(summary_json,'{}'::jsonb)
             ||jsonb_build_object('footage_status','ready')
     where id=v_inc.id;
    if v_old_state is distinct from 'report_ready' then
      insert into public.operations_incident_lifecycle_events
        (incident_id,tenant_id,event_type,occurred_at,detail)
      values(v_inc.id,v_inc.tenant_id,'report_ready',now(),jsonb_build_object('footage_status','ready'));
    end if;
    return jsonb_build_object('ok',true,'state','report_ready','queued',false);
  end if;

  v_next_end := least(v_inc.evidence_window_end,v_next_start+interval '55 seconds');
  v_seconds := greatest(1,floor(extract(epoch from(v_next_end-v_next_start)))::int);
  v_pre := floor(v_seconds/2.0)::int;
  v_post := greatest(1,v_seconds-v_pre);
  v_mid := v_next_start+make_interval(secs=>v_pre);

  v_request := public.wl_create_incident_clip_request(
    v_inc.id,v_inc.camera_id,v_mid,v_pre,v_post
  );
  if v_request is null then
    return jsonb_build_object('ok',true,'state','evidence_processing','queued',false,'reason','request_not_created');
  end if;

  insert into public.operations_incident_actions
    (incident_id,tenant_id,action_type,actor,detail)
  values(
    v_inc.id,v_inc.tenant_id,'request_footage','engine',
    jsonb_build_object(
      'window_kind','incident_window',
      'clip_request_id',v_request,
      'segment_start',v_next_start,
      'segment_end',v_next_end
    )
  );

  update public.operations_incidents
     set lifecycle_state='evidence_processing',
         summary_json=coalesce(summary_json,'{}'::jsonb)
           ||jsonb_build_object('footage_status','retrieving')
   where id=v_inc.id;

  return jsonb_build_object(
    'ok',true,'state','evidence_processing','queued',true,
    'request_id',v_request,'start_at',v_next_start,'end_at',v_next_end
  );
end $$;

revoke all on function public.wl_advance_operations_incident_evidence(bigint)
  from public,anon,authenticated;
grant execute on function public.wl_advance_operations_incident_evidence(bigint)
  to service_role;

create or replace function public.wl_advance_all_operations_incident_evidence()
returns integer
language plpgsql
volatile
security definer
set search_path = public
as $$
declare v_id bigint; v_count int:=0;
begin
  for v_id in
    select i.id
      from public.operations_incidents i
      join public.sites s on s.id=i.site_id
     where coalesce(s.operations_runtime_enabled,false)
       and i.lifecycle_state in ('ended','evidence_processing')
     order by i.incident_ended_at nulls last,i.id
     limit 200
  loop
    perform public.wl_advance_operations_incident_evidence(v_id);
    v_count:=v_count+1;
  end loop;
  return v_count;
end $$;

revoke all on function public.wl_advance_all_operations_incident_evidence()
  from public,anon,authenticated;
grant execute on function public.wl_advance_all_operations_incident_evidence()
  to service_role;

do $$
declare v_jobid bigint;
begin
  if exists(select 1 from pg_extension where extname='pg_cron') then
    begin
      select jobid into v_jobid from cron.job
       where jobname='watchlog-advance-incident-evidence'
       order by jobid desc limit 1;
      if v_jobid is not null then perform cron.unschedule(v_jobid); end if;
    exception when undefined_table or invalid_schema_name then
      v_jobid:=null;
    end;
    perform cron.schedule(
      'watchlog-advance-incident-evidence','* * * * *',
      'select public.wl_advance_all_operations_incident_evidence();'
    );
  end if;
end $$;

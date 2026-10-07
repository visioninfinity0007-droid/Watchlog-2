-- 0163 - Remote full acceptance test + remote maintenance (5.1.2, workstream D).
--
-- STACKED AFTER 0160 (0161/0162 belong to sibling 5.1.2 workstreams).
-- REPO ONLY until separately approved for production.
--
-- 1. wl_site_command_enqueue: the 0150 definition restated verbatim. Only the
--    read catalog grows, by the Agent's remote-maintenance read actions
--    (site_control.MAINTENANCE_ACTIONS, executed by site_maintenance.py):
--      run_full_acceptance_test, run_recording_check, run_archive_check,
--      collect_diagnostics, refresh_inventory, refresh_capabilities,
--      reconnect_recorder, restart_agent.
--    The hard deny-list, the read-tier gate, the tenant check and the
--    recorder routing are unchanged. Every new action is outbound-only on the
--    site (the Agent claims it) and never writes to a recorder.
-- 2. acceptance_runs + a completion trigger: every succeeded
--    run_full_acceptance_test command is recorded with its hardware, summary
--    and per-check verdicts. wl_agent_complete_command keeps its signature.
-- 3. wl_site_run_acceptance(site, recorder default null): owner/admin (or
--    platform staff) enqueue of the full test for the site's current Agent.
-- 4. wl_site_acceptance_result(command): tenant-scoped read of one run.
--
-- No recorder credential, address or media bytes are stored: the Agent's
-- result carries sizes, hashes and redacted error text only.

create or replace function public.wl_site_command_enqueue(
  p_site_id uuid,
  p_action text,
  p_params jsonb default '{}'::jsonb,
  p_tier text default 'read',
  p_created_by text default null
) returns uuid
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_id uuid;
  v_recorder_id uuid;
  v_params jsonb;
begin
  select tenant_id into v_tenant
    from public.sites
   where id=p_site_id;

  if v_tenant is null then
    raise exception 'no such site' using errcode='22023';
  end if;
  if public.wl_platform_role() is null
     and v_tenant is distinct from public.wl_my_tenant()
  then
    raise exception 'not authorised for this site' using errcode='42501';
  end if;

  if p_action ~* '(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser|user_|network_|password|wipe|erase)' then
    raise exception 'prohibited action' using errcode='42501';
  end if;
  if p_tier<>'read' then
    raise exception 'P1 Site Control allows the read tier only'
      using errcode='42501';
  end if;
  -- Exactly the Agent's site_control.READ_ACTIONS (MNVR-069). An action the
  -- Agent cannot run would be claimed and always fail, so it is not accepted.
  if p_action not in (
    'get_recorder_identity','get_channels','get_clock_config',
    'get_video_loss_state','get_analytics_config','get_recording_status',
    'get_storage_status','request_snapshot','inspect_recorder',
    'run_full_acceptance_test','run_recording_check','run_archive_check',
    'collect_diagnostics','refresh_inventory','refresh_capabilities',
    'reconnect_recorder','restart_agent'
  ) then
    raise exception 'action % is not in the read catalog',p_action
      using errcode='42501';
  end if;

  v_params := public.wl_site_command_params(
    v_tenant,p_site_id,p_action,coalesce(p_params,'{}'::jsonb)
  );
  v_recorder_id := public.wl_resolve_site_recorder_target(
    v_tenant,p_site_id,v_params
  );

  insert into public.site_commands(
    tenant_id,site_id,recorder_id,action,params,tier,created_by
  )
  values(
    v_tenant,p_site_id,v_recorder_id,p_action,v_params,'read',
    coalesce(p_created_by,'portal')
  )
  returning id into v_id;

  return v_id;
end
$function$;

revoke all on function public.wl_site_command_enqueue(
  uuid,text,jsonb,text,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_site_command_enqueue(
  uuid,text,jsonb,text,text
) to authenticated,service_role;

-- ---------------------------------------------------------------------
-- Acceptance runs: the durable record of every completed full acceptance
-- test, written when its Site Control command completes as succeeded.
-- A succeeded command means the suite RAN; its verdict (PASS/FAIL/UNKNOWN/
-- UNSUPPORTED per check) is in summary/checks. A failed command (the suite
-- could not run) leaves no row.
-- ---------------------------------------------------------------------
create table if not exists public.acceptance_runs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  -- The recorder the run was asked for; NULL when it covered every
  -- configured recorder of the site (one section per recorder in checks).
  recorder_id   uuid references public.recorders(id) on delete set null,
  command_id    uuid not null unique
                  references public.site_commands(id) on delete cascade,
  agent_id      uuid references public.agents(id) on delete set null,
  agent_version text,
  all_recorders boolean not null default false,
  hardware      jsonb not null default '{}'::jsonb,
  summary       jsonb not null default '{}'::jsonb,
  checks        jsonb not null default '[]'::jsonb,
  created_at    timestamptz not null default now()
);

create index if not exists acceptance_runs_site_created_idx
  on public.acceptance_runs(site_id, created_at desc);

alter table public.acceptance_runs enable row level security;
revoke all on table public.acceptance_runs from public, anon, authenticated;
grant select on table public.acceptance_runs to authenticated;

drop policy if exists portal_read_acceptance_runs on public.acceptance_runs;
create policy portal_read_acceptance_runs on public.acceptance_runs
  for select to authenticated
  using (public.wl_is_member(tenant_id));

-- Populated by a trigger on the command's own completion, so the Agent's
-- completion RPC (wl_agent_complete_command, 0064) keeps its signature and
-- body. The result is the Agent's document (full_acceptance.run_suite); a
-- non-object result or one without a summary object records nothing, and an
-- oversized one keeps its summary but not its per-check list.
create or replace function public.wl_record_acceptance_run()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_result jsonb := new.result_after;
  v_all boolean := lower(coalesce(new.params->>'all_recorders','')) = 'true';
  v_checks jsonb := '[]'::jsonb;
begin
  if v_result is null
     or jsonb_typeof(v_result) <> 'object'
     or jsonb_typeof(v_result->'summary') is distinct from 'object'
  then
    return new;
  end if;

  if jsonb_typeof(v_result->'checks') = 'array'
     and octet_length(v_result::text) <= 1048576
  then
    v_checks := v_result->'checks';
  end if;

  insert into public.acceptance_runs(
    tenant_id,site_id,recorder_id,command_id,agent_id,agent_version,
    all_recorders,hardware,summary,checks
  )
  values(
    new.tenant_id,
    new.site_id,
    case when v_all then null else new.recorder_id end,
    new.id,
    new.agent_id,
    left(v_result#>>'{summary,agent,version}',64),
    v_all,
    case when jsonb_typeof(v_result#>'{summary,hardware}') = 'object'
         then v_result#>'{summary,hardware}' else '{}'::jsonb end,
    v_result->'summary',
    v_checks
  )
  on conflict (command_id) do nothing;

  return new;
end
$function$;

revoke all on function public.wl_record_acceptance_run()
  from public,anon,authenticated,service_role;

drop trigger if exists site_commands_record_acceptance_run on public.site_commands;
create trigger site_commands_record_acceptance_run
  after update of status on public.site_commands
  for each row
  when (
    new.action = 'run_full_acceptance_test'
    and new.status = 'succeeded'
    and old.status is distinct from 'succeeded'
  )
  execute function public.wl_record_acceptance_run();

-- ---------------------------------------------------------------------
-- Owner convenience: run the full acceptance test on a site now.
-- Owner/admin of the site's account (the role held in THAT account, never an
-- account-blind wl_my_role()), or platform staff. p_recorder_id NULL tests
-- every configured recorder (the command is anchored to the primary
-- recorder, because the claim only executes recorder-targeted commands).
-- ---------------------------------------------------------------------
create or replace function public.wl_site_run_acceptance(
  p_site_id uuid,
  p_recorder_id uuid default null
) returns uuid
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_enabled boolean;
  v_role text;
  v_agent uuid;
  v_recorder uuid;
  v_params jsonb;
  v_id uuid;
begin
  select tenant_id, coalesce(site_control_enabled,false)
    into v_tenant, v_enabled
    from public.sites
   where id=p_site_id;

  if v_tenant is null then
    raise exception 'no such site' using errcode='22023';
  end if;

  if public.wl_platform_role() is null then
    if not public.wl_is_member(v_tenant) then
      raise exception 'not authorised for this site' using errcode='42501';
    end if;
    select m.role
      into v_role
      from public.memberships m
     where m.user_id=auth.uid()
       and m.tenant_id=v_tenant;
    if v_role is null or not (v_role = any(array['owner','admin'])) then
      raise exception 'this needs the owner or admin role; you are %',
        coalesce(v_role,'not a member')
        using errcode='42501';
    end if;
  end if;

  if not v_enabled then
    raise exception 'Site Control is not enabled for this site'
      using errcode='55000';
  end if;

  v_agent := public.wl_current_site_agent(p_site_id);
  if v_agent is null then
    raise exception 'this site has no current Site Agent' using errcode='55000';
  end if;

  if p_recorder_id is not null then
    v_recorder := public.wl_resolve_site_recorder_target(
      v_tenant,p_site_id,jsonb_build_object('recorder_id',p_recorder_id::text)
    );
    v_params := jsonb_build_object(
      'recorder_id',v_recorder::text,'all_recorders',false,'include_clip',true
    );
  else
    select r.id
      into v_recorder
      from public.recorders r
     where r.tenant_id=v_tenant
       and r.site_id=p_site_id
       and r.is_configured
     order by r.is_primary desc, r.created_at, r.id
     limit 1;
    if v_recorder is null then
      raise exception 'site has no configured recorder target' using errcode='42501';
    end if;
    v_params := jsonb_build_object('all_recorders',true,'include_clip',true);
  end if;

  insert into public.site_commands(
    tenant_id,site_id,recorder_id,action,params,tier,created_by
  )
  values(
    v_tenant,p_site_id,v_recorder,'run_full_acceptance_test',v_params,'read',
    coalesce('acceptance:'||auth.uid()::text,'acceptance')
  )
  returning id into v_id;

  return v_id;
end
$function$;

revoke all on function public.wl_site_run_acceptance(uuid,uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_site_run_acceptance(uuid,uuid)
  to authenticated,service_role;

-- ---------------------------------------------------------------------
-- Read one acceptance run (tenant-scoped: any member of the site's account,
-- or platform staff). Status comes from the command; the verdict from the
-- recorded run, or from the command result while it is not recorded yet.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_acceptance_result(
  p_command_id uuid
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_cmd public.site_commands;
  v_run public.acceptance_runs;
  v_result jsonb;
begin
  select * into v_cmd from public.site_commands where id=p_command_id;
  if v_cmd.id is null then
    return null;
  end if;
  if public.wl_platform_role() is null
     and not public.wl_is_member(v_cmd.tenant_id)
  then
    raise exception 'not authorised' using errcode='42501';
  end if;
  if v_cmd.action <> 'run_full_acceptance_test' then
    raise exception 'not an acceptance test command' using errcode='22023';
  end if;

  select * into v_run from public.acceptance_runs where command_id=p_command_id;
  v_result := case when jsonb_typeof(v_cmd.result_after) = 'object'
                   then v_cmd.result_after else null end;

  return jsonb_build_object(
    'command_id', v_cmd.id,
    'site_id', v_cmd.site_id,
    'recorder_id', v_cmd.recorder_id,
    'all_recorders', lower(coalesce(v_cmd.params->>'all_recorders','')) = 'true',
    'status', case when v_cmd.status = 'queued' and v_cmd.expires_at <= now()
                   then 'expired' else v_cmd.status end,
    'error', v_cmd.error,
    'created_at', v_cmd.created_at,
    'claimed_at', v_cmd.claimed_at,
    'completed_at', v_cmd.completed_at,
    'expires_at', v_cmd.expires_at,
    'recorded', v_run.id is not null,
    'agent_version', coalesce(v_run.agent_version, v_result#>>'{summary,agent,version}'),
    'summary', coalesce(v_run.summary, v_result->'summary'),
    'checks', coalesce(v_run.checks, v_result->'checks'),
    'recorders', v_result->'recorders'
  );
end
$function$;

revoke all on function public.wl_site_acceptance_result(uuid)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_site_acceptance_result(uuid)
  to authenticated,service_role;

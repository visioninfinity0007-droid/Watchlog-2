-- 0150 - Multi-recorder camera/job routing.
--
-- STACKED AFTER 0149. REPO ONLY until separately approved for production.
--
-- This migration closes the deterministic routing gap for any cloud job that
-- reaches back to a physical recorder:
--   * incident footage;
--   * incident stills;
--   * configuration snapshot requests;
--   * archive scans;
--   * Site Control commands.
--
-- Rule: camera_id -> cameras.recorder_id is authoritative. Recorder-level
-- commands carry an explicit recorder_id. A multi-recorder site never falls
-- back to "the site's recorder" or chooses a recorder by channel number.
--
-- Existing singleton callers remain compatible: where a command has no
-- explicit target, exactly one configured recorder may be resolved. More than
-- one configured recorder fails closed.
--
-- No recorder credentials or local recorder addresses are added to cloud jobs.

-- ---------------------------------------------------------------------
-- Generic deterministic command-target resolver.
-- Owner-only: public wrappers authenticate/authorize before using it.
-- ---------------------------------------------------------------------
create or replace function public.wl_resolve_site_recorder_target(
  p_tenant_id uuid,
  p_site_id uuid,
  p_params jsonb default '{}'::jsonb
) returns uuid
language plpgsql
stable
set search_path = public
as $function$
declare
  v_params jsonb := coalesce(p_params,'{}'::jsonb);
  v_camera_text text := nullif(v_params->>'camera_id','');
  v_recorder_text text := nullif(v_params->>'recorder_id','');
  v_camera_id uuid;
  v_requested_recorder uuid;
  v_camera_recorder uuid;
  v_count int;
  v_only uuid;
begin
  if p_tenant_id is null or p_site_id is null then
    raise exception 'tenant/site target required' using errcode='22023';
  end if;

  if v_camera_text is not null then
    v_camera_id := public.wl_try_uuid(v_camera_text);
    if v_camera_id is null then
      raise exception 'invalid camera_id target' using errcode='22023';
    end if;

    select c.recorder_id
      into v_camera_recorder
      from public.cameras c
     where c.id=v_camera_id
       and c.tenant_id=p_tenant_id
       and c.site_id=p_site_id;

    if v_camera_recorder is null then
      raise exception 'camera target is not in this site' using errcode='42501';
    end if;
  end if;

  if v_recorder_text is not null then
    v_requested_recorder := public.wl_try_uuid(v_recorder_text);
    if v_requested_recorder is null then
      raise exception 'invalid recorder_id target' using errcode='22023';
    end if;

    if not exists (
      select 1
        from public.recorders r
       where r.id=v_requested_recorder
         and r.tenant_id=p_tenant_id
         and r.site_id=p_site_id
         and r.is_configured
    ) then
      raise exception 'recorder target is not configured for this site'
        using errcode='42501';
    end if;
  end if;

  if v_camera_recorder is not null
     and v_requested_recorder is not null
     and v_camera_recorder is distinct from v_requested_recorder
  then
    raise exception 'camera and recorder targets do not match'
      using errcode='42501';
  end if;

  if v_camera_recorder is not null then
    return v_camera_recorder;
  end if;
  if v_requested_recorder is not null then
    return v_requested_recorder;
  end if;

  select count(*),(array_agg(r.id order by r.id))[1]
    into v_count,v_only
    from public.recorders r
   where r.tenant_id=p_tenant_id
     and r.site_id=p_site_id
     and r.is_configured;

  if v_count=1 then
    return v_only;
  end if;
  if v_count=0 then
    raise exception 'site has no configured recorder target'
      using errcode='42501';
  end if;

  raise exception 'recorder target required for multi-recorder site'
    using errcode='42501';
end
$function$;

revoke all on function public.wl_resolve_site_recorder_target(
  uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

-- Canonicalize a camera-bound command's channel when camera_id is supplied.
-- This prevents a valid camera UUID paired with another recorder's channel.
create or replace function public.wl_site_command_params(
  p_tenant_id uuid,
  p_site_id uuid,
  p_action text,
  p_params jsonb
) returns jsonb
language plpgsql
stable
set search_path = public
as $function$
declare
  v_params jsonb := coalesce(p_params,'{}'::jsonb);
  v_camera_text text := nullif(v_params->>'camera_id','');
  v_camera_id uuid;
  v_channel text;
begin
  if v_camera_text is null then
    return v_params;
  end if;

  v_camera_id := public.wl_try_uuid(v_camera_text);
  if v_camera_id is null then
    raise exception 'invalid camera_id target' using errcode='22023';
  end if;

  select c.channel
    into v_channel
    from public.cameras c
   where c.id=v_camera_id
     and c.tenant_id=p_tenant_id
     and c.site_id=p_site_id;

  if v_channel is null then
    raise exception 'camera target is not in this site' using errcode='42501';
  end if;

  if nullif(v_params->>'channel','') is not null
     and (v_params->>'channel') is distinct from v_channel
  then
    raise exception 'camera_id and channel target do not match'
      using errcode='42501';
  end if;

  -- Camera-targeted actions consume the canonical camera channel.
  if p_action in ('request_snapshot','rename_channel','configure_smd') then
    v_params := jsonb_set(v_params,'{channel}',to_jsonb(v_channel),true);
  end if;

  return v_params;
end
$function$;

revoke all on function public.wl_site_command_params(
  uuid,uuid,text,jsonb
) from public,anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- Site Control commands become explicitly recorder-targeted.
-- ---------------------------------------------------------------------
alter table public.site_commands
  add column if not exists recorder_id uuid;

alter table public.site_commands
  drop constraint if exists site_commands_recorder_lineage_fkey;

alter table public.site_commands
  add constraint site_commands_recorder_lineage_fkey
  foreign key (recorder_id,tenant_id,site_id)
  references public.recorders(id,tenant_id,site_id)
  on delete restrict;

create index if not exists site_commands_recorder_status_idx
  on public.site_commands(recorder_id,status,created_at)
  where recorder_id is not null;

-- Historical/queued singleton commands get deterministic provenance where
-- exactly one configured recorder exists. Ambiguous historical rows stay NULL;
-- the claim RPC below never executes a NULL-target command.
update public.site_commands c
   set recorder_id=x.recorder_id
  from (
    select tenant_id,site_id,(array_agg(id order by id))[1] as recorder_id
      from public.recorders
     where is_configured
     group by tenant_id,site_id
    having count(*)=1
  ) x
 where c.recorder_id is null
   and c.tenant_id=x.tenant_id
   and c.site_id=x.site_id;

-- Managed pre-authorization names the recorder it was given for (MNVR-049).
-- An authorization given while only Recorder A existed must not auto-run a
-- write on a Recorder B added later. Existing rows on a site with exactly one
-- configured recorder are scoped to it; any other row stays NULL and never
-- auto-queues a write (the proposal then waits for human approval).
alter table public.site_managed_actions
  add column if not exists recorder_id uuid
    references public.recorders(id) on delete cascade;

alter table public.site_managed_actions
  drop constraint if exists site_managed_actions_site_id_action_key;

create unique index if not exists site_managed_actions_site_recorder_action_idx
  on public.site_managed_actions(site_id,recorder_id,action);

update public.site_managed_actions m
   set recorder_id=x.recorder_id
  from (
    select site_id,(array_agg(id order by id))[1] as recorder_id
      from public.recorders
     where is_configured
     group by site_id
    having count(*)=1
  ) x
 where m.recorder_id is null
   and m.site_id=x.site_id;

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
    'get_storage_status','request_snapshot','inspect_recorder'
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

create or replace function public.wl_site_command_propose_write(
  p_site_id uuid,
  p_action text,
  p_params jsonb default '{}'::jsonb,
  p_mode text default 'recommend',
  p_created_by text default null,
  p_reason text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_recorder public.recorders;
  v_cap text;
  v_capjson jsonb;
  v_status text;
  v_id uuid;
  v_params jsonb;
begin
  select tenant_id into v_tenant
    from public.sites
   where id=p_site_id;

  if v_tenant is null then
    raise exception 'no such site' using errcode='22023';
  end if;
  if public.wl_platform_role() is null then
    if v_tenant is distinct from public.wl_my_tenant() then
      raise exception 'not authorised for this site' using errcode='42501';
    end if;
    -- The recommend tier wl_my_site_diagnosis advertises is enforced here:
    -- a viewer cannot propose a recorder write (MNVR-050).
    perform public.wl_require_role(array['owner','admin','manager']);
  end if;

  if p_action ~* '(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser|user_|network_|password|wipe|erase)' then
    raise exception 'prohibited action' using errcode='42501';
  end if;
  if p_mode not in ('recommend','managed') then
    raise exception 'mode must be recommend or managed' using errcode='22023';
  end if;

  v_cap := case p_action
    when 'rename_channel' then 'channel_title'
    when 'configure_smd' then 'human_vehicle_classification'
    when 'configure_time' then 'time_ntp_config'
    else null
  end;
  if v_cap is null then
    raise exception 'action % is not in the safe-write catalog',p_action
      using errcode='42501';
  end if;

  v_params := public.wl_site_command_params(
    v_tenant,p_site_id,p_action,coalesce(p_params,'{}'::jsonb)
  );

  select *
    into v_recorder
    from public.recorders r
   where r.id=public.wl_resolve_site_recorder_target(
     v_tenant,p_site_id,v_params
   )
     and r.tenant_id=v_tenant
     and r.site_id=p_site_id
     and r.is_configured;

  if v_recorder.id is null
     or nullif(v_recorder.vendor,'') is null
     or nullif(v_recorder.model,'') is null
  then
    raise exception 'target recorder identity unknown; cannot verify a safe write'
      using errcode='42501';
  end if;

  -- FIELD_VERIFIED must come from evidence taken on THIS recorder (same
  -- identity and firmware), never from another unit of the same model.
  v_capjson := public.wl_recorder_capability_for_recorder(
    v_recorder.id,
    v_cap
  );

  if not (
    v_capjson->>'verdict'='supported'
    and (v_capjson->>'write')::boolean is true
    and v_capjson->>'safety_class'='safe_write'
    and v_capjson->>'evidence_class'='FIELD_VERIFIED'
    and (v_capjson->>'field_write_verified')::boolean is true
  ) then
    raise exception
      'action % is not a FIELD-VERIFIED safe write for target recorder % (%)',
      p_action,v_recorder.model,v_cap
      using errcode='42501';
  end if;

  if p_mode='managed'
     and exists (
       select 1
         from public.site_managed_actions m
        where m.site_id=p_site_id
          and m.recorder_id=v_recorder.id
          and m.action=p_action
     )
  then
    v_status := 'queued';
  else
    v_status := 'proposed';
  end if;

  insert into public.site_commands(
    tenant_id,site_id,recorder_id,action,params,tier,status,created_by,detail
  )
  values(
    v_tenant,p_site_id,v_recorder.id,p_action,v_params,'managed',v_status,
    coalesce(p_created_by,'ai'),
    jsonb_build_object(
      'mode',p_mode,
      'reason',p_reason,
      'capability',v_cap,
      'recorder',v_recorder.model,
      'evidence',v_capjson->>'evidence_class'
    )
  )
  returning id into v_id;

  return jsonb_build_object(
    'id',v_id,
    'status',v_status,
    'evidence',v_capjson->>'evidence_class'
  );
end
$function$;

revoke all on function public.wl_site_command_propose_write(
  uuid,text,jsonb,text,text,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_site_command_propose_write(
  uuid,text,jsonb,text,text,text
) to authenticated,service_role;

-- Approving makes a proposed recorder write executable. The approve tier
-- wl_my_site_diagnosis advertises (owner/admin) is enforced here, not only in
-- the portal (MNVR-050). Platform staff keep their existing access.
create or replace function public.wl_site_command_approve(
  p_command_id uuid,
  p_approved_by text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_cmd public.site_commands;
  v_capjson jsonb;
begin
  select *
    into v_cmd
    from public.site_commands
   where id=p_command_id
   for update;

  if v_cmd.id is null then
    raise exception 'no such command' using errcode='22023';
  end if;
  if public.wl_platform_role() is null then
    if v_cmd.tenant_id is distinct from public.wl_my_tenant() then
      raise exception 'not authorised' using errcode='42501';
    end if;
    perform public.wl_require_role(array['owner','admin']);
  end if;

  if v_cmd.status<>'proposed' then
    return jsonb_build_object(
      'ok',false,'reason','not_proposed','status',v_cmd.status
    );
  end if;

  -- Re-check write eligibility now (MNVR-049): the target recorder's
  -- identity, firmware or evidence may have changed since the proposal, and a
  -- proposal without a recorder target is never approvable.
  if v_cmd.recorder_id is not null then
    v_capjson := public.wl_recorder_capability_for_recorder(
      v_cmd.recorder_id,
      v_cmd.detail->>'capability'
    );
  end if;
  if v_capjson is null
     or not (
       v_capjson->>'verdict'='supported'
       and (v_capjson->>'write')::boolean is true
       and v_capjson->>'safety_class'='safe_write'
       and v_capjson->>'evidence_class'='FIELD_VERIFIED'
       and (v_capjson->>'field_write_verified')::boolean is true
     )
  then
    raise exception
      'action % is no longer a FIELD-VERIFIED safe write for its target recorder',
      v_cmd.action
      using errcode='42501';
  end if;

  update public.site_commands
     set status='queued',
         detail=coalesce(detail,'{}'::jsonb)
                || jsonb_build_object(
                     'approved_by',coalesce(p_approved_by,'operator')
                   )
   where id=p_command_id;

  return jsonb_build_object('ok',true,'status','queued');
end
$function$;

revoke all on function public.wl_site_command_approve(
  uuid,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_site_command_approve(
  uuid,text
) to authenticated;

create or replace function public.wl_agent_claim_command(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_enabled boolean := false;
  v_current uuid;
  v_cmd public.site_commands;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  select coalesce(site_control_enabled,false)
    into v_enabled
    from public.sites
   where id=v_agent.site_id;

  if not v_enabled then
    return jsonb_build_object('command',null,'enabled',false);
  end if;

  v_current := public.wl_current_site_agent(v_agent.site_id);
  if v_current is distinct from v_agent.id then
    return jsonb_build_object(
      'command',null,'enabled',true,'authority',false,
      'current_agent_id',v_current
    );
  end if;

  update public.site_commands c
     set status='claimed',
         claimed_at=now(),
         agent_id=v_agent.id,
         lease_fence=coalesce(c.lease_fence,0)+1
   where c.id=(
     select q.id
       from public.site_commands q
       join public.recorders r
         on r.id=q.recorder_id
        and r.tenant_id=v_agent.tenant_id
        and r.site_id=v_agent.site_id
        and r.is_configured
      where q.site_id=v_agent.site_id
        and q.tenant_id=v_agent.tenant_id
        and q.status='queued'
        and q.expires_at>now()
      order by q.created_at
      for update of q skip locked
      limit 1
   )
  returning * into v_cmd;

  if v_cmd.id is null then
    return jsonb_build_object(
      'command',null,'enabled',true,'authority',true
    );
  end if;

  return jsonb_build_object(
    'enabled',true,
    'authority',true,
    'command',jsonb_build_object(
      'id',v_cmd.id,
      'recorder_id',v_cmd.recorder_id,
      'action',v_cmd.action,
      'params',v_cmd.params,
      'tier',v_cmd.tier,
      'lease_fence',v_cmd.lease_fence
    )
  );
end
$function$;

revoke all on function public.wl_agent_claim_command(
  uuid,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_command(
  uuid,text
) to anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- Incident footage/still claims now return the recorder owning camera_id.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_clip_requests(
  p_agent_id uuid,
  p_agent_key text,
  p_limit integer default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),2);
  v_result jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  delete from public.incident_clip_chunks c
   using public.incident_clip_requests r
   where c.request_id=r.id
     and r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.expires_at<=now();

  update public.incident_clip_requests
     set status='expired'
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and expires_at<=now()
     and status in ('pending','processing','ready');

  with picked as (
    select r.id
      from public.incident_clip_requests r
      join public.cameras c
        on c.id=r.camera_id
       and c.tenant_id=v_agent.tenant_id
       and c.site_id=v_agent.site_id
       and c.recorder_id is not null
     where r.site_id=v_agent.site_id
       and r.tenant_id=v_agent.tenant_id
       and r.status='pending'
       and r.expires_at>now()
     order by r.requested_at
     for update of r skip locked
     limit v_limit
  ),
  claimed as (
    update public.incident_clip_requests r
       set status='processing',
           claimed_by_agent_id=v_agent.id,
           started_at=now(),
           error_message=null
      from picked p
     where r.id=p.id
    returning r.*
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'request_id',r.id,
        'event_id',r.event_id,
        'camera_id',r.camera_id,
        'recorder_id',c.recorder_id,
        'channel',c.channel,
        'start_at',r.start_at,
        'end_at',r.end_at
      )
      order by r.requested_at
    ),
    '[]'::jsonb
  )
  into v_result
  from claimed r
  join public.cameras c
    on c.id=r.camera_id
   and c.tenant_id=v_agent.tenant_id
   and c.site_id=v_agent.site_id
   and c.recorder_id is not null;

  return v_result;
end
$function$;

revoke all on function public.wl_agent_claim_clip_requests(
  uuid,text,integer
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_clip_requests(
  uuid,text,integer
) to anon,authenticated,service_role;

create or replace function public.wl_agent_claim_incident_stills(
  p_agent_id uuid,
  p_agent_key text,
  p_limit integer default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),3);
  v_result jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  update public.operations_incident_evidence
     set status='expired'
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and expires_at<=now()
     and status in ('pending','processing','ready');

  with picked as (
    select e.id
      from public.operations_incident_evidence e
      join public.cameras c
        on c.id=e.camera_id
       and c.tenant_id=v_agent.tenant_id
       and c.site_id=v_agent.site_id
       and c.recorder_id is not null
     where e.site_id=v_agent.site_id
       and e.tenant_id=v_agent.tenant_id
       and e.expires_at>now()
       and (
         e.status='pending'
         or (
           e.status='processing'
           and coalesce(e.claim_expires_at,now())<=now()
         )
       )
     order by e.requested_at
     for update of e skip locked
     limit v_limit
  ),
  claimed as (
    update public.operations_incident_evidence e
       set status='processing',
           claimed_by_agent_id=v_agent.id,
           claim_expires_at=now()+interval '5 minutes',
           attempts=e.attempts+1,
           error_message=null
      from picked p
     where e.id=p.id
    returning e.*
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'request_id',e.id,
        'incident_id',e.incident_id,
        'camera_id',e.camera_id,
        'recorder_id',c.recorder_id,
        'channel',c.channel,
        'occurred_at',e.occurred_at,
        'purpose',e.purpose
      )
      order by e.requested_at
    ),
    '[]'::jsonb
  )
  into v_result
  from claimed e
  join public.cameras c
    on c.id=e.camera_id
   and c.tenant_id=v_agent.tenant_id
   and c.site_id=v_agent.site_id
   and c.recorder_id is not null;

  return v_result;
end
$function$;

revoke all on function public.wl_agent_claim_incident_stills(
  uuid,text,integer
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_incident_stills(
  uuid,text,integer
) to anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- Analytics/config snapshot work includes recorder identity end-to-end.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_analytics_config(
  p_agent_id uuid,
  p_agent_key text,
  p_known_version bigint default 0
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_version bigint;
  v_multi boolean;
  v_config jsonb;
  v_requests jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  select analytics_config_version,coalesce(multi_agent_enabled,false)
    into v_version,v_multi
    from public.sites
   where id=v_agent.site_id;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'request_id',cr.id,
        'camera_id',cr.camera_id,
        'recorder_id',c.recorder_id,
        'channel',c.channel
      )
      order by cr.requested_at
    ),
    '[]'::jsonb
  )
  into v_requests
  from public.camera_snapshot_requests cr
  join public.cameras c
    on c.id=cr.camera_id
   and c.tenant_id=v_agent.tenant_id
   and c.site_id=v_agent.site_id
   and c.recorder_id is not null
  where cr.site_id=v_agent.site_id
    and cr.tenant_id=v_agent.tenant_id
    and cr.completed_at is null
    and coalesce(c.is_canonical,true);

  if coalesce(p_known_version,0)=v_version then
    return jsonb_build_object(
      'version',v_version,
      'changed',false,
      'multi_agent_enabled',v_multi,
      'snapshot_requests',v_requests
    );
  end if;

  select jsonb_build_object(
    'site_id',s.id,
    'site_type',s.site_type,
    'timezone',s.timezone,
    'version',s.analytics_config_version,
    'multi_agent_enabled',coalesce(s.multi_agent_enabled,false),
    'schedules',coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id',ms.id,
          'name',ms.name,
          'timezone',ms.timezone,
          'schedule',ms.schedule_json,
          'enabled',ms.enabled
        )
      )
      from public.monitoring_schedules ms
      where ms.site_id=s.id and ms.enabled
    ),'[]'::jsonb),
    'cameras',coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id',c.id,
          'recorder_id',c.recorder_id,
          'channel',c.channel,
          'name',c.name,
          'purpose',c.purpose,
          'analytics_enabled',c.analytics_enabled,
          'rules',coalesce((
            select jsonb_agg(
              jsonb_build_object(
                'id',r.id,
                'analytic_key',r.analytic_key,
                'name',r.name,
                'rule_type',r.rule_type,
                'object_classes',r.object_classes,
                'geometry',r.geometry_json,
                'direction',r.direction_json,
                'schedule_id',r.schedule_id,
                'dwell_seconds',r.dwell_seconds,
                'sample_seconds',r.sample_seconds,
                'severity',r.severity,
                'promote_incident',r.promote_incident,
                'occupancy_min',r.occupancy_min,
                'occupancy_max',r.occupancy_max,
                'confidence_min',r.confidence_min,
                'cooldown_seconds',r.cooldown_seconds,
                'actions',coalesce(r.actions,'[]'::jsonb),
                'evidence_json',coalesce(r.evidence_json,'{}'::jsonb),
                'sensitive',coalesce(r.sensitive,false),
                'review_required',coalesce(r.review_required,false),
                'rule_version',r.rule_version
              )
            )
            from public.monitoring_rules r
            where r.camera_id=c.id
              and r.enabled
              and r.rule_type<>'health'
          ),'[]'::jsonb)
        )
        order by
          c.recorder_id,
          case when c.channel~'^[0-9]+$' then c.channel::int
               else 2147483647 end,
          c.channel
      )
      from public.cameras c
      where c.site_id=s.id
        and c.tenant_id=v_agent.tenant_id
        and c.analytics_enabled
        and coalesce(c.is_canonical,true)
    ),'[]'::jsonb)
  )
  into v_config
  from public.sites s
  where s.id=v_agent.site_id
    and s.tenant_id=v_agent.tenant_id;

  return jsonb_build_object(
    'version',v_version,
    'changed',true,
    'multi_agent_enabled',v_multi,
    'config',v_config,
    'snapshot_requests',v_requests
  );
end
$function$;

revoke all on function public.wl_agent_analytics_config(
  uuid,text,bigint
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_analytics_config(
  uuid,text,bigint
) to anon,authenticated,service_role;

create or replace function public.wl_upload_config_snapshot(
  p_agent_id uuid,
  p_agent_key text,
  p_camera_id uuid,
  p_image_b64 text,
  p_content_type text default 'image/jpeg'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_cam public.cameras;
  v_img bytea;
  v_request_id uuid;
  v_request_source text;
  v_role text;
  v_ingest jsonb;
  v_now timestamptz := now();
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  select *
    into v_cam
    from public.cameras
   where id=p_camera_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and recorder_id is not null;

  if v_cam.id is null then
    raise exception 'camera not in this site' using errcode='42501';
  end if;

  select q.id,q.request_source
    into v_request_id,v_request_source
    from public.camera_snapshot_requests q
   where q.camera_id=v_cam.id
     and q.tenant_id=v_agent.tenant_id
     and q.site_id=v_agent.site_id
     and q.completed_at is null
   order by q.requested_at
   limit 1;

  begin
    v_img := decode(p_image_b64,'base64');
  exception when others then
    raise exception 'invalid snapshot encoding' using errcode='22023';
  end;

  if octet_length(v_img) not between 1 and 3145728 then
    raise exception 'snapshot size invalid';
  end if;

  insert into public.camera_config_snapshots(
    camera_id,tenant_id,site_id,image,bytes,content_type,captured_at
  )
  values(
    v_cam.id,v_agent.tenant_id,v_agent.site_id,v_img,octet_length(v_img),
    coalesce(nullif(p_content_type,''),'image/jpeg'),v_now
  )
  on conflict(camera_id) do update
     set image=excluded.image,
         bytes=excluded.bytes,
         content_type=excluded.content_type,
         captured_at=excluded.captured_at;

  if v_request_source='restaurant_analytics'
     and v_request_id is not null
  then
    select analytics_role
      into v_role
      from public.restaurant_camera_profiles
     where camera_id=v_cam.id
       and site_id=v_agent.site_id
       and enabled
     limit 1;

    if v_role is not null then
      v_ingest := public.wl_ingest_events(
        p_agent_id,
        p_agent_key,
        jsonb_build_array(
          jsonb_build_object(
            'recorder_id',v_cam.recorder_id,
            'channel',v_cam.channel,
            'event_type','visual_sample',
            'device_event_id','restaurant-request-'||v_request_id::text,
            'device_ts',v_now,
            'agent_ts',v_now,
            'payload',jsonb_build_object(
              'sample',true,
              'source','restaurant_requested_snapshot',
              'restaurant_role',v_role,
              'request_id',v_request_id
            ),
            'snapshot_b64',p_image_b64
          )
        )
      );
    end if;
  end if;

  update public.camera_snapshot_requests
     set completed_at=v_now
   where camera_id=v_cam.id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and completed_at is null;

  return jsonb_build_object(
    'ok',true,
    'bytes',octet_length(v_img),
    'recorder_id',v_cam.recorder_id,
    'request_source',coalesce(v_request_source,'manual'),
    'ingested',coalesce(v_ingest,'{}'::jsonb)
  );
end
$function$;

revoke all on function public.wl_upload_config_snapshot(
  uuid,text,uuid,text,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_upload_config_snapshot(
  uuid,text,uuid,text,text
) to anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- Archive scans remain one owner request, but the Agent receives explicit
-- camera -> recorder -> channel targets and can partition work locally.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_archive_scans(
  p_agent_id uuid,
  p_agent_key text,
  p_limit integer default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),4);
  v_result jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  with picked as (
    select s.id
      from public.archive_scans s
     where s.site_id=v_agent.site_id
       and s.tenant_id=v_agent.tenant_id
       and s.status='requested'
       and not exists (
         select 1
           from unnest(s.camera_ids) cid
          where not exists (
            select 1
              from public.cameras c
             where c.id=cid
               and c.tenant_id=v_agent.tenant_id
               and c.site_id=v_agent.site_id
               and c.recorder_id is not null
          )
       )
     order by s.requested_at
     for update skip locked
     limit v_limit
  ),
  claimed as (
    update public.archive_scans s
       set status='analyzing',
           started_at=coalesce(s.started_at,now())
      from picked p
     where s.id=p.id
    returning s.*
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'scan_id',c.id,
        'camera_ids',c.camera_ids,
        'camera_targets',coalesce((
          select jsonb_agg(
            jsonb_build_object(
              'camera_id',cam.id,
              'recorder_id',cam.recorder_id,
              'channel',cam.channel
            )
            order by cam.recorder_id,cam.channel,cam.id
          )
          from public.cameras cam
          where cam.id=any(c.camera_ids)
            and cam.tenant_id=v_agent.tenant_id
            and cam.site_id=v_agent.site_id
        ),'[]'::jsonb),
        'rule_ids',c.rule_ids,
        'from_ts',c.from_ts,
        'to_ts',c.to_ts
      )
      order by c.requested_at
    ),
    '[]'::jsonb
  )
  into v_result
  from claimed c;

  return v_result;
end
$function$;

revoke all on function public.wl_agent_claim_archive_scans(
  uuid,text,integer
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_archive_scans(
  uuid,text,integer
) to anon,authenticated,service_role;

-- The analytics camera payload now contains recorder_id. Force one governed
-- config refresh so already-installed Agents do not keep a pre-0150 cached
-- channel-only camera map indefinitely.
update public.sites s
   set analytics_config_version=coalesce(s.analytics_config_version,0)+1
 where exists (
   select 1 from public.recorders r
    where r.site_id=s.id
      and r.tenant_id=s.tenant_id
      and r.is_configured
 );

-- ---------------------------------------------------------------------
-- Contract v2: worker fan-out must not activate unless deterministic
-- recorder job routing is present too.
-- ---------------------------------------------------------------------
create or replace function public.wl_multi_recorder_agent_contract(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  return jsonb_build_object(
    'ok',true,
    'version',2,
    'configured_recorders',(
      select count(*)
        from public.recorders r
       where r.tenant_id=v_agent.tenant_id
         and r.site_id=v_agent.site_id
         and r.is_configured
    ),
    'features',jsonb_build_array(
      'recorders',
      'recorder_cameras',
      'recorder_events',
      'recorder_health',
      'recorder_recovery',
      'recorder_reconciliation',
      'recorder_capabilities',
      'recorder_job_routing'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(
  uuid,text
) from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(
  uuid,text
) to anon;

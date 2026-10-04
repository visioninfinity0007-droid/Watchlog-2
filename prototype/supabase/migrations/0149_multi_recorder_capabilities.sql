-- 0149 - Recorder-scoped capability truth.
--
-- STACKED AFTER 0148. REPO ONLY until separately approved.
--
-- The legacy capability RPC remains the compatibility path for a true
-- single-recorder site. Multi-recorder Agents use the explicit recorder RPC.
-- Recorder capability sync never fabricates or unions a site-wide capability:
-- one recorder proving a feature does not prove every recorder/camera at the site.

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
    'version',1,
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
      'recorder_capabilities'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(uuid,text)
  to anon;

create or replace function public.wl_overlay_recorder_camera_truth(
  p_site_id uuid,
  p_recorder_id uuid,
  p_capabilities jsonb
) returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  select case
    when p_capabilities is null then null
    when jsonb_typeof(p_capabilities) <> 'object' then p_capabilities
    when jsonb_typeof(p_capabilities->'channels') <> 'array' then p_capabilities
    else jsonb_set(
      p_capabilities,
      '{channels}',
      coalesce((
        select jsonb_agg(
          case
            when c.id is null then
              q.item || jsonb_build_object(
                'configuration_state','unknown',
                'configured',null
              )
            else
              q.item || jsonb_build_object(
                'camera_id',c.id,
                'name',c.name,
                'configured',c.is_configured,
                'configuration_state',
                  case when c.is_configured then 'configured'
                       else 'no_camera_configured' end
              )
          end
          order by q.ord
        )
        from jsonb_array_elements(p_capabilities->'channels')
             with ordinality as q(item,ord)
        left join public.cameras c
          on c.site_id=p_site_id
         and c.recorder_id=p_recorder_id
         and c.channel=q.item->>'channel'
      ),'[]'::jsonb),
      true
    )
  end
$function$;

revoke all on function public.wl_overlay_recorder_camera_truth(
  uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

create or replace function public.wl_sync_recorder_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_effective jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  if not exists (
    select 1
      from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  v_effective := public.wl_overlay_recorder_camera_truth(
    v_agent.site_id,p_recorder_id,p_capabilities
  );

  update public.recorders
     set capabilities=v_effective,
         capabilities_at=now(),
         updated_at=now()
   where id=p_recorder_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id;

  update public.agents
     set last_seen_at=now()
   where id=v_agent.id;

  return jsonb_build_object(
    'ok',true,
    'recorder_id',p_recorder_id,
    'channels',case
      when jsonb_typeof(v_effective->'channels')='array'
      then jsonb_array_length(v_effective->'channels')
      else 0
    end,
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_sync_recorder_capabilities(
  uuid,text,uuid,jsonb
) from public,anon,authenticated,service_role;
grant execute on function public.wl_sync_recorder_capabilities(
  uuid,text,uuid,jsonb
) to anon;

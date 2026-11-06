-- 0209 — Site Control can ask a recorder which camera view sizes it can give (read only).
--
-- 6 Nov 2026: camera views arrive at 352 x 288 (Al-Khalid, Dahua XVR) and 704 x 576 (HASCO, Hikvision
-- NVR) although the cameras record far more. Agent 5.1.12 adds the READ action probe_view_quality
-- (site_control.READ_ACTIONS): it measures the still sizes per camera and, with apply=true, switches the
-- site to sharp views only when a larger still was proven. It never changes the recorder. The read
-- catalog below must equal the Agent's READ_ACTIONS (test_site_control_catalog_parity).

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
    'probe_view_quality',
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

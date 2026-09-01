-- =====================================================================
-- 0025 — Analytics bootstrap from the first-run installer.
--
-- The installer can classify the site/cameras before the agent has a cloud
-- identity. After enrollment, the agent sends that local bootstrap ONCE.
-- The server still derives tenant/site from the agent secret and only allows
-- channels already synced for that site.
-- =====================================================================

create or replace function public.wl_agent_bootstrap_analytics(
  p_agent_id uuid,
  p_agent_key text,
  p_site_type text,
  p_camera_profiles jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_changed int := 0;
  v_profile jsonb;
  v_purpose text;
  v_version bigint;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if wl_analytics_valid_site_type(coalesce(p_site_type,'custom')) then
    update sites set site_type=coalesce(p_site_type,'custom') where id=v_agent.site_id;
  end if;

  for v_profile in select * from jsonb_array_elements(coalesce(p_camera_profiles,'[]'::jsonb))
  loop
    v_purpose := coalesce(nullif(v_profile->>'purpose',''),'custom');
    if not wl_analytics_valid_purpose(v_purpose) then
      v_purpose := 'custom';
    end if;
    update cameras
       set name=coalesce(nullif(trim(v_profile->>'name'),''),name),
           purpose=v_purpose,
           analytics_enabled=coalesce((v_profile->>'analytics_enabled')::boolean,true)
     where site_id=v_agent.site_id
       and channel=v_profile->>'channel';
    if found then v_changed := v_changed + 1; end if;
  end loop;

  v_version := wl_analytics_bump_site(v_agent.site_id);
  return jsonb_build_object('ok',true,'updated_cameras',v_changed,'version',v_version);
end $$;

revoke all on function public.wl_agent_bootstrap_analytics(uuid,text,text,jsonb) from public;
grant execute on function public.wl_agent_bootstrap_analytics(uuid,text,text,jsonb) to anon,authenticated;

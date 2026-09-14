-- =====================================================================
-- 0105 - Agent-authed camera (re)configuration for the Site Status panel
--
-- Post-install "Configure Cameras" (0.4.4 P1.2) must let the operator flip a
-- channel Monitor <-> Ignore and rename it WITHOUT a reinstall, from the Site
-- Status app (which authenticates as the agent, not a portal user).
--
-- wl_sync_cameras deliberately never flips is_configured on conflict; this is the
-- explicit mutation surface that DOES. Flipping is_configured drives the 0085
-- cameras_configuration_truth trigger: Monitor->Ignore resolves faults + marks the
-- slot disabled; Ignore->Monitor brings it back into monitoring from a clean
-- UNKNOWN state. Read/replace only; no schema change.
-- =====================================================================

create or replace function public.wl_agent_set_camera_configured(
  p_agent_id uuid,
  p_agent_key text,
  p_channel text,
  p_configured boolean,
  p_name text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_row   cameras;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  update cameras c
     set is_configured = coalesce(p_configured, c.is_configured),
         name = case when nullif(trim(coalesce(p_name, '')), '') is not null
                     then trim(p_name) else c.name end,
         analytics_enabled = case when coalesce(p_configured, c.is_configured)
                                  then c.analytics_enabled else false end
   where c.tenant_id = v_agent.tenant_id
     and c.site_id   = v_agent.site_id
     and c.channel   = p_channel
  returning * into v_row;

  if v_row.id is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_channel');
  end if;

  return jsonb_build_object('ok', true, 'camera_id', v_row.id, 'channel', v_row.channel,
    'is_configured', v_row.is_configured, 'name', v_row.name);
end $$;

revoke all on function public.wl_agent_set_camera_configured(uuid,text,text,boolean,text) from public;
grant execute on function public.wl_agent_set_camera_configured(uuid,text,text,boolean,text) to anon, authenticated;

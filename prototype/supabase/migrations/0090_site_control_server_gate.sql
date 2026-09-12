-- =====================================================================
-- 0090 — Site Control server-side authority gate
--
-- The original Agent required a local INI switch before it would poll Site
-- Control. That makes a remotely managed product depend on hand-editing the
-- customer PC. The final product may poll outbound-only by default, but the
-- SERVER remains the authority: a site must be explicitly enabled before an
-- Agent can claim any command.
--
-- Default is FALSE and existing sites remain disabled. This migration changes
-- no recorder setting and executes no command.
-- =====================================================================

alter table public.sites
  add column if not exists site_control_enabled boolean not null default false;

create or replace function public.wl_agent_claim_command(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_enabled boolean := false;
  v_current uuid;
  v_cmd site_commands;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  select coalesce(site_control_enabled, false)
    into v_enabled
    from public.sites where id = v_agent.site_id;

  if not v_enabled then
    return jsonb_build_object('command', null, 'enabled', false);
  end if;

  -- Only the current/fenced site authority can execute recorder commands.
  v_current := wl_current_site_agent(v_agent.site_id);
  if v_current is distinct from v_agent.id then
    return jsonb_build_object('command', null, 'enabled', true,
                              'authority', false,
                              'current_agent_id', v_current);
  end if;

  update public.site_commands c
     set status = 'claimed',
         claimed_at = now(),
         agent_id = v_agent.id,
         lease_fence = coalesce(c.lease_fence, 0) + 1
   where c.id = (
     select q.id
       from public.site_commands q
      where q.site_id = v_agent.site_id
        and q.status = 'queued'
        and q.expires_at > now()
      order by q.created_at
      for update skip locked
      limit 1)
  returning * into v_cmd;

  if v_cmd.id is null then
    return jsonb_build_object('command', null, 'enabled', true,
                              'authority', true);
  end if;

  return jsonb_build_object(
    'enabled', true,
    'authority', true,
    'command', jsonb_build_object(
      'id', v_cmd.id,
      'action', v_cmd.action,
      'params', v_cmd.params,
      'tier', v_cmd.tier,
      'lease_fence', v_cmd.lease_fence));
end $$;

create or replace function public.wl_site_control_set_enabled(
  p_site_id uuid,
  p_enabled boolean
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v_role text;
begin
  select tenant_id into v_tenant from public.sites where id = p_site_id;
  if v_tenant is null then
    raise exception 'no such site' using errcode = '22023';
  end if;

  v_role := wl_platform_role();
  if v_role is null then
    if v_tenant is distinct from wl_my_tenant() then
      raise exception 'not authorised for this site' using errcode = '42501';
    end if;
    perform wl_require_role(array['owner','admin']);
  end if;

  -- Enabling requires a current compatible Agent. Disabling is always allowed.
  if coalesce(p_enabled, false) and not exists (
    select 1
      from public.agents a
     where a.id = wl_current_site_agent(p_site_id)
       and 'operations_runtime' = any(coalesce(a.capabilities,'{}'::text[]))
  ) then
    raise exception 'current agent does not advertise the required control runtime'
      using errcode = '42501';
  end if;

  update public.sites
     set site_control_enabled = coalesce(p_enabled, false)
   where id = p_site_id;

  return jsonb_build_object('ok', true, 'site_id', p_site_id,
                            'site_control_enabled', coalesce(p_enabled, false));
end $$;

revoke all on function public.wl_site_control_set_enabled(uuid,boolean) from public;
grant execute on function public.wl_site_control_set_enabled(uuid,boolean) to authenticated;

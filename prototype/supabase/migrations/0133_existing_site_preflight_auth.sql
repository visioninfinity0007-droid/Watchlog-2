-- Read-only authentication probe for the staged existing-site Repair/Upgrade flow.
-- It proves that the machine's existing encrypted agent identity is still accepted
-- by production without heartbeating a staged version or claiming any queued work.
create or replace function public.wl_agent_preflight_auth(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare
  v_agent agents;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  return jsonb_build_object(
    'agent_id', v_agent.id,
    'site_id', v_agent.site_id,
    'tenant_id', v_agent.tenant_id,
    'agent_version', v_agent.agent_version,
    'last_seen_at', v_agent.last_seen_at
  );
end
$$;

revoke all on function public.wl_agent_preflight_auth(uuid,text) from public;
grant execute on function public.wl_agent_preflight_auth(uuid,text) to anon, authenticated;

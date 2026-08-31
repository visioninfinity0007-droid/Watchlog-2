-- =====================================================================
-- Store what analytics each site's recorder supports, so the portal can
-- show "your recorder supports motion, human/vehicle, line-crossing...
-- these are on, these you could enable".
--
-- The agent reports this (read-only from the device) after it syncs
-- cameras. It is descriptive only — nothing here changes a device.
-- =====================================================================

alter table public.sites
  add column if not exists capabilities jsonb,
  add column if not exists capabilities_at timestamptz;

-- Agent-authenticated, like the rest of the agent API.
create or replace function public.wl_sync_capabilities(
  p_agent_id uuid, p_agent_key text, p_capabilities jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_agent agents;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  update sites
     set capabilities = p_capabilities, capabilities_at = now()
   where id = v_agent.site_id;
  return jsonb_build_object('ok', true,
    'channels', coalesce(jsonb_array_length(p_capabilities->'channels'), 0));
end $$;

-- Portal read: the caller's tenant's sites and their reported analytics.
create or replace function public.wl_capabilities()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'site', s.name, 'site_id', s.id,
             'reported_at', s.capabilities_at,
             'capabilities', s.capabilities)
             order by s.name)
      from sites s
     where s.tenant_id = v_tenant and s.capabilities is not null), '[]'::jsonb);
end $$;

revoke all on function public.wl_capabilities() from public, anon;
grant execute on function public.wl_capabilities() to authenticated;
grant execute on function public.wl_sync_capabilities(uuid, text, jsonb) to anon, authenticated;

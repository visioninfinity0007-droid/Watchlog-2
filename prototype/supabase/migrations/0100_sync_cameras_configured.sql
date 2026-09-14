-- =====================================================================
-- 0100 - Setup-time camera configuration: honor per-camera is_configured
--
-- The agent's wl_sync_cameras discovers channels; at setup the operator now
-- chooses Monitor / Ignore per channel (0.4.4 P4). Honor that decision on the
-- FIRST insert of a channel so a monitored camera is immediately is_configured
-- (and thus health-monitored) and an intentionally unused channel stays
-- is_configured = false (and never generates a false health/fault warning).
--
-- Backward-compatible: when a caller does not send is_configured the value
-- defaults to false, exactly as before. ON CONFLICT does NOT change
-- is_configured -- the operator / system-of-record decision (portal
-- wl_set_camera_configured) stands, and a routine re-sync never flips it.
-- Read/replace only; no schema change, no data migration.
-- =====================================================================

create or replace function public.wl_sync_cameras(
  p_agent_id uuid,
  p_agent_key text,
  p_cameras jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_out   jsonb := '{}'::jsonb;
  v_row   record;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  insert into cameras (tenant_id, site_id, channel, name, is_configured)
  select v_agent.tenant_id, v_agent.site_id,
         c->>'channel',
         coalesce(nullif(c->>'name',''), 'Channel ' || (c->>'channel')),
         coalesce((c->>'is_configured')::boolean, false)   -- honor Monitor/Ignore on first insert
    from jsonb_array_elements(coalesce(p_cameras, '[]'::jsonb)) c
   where coalesce(c->>'channel','') <> ''
  on conflict (site_id, channel) do update
     set name = case when cameras.is_configured then cameras.name else excluded.name end;
     -- is_configured intentionally NOT touched on conflict: the operator decision is authoritative.

  for v_row in
    select channel, id from cameras where site_id = v_agent.site_id
  loop
    v_out := v_out || jsonb_build_object(v_row.channel, v_row.id);
  end loop;

  return v_out;
end $$;

revoke all on function public.wl_sync_cameras(uuid,text,jsonb) from public;
grant execute on function public.wl_sync_cameras(uuid,text,jsonb) to anon, authenticated;

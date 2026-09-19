-- =====================================================================
-- 0063 — Site Control command plane, READ tier (H6, P1).
--
-- The AI/Portal never touch the recorder or its credential. They enqueue a structured,
-- capability-gated command; the authoritative Site Agent claims it, runs it locally
-- against the recorder, and returns a verified result:
--   AI / Portal -> wl_site_command_enqueue -> site_commands -> Agent (claim) ->
--   local recorder read -> Agent (complete) -> wl_site_command_result -> caller.
--
-- P1 is strictly READ. The enqueue gate enforces tier='read', a fixed read-action
-- catalog, AND a hard deny-list (firmware/format/factory/reset/reboot/user/network/
-- password/delete) that no tier may ever express. Safe writes are a separate migration.
-- =====================================================================

create table if not exists public.site_commands (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  agent_id      uuid references public.agents(id) on delete set null,
  action        text not null,
  params        jsonb not null default '{}'::jsonb,
  tier          text not null default 'read'
                  check (tier in ('read','recommend','managed')),
  status        text not null default 'queued'
                  check (status in ('queued','claimed','executing','verifying',
                                    'succeeded','failed','rolled_back','expired')),
  lease_fence   bigint,
  result_before jsonb,
  result_after  jsonb,
  verified      boolean,
  rollback_token text,
  error         text,
  created_by    text,
  created_at    timestamptz not null default now(),
  claimed_at    timestamptz,
  completed_at  timestamptz,
  expires_at    timestamptz not null default now() + interval '10 minutes'
);
create index if not exists site_commands_claim_idx
  on public.site_commands (site_id, status, created_at);
alter table public.site_commands enable row level security;

-- ---------------------------------------------------------------------
-- Enqueue (portal / AI). Guarded: platform staff or a member of the site's tenant;
-- hard deny-list; P1 read catalog only.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_command_enqueue(
  p_site_id   uuid,
  p_action    text,
  p_params    jsonb default '{}'::jsonb,
  p_tier      text default 'read',
  p_created_by text default null
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v_id     uuid;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null then
    raise exception 'no such site' using errcode = '22023';
  end if;
  if wl_platform_role() is null and v_tenant is distinct from wl_my_tenant() then
    raise exception 'not authorised for this site' using errcode = '42501';
  end if;
  -- Hard deny-list: never expressible by the control plane, any tier, any caller.
  if p_action ~* '(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser|user_|network_|password|wipe|erase)' then
    raise exception 'prohibited action' using errcode = '42501';
  end if;
  if p_tier <> 'read' then
    raise exception 'P1 Site Control allows the read tier only' using errcode = '42501';
  end if;
  if p_action not in (
      'get_recorder_identity','get_channels','get_clock_config','get_video_loss_state',
      'get_analytics_config','get_recording_status','get_storage_status','request_snapshot',
      'inspect_recorder','get_configuration_drift','get_recorder_capabilities') then
    raise exception 'action % is not in the read catalog', p_action using errcode = '42501';
  end if;
  insert into site_commands (tenant_id, site_id, action, params, tier, created_by)
    values (v_tenant, p_site_id, p_action, coalesce(p_params, '{}'::jsonb), 'read',
            coalesce(p_created_by, 'portal'))
    returning id into v_id;
  return v_id;
end
$$;
revoke all on function public.wl_site_command_enqueue(uuid,text,jsonb,text,text) from public, anon;
grant execute on function public.wl_site_command_enqueue(uuid,text,jsonb,text,text) to authenticated;

-- ---------------------------------------------------------------------
-- Agent claims the next queued command for ITS site (fenced, skip-locked).
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_command(
  p_agent_id uuid, p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_cmd   site_commands;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  update site_commands c
     set status = 'claimed', claimed_at = now(), agent_id = v_agent.id,
         lease_fence = coalesce(c.lease_fence, 0) + 1
   where c.id = (select q.id from site_commands q
                  where q.site_id = v_agent.site_id and q.status = 'queued'
                    and q.expires_at > now()
                  order by q.created_at
                  for update skip locked
                  limit 1)
   returning * into v_cmd;
  if v_cmd.id is null then
    return jsonb_build_object('command', null);
  end if;
  return jsonb_build_object('command', jsonb_build_object(
    'id', v_cmd.id, 'action', v_cmd.action, 'params', v_cmd.params,
    'tier', v_cmd.tier, 'lease_fence', v_cmd.lease_fence));
end
$$;
revoke all on function public.wl_agent_claim_command(uuid,text) from public;
grant execute on function public.wl_agent_claim_command(uuid,text) to anon, authenticated;

-- ---------------------------------------------------------------------
-- Agent reports the result of a command it claimed.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_complete_command(
  p_agent_id uuid, p_agent_key text, p_command_id uuid,
  p_status text, p_result jsonb default null, p_error text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_rows  int;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  if p_status not in ('succeeded','failed') then
    raise exception 'bad status' using errcode = '22023';
  end if;
  update site_commands c
     set status = p_status, result_after = p_result, error = p_error, completed_at = now()
   where c.id = p_command_id and c.agent_id = v_agent.id
     and c.status in ('claimed','executing','verifying');
  get diagnostics v_rows = row_count;
  if v_rows = 0 then
    return jsonb_build_object('ok', false, 'reason', 'not_claimed_by_this_agent');
  end if;
  return jsonb_build_object('ok', true);
end
$$;
revoke all on function public.wl_agent_complete_command(uuid,text,uuid,text,jsonb,text) from public;
grant execute on function public.wl_agent_complete_command(uuid,text,uuid,text,jsonb,text) to anon, authenticated;

-- ---------------------------------------------------------------------
-- Read a command's result (portal / AI). Tenant-scoped.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_command_result(
  p_command_id uuid
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_cmd site_commands;
begin
  select * into v_cmd from site_commands where id = p_command_id;
  if v_cmd.id is null then
    return null;
  end if;
  if wl_platform_role() is null and v_cmd.tenant_id is distinct from wl_my_tenant() then
    raise exception 'not authorised' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'id', v_cmd.id, 'action', v_cmd.action, 'status', v_cmd.status,
    'result', v_cmd.result_after, 'error', v_cmd.error,
    'created_at', v_cmd.created_at, 'completed_at', v_cmd.completed_at);
end
$$;
revoke all on function public.wl_site_command_result(uuid) from public, anon;
grant execute on function public.wl_site_command_result(uuid) to authenticated;

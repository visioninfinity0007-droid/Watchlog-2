-- =====================================================================
-- 0064 — Site Control SAFE-WRITE plane + AI permission model (items 4-5).
--
-- Writes are proposed through wl_site_command_propose_write, which enforces, in order:
--   1. authorization  — platform staff or a member of the site's tenant;
--   2. hard deny-list — firmware/format/factory/reset/reboot/user/network/password/wipe;
--   3. safe-write catalog — only rename_channel / configure_smd / configure_time;
--   4. HARDWARE-PROVEN gate — the recorder capability model (0061) must record the action's
--      capability as verdict=supported, write=true, safety_class=safe_write FOR THIS recorder
--      model; otherwise the write is refused (never assume a vendor-wide capability).
--
-- AI permission model:
--   READ      — 0063 (inspect only).
--   RECOMMEND — propose with a reason; lands 'proposed' and requires human approval to run.
--   MANAGED   — auto-runs ONLY if the exact action is in the site's managed-allow policy
--               (site_managed_actions); otherwise it also lands 'proposed'. Default: nothing
--               auto-runs — a capability is never self-approving.
--
-- The agent then executes it transactionally (read->backup->diff->apply->read-back->verify;
-- rollback on failure) and reports before/after/verified — the full audit lives on the
-- site_commands row (requester, mode, reason, approver, before, after, verified, rollback).
-- =====================================================================

alter table public.site_commands add column if not exists detail jsonb;

-- Recommend-mode writes land 'proposed' (await human approval); widen the 0063 status check.
alter table public.site_commands drop constraint if exists site_commands_status_check;
alter table public.site_commands add constraint site_commands_status_check
  check (status in ('proposed','queued','claimed','executing','verifying',
                    'succeeded','failed','rolled_back','expired'));

create table if not exists public.site_managed_actions (
  id         bigint generated always as identity primary key,
  site_id    uuid not null references public.sites(id) on delete cascade,
  action     text not null,
  allowed_by text,
  created_at timestamptz not null default now(),
  unique (site_id, action)
);
alter table public.site_managed_actions enable row level security;

-- ---------------------------------------------------------------------
-- Propose a safe write (RECOMMEND or MANAGED). Capability-gated per recorder model.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_command_propose_write(
  p_site_id    uuid,
  p_action     text,
  p_params     jsonb default '{}'::jsonb,
  p_mode       text default 'recommend',
  p_created_by text default null,
  p_reason     text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant  uuid;
  v_vendor  text;
  v_model   text;
  v_cap     text;
  v_capjson jsonb;
  v_status  text;
  v_id      uuid;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null then
    raise exception 'no such site' using errcode = '22023';
  end if;
  if wl_platform_role() is null and v_tenant is distinct from wl_my_tenant() then
    raise exception 'not authorised for this site' using errcode = '42501';
  end if;
  if p_action ~* '(firmware|format|factory|reset|reboot|deleterec|delete_rec|adduser|user_|network_|password|wipe|erase)' then
    raise exception 'prohibited action' using errcode = '42501';
  end if;
  if p_mode not in ('recommend','managed') then
    raise exception 'mode must be recommend or managed' using errcode = '22023';
  end if;

  v_cap := case p_action
             when 'rename_channel'  then 'channel_title'
             when 'configure_smd'   then 'human_vehicle_classification'
             when 'configure_time'  then 'time_ntp_config'
             else null end;
  if v_cap is null then
    raise exception 'action % is not in the safe-write catalog', p_action using errcode = '42501';
  end if;

  -- Resolve the site's actual recorder from the reporting agent, then gate on its capability.
  select a.device_vendor, a.device_model into v_vendor, v_model
    from agents a
   where a.site_id = p_site_id and a.device_model is not null
   order by a.last_seen_at desc nulls last
   limit 1;
  if v_model is null then
    raise exception 'recorder model unknown for this site; cannot verify a safe write'
      using errcode = '42501';
  end if;
  v_capjson := wl_recorder_capability(coalesce(v_vendor,'Dahua'), v_model, v_cap);
  if not (v_capjson->>'verdict' = 'supported'
          and (v_capjson->>'write')::boolean is true
          and v_capjson->>'safety_class' = 'safe_write') then
    raise exception 'action % is not a hardware-proven safe write for % (%): %',
      p_action, v_model, v_cap, v_capjson->>'verdict' using errcode = '42501';
  end if;

  -- MANAGED auto-runs only if this exact action is in the site's managed-allow policy.
  if p_mode = 'managed'
     and exists (select 1 from site_managed_actions m
                  where m.site_id = p_site_id and m.action = p_action) then
    v_status := 'queued';
  else
    v_status := 'proposed';
  end if;

  insert into site_commands (tenant_id, site_id, action, params, tier, status, created_by, detail)
    values (v_tenant, p_site_id, p_action, coalesce(p_params, '{}'::jsonb), 'managed', v_status,
            coalesce(p_created_by, 'ai'),
            jsonb_build_object('mode', p_mode, 'reason', p_reason,
                               'capability', v_cap, 'recorder', v_model,
                               'evidence', v_capjson->>'evidence_class'))
    returning id into v_id;
  return jsonb_build_object('id', v_id, 'status', v_status,
                            'evidence', v_capjson->>'evidence_class');
end
$$;
revoke all on function public.wl_site_command_propose_write(uuid,text,jsonb,text,text,text) from public, anon;
grant execute on function public.wl_site_command_propose_write(uuid,text,jsonb,text,text,text) to authenticated;

-- ---------------------------------------------------------------------
-- Human approves a proposed write (RECOMMEND -> apply). Tenant/platform scoped.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_command_approve(
  p_command_id uuid, p_approved_by text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_cmd site_commands;
begin
  select * into v_cmd from site_commands where id = p_command_id;
  if v_cmd.id is null then
    raise exception 'no such command' using errcode = '22023';
  end if;
  if wl_platform_role() is null and v_cmd.tenant_id is distinct from wl_my_tenant() then
    raise exception 'not authorised' using errcode = '42501';
  end if;
  if v_cmd.status <> 'proposed' then
    return jsonb_build_object('ok', false, 'reason', 'not_proposed', 'status', v_cmd.status);
  end if;
  update site_commands
     set status = 'queued',
         detail = coalesce(detail, '{}'::jsonb)
                  || jsonb_build_object('approved_by', coalesce(p_approved_by, 'operator'))
   where id = p_command_id;
  return jsonb_build_object('ok', true, 'status', 'queued');
end
$$;
revoke all on function public.wl_site_command_approve(uuid,text) from public, anon;
grant execute on function public.wl_site_command_approve(uuid,text) to authenticated;

-- ---------------------------------------------------------------------
-- Extend complete to fold a write result's before/verified into the audit columns.
-- (0063 body verbatim + verified/result_before/rollback_token population.)
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
     set status = p_status, result_after = p_result, error = p_error, completed_at = now(),
         verified = coalesce((p_result->>'verified')::boolean, c.verified),
         result_before = coalesce(p_result->'before', c.result_before),
         rollback_token = coalesce(p_result->>'rollback_token', c.rollback_token)
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

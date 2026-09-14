-- =====================================================================
-- 0104 — WatchLog AI runtime guardrails
--
-- Keeps conversational history bound to one site and adds server-only usage
-- accounting/rate limiting so a valid customer session cannot create unbounded
-- model spend. Direct table access remains disabled.
-- =====================================================================

create table if not exists public.ai_usage_events (
  id          bigint generated always as identity primary key,
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  user_id     uuid not null,
  created_at  timestamptz not null default now(),
  prompt_chars int not null check (prompt_chars between 0 and 12000)
);
create index if not exists ai_usage_events_user_time_idx
  on public.ai_usage_events(user_id, created_at desc);
create index if not exists ai_usage_events_tenant_time_idx
  on public.ai_usage_events(tenant_id, created_at desc);
alter table public.ai_usage_events enable row level security;
revoke all on table public.ai_usage_events from public, anon, authenticated;

-- Tenant/user-guarded conversation metadata lookup used by the Edge Function to
-- reject a conversation from Site A when the request is currently scoped to Site B.
create or replace function public.wl_ai_conversation_context(p_conversation_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_row public.ai_conversations;
begin
  if v_tenant is null or auth.uid() is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;
  select * into v_row
    from public.ai_conversations
   where id=p_conversation_id
     and tenant_id=v_tenant
     and user_id=auth.uid();
  if v_row.id is null then
    raise exception 'conversation not found' using errcode='42501';
  end if;
  return jsonb_build_object(
    'id',v_row.id,'site_id',v_row.site_id,'title',v_row.title,
    'created_at',v_row.created_at,'updated_at',v_row.updated_at
  );
end $$;
revoke all on function public.wl_ai_conversation_context(uuid) from public, anon;
grant execute on function public.wl_ai_conversation_context(uuid) to authenticated, service_role;

-- Server-only usage gate. The service role must supply a user/tenant pair that
-- actually exists in memberships. The event is recorded only when the request is
-- admitted, so rejected traffic cannot fill the ledger itself.
create or replace function public.wl_ai_record_usage(
  p_user_id uuid,
  p_tenant_id uuid,
  p_prompt_chars int,
  p_minute_limit int default 20,
  p_daily_limit int default 500
) returns jsonb
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  v_minute int;
  v_day int;
  v_minute_limit int := least(greatest(coalesce(p_minute_limit,20),1),120);
  v_daily_limit int := least(greatest(coalesce(p_daily_limit,500),10),5000);
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  if p_user_id is null or p_tenant_id is null
     or p_prompt_chars is null or p_prompt_chars < 0 or p_prompt_chars > 12000 then
    raise exception 'invalid usage request' using errcode='22023';
  end if;
  if not exists(
    select 1 from public.memberships m
     where m.user_id=p_user_id and m.tenant_id=p_tenant_id
  ) then
    raise exception 'user is not a member of tenant' using errcode='42501';
  end if;

  select count(*) into v_minute
    from public.ai_usage_events
   where user_id=p_user_id and created_at >= now()-interval '1 minute';
  select count(*) into v_day
    from public.ai_usage_events
   where user_id=p_user_id and created_at >= now()-interval '24 hours';

  if v_minute >= v_minute_limit then
    return jsonb_build_object('ok',false,'reason','minute_limit','retry_after_seconds',60);
  end if;
  if v_day >= v_daily_limit then
    return jsonb_build_object('ok',false,'reason','daily_limit','retry_after_seconds',3600);
  end if;

  insert into public.ai_usage_events(tenant_id,user_id,prompt_chars)
  values(p_tenant_id,p_user_id,p_prompt_chars);

  return jsonb_build_object(
    'ok',true,
    'minute_used',v_minute+1,'minute_limit',v_minute_limit,
    'daily_used',v_day+1,'daily_limit',v_daily_limit
  );
end $$;
revoke all on function public.wl_ai_record_usage(uuid,uuid,int,int,int)
  from public, anon, authenticated;
grant execute on function public.wl_ai_record_usage(uuid,uuid,int,int,int)
  to service_role;

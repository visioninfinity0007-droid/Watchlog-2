-- =====================================================================
-- 0038 - Customer account lifecycle
--
-- Commercial subscription state and administrative account access are not
-- the same thing. This adds an explicit active/suspended lifecycle so WatchLog
-- can temporarily suspend customer portal/reporting access without rewriting
-- memberships, deleting data or pretending a subscription was cancelled.
-- =====================================================================

alter table public.tenants
  add column if not exists account_status text not null default 'active';

alter table public.tenants
  drop constraint if exists tenants_account_status_check;
alter table public.tenants
  add constraint tenants_account_status_check
  check (account_status in ('active','suspended'));

-- Customer membership remains present while suspended, but ordinary tenant
-- RLS and tenant-scoped RPCs fail closed until a platform operator reactivates
-- the account.
create or replace function public.wl_is_member(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
      from memberships m
      join tenants t on t.id=m.tenant_id
     where m.tenant_id=p_tenant
       and m.user_id=auth.uid()
       and t.account_status='active'
  )
$$;
revoke all on function public.wl_is_member(uuid) from public, anon;
grant execute on function public.wl_is_member(uuid) to authenticated;

create or replace function public.wl_my_tenant()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select m.tenant_id
    from memberships m
    join tenants t on t.id=m.tenant_id
   where m.user_id=auth.uid()
     and t.account_status='active'
   order by m.created_at
   limit 1
$$;
revoke all on function public.wl_my_tenant() from public, anon;
grant execute on function public.wl_my_tenant() to authenticated;

-- Routing/status helper deliberately returns only minimal account identity.
-- It can report a suspended membership without unlocking tenant data.
create or replace function public.wl_my_account()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_user uuid:=auth.uid(); v_tenant uuid; v_name text; v_status text;
begin
  if v_user is null then return null; end if;
  select t.id,t.name,t.account_status into v_tenant,v_name,v_status
    from memberships m join tenants t on t.id=m.tenant_id
   where m.user_id=v_user order by m.created_at limit 1;
  if v_tenant is null then return null; end if;
  return jsonb_build_object('tenant_id',v_tenant,'name',v_name,'account_status',v_status);
end $$;
revoke all on function public.wl_my_account() from public, anon;
grant execute on function public.wl_my_account() to authenticated;

-- Reporting stops for an administratively suspended customer but data is not
-- deleted and the underlying commercial status is preserved.
create or replace function public.wl_reporting_enabled(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select case
    when t.account_status <> 'active' then false
    when t.subscription_status = 'active'   then true
    when t.subscription_status = 'past_due' then true
    when t.subscription_status = 'trialing'
      then (t.trial_started_at + make_interval(days => t.trial_days)) > now()
    else false
  end
  from tenants t where t.id = p_tenant
$$;
revoke all on function public.wl_reporting_enabled(uuid) from public, anon;
grant execute on function public.wl_reporting_enabled(uuid) to authenticated;

-- Platform lifecycle directory is intentionally narrow so customer lists can
-- display administrative access state without duplicating the large 0029
-- customer summary function.
create or replace function public.wl_platform_customer_lifecycles()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text:=wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  return jsonb_build_object(
    'role',v_role,
    'items',coalesce((select jsonb_agg(jsonb_build_object(
      'tenant_id',t.id,'account_status',t.account_status
    ) order by t.created_at desc) from tenants t),'[]'::jsonb)
  );
end $$;
revoke all on function public.wl_platform_customer_lifecycles() from public, anon;
grant execute on function public.wl_platform_customer_lifecycles() to authenticated;

create or replace function public.wl_platform_customer_lifecycle(p_tenant_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text:=wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  return (
    select jsonb_build_object(
      'role',v_role,
      'tenant_id',t.id,
      'account_status',t.account_status,
      'subscription_status',t.subscription_status,
      'plan',t.plan
    ) from tenants t where t.id=p_tenant_id
  );
end $$;
revoke all on function public.wl_platform_customer_lifecycle(uuid) from public, anon;
grant execute on function public.wl_platform_customer_lifecycle(uuid) to authenticated;

create or replace function public.wl_platform_set_account_status(
  p_tenant_id uuid,
  p_status text,
  p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text:=wl_platform_require(array['platform_owner','platform_admin']); v_before jsonb; v_after jsonb;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if p_status not in ('active','suspended') then raise exception 'invalid account status'; end if;
  if length(btrim(coalesce(p_reason,''))) < 4 then raise exception 'an audit reason is required'; end if;
  select jsonb_build_object('account_status',account_status) into v_before from tenants where id=p_tenant_id;
  if (v_before->>'account_status')=p_status then
    return v_before || jsonb_build_object('tenant_id',p_tenant_id,'role',v_role,'unchanged',true);
  end if;
  update tenants set account_status=p_status where id=p_tenant_id;
  select jsonb_build_object('account_status',account_status) into v_after from tenants where id=p_tenant_id;
  perform wl_platform_write_audit(
    p_tenant_id,
    case when p_status='suspended' then 'customer_account_suspended' else 'customer_account_reactivated' end,
    p_reason,
    v_before,
    v_after
  );
  return v_after || jsonb_build_object('tenant_id',p_tenant_id,'role',v_role,'unchanged',false);
end $$;
revoke all on function public.wl_platform_set_account_status(uuid,text,text) from public, anon;
grant execute on function public.wl_platform_set_account_status(uuid,text,text) to authenticated;

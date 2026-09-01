-- =====================================================================
-- 0029 - WatchLog platform administration control plane
--
-- A platform admin is NOT a tenant role. Tenant owner/admin/viewer remains
-- scoped to one customer account. Platform roles are explicit, separate,
-- and every cross-tenant write is audited with actor + reason.
--
-- Roles:
--   platform_owner   commercial overrides + platform-admin management
--   platform_admin   tenant operations + trial management
--   platform_support read-only cross-tenant support visibility
--
-- No browser role receives direct table access. All access is through RPCs.
-- There is deliberately no tenant deletion and no silent impersonation.
-- The first platform_owner is bootstrapped once by a DB owner using:
--
--   insert into public.platform_admins(user_id, role)
--   select id, 'platform_owner' from auth.users where lower(email)=lower('...');
-- =====================================================================

create table if not exists public.platform_admins (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  role        text not null check (role in ('platform_owner','platform_admin','platform_support')),
  created_at  timestamptz not null default now(),
  created_by  uuid references auth.users(id) on delete set null
);

create table if not exists public.platform_admin_audit (
  id            bigint generated always as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  actor_role    text,
  tenant_id     uuid references public.tenants(id) on delete set null,
  action        text not null,
  reason        text not null,
  before_json   jsonb,
  after_json    jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists platform_admin_audit_tenant_idx
  on public.platform_admin_audit(tenant_id, created_at desc);
create index if not exists platform_admin_audit_actor_idx
  on public.platform_admin_audit(actor_user_id, created_at desc);

alter table public.platform_admins enable row level security;
alter table public.platform_admin_audit enable row level security;
revoke all on public.platform_admins from anon, authenticated;
revoke all on public.platform_admin_audit from anon, authenticated;

-- ---------------------------------------------------------------------
-- Internal authorization helpers
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select pa.role from platform_admins pa where pa.user_id = auth.uid()
$$;
revoke all on function public.wl_platform_role() from public,anon,authenticated;

create or replace function public.wl_platform_require(p_roles text[])
returns text
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_role();
begin
  if v_role is null or not (v_role = any(p_roles)) then
    raise exception 'platform administrator access required' using errcode='42501';
  end if;
  return v_role;
end $$;
revoke all on function public.wl_platform_require(text[]) from public,anon,authenticated;

create or replace function public.wl_platform_write_audit(
  p_tenant uuid, p_action text, p_reason text, p_before jsonb default null, p_after jsonb default null
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_role();
begin
  if v_role is null then raise exception 'platform administrator access required' using errcode='42501'; end if;
  if length(btrim(coalesce(p_reason,''))) < 4 then
    raise exception 'an audit reason is required';
  end if;
  insert into platform_admin_audit(actor_user_id,actor_role,tenant_id,action,reason,before_json,after_json)
  values(auth.uid(),v_role,p_tenant,p_action,btrim(p_reason),p_before,p_after);
end $$;
revoke all on function public.wl_platform_write_audit(uuid,text,text,jsonb,jsonb) from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- Current admin identity. Safe for every authenticated user: normal tenant
-- users simply receive null and cannot call any other platform RPC.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_me()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_role(); v_email text; v_tenant uuid;
begin
  if v_role is null then return null; end if;
  select email into v_email from auth.users where id=auth.uid();
  select tenant_id into v_tenant from memberships where user_id=auth.uid() order by created_at limit 1;
  return jsonb_build_object('user_id',auth.uid(),'email',v_email,'role',v_role,
                            'tenant_id',v_tenant,'has_tenant',v_tenant is not null);
end $$;
revoke all on function public.wl_platform_me() from public,anon;
grant execute on function public.wl_platform_me() to authenticated;

-- ---------------------------------------------------------------------
-- Platform overview
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_overview()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  return jsonb_build_object(
    'role',v_role,
    'tenants',jsonb_build_object(
      'total',(select count(*) from tenants),
      'trialing',(select count(*) from tenants where subscription_status='trialing'),
      'active',(select count(*) from tenants where subscription_status='active'),
      'past_due',(select count(*) from tenants where subscription_status='past_due'),
      'inactive',(select count(*) from tenants where subscription_status in ('cancelled','expired'))
    ),
    'sites',(select count(*) from sites),
    'cameras',(select count(*) from cameras),
    'agents',jsonb_build_object(
      'total',(select count(*) from agents),
      'online',(select count(*) from agents where last_seen_at >= now()-interval '5 minutes'),
      'offline',(select count(*) from agents where last_seen_at is null or last_seen_at < now()-interval '5 minutes')
    ),
    'last_24h',jsonb_build_object(
      'incidents',(select count(*) from events where received_at >= now()-interval '24 hours'),
      'analytics',(select count(*) from analytic_events where received_at >= now()-interval '24 hours'),
      'reports_sent',(select count(*) from report_deliveries where sent_at >= now()-interval '24 hours' and status='sent'),
      'report_failures',(select count(*) from report_deliveries where sent_at >= now()-interval '24 hours' and status='failed')
    ),
    'trials_expiring_3d',(select count(*) from tenants
      where subscription_status='trialing'
        and trial_started_at + make_interval(days=>trial_days) between now() and now()+interval '3 days'),
    'recent_tenants',coalesce((select jsonb_agg(to_jsonb(x) order by x.created_at desc) from (
      select t.id,t.name,t.plan,t.subscription_status,t.created_at,
        (select count(*) from sites s where s.tenant_id=t.id) as sites,
        (select max(a.last_seen_at) from agents a where a.tenant_id=t.id) as last_seen_at
      from tenants t order by t.created_at desc limit 8
    ) x),'[]'::jsonb)
  );
end $$;

-- ---------------------------------------------------------------------
-- Tenant directory with operational/commercial summary
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_tenants(
  p_search text default null, p_status text default null, p_limit int default 100, p_offset int default 0
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
        v_limit int := least(greatest(coalesce(p_limit,100),1),250);
        v_offset int := greatest(coalesce(p_offset,0),0);
begin
  return jsonb_build_object(
    'role',v_role,
    'items',coalesce((select jsonb_agg(to_jsonb(x) order by x.created_at desc) from (
      select t.id,t.name,t.plan,t.requested_plan,t.subscription_status,t.created_at,
        t.trial_started_at,t.trial_days,
        t.trial_started_at + make_interval(days=>t.trial_days) as trial_ends_at,
        (select count(*) from memberships m where m.tenant_id=t.id) as members,
        (select count(*) from sites s where s.tenant_id=t.id) as sites,
        (select count(*) from cameras c where c.tenant_id=t.id) as cameras,
        (select count(*) from agents a where a.tenant_id=t.id and a.last_seen_at>=now()-interval '5 minutes') as agents_online,
        (select count(*) from agents a where a.tenant_id=t.id) as agents_total,
        (select max(a.last_seen_at) from agents a where a.tenant_id=t.id) as last_seen_at,
        (select count(*) from events e where e.tenant_id=t.id and e.received_at>=now()-interval '24 hours') as incidents_24h,
        (select count(*) from analytic_events ae where ae.tenant_id=t.id and ae.received_at>=now()-interval '24 hours') as analytics_24h,
        (select max(rd.sent_at) from report_deliveries rd where rd.tenant_id=t.id and rd.status='sent') as last_report_at,
        (select u.email from memberships m join auth.users u on u.id=m.user_id
          where m.tenant_id=t.id and m.role='owner' order by m.created_at limit 1) as owner_email
      from tenants t
      where (p_status is null or p_status='' or t.subscription_status=p_status)
        and (p_search is null or btrim(p_search)='' or
             t.name ilike '%'||btrim(p_search)||'%' or exists(
               select 1 from memberships m join auth.users u on u.id=m.user_id
               where m.tenant_id=t.id and u.email ilike '%'||btrim(p_search)||'%'))
      order by t.created_at desc limit v_limit offset v_offset
    ) x),'[]'::jsonb),
    'total',(select count(*) from tenants t
      where (p_status is null or p_status='' or t.subscription_status=p_status)
        and (p_search is null or btrim(p_search)='' or t.name ilike '%'||btrim(p_search)||'%' or exists(
          select 1 from memberships m join auth.users u on u.id=m.user_id
          where m.tenant_id=t.id and u.email ilike '%'||btrim(p_search)||'%')))
  );
end $$;

-- ---------------------------------------------------------------------
-- Full support view for one tenant. This is intentionally data-rich but
-- does not expose agent secrets, recorder credentials, snapshot bytes or
-- billing webhook payloads.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_tenant(p_tenant_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']); t tenants;
begin
  select * into t from tenants where id=p_tenant_id;
  if t.id is null then raise exception 'tenant not found' using errcode='22023'; end if;
  return jsonb_build_object(
    'role',v_role,
    'tenant',jsonb_build_object(
      'id',t.id,'name',t.name,'created_at',t.created_at,'plan',t.plan,
      'requested_plan',t.requested_plan,'subscription_status',t.subscription_status,
      'trial_started_at',t.trial_started_at,'trial_days',t.trial_days,
      'trial_ends_at',t.trial_started_at + make_interval(days=>t.trial_days)
    ),
    'members',coalesce((select jsonb_agg(jsonb_build_object('user_id',m.user_id,'email',u.email,'role',m.role,'joined_at',m.created_at)
      order by m.created_at) from memberships m join auth.users u on u.id=m.user_id where m.tenant_id=t.id),'[]'::jsonb),
    'sites',coalesce((select jsonb_agg(jsonb_build_object(
      'id',s.id,'name',s.name,'timezone',s.timezone,'site_type',s.site_type,
      'cameras',(select count(*) from cameras c where c.site_id=s.id),
      'agents',(select count(*) from agents a where a.site_id=s.id),
      'agents_online',(select count(*) from agents a where a.site_id=s.id and a.last_seen_at>=now()-interval '5 minutes'),
      'last_seen_at',(select max(a.last_seen_at) from agents a where a.site_id=s.id),
      'agent_versions',coalesce((select jsonb_agg(distinct a.agent_version) from agents a where a.site_id=s.id and a.agent_version is not null),'[]'::jsonb),
      'device_vendors',coalesce((select jsonb_agg(distinct a.device_vendor) from agents a where a.site_id=s.id and a.device_vendor is not null),'[]'::jsonb),
      'incidents_24h',(select count(*) from events e where e.site_id=s.id and e.received_at>=now()-interval '24 hours'),
      'analytics_24h',(select count(*) from analytic_events ae where ae.site_id=s.id and ae.received_at>=now()-interval '24 hours'),
      'last_report_at',(select max(rd.sent_at) from report_deliveries rd where rd.site_id=s.id and rd.status='sent')
    ) order by s.created_at) from sites s where s.tenant_id=t.id),'[]'::jsonb),
    'subscription',(select to_jsonb(s) - 'tenant_id' from subscriptions s where s.tenant_id=t.id),
    'transactions',coalesce((select jsonb_agg(jsonb_build_object(
      'id',p.id,'provider',p.provider,'amount_minor',p.amount_minor,'currency',p.currency,
      'status',p.status,'plan',p.plan,'description',p.description,'created_at',p.created_at)
      order by p.created_at desc) from payment_transactions p where p.tenant_id=t.id limit 30),'[]'::jsonb),
    'report_recipients',coalesce((select jsonb_agg(jsonb_build_object(
      'id',r.id,'name',r.name,'channel',r.channel,'destination',r.destination,'enabled',r.enabled,'site_id',r.site_id)
      order by r.created_at) from report_recipients r where r.tenant_id=t.id),'[]'::jsonb),
    'deliveries',coalesce((select jsonb_agg(jsonb_build_object(
      'report_date',d.report_date,'site_id',d.site_id,'channel',d.channel,'destination',d.destination,
      'status',d.status,'events',d.events,'error',d.error,'sent_at',d.sent_at)
      order by d.sent_at desc) from report_deliveries d where d.tenant_id=t.id and d.sent_at>=now()-interval '30 days'),'[]'::jsonb),
    'analytics',jsonb_build_object(
      'rules',(select count(*) from monitoring_rules r where r.tenant_id=t.id and r.enabled),
      'measurements_30d',(select count(*) from analytic_events ae where ae.tenant_id=t.id and ae.received_at>=now()-interval '30 days')
    ),
    'audit',coalesce((select jsonb_agg(jsonb_build_object(
      'action',a.action,'reason',a.reason,'actor_role',a.actor_role,'created_at',a.created_at)
      order by a.created_at desc) from platform_admin_audit a where a.tenant_id=t.id limit 30),'[]'::jsonb)
  );
end $$;

-- ---------------------------------------------------------------------
-- Fleet/operations view
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_operations(p_limit int default 200)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
        v_limit int := least(greatest(coalesce(p_limit,200),1),500);
begin
  return jsonb_build_object('role',v_role,'sites',coalesce((select jsonb_agg(to_jsonb(x) order by x.tenant,x.site) from (
    select t.id as tenant_id,t.name as tenant,s.id as site_id,s.name as site,s.site_type,
      (select count(*) from cameras c where c.site_id=s.id) as cameras,
      (select count(*) from monitoring_rules r where r.site_id=s.id and r.enabled) as analytics_rules,
      (select max(a.last_seen_at) from agents a where a.site_id=s.id) as last_seen_at,
      exists(select 1 from agents a where a.site_id=s.id and a.last_seen_at>=now()-interval '5 minutes') as agent_online,
      (select string_agg(distinct coalesce(a.device_vendor,'Unknown'),', ') from agents a where a.site_id=s.id) as recorder_vendor,
      (select string_agg(distinct coalesce(a.agent_version,'Unknown'),', ') from agents a where a.site_id=s.id) as agent_version,
      (select count(*) from events e where e.site_id=s.id and e.received_at>=now()-interval '24 hours') as incidents_24h,
      (select count(*) from analytic_events ae where ae.site_id=s.id and ae.received_at>=now()-interval '24 hours') as analytics_24h,
      (select max(rd.sent_at) from report_deliveries rd where rd.site_id=s.id and rd.status='sent') as last_report_at,
      (select count(*) from report_deliveries rd where rd.site_id=s.id and rd.status='failed' and rd.sent_at>=now()-interval '7 days') as report_failures_7d
    from sites s join tenants t on t.id=s.tenant_id
    order by t.name,s.name limit v_limit
  ) x),'[]'::jsonb));
end $$;

-- ---------------------------------------------------------------------
-- Billing control plane. Reading needs platform_admin/owner. A manual paid
-- state override is platform_owner only, creates/updates a subscription with
-- provider='manual', and is always audited. This preserves the invariant
-- that a TENANT can never self-mark paid.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_billing()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']);
begin
  return jsonb_build_object(
    'role',v_role,
    'by_plan',coalesce((select jsonb_agg(to_jsonb(x)) from (
      select plan,count(*) as tenants from tenants group by plan order by plan) x),'[]'::jsonb),
    'by_status',coalesce((select jsonb_agg(to_jsonb(x)) from (
      select subscription_status as status,count(*) as tenants from tenants group by subscription_status order by subscription_status) x),'[]'::jsonb),
    'trials_expiring',coalesce((select jsonb_agg(jsonb_build_object(
      'tenant_id',t.id,'tenant',t.name,'ends_at',t.trial_started_at+make_interval(days=>t.trial_days))
      order by t.trial_started_at+make_interval(days=>t.trial_days))
      from tenants t where t.subscription_status='trialing'
        and t.trial_started_at+make_interval(days=>t.trial_days) between now() and now()+interval '7 days'),'[]'::jsonb),
    'transactions',coalesce((select jsonb_agg(jsonb_build_object(
      'tenant_id',p.tenant_id,'tenant',t.name,'provider',p.provider,'amount_minor',p.amount_minor,
      'currency',p.currency,'status',p.status,'plan',p.plan,'description',p.description,'created_at',p.created_at)
      order by p.created_at desc) from payment_transactions p join tenants t on t.id=p.tenant_id
      where p.created_at>=now()-interval '90 days'),'[]'::jsonb)
  );
end $$;

create or replace function public.wl_platform_grant_trial(
  p_tenant_id uuid, p_days int, p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']);
        v_before jsonb; v_after jsonb;
begin
  if p_days not between 1 and 90 then raise exception 'trial days must be 1 to 90'; end if;
  select jsonb_build_object('plan',plan,'status',subscription_status,'trial_started_at',trial_started_at,'trial_days',trial_days)
    into v_before from tenants where id=p_tenant_id;
  if v_before is null then raise exception 'tenant not found' using errcode='22023'; end if;
  update tenants set plan='trial',subscription_status='trialing',requested_plan=null,
    trial_started_at=now(),trial_days=p_days where id=p_tenant_id;
  select jsonb_build_object('plan',plan,'status',subscription_status,'trial_started_at',trial_started_at,'trial_days',trial_days)
    into v_after from tenants where id=p_tenant_id;
  perform wl_platform_write_audit(p_tenant_id,'grant_trial',p_reason,v_before,v_after);
  return jsonb_build_object('ok',true,'tenant_id',p_tenant_id,'trial_days',p_days,'status','trialing');
end $$;

create or replace function public.wl_platform_set_subscription(
  p_tenant_id uuid, p_plan text, p_status text, p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner']); v_before jsonb; v_after jsonb;
begin
  if p_plan not in ('starter','growth','enterprise') then raise exception 'paid plan required'; end if;
  if p_status not in ('active','past_due','cancelled','expired') then raise exception 'invalid paid status'; end if;
  select jsonb_build_object('plan',plan,'status',subscription_status,
    'subscription',(select to_jsonb(s)-'tenant_id' from subscriptions s where s.tenant_id=tenants.id))
    into v_before from tenants where id=p_tenant_id;
  if v_before is null then raise exception 'tenant not found' using errcode='22023'; end if;

  insert into subscriptions(tenant_id,provider,provider_subscription_id,plan,status,current_period_end,cancel_at_period_end,updated_at)
  values(p_tenant_id,'manual',null,p_plan,p_status,
         case when p_status in ('active','past_due') then now()+interval '30 days' else null end,false,now())
  on conflict(tenant_id) do update set provider='manual',provider_subscription_id=null,plan=excluded.plan,
    status=excluded.status,current_period_end=excluded.current_period_end,cancel_at_period_end=false,updated_at=now();
  perform wl_billing_set_subscription(p_tenant_id,p_plan,p_status);

  select jsonb_build_object('plan',plan,'status',subscription_status,
    'subscription',(select to_jsonb(s)-'tenant_id' from subscriptions s where s.tenant_id=tenants.id))
    into v_after from tenants where id=p_tenant_id;
  perform wl_platform_write_audit(p_tenant_id,'manual_subscription_override',p_reason,v_before,v_after);
  return jsonb_build_object('ok',true,'tenant_id',p_tenant_id,'plan',p_plan,'status',p_status,'provider','manual');
end $$;

-- ---------------------------------------------------------------------
-- Audit + platform-admin lifecycle
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_audit(p_limit int default 200)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']);
        v_limit int := least(greatest(coalesce(p_limit,200),1),500);
begin
  return coalesce((select jsonb_agg(jsonb_build_object(
    'id',a.id,'tenant_id',a.tenant_id,'tenant',t.name,'actor_user_id',a.actor_user_id,
    'actor_email',u.email,'actor_role',a.actor_role,'action',a.action,'reason',a.reason,
    'before',a.before_json,'after',a.after_json,'created_at',a.created_at)
    order by a.created_at desc)
    from platform_admin_audit a
    left join tenants t on t.id=a.tenant_id
    left join auth.users u on u.id=a.actor_user_id
    where a.id in (select id from platform_admin_audit order by created_at desc limit v_limit)),'[]'::jsonb);
end $$;

create or replace function public.wl_platform_admins()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner']);
begin
  return coalesce((select jsonb_agg(jsonb_build_object(
    'user_id',pa.user_id,'email',u.email,'role',pa.role,'created_at',pa.created_at,
    'is_you',pa.user_id=auth.uid()) order by pa.created_at)
    from platform_admins pa join auth.users u on u.id=pa.user_id),'[]'::jsonb);
end $$;

create or replace function public.wl_platform_set_admin(
  p_email text, p_role text, p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner']); v_user uuid; v_before jsonb; v_after jsonb;
begin
  if p_role not in ('platform_owner','platform_admin','platform_support') then raise exception 'invalid platform role'; end if;
  select id into v_user from auth.users where lower(email)=lower(btrim(p_email)) order by created_at limit 1;
  if v_user is null then raise exception 'that email must sign up/sign in to WatchLog before platform access can be granted'; end if;
  select to_jsonb(pa) into v_before from platform_admins pa where pa.user_id=v_user;
  insert into platform_admins(user_id,role,created_by) values(v_user,p_role,auth.uid())
  on conflict(user_id) do update set role=excluded.role;
  select jsonb_build_object('user_id',pa.user_id,'role',pa.role) into v_after from platform_admins pa where pa.user_id=v_user;
  perform wl_platform_write_audit(null,'set_platform_admin',p_reason,v_before,v_after);
  return jsonb_build_object('ok',true,'user_id',v_user,'email',lower(btrim(p_email)),'role',p_role);
end $$;

create or replace function public.wl_platform_remove_admin(
  p_user_id uuid, p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner']); v_before jsonb; v_owners int;
begin
  select to_jsonb(pa) into v_before from platform_admins pa where pa.user_id=p_user_id;
  if v_before is null then return jsonb_build_object('ok',true,'removed',false); end if;
  if (v_before->>'role')='platform_owner' then
    select count(*) into v_owners from platform_admins where role='platform_owner';
    if v_owners<=1 then raise exception 'cannot remove the last platform owner'; end if;
  end if;
  delete from platform_admins where user_id=p_user_id;
  perform wl_platform_write_audit(null,'remove_platform_admin',p_reason,v_before,null);
  return jsonb_build_object('ok',true,'removed',true,'user_id',p_user_id);
end $$;

-- ---------------------------------------------------------------------
-- Public privilege surface
-- ---------------------------------------------------------------------
revoke all on function public.wl_platform_overview() from public,anon;
revoke all on function public.wl_platform_tenants(text,text,int,int) from public,anon;
revoke all on function public.wl_platform_tenant(uuid) from public,anon;
revoke all on function public.wl_platform_operations(int) from public,anon;
revoke all on function public.wl_platform_billing() from public,anon;
revoke all on function public.wl_platform_grant_trial(uuid,int,text) from public,anon;
revoke all on function public.wl_platform_set_subscription(uuid,text,text,text) from public,anon;
revoke all on function public.wl_platform_audit(int) from public,anon;
revoke all on function public.wl_platform_admins() from public,anon;
revoke all on function public.wl_platform_set_admin(text,text,text) from public,anon;
revoke all on function public.wl_platform_remove_admin(uuid,text) from public,anon;

grant execute on function public.wl_platform_overview() to authenticated;
grant execute on function public.wl_platform_tenants(text,text,int,int) to authenticated;
grant execute on function public.wl_platform_tenant(uuid) to authenticated;
grant execute on function public.wl_platform_operations(int) to authenticated;
grant execute on function public.wl_platform_billing() to authenticated;
grant execute on function public.wl_platform_grant_trial(uuid,int,text) to authenticated;
grant execute on function public.wl_platform_set_subscription(uuid,text,text,text) to authenticated;
grant execute on function public.wl_platform_audit(int) to authenticated;
grant execute on function public.wl_platform_admins() to authenticated;
grant execute on function public.wl_platform_set_admin(text,text,text) to authenticated;
grant execute on function public.wl_platform_remove_admin(uuid,text) to authenticated;

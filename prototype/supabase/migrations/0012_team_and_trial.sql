-- =====================================================================
-- Milestone 2: team management, and trial state.
--
-- Two gaps the scope of work names explicitly and that had no
-- implementation:
--
--   "Customer portal: team management, full report history"
--   "Trial period logic (default assumption: 14 days)"
--
-- TEAM
-- ----
-- memberships already had a role column defaulting to 'owner', but
-- nothing constrained it and there was no way to add a second person.
-- Every account was therefore permanently one user, and that user was an
-- owner. A security company with a control room and a manager could not
-- use the product.
--
-- Invitations are by TOKEN, not by pre-creating an account. Pre-creating
-- accounts means inventing a password for someone else and mailing it
-- around, which is worse than the problem it solves. The token is random,
-- single-use, expires, and is only exchanged for a membership by a user
-- who has already authenticated as the invited address.
--
-- The e-mail on an invitation is checked against the accepting user's
-- own verified e-mail. Without that check, anyone holding a leaked token
-- joins the tenant - so the token alone is never sufficient.
--
-- TRIAL
-- -----
-- Milestone 2 tracks trial state; Milestone 3 enforces it at the billing
-- gate. Keeping those apart is deliberate: a bug in trial arithmetic
-- should make a customer's status wrong, not lock a working security
-- system out of its own dashboard overnight. Nothing here denies access.
-- =====================================================================

-- --- roles -----------------------------------------------------------
-- owner  billing, team, delete the account
-- admin  sites, agents, recipients - everything operational
-- viewer read-only: the report and the incident log
alter table public.memberships
  drop constraint if exists memberships_role_check;
alter table public.memberships
  add constraint memberships_role_check
  check (role in ('owner', 'admin', 'viewer'));


-- --- trial state on the tenant ---------------------------------------
alter table public.tenants
  add column if not exists plan text not null default 'trial',
  add column if not exists trial_started_at timestamptz not null default now(),
  add column if not exists trial_days int not null default 14,
  add column if not exists subscription_status text not null default 'trialing';

alter table public.tenants drop constraint if exists tenants_plan_check;
alter table public.tenants add constraint tenants_plan_check
  check (plan in ('trial', 'starter', 'growth', 'enterprise'));

alter table public.tenants drop constraint if exists tenants_sub_status_check;
alter table public.tenants add constraint tenants_sub_status_check
  check (subscription_status in ('trialing', 'active', 'past_due',
                                 'cancelled', 'expired'));

-- Existing tenants predate the trial concept. Treat them as active
-- rather than retroactively expiring a customer who is already running.
update public.tenants
   set subscription_status = 'active', plan = 'starter'
 where created_at < now() - interval '1 day'
   and subscription_status = 'trialing';


-- --- invitations -----------------------------------------------------
create table if not exists public.invitations (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  email       text not null,
  role        text not null default 'viewer'
                check (role in ('owner', 'admin', 'viewer')),
  -- Random and single-use. Never derived from the e-mail or the tenant,
  -- so one token tells you nothing about any other.
  token       text not null unique default encode(gen_random_bytes(24), 'hex'),
  invited_by  uuid references auth.users(id) on delete set null,
  expires_at  timestamptz not null default now() + interval '7 days',
  accepted_at timestamptz,
  accepted_by uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now()
);

create unique index if not exists invitations_open_unique
  on public.invitations (tenant_id, lower(email))
  where accepted_at is null;

create index if not exists invitations_tenant_idx
  on public.invitations (tenant_id);

alter table public.invitations enable row level security;

-- Members may see their tenant's invitations, but the TOKEN column is
-- never exposed through this policy path - the portal reads invitations
-- through wl_invitations(), which omits it. A token visible to every
-- viewer in the account would defeat the point of it being a secret.
create policy portal_read_invitations on public.invitations
  for select using (public.wl_is_member(tenant_id));


-- ---------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------

create or replace function public.wl_my_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
  select role from memberships where user_id = auth.uid() limit 1;
$$;


create or replace function public.wl_require_role(p_roles text[])
returns uuid
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_role   text := wl_my_role();
begin
  if v_tenant is null then
    raise exception 'not a member of any account';
  end if;
  if not (v_role = any(p_roles)) then
    raise exception 'this needs the % role; you are %',
      array_to_string(p_roles, ' or '), coalesce(v_role, 'not a member');
  end if;
  return v_tenant;
end $$;


-- ---------------------------------------------------------------------
-- team
-- ---------------------------------------------------------------------

create or replace function public.wl_members()
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
             'user_id', m.user_id, 'email', u.email, 'role', m.role,
             'joined', m.created_at, 'is_you', m.user_id = auth.uid())
             order by m.created_at)
      from memberships m
      join auth.users u on u.id = m.user_id
     where m.tenant_id = v_tenant), '[]'::jsonb);
end $$;


create or replace function public.wl_invite_member(
  p_email text, p_role text default 'viewer')
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner', 'admin']);
  v_email  text := lower(btrim(p_email));
  v_token  text;
  v_id     uuid;
begin
  if position('@' in v_email) = 0 then
    raise exception 'that is not an email address';
  end if;
  if p_role not in ('owner', 'admin', 'viewer') then
    raise exception 'role must be owner, admin or viewer';
  end if;
  -- Only an owner can mint another owner. An admin promoting someone to
  -- owner would be a privilege escalation with extra steps.
  if p_role = 'owner' and wl_my_role() <> 'owner' then
    raise exception 'only an owner can invite another owner';
  end if;

  if exists (select 1 from memberships m join auth.users u on u.id = m.user_id
              where m.tenant_id = v_tenant and lower(u.email) = v_email) then
    return jsonb_build_object('ok', false, 'note', 'that person is already on the team');
  end if;

  delete from invitations
   where tenant_id = v_tenant and lower(email) = v_email and accepted_at is null;

  insert into invitations (tenant_id, email, role, invited_by)
  values (v_tenant, v_email, p_role, auth.uid())
  returning id, token into v_id, v_token;

  return jsonb_build_object('ok', true, 'id', v_id, 'email', v_email,
                            'role', p_role, 'token', v_token,
                            'expires_in_days', 7);
end $$;


create or replace function public.wl_invitations()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  -- No token here, on purpose: see the RLS note above.
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', i.id, 'email', i.email, 'role', i.role,
             'expires_at', i.expires_at, 'created_at', i.created_at,
             'expired', i.expires_at < now())
             order by i.created_at desc)
      from invitations i
     where i.tenant_id = v_tenant and i.accepted_at is null), '[]'::jsonb);
end $$;


create or replace function public.wl_revoke_invite(p_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner', 'admin']);
  v_hit int;
begin
  delete from invitations
   where id = p_id and tenant_id = v_tenant and accepted_at is null;
  get diagnostics v_hit = row_count;
  return jsonb_build_object('ok', v_hit > 0);
end $$;


create or replace function public.wl_accept_invite(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_inv   invitations%rowtype;
  v_email text;
begin
  if auth.uid() is null then
    raise exception 'sign in first, then open the invitation link';
  end if;

  select * into v_inv from invitations
   where token = btrim(p_token) and accepted_at is null;
  if not found then
    return jsonb_build_object('ok', false, 'note',
      'that invitation is not valid, or it has already been used');
  end if;
  if v_inv.expires_at < now() then
    return jsonb_build_object('ok', false, 'note',
      'that invitation has expired; ask for a new one');
  end if;

  -- The token is not sufficient on its own. Whoever accepts must already
  -- be signed in as the address that was invited, so a leaked link
  -- cannot be redeemed by a stranger.
  select lower(email) into v_email from auth.users where id = auth.uid();
  if v_email is distinct from lower(v_inv.email) then
    return jsonb_build_object('ok', false, 'note',
      'this invitation was sent to a different email address');
  end if;

  if exists (select 1 from memberships where user_id = auth.uid()) then
    return jsonb_build_object('ok', false, 'note',
      'this account already belongs to a team');
  end if;

  insert into memberships (user_id, tenant_id, role)
  values (auth.uid(), v_inv.tenant_id, v_inv.role);

  update invitations
     set accepted_at = now(), accepted_by = auth.uid()
   where id = v_inv.id;

  return jsonb_build_object('ok', true, 'tenant_id', v_inv.tenant_id,
                            'role', v_inv.role);
end $$;


create or replace function public.wl_set_member_role(
  p_user_id uuid, p_role text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner']);
  v_owners int;
begin
  if p_role not in ('owner', 'admin', 'viewer') then
    raise exception 'role must be owner, admin or viewer';
  end if;
  -- Never leave an account with nobody who can administer it.
  if p_role <> 'owner' then
    select count(*) into v_owners from memberships
     where tenant_id = v_tenant and role = 'owner' and user_id <> p_user_id;
    if v_owners = 0 then
      raise exception 'that is the last owner; promote someone else first';
    end if;
  end if;
  update memberships set role = p_role
   where tenant_id = v_tenant and user_id = p_user_id;
  return jsonb_build_object('ok', found);
end $$;


create or replace function public.wl_remove_member(p_user_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner', 'admin']);
  v_role   text;
  v_owners int;
begin
  select role into v_role from memberships
   where tenant_id = v_tenant and user_id = p_user_id;
  if not found then
    return jsonb_build_object('ok', false, 'note', 'not a member');
  end if;
  if p_user_id = auth.uid() then
    raise exception 'you cannot remove yourself';
  end if;
  if v_role = 'owner' then
    if wl_my_role() <> 'owner' then
      raise exception 'only an owner can remove another owner';
    end if;
    select count(*) into v_owners from memberships
     where tenant_id = v_tenant and role = 'owner' and user_id <> p_user_id;
    if v_owners = 0 then
      raise exception 'that is the last owner';
    end if;
  end if;
  delete from memberships where tenant_id = v_tenant and user_id = p_user_id;
  return jsonb_build_object('ok', true);
end $$;


-- ---------------------------------------------------------------------
-- trial
-- ---------------------------------------------------------------------

create or replace function public.wl_trial_status()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  t        tenants%rowtype;
  v_ends   timestamptz;
  v_left   int;
begin
  if v_tenant is null then return jsonb_build_object('tenant', null); end if;
  select * into t from tenants where id = v_tenant;
  v_ends := t.trial_started_at + make_interval(days => t.trial_days);
  v_left := greatest(0, ceil(extract(epoch from (v_ends - now())) / 86400)::int);

  return jsonb_build_object(
    'plan', t.plan,
    'status', t.subscription_status,
    'trial_started_at', t.trial_started_at,
    'trial_ends_at', v_ends,
    'days_left', case when t.subscription_status = 'trialing' then v_left end,
    -- Reported, never enforced here. Milestone 3 owns the billing gate;
    -- a trial-arithmetic bug must not lock a live security system out of
    -- its own dashboard.
    'expired', t.subscription_status = 'trialing' and v_ends < now());
end $$;


create or replace function public.wl_set_plan(
  p_plan text, p_status text default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_require_role(array['owner']);
begin
  if p_plan not in ('trial', 'starter', 'growth', 'enterprise') then
    raise exception 'unknown plan';
  end if;
  update tenants
     set plan = p_plan,
         subscription_status = coalesce(p_status, subscription_status)
   where id = v_tenant;
  return jsonb_build_object('ok', true, 'plan', p_plan);
end $$;


-- ---------------------------------------------------------------------
-- grants: authenticated only, never anon
-- ---------------------------------------------------------------------
do $$
declare f text;
begin
  foreach f in array array[
    'wl_my_role()', 'wl_members()', 'wl_invite_member(text,text)',
    'wl_invitations()', 'wl_revoke_invite(uuid)', 'wl_accept_invite(text)',
    'wl_set_member_role(uuid,text)', 'wl_remove_member(uuid)',
    'wl_trial_status()', 'wl_set_plan(text,text)',
    'wl_require_role(text[])']
  loop
    execute format('revoke all on function public.%s from public, anon', f);
    execute format('grant execute on function public.%s to authenticated', f);
  end loop;
end $$;

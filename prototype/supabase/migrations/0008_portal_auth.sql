-- =====================================================================
-- WatchLog — portal authentication and multi-tenancy
-- =====================================================================
-- Everything so far has been MACHINE auth: an agent proving itself with a
-- per-agent secret through SECURITY DEFINER functions. That stays exactly
-- as it is and is untouched here.
--
-- This adds HUMAN auth: real people signing in through Supabase Auth,
-- scoped to the tenant they belong to.
--
-- Two different problems, two different mechanisms, deliberately:
--   agent  -> publishable key + per-agent secret -> wl_* functions
--   person -> Supabase Auth session (JWT)        -> RLS on the tables
--
-- The person path uses ordinary RLS rather than more RPCs, because a
-- logged-in user's identity is already carried by the JWT. Adding a
-- function layer would mean re-implementing authorisation that Postgres
-- already does correctly.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Who belongs to which tenant.
-- ---------------------------------------------------------------------
create table if not exists memberships (
  user_id    uuid not null references auth.users(id) on delete cascade,
  tenant_id  uuid not null references tenants(id)    on delete cascade,
  role       text not null default 'owner'
             check (role in ('owner', 'admin', 'viewer')),
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);
create index if not exists memberships_tenant_idx on memberships(tenant_id);

alter table memberships enable row level security;

-- ---------------------------------------------------------------------
-- The authorisation predicate, in ONE place.
--
-- SECURITY DEFINER on purpose: a policy on `memberships` that queries
-- `memberships` recurses forever. Defining the check here breaks that
-- cycle, and means every table's policy is the same single expression -
-- there is no second copy to get subtly wrong.
-- ---------------------------------------------------------------------
create or replace function public.wl_is_member(p_tenant uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from memberships m
     where m.tenant_id = p_tenant
       and m.user_id = auth.uid()
  )
$$;

create or replace function public.wl_my_tenant()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select m.tenant_id from memberships m
   where m.user_id = auth.uid()
   order by m.created_at
   limit 1
$$;

grant execute on function public.wl_is_member(uuid) to authenticated;
grant execute on function public.wl_my_tenant()     to authenticated;

-- ---------------------------------------------------------------------
-- Read policies. Signed-in users see their own tenant's data and
-- nothing else. Writes still go through functions, so there is no
-- policy here that lets a browser insert an event.
-- ---------------------------------------------------------------------
drop policy if exists portal_read_memberships on memberships;
create policy portal_read_memberships on memberships
  for select to authenticated using (user_id = auth.uid());

drop policy if exists portal_read_tenants on tenants;
create policy portal_read_tenants on tenants
  for select to authenticated using (wl_is_member(id));

drop policy if exists portal_read_sites on sites;
create policy portal_read_sites on sites
  for select to authenticated using (wl_is_member(tenant_id));

drop policy if exists portal_read_agents on agents;
create policy portal_read_agents on agents
  for select to authenticated using (wl_is_member(tenant_id));

drop policy if exists portal_read_cameras on cameras;
create policy portal_read_cameras on cameras
  for select to authenticated using (wl_is_member(tenant_id));

drop policy if exists portal_read_events on events;
create policy portal_read_events on events
  for select to authenticated using (wl_is_member(tenant_id));

drop policy if exists portal_read_snapshots on snapshots;
create policy portal_read_snapshots on snapshots
  for select to authenticated using (wl_is_member(tenant_id));

-- Enrollment codes are bearer secrets. A member may read only codes that
-- are still unused, so the onboarding screen can show one - never the
-- history of codes already redeemed.
drop policy if exists portal_read_open_codes on enrollment_codes;
create policy portal_read_open_codes on enrollment_codes
  for select to authenticated
  using (wl_is_member(tenant_id) and used_at is null and expires_at > now());

-- ---------------------------------------------------------------------
-- wl_bootstrap_tenant — what happens the first time somebody signs up.
--
-- Creates the tenant, their first site, the membership, and an
-- enrollment code, in one transaction. A half-finished signup that
-- leaves a tenant with no membership would lock the user out of their
-- own data with no way back.
-- ---------------------------------------------------------------------
create or replace function public.wl_bootstrap_tenant(
  p_company   text,
  p_site_name text default 'Main site',
  p_timezone  text default 'Asia/Karachi'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user   uuid := auth.uid();
  v_tenant uuid;
  v_site   uuid;
  v_code   text;
begin
  if v_user is null then
    raise exception 'not signed in' using errcode = '28000';
  end if;

  -- Idempotent: signing up twice must not create a second tenant.
  select tenant_id into v_tenant from memberships
   where user_id = v_user order by created_at limit 1;
  if v_tenant is not null then
    return jsonb_build_object('tenant_id', v_tenant, 'existing', true);
  end if;

  insert into tenants (name) values (coalesce(nullif(trim(p_company), ''), 'My company'))
    returning id into v_tenant;

  insert into memberships (user_id, tenant_id, role)
    values (v_user, v_tenant, 'owner');

  insert into sites (tenant_id, name, timezone)
    values (v_tenant, coalesce(nullif(trim(p_site_name), ''), 'Main site'),
            coalesce(nullif(trim(p_timezone), ''), 'Asia/Karachi'))
    returning id into v_site;

  v_code := 'WL-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4))
                  || '-' ||
            upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4));
  insert into enrollment_codes (code, tenant_id, site_id, expires_at)
    values (v_code, v_tenant, v_site, now() + interval '14 days');

  return jsonb_build_object('tenant_id', v_tenant, 'site_id', v_site,
                            'enrollment_code', v_code, 'existing', false);
end
$$;

-- ---------------------------------------------------------------------
-- wl_add_site / wl_issue_code — the two write actions the portal needs.
-- Both re-check membership themselves; being SECURITY DEFINER, they
-- cannot lean on RLS to do it for them.
-- ---------------------------------------------------------------------
create or replace function public.wl_add_site(
  p_name text, p_timezone text default 'Asia/Karachi'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant(); v_site uuid;
begin
  if v_tenant is null then
    raise exception 'no tenant for this user' using errcode = '28000';
  end if;
  insert into sites (tenant_id, name, timezone)
    values (v_tenant, coalesce(nullif(trim(p_name), ''), 'New site'),
            coalesce(nullif(trim(p_timezone), ''), 'Asia/Karachi'))
    returning id into v_site;
  return jsonb_build_object('site_id', v_site);
end
$$;

create or replace function public.wl_issue_code(
  p_site_id uuid, p_days int default 14
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_code text;
begin
  select tenant_id into v_tenant from sites where id = p_site_id;
  if v_tenant is null or not wl_is_member(v_tenant) then
    raise exception 'not your site' using errcode = '42501';
  end if;

  v_code := 'WL-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4))
                  || '-' ||
            upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 4));
  insert into enrollment_codes (code, tenant_id, site_id, expires_at)
    values (v_code, v_tenant, p_site_id,
            now() + make_interval(days => least(greatest(p_days, 1), 90)));
  return jsonb_build_object('code', v_code);
end
$$;

grant execute on function public.wl_bootstrap_tenant(text, text, text) to authenticated;
grant execute on function public.wl_add_site(text, text)                to authenticated;
grant execute on function public.wl_issue_code(uuid, int)               to authenticated;

revoke all on function public.wl_bootstrap_tenant(text, text, text) from anon;
revoke all on function public.wl_add_site(text, text)                from anon;
revoke all on function public.wl_issue_code(uuid, int)               from anon;

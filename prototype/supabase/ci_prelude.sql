-- =====================================================================
-- ci_prelude.sql — minimal Supabase-shaped shims so the WatchLog migrations (0001..) apply AND run
-- on a vanilla Postgres (the disposable CI integration database). This is NEVER used in production:
-- production IS Supabase, which provides auth.users, auth.uid(), the anon/authenticated/service_role
-- roles and pgcrypto natively. Applied once, before apply_migrations.py.
-- =====================================================================

-- gen_random_uuid() is core in PG13+, but a few migrations use pgcrypto's crypt()/gen_salt().
create extension if not exists pgcrypto;

-- Supabase's three PostgREST roles.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin noinherit; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin noinherit bypassrls; end if;
end $$;

-- GoTrue's user table — only the columns the migrations FK to / the harness touches.
create schema if not exists auth;
create table if not exists auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text,
  email_confirmed_at timestamptz,
  raw_user_meta_data jsonb default '{}'::jsonb,
  created_at         timestamptz not null default now()
);
grant usage on schema auth to anon, authenticated, service_role;

-- auth.uid() — reads the JWT 'sub' claim from the request GUC, exactly like Supabase. The harness
-- (and PostgREST in prod) set request.jwt.claim.sub / request.jwt.claims before a tenant-scoped call.
create or replace function auth.uid() returns uuid
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'
  )::uuid
$$;

-- auth.role()/auth.email() are not referenced by the migrations, so they are intentionally omitted.

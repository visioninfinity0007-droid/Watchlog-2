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

-- auth.role() — reads the JWT 'role' claim, exactly like Supabase. Referenced by service-role-gated
-- RPCs (0102 assistant-message authoring; 0105 AI provider key resolver / health writeback).
-- Defaults to 'authenticated' when unset, matching Supabase for an authenticated request.
create or replace function auth.role() returns text
language sql stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.role', true), ''),
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role',
    'authenticated'
  )
$$;

-- auth.email() is not referenced by the migrations, so it is intentionally omitted.


-- Supabase platform-service shims for disposable vanilla Postgres only.
-- Production never runs this file.
create schema if not exists vault;
create table if not exists vault.secrets (
  id uuid primary key default gen_random_uuid(),
  secret text not null,
  name text not null unique,
  description text,
  created_at timestamptz not null default now()
);
create or replace function vault.create_secret(
  new_secret text,
  new_name text,
  new_description text default null
) returns uuid
language plpgsql
security definer
as $$
declare v_id uuid;
begin
  insert into vault.secrets(secret,name,description)
  values (new_secret,new_name,new_description)
  on conflict(name) do update
    set secret=excluded.secret,description=excluded.description
  returning id into v_id;
  return v_id;
end $$;
create or replace view vault.decrypted_secrets as
select id,name,description,secret as decrypted_secret,created_at
from vault.secrets;

create schema if not exists cron;
create table if not exists cron.job (
  jobid bigserial primary key,
  jobname text not null unique,
  schedule text not null,
  command text not null
);
create or replace function cron.schedule(
  p_jobname text,
  p_schedule text,
  p_command text
) returns bigint
language plpgsql
as $$
declare v_id bigint;
begin
  insert into cron.job(jobname,schedule,command)
  values (p_jobname,p_schedule,p_command)
  on conflict(jobname) do update
    set schedule=excluded.schedule,command=excluded.command
  returning jobid into v_id;
  return v_id;
end $$;
create or replace function cron.unschedule(p_jobname text) returns boolean
language plpgsql
as $$
begin
  delete from cron.job where jobname=p_jobname;
  return found;
end $$;

create schema if not exists net;
create or replace function net.http_post(
  url text,
  headers jsonb default '{}'::jsonb,
  body jsonb default '{}'::jsonb,
  timeout_milliseconds integer default 1000
) returns bigint
language sql
as $$ select 1::bigint $$;

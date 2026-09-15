-- =====================================================================
-- 0106_ai_routing.sql — Phase 5: deterministic mode router support.
--
-- Two tenant-safe pieces the edge-function router needs, kept OUT of the model's reach:
--
--   1. Per-site data-egress policy. Local-only by DEFAULT (privacy-first). Only the site's own
--      tenant owner/admin can relax it — never a platform admin, never the model. This is the
--      restriction that an admin-configured cloud provider can NEVER override: the router reads it
--      AFTER resolving a mode and refuses to send a local-only site's prompt to an external model.
--
--   2. Route audit. The edge function (service_role) records every routing decision — mode,
--      resolved provider/model, primary-vs-fallback, egress decision, latency, tool calls, outcome.
--      Provider/model identities live here for Admin + audit ONLY; they are never returned to the
--      browser. Platform admins read it (benchmark/eval, ops); tenants and anon never touch it.
--
-- Depends on: sites, wl_my_tenant (0008/0038), wl_assert_my_site + wl_require_role (0072/0012),
-- wl_platform_require (0029). No secret material is stored or logged here.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Per-site egress policy — tenant-owned, local-only by default.
-- ---------------------------------------------------------------------
create table if not exists public.ai_site_egress_policy (
  site_id                 uuid primary key references public.sites(id) on delete cascade,
  external_egress_allowed boolean not null default false,   -- false = local-only (privacy-first default)
  updated_by              uuid,
  updated_at              timestamptz not null default now()
);
alter table public.ai_site_egress_policy enable row level security;
revoke all on public.ai_site_egress_policy from anon, authenticated;

-- Read the site's egress policy (default local-only when unset). Site-owner scoped: a tenant can
-- only read their own site (wl_assert_my_site raises 42501 otherwise). Callable by the edge function.
create or replace function public.wl_ai_site_egress(p_site_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id); v_allowed boolean;
begin
  select external_egress_allowed into v_allowed from ai_site_egress_policy where site_id = p_site_id;
  return jsonb_build_object('site_id', p_site_id, 'external_egress_allowed', coalesce(v_allowed, false));
end $$;
revoke all on function public.wl_ai_site_egress(uuid) from public, anon;
grant execute on function public.wl_ai_site_egress(uuid) to authenticated, service_role;

-- Set the site's egress policy. Tenant owner/admin ONLY, and only for a site they own. A platform
-- admin cannot call this on a tenant's behalf; the model cannot call it at all.
create or replace function public.wl_ai_set_site_egress(p_site_id uuid, p_allowed boolean)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']);
begin
  if not exists (select 1 from sites where id = p_site_id and tenant_id = v_tenant) then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  insert into ai_site_egress_policy(site_id, external_egress_allowed, updated_by, updated_at)
  values (p_site_id, coalesce(p_allowed, false), auth.uid(), now())
  on conflict (site_id) do update
    set external_egress_allowed = excluded.external_egress_allowed,
        updated_by = excluded.updated_by, updated_at = now();
  return jsonb_build_object('site_id', p_site_id, 'external_egress_allowed', coalesce(p_allowed, false));
end $$;
revoke all on function public.wl_ai_set_site_egress(uuid, boolean) from public, anon;
grant execute on function public.wl_ai_set_site_egress(uuid, boolean) to authenticated;

-- ---------------------------------------------------------------------
-- 2. Route audit — service writes, platform-admin reads. No key material.
-- ---------------------------------------------------------------------
create table if not exists public.ai_route_audit (
  id               uuid primary key default gen_random_uuid(),
  created_at       timestamptz not null default now(),
  tenant_id        uuid,
  site_id          uuid,
  user_id          uuid,
  conversation_id  uuid,
  mode             text,                         -- instant | thinking | hive
  route            text not null,                -- no_model | ai_primary | ai_fallback | guided_fallback
  provider_id      uuid,                         -- resolved provider (Admin/audit visibility only)
  provider_name    text,
  model            text,
  used_fallback    boolean not null default false,
  egress           text,                         -- local | external | blocked_local_only | n/a
  latency_ms       integer,
  candidates_tried integer,
  tool_calls       jsonb not null default '[]'::jsonb,
  outcome          text                          -- ok | all_providers_failed | no_provider_configured
);                                               --   | config_invalid | egress_blocked | deterministic | router_error
create index if not exists ai_route_audit_created_idx on public.ai_route_audit(created_at desc);
create index if not exists ai_route_audit_site_idx    on public.ai_route_audit(site_id, created_at desc);
alter table public.ai_route_audit enable row level security;
revoke all on public.ai_route_audit from anon, authenticated;

-- Writer: edge function (service_role) only. Accepts one jsonb envelope; never carries a key.
create or replace function public.wl_ai_log_route(p jsonb)
returns void language plpgsql security definer set search_path = public as $$
begin
  if auth.role() <> 'service_role' then raise exception 'service role required' using errcode = '42501'; end if;
  insert into ai_route_audit(tenant_id, site_id, user_id, conversation_id, mode, route,
    provider_id, provider_name, model, used_fallback, egress, latency_ms, candidates_tried, tool_calls, outcome)
  values (
    nullif(p->>'tenant_id','')::uuid, nullif(p->>'site_id','')::uuid, nullif(p->>'user_id','')::uuid,
    nullif(p->>'conversation_id','')::uuid, p->>'mode', coalesce(nullif(p->>'route',''),'unknown'),
    nullif(p->>'provider_id','')::uuid, p->>'provider_name', p->>'model',
    coalesce((p->>'used_fallback')::boolean, false), p->>'egress',
    nullif(p->>'latency_ms','')::integer, nullif(p->>'candidates_tried','')::integer,
    case when jsonb_typeof(p->'tool_calls') = 'array' then p->'tool_calls' else '[]'::jsonb end,
    p->>'outcome'
  );
end $$;
revoke all on function public.wl_ai_log_route(jsonb) from public, anon, authenticated;
grant execute on function public.wl_ai_log_route(jsonb) to service_role;

-- Reader: platform admins only (in-body gate, per the 0029 convention). Exposes provider/model to
-- Admin + audit; never to a tenant.
create or replace function public.wl_ai_route_audit(p_limit integer default 100, p_site_id uuid default null)
returns jsonb language plpgsql stable security definer set search_path = public as $$
begin
  perform wl_platform_require(array['platform_owner','platform_admin','platform_support']);
  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.created_at desc)
    from (
      select id, created_at, tenant_id, site_id, user_id, conversation_id, mode, route,
             provider_id, provider_name, model, used_fallback, egress, latency_ms,
             candidates_tried, tool_calls, outcome
      from ai_route_audit
      where p_site_id is null or site_id = p_site_id
      order by created_at desc
      limit greatest(1, least(1000, coalesce(p_limit, 100)))
    ) r), '[]'::jsonb);
end $$;
revoke all on function public.wl_ai_route_audit(integer, uuid) from public, anon;
grant execute on function public.wl_ai_route_audit(integer, uuid) to authenticated;

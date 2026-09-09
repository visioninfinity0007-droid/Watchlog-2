-- ===================================================================
-- 0052 — Multi-agent architecture: a single-authority OWNERSHIP LEASE with a
-- FENCING TOKEN per site, so multiple agents/PCs can exist without duplicate
-- ingestion or conflicting health authority.
--
-- Model: at most one agent holds a site's lease at a time. Every takeover bumps
-- a monotonic `generation` (the fencing token). A write tagged with a stale
-- generation is rejected — so a superseded primary can never dual-write during a
-- takeover. A standby acquires only after the primary's lease expires (a
-- heartbeat gap), giving clean failover, and clean recovery when the primary
-- returns (it re-acquires, bumping the generation again).
--
-- FEATURE-GATED: sites.multi_agent_enabled defaults FALSE. With the flag OFF the
-- behavior is unchanged — every agent is trivially the authority (generation 0),
-- no contention, exactly as today. The lease/fencing only engages when a site
-- explicitly opts in. Wiring the agent to acquire/renew the lease and to tag its
-- ingestion with the generation REQUIRES A FUTURE AGENT RELEASE; this migration
-- is the server model + primitives, proven on disposable Postgres first. Not to
-- be exposed as "redundancy" until failover is proven in the field.
-- ===================================================================

alter table public.sites
  add column if not exists multi_agent_enabled boolean not null default false;

create table if not exists public.site_agent_leases (
  site_id          uuid primary key references public.sites(id) on delete cascade,
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  holder_agent_id  uuid references public.agents(id) on delete set null,
  generation       bigint not null default 0,          -- fencing token; bumps on every NEW holder
  acquired_at      timestamptz,
  renewed_at       timestamptz,
  lease_expires_at timestamptz,
  lease_seconds    int not null default 90,
  updated_at       timestamptz not null default now(),
  constraint site_agent_leases_lease_secs_chk check (lease_seconds between 10 and 3600)
);
create index if not exists site_agent_leases_tenant_idx on public.site_agent_leases(tenant_id);

-- -------------------------------------------------------------------
-- Acquire or renew the lease. Returns {granted, generation, mode, holder_agent_id, lease_expires_at}.
--   * feature OFF -> {granted:true, generation:0, mode:'single_agent'} (no contention, unchanged behavior)
--   * feature ON  -> real lease: same holder renews (generation kept); an expired/absent lease is
--                    taken over (generation bumped); a live lease held by another agent -> granted:false.
-- -------------------------------------------------------------------
create or replace function public.wl_agent_acquire_lease(
  p_agent_id uuid, p_agent_key text, p_lease_seconds int default 90
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent   public.agents;
  v_site    uuid;
  v_enabled boolean;
  v_lease   public.site_agent_leases;
  v_secs    int := least(greatest(coalesce(p_lease_seconds, 90), 10), 3600);
  v_gen     bigint;
  v_expired boolean;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  v_site := v_agent.site_id;
  select multi_agent_enabled into v_enabled from public.sites where id = v_site;

  if not coalesce(v_enabled, false) then
    -- feature OFF: single-agent mode, trivially the authority, no lease row needed
    return jsonb_build_object('granted', true, 'generation', 0, 'mode', 'single_agent',
                              'holder_agent_id', v_agent.id, 'lease_expires_at', null);
  end if;

  -- serialize per-site acquire attempts
  insert into public.site_agent_leases (site_id, tenant_id, lease_seconds)
  values (v_site, v_agent.tenant_id, v_secs)
  on conflict (site_id) do nothing;
  select * into v_lease from public.site_agent_leases where site_id = v_site for update;

  v_expired := v_lease.lease_expires_at is null or v_lease.lease_expires_at <= now();

  if v_lease.holder_agent_id is not null
     and v_lease.holder_agent_id <> v_agent.id
     and not v_expired then
    -- a different agent holds a LIVE lease -> caller is standby
    return jsonb_build_object('granted', false, 'generation', v_lease.generation,
                              'mode', 'standby', 'holder_agent_id', v_lease.holder_agent_id,
                              'lease_expires_at', v_lease.lease_expires_at);
  end if;

  if v_lease.holder_agent_id = v_agent.id and not v_expired then
    v_gen := v_lease.generation;                 -- renew: same holder, keep the fencing token
  else
    v_gen := v_lease.generation + 1;             -- takeover (or first acquire): bump the fencing token
  end if;

  update public.site_agent_leases
     set holder_agent_id = v_agent.id,
         generation = v_gen,
         acquired_at = case when v_lease.holder_agent_id is distinct from v_agent.id or v_expired
                            then now() else v_lease.acquired_at end,
         renewed_at = now(),
         lease_expires_at = now() + make_interval(secs => v_secs),
         lease_seconds = v_secs,
         updated_at = now()
   where site_id = v_site;

  return jsonb_build_object('granted', true, 'generation', v_gen, 'mode', 'primary',
                            'holder_agent_id', v_agent.id,
                            'lease_expires_at', now() + make_interval(secs => v_secs));
end $$;

-- Is (agent, generation) the CURRENT fenced authority for its site? A write tagged with a
-- stale generation returns false and must be rejected — this is the anti-dual-active guard.
-- Feature OFF -> always true (single-agent). Called by ingestion/health writes (future agent).
create or replace function public.wl_agent_is_fenced_authority(
  p_agent_id uuid, p_generation bigint
) returns boolean
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_agent public.agents; v_enabled boolean;
begin
  v_agent := (select a from public.agents a where a.id = p_agent_id);
  if v_agent.id is null then
    return false;
  end if;
  select multi_agent_enabled into v_enabled from public.sites where id = v_agent.site_id;
  if not coalesce(v_enabled, false) then
    return true;                                 -- single-agent mode: trivially authoritative
  end if;
  return exists (
    select 1 from public.site_agent_leases l
     where l.site_id = v_agent.site_id
       and l.holder_agent_id = p_agent_id
       and l.generation = p_generation
       and l.lease_expires_at > now());
end $$;

create or replace function public.wl_agent_release_lease(p_agent_id uuid, p_agent_key text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_agent public.agents;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  update public.site_agent_leases
     set holder_agent_id = null, lease_expires_at = now(), updated_at = now()
   where site_id = v_agent.site_id and holder_agent_id = v_agent.id;
  return jsonb_build_object('released', found);
end $$;

alter table public.site_agent_leases enable row level security;
drop policy if exists portal_read_site_agent_leases on public.site_agent_leases;
create policy portal_read_site_agent_leases on public.site_agent_leases
  for select to authenticated using (public.wl_is_member(tenant_id));

-- agent-facing lease RPCs authenticate via the agent key (wl_auth_agent), so they are
-- available to the agent roles exactly like the other agent endpoints.
revoke all on function public.wl_agent_acquire_lease(uuid,text,int) from public;
grant execute on function public.wl_agent_acquire_lease(uuid,text,int) to anon, authenticated;
revoke all on function public.wl_agent_is_fenced_authority(uuid,bigint) from public;
grant execute on function public.wl_agent_is_fenced_authority(uuid,bigint) to anon, authenticated, service_role;
revoke all on function public.wl_agent_release_lease(uuid,text) from public;
grant execute on function public.wl_agent_release_lease(uuid,text) to anon, authenticated;

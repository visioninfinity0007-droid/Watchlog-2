-- =====================================================================
-- 0080 — Journey Intelligence v2: topology-aware, concurrency-separated, confidence-scored
-- (final integration pass, item 3).
--
-- v1 (0070) was site-wide timing-only sessionization that admitted concurrent people could
-- merge. v2 links an episode to a predecessor ONLY when the site's camera topology allows that
-- transition within its per-edge temporal bounds and the same object class — so an impossible
-- hop (Director -> unrelated remote camera in 1 s) is rejected, and two simultaneous subjects
-- are not fused into one journey. Each journey carries a confidence, the signals behind it, its
-- uncertainty, its source episodes, and a STABLE business identity across re-derivation.
--
-- Where a site has NO topology configured, v2 falls back to a timing-only link but labels the
-- journey explicitly as lower-confidence fallback (never pretends the topology model ran).
--
-- Reporting language for these is "plausible movement journeys", never "unique visitors".
-- =====================================================================

create table if not exists public.camera_topology (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  from_camera_id uuid not null references public.cameras(id) on delete cascade,
  to_camera_id   uuid not null references public.cameras(id) on delete cascade,
  min_seconds   int not null default 1,        -- a real walk takes at least this long
  max_seconds   int not null default 300,      -- and no longer than this to be one movement
  bidirectional boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (site_id, from_camera_id, to_camera_id)
);
alter table public.camera_topology enable row level security;

-- journeys gains confidence + the reasoning + a stable identity column.
alter table public.journeys add column if not exists confidence numeric;
alter table public.journeys add column if not exists reasons jsonb not null default '{}'::jsonb;
alter table public.journeys add column if not exists uncertainty text;
alter table public.journeys add column if not exists first_camera_id uuid;
-- Stable business identity: the root episode's (start, camera, class) is deterministic for the
-- same input, so re-derivation refreshes the same journey row instead of churning its id.
create unique index if not exists journeys_identity_idx
  on public.journeys (site_id, object_class, started_at, first_camera_id);

-- Tenant-guarded topology authoring.
create or replace function public.wl_set_camera_topology(
  p_site_id uuid, p_from uuid, p_to uuid, p_min int default 1, p_max int default 300, p_bidirectional boolean default true
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_assert_my_site(p_site_id);
begin
  if not exists (select 1 from cameras where id = p_from and site_id = p_site_id)
     or not exists (select 1 from cameras where id = p_to and site_id = p_site_id) then
    raise exception 'both cameras must belong to this site' using errcode = '22023';
  end if;
  insert into camera_topology (tenant_id, site_id, from_camera_id, to_camera_id, min_seconds, max_seconds, bidirectional)
  values (v_tenant, p_site_id, p_from, p_to, greatest(0,p_min), greatest(1,p_max), coalesce(p_bidirectional,true))
  on conflict (site_id, from_camera_id, to_camera_id) do update
    set min_seconds = excluded.min_seconds, max_seconds = excluded.max_seconds, bidirectional = excluded.bidirectional;
  return jsonb_build_object('ok', true);
end $$;
revoke all on function public.wl_set_camera_topology(uuid,uuid,uuid,int,int,boolean) from public, anon;
grant execute on function public.wl_set_camera_topology(uuid,uuid,uuid,int,int,boolean) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- Derive journeys v2. Upserts on the stable identity (no id churn).
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_journeys(
  p_site_id uuid, p_from timestamptz, p_to timestamptz, p_hop_gap_seconds integer default 300
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows integer; v_topo boolean;
begin
  v_topo := exists (select 1 from camera_topology where site_id = p_site_id);
  -- No window-delete: the upsert below refreshes each journey on its STABLE identity
  -- (site, object_class, start, first camera), so re-derivation keeps the same id (no churn).
  -- Events are append-only, so a journey present in a prior run stays valid.

  with recursive e as (
    select ep.id, ep.tenant_id, ep.site_id, ep.camera_id, ep.object_class, ep.started_at, ep.ended_at,
           coalesce(c.name,'unassigned') camname
      from episodes ep left join cameras c on c.id = ep.camera_id
     where ep.site_id = p_site_id and ep.episode_type = 'presence'
       and ep.started_at >= p_from and ep.started_at < p_to
  ),
  -- each episode's best valid predecessor (topology-gated, or timing fallback)
  linked as (
    select e.*, (
      select p.id from e p
       where p.ended_at <= e.started_at and p.camera_id is distinct from e.camera_id
         and coalesce(p.object_class,'') = coalesce(e.object_class,'')
         and (
           case when v_topo then exists (
             select 1 from camera_topology t
              where t.site_id = p_site_id
                and ((t.from_camera_id = p.camera_id and t.to_camera_id = e.camera_id)
                     or (t.bidirectional and t.from_camera_id = e.camera_id and t.to_camera_id = p.camera_id))
                and extract(epoch from (e.started_at - p.ended_at)) between t.min_seconds and t.max_seconds)
           else extract(epoch from (e.started_at - p.ended_at)) <= p_hop_gap_seconds end)
       order by p.ended_at desc limit 1) as pred_id
      from e
  ),
  up as (
    select id as node, id as anc, pred_id from linked
    union all
    select u.node, l.id, l.pred_id from up u join linked l on l.id = u.pred_id
  ),
  root as (select node, anc as root_id from up where pred_id is null),
  grouped as (
    select r.root_id, l.tenant_id, l.site_id, l.object_class, l.camera_id, l.camname, l.id, l.started_at, l.ended_at
      from linked l join root r on r.node = l.id
  ),
  agg as (
    select root_id, (array_agg(tenant_id))[1] tenant_id, site_id,
           min(started_at) started_at, max(ended_at) ended_at, max(object_class) object_class,
           (array_agg(camname order by started_at))[1] first_name,
           (array_agg(camera_id order by started_at))[1] first_camera_id,
           array_agg(camname order by started_at) path,
           count(distinct camera_id) hops,
           array_agg(id order by started_at) eids
      from grouped group by root_id, site_id
     having count(distinct camera_id) >= 2
  )
  insert into journeys (tenant_id, site_id, started_at, ended_at, object_class, camera_path, hop_count,
                        episode_ids, first_camera_id, confidence, uncertainty, reasons)
  select tenant_id, site_id, started_at, ended_at, object_class, path, hops, eids, first_camera_id,
         case when v_topo then round(least(0.95, 0.7 + 0.05*hops), 2) else 0.4 end,
         case when v_topo then 'low' else 'high' end,
         jsonb_build_object('mode', case when v_topo then 'topology' else 'timing_fallback' end,
                            'topology_used', v_topo, 'hops', hops,
                            'signals', case when v_topo then jsonb_build_array('topology-validated transitions','object-class match','temporal bounds')
                                            else jsonb_build_array('timing-only (no camera topology configured)') end,
                            'label', 'plausible movement journey')
    from agg
  on conflict (site_id, object_class, started_at, first_camera_id) do update
    set ended_at = excluded.ended_at, camera_path = excluded.camera_path, hop_count = excluded.hop_count,
        episode_ids = excluded.episode_ids, confidence = excluded.confidence,
        uncertainty = excluded.uncertainty, reasons = excluded.reasons;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_journeys(uuid,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.wl_derive_journeys(uuid,timestamptz,timestamptz,integer) to service_role;

-- read: include confidence + reasons + uncertainty; language is "plausible movement journeys".
create or replace function public.wl_site_journeys(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('label', 'Plausible movement journeys',
    'journeys', coalesce(jsonb_agg(jsonb_build_object(
      'started_at', j.started_at, 'ended_at', j.ended_at,
      'duration_seconds', extract(epoch from (j.ended_at - j.started_at)),
      'object_class', j.object_class, 'path', to_jsonb(j.camera_path), 'hops', j.hop_count,
      'confidence', j.confidence, 'uncertainty', j.uncertainty, 'reasons', j.reasons,
      'episodes', to_jsonb(j.episode_ids)) order by j.started_at), '[]'::jsonb))
    from journeys j
   where j.site_id = p_site_id and j.started_at >= p_from and j.started_at < p_to;
$$;
revoke all on function public.wl_site_journeys(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_journeys(uuid,timestamptz,timestamptz) to service_role;

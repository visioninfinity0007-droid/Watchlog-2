-- =====================================================================
-- 0070 — Journeys: multi-camera movement paths (the Journey tier of Activity/Episode/
-- Journey/Incident).
--
-- An episode (0065) is presence on ONE camera. A journey links consecutive episodes across
-- DIFFERENT cameras that occur within a short hop window into one movement path — e.g.
-- Reception -> Admin -> Director's office over a couple of minutes is one journey with three
-- hops, not three unrelated presences.
--
-- HONEST LIMITATION (recorded, not hidden): this is timing-based sessionization, NOT identity
-- tracking. WatchLog has no cross-camera re-identification, so two people moving at once can
-- merge into one path. A journey is therefore "a plausible movement through the site by
-- timing", never "person X went here". Provenance (episode_ids) is preserved so a reviewer
-- can always open the underlying episodes.
-- =====================================================================

create table if not exists public.journeys (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  started_at    timestamptz not null,
  ended_at      timestamptz not null,
  object_class  text,
  camera_path   text[] not null default '{}',       -- camera names in order of appearance
  hop_count     int not null default 0,             -- DISTINCT cameras in the path
  episode_ids   uuid[] not null default '{}',       -- PROVENANCE -> episodes.id[]
  metadata_json jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists journeys_site_time_idx on public.journeys (site_id, started_at desc);
alter table public.journeys enable row level security;

-- ---------------------------------------------------------------------
-- Derive journeys for a window. Site-wide (NOT partitioned by camera): presence episodes are
-- ordered by time and chained while each next episode starts within p_hop_gap of the previous
-- episode's end. A chain that touches >= 2 distinct cameras is a journey; single-camera chains
-- are just the episode already captured. Re-derivation rebuilds the window (journeys are a
-- leaf analytical layer, referenced by nothing, so rebuilding their ids is safe).
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_journeys(
  p_site_id uuid, p_from timestamptz, p_to timestamptz, p_hop_gap_seconds integer default 300
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows integer;
begin
  delete from journeys j
   where j.site_id = p_site_id and j.started_at >= p_from and j.started_at < p_to;
  with e as (
    select ep.id, ep.tenant_id, ep.site_id, ep.camera_id, ep.object_class,
           ep.started_at, ep.ended_at, coalesce(c.name, 'unassigned') as camname
      from episodes ep
      left join cameras c on c.id = ep.camera_id
     where ep.site_id = p_site_id and ep.episode_type = 'presence'
       and ep.started_at >= p_from and ep.started_at < p_to
  ),
  marked as (
    select *,
           case when extract(epoch from (started_at - lag(ended_at) over (order by started_at, id)))
                       > p_hop_gap_seconds
                  or lag(ended_at) over (order by started_at, id) is null
                then 1 else 0 end as newgrp
      from e
  ),
  grouped as (
    select *, sum(newgrp) over (order by started_at, id rows unbounded preceding) as grp
      from marked
  )
  insert into journeys (tenant_id, site_id, started_at, ended_at, object_class,
                        camera_path, hop_count, episode_ids)
  select tenant_id, site_id, min(started_at), max(ended_at), max(object_class),
         array_agg(camname order by started_at, id),
         count(distinct camera_id),
         array_agg(id order by started_at, id)
    from grouped
   group by tenant_id, site_id, grp
  having count(distinct camera_id) >= 2;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_journeys(uuid,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.wl_derive_journeys(uuid,timestamptz,timestamptz,integer) to service_role;

-- ---------------------------------------------------------------------
-- Read journeys for a window as a JSON array (service_role) + a tenant-guarded portal wrapper.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_journeys(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
           'started_at', j.started_at, 'ended_at', j.ended_at,
           'duration_seconds', extract(epoch from (j.ended_at - j.started_at)),
           'object_class', j.object_class, 'path', to_jsonb(j.camera_path),
           'hops', j.hop_count, 'episodes', to_jsonb(j.episode_ids)) order by j.started_at), '[]'::jsonb)
    from journeys j
   where j.site_id = p_site_id and j.started_at >= p_from and j.started_at < p_to;
$$;
revoke all on function public.wl_site_journeys(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_journeys(uuid,timestamptz,timestamptz) to service_role;

create or replace function public.wl_my_site_journeys(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  return wl_site_journeys(p_site_id, p_from, p_to);
end $$;
revoke all on function public.wl_my_site_journeys(uuid,timestamptz,timestamptz) from public, anon;
grant execute on function public.wl_my_site_journeys(uuid,timestamptz,timestamptz) to authenticated, service_role;

-- =====================================================================
-- 0065 — Intelligence pipeline: Detection/Event -> Activity -> Episode -> Incident.
--
-- Raw `events` are the source of truth and are NEVER modified or deleted. These are
-- DERIVED, provenance-preserving layers so the customer experience operates at higher
-- semantic levels while every derived row traces back to its source event id(s):
--
--   events (raw)  ->  activities (meaningful observation, 1:1 to a source event)
--                 ->  episodes  (grouped related activity on one camera/context)
--                 ->  intel_incidents (promoted per PER-SITE policy; never global)
--
-- Promotion is driven by `incident_policies` rows keyed to a site — routine daytime
-- Reception activity stays Activity; after-hours restricted-area access, camera video
-- loss, and unusual dwell promote to Incident, per that site's configuration.
-- =====================================================================

create table if not exists public.activities (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  camera_id     uuid references public.cameras(id) on delete set null,
  activity_type text not null,                 -- generic: person | vehicle | camera_fault
  object_class  text,
  semantic      text,                          -- camera-purpose refined label (site-specific)
  occurred_at   timestamptz not null,
  source_event_id bigint not null,             -- PROVENANCE -> events.id (bigint identity)
  metadata_json jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  unique (source_event_id, activity_type)      -- idempotent derivation, no duplicates
);
create index if not exists activities_site_time_idx on public.activities (site_id, occurred_at desc);
alter table public.activities enable row level security;

create table if not exists public.episodes (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  camera_id     uuid references public.cameras(id) on delete set null,
  episode_type  text not null,                 -- presence | access | video_loss
  object_class  text,
  started_at    timestamptz not null,
  ended_at      timestamptz not null,
  detection_count int not null default 1,
  dwell_seconds numeric not null default 0,
  confidence    numeric,
  source_activity_ids uuid[] not null default '{}',   -- PROVENANCE -> activities.id[]
  metadata_json jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index if not exists episodes_site_time_idx on public.episodes (site_id, started_at desc);
alter table public.episodes enable row level security;

create table if not exists public.intel_incidents (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  camera_id     uuid references public.cameras(id) on delete set null,
  incident_type text not null,
  severity      text not null default 'warning' check (severity in ('info','warning','critical')),
  occurred_at   timestamptz not null,
  source_kind   text not null check (source_kind in ('episode','activity','event')),
  source_id     uuid not null,                 -- PROVENANCE -> episodes/activities/events
  policy_id     uuid,
  confidence    numeric,
  status        text not null default 'open' check (status in ('open','acknowledged','resolved')),
  detail_json   jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  -- Stable BUSINESS identity, independent of the episode uuid (which is rebuilt on every
  -- re-derivation). Re-running the pipeline therefore refreshes the same incident row rather
  -- than duplicating it, and an operator's status/ack survives the refresh. (camera_id is
  -- effectively always set for a promoted incident; a null-camera incident is degenerate.)
  unique (site_id, incident_type, camera_id, occurred_at)
);
create index if not exists intel_incidents_site_time_idx on public.intel_incidents (site_id, occurred_at desc);
alter table public.intel_incidents enable row level security;

-- Per-site promotion policy (NEVER global hardcoding). A site with no rows promotes nothing.
create table if not exists public.incident_policies (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  name          text not null,
  match_object_class text,                     -- person | vehicle | null(any)
  match_purpose_ilike text,                    -- camera purpose/name ilike, or null(any)
  match_episode_type text,                     -- presence | access | video_loss | null(any)
  after_hours_only boolean not null default false,
  min_dwell_seconds numeric,                   -- promote when dwell >= this (null = ignore)
  promote_to    text not null,                 -- incident_type label
  severity      text not null default 'warning' check (severity in ('info','warning','critical')),
  enabled       boolean not null default true,
  created_at    timestamptz not null default now()
);
create index if not exists incident_policies_site_idx on public.incident_policies (site_id);
alter table public.incident_policies enable row level security;

-- ---------------------------------------------------------------------
-- Derive activities from events (idempotent; provenance = source_event_id).
-- Semantic label is refined from the camera's purpose, so it stays site-specific.
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_activities(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows integer;
begin
  insert into activities (tenant_id, site_id, camera_id, activity_type, object_class, semantic,
                          occurred_at, source_event_id, metadata_json)
  select s.tenant_id, e.site_id, e.camera_id,
         case when e.event_type in ('video_loss','tamper','disk_error','disk_full') then 'camera_fault'
              else e.event_type end,
         e.event_type,
         coalesce(nullif(c.purpose,''), 'unspecified') || ':' || e.event_type,
         e.device_ts, e.id,
         jsonb_build_object('camera_name', c.name, 'camera_purpose', c.purpose)
    from events e
    join sites s on s.id = e.site_id
    left join cameras c on c.id = e.camera_id
   where e.site_id = p_site_id and e.device_ts >= p_from and e.device_ts < p_to
  on conflict (source_event_id, activity_type) do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_activities(uuid,timestamptz,timestamptz) from public, anon;

-- ---------------------------------------------------------------------
-- Group activities into episodes per camera (gap-based; >10 min = new episode).
-- Deterministic; provenance = source_activity_ids. Re-runs replace the window's episodes.
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_episodes(
  p_site_id uuid, p_from timestamptz, p_to timestamptz, p_gap_seconds integer default 600
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows integer;
begin
  delete from episodes e
   where e.site_id = p_site_id and e.started_at >= p_from and e.started_at < p_to;
  with a as (
    select id, tenant_id, site_id, camera_id, object_class, occurred_at,
           case when object_class in ('video_loss','tamper') then 'video_loss'
                else 'presence' end as etype
      from activities
     where site_id = p_site_id and occurred_at >= p_from and occurred_at < p_to
  ),
  marked as (
    select *,
           case when extract(epoch from (occurred_at - lag(occurred_at)
                       over (partition by camera_id, etype order by occurred_at))) > p_gap_seconds
                  or lag(occurred_at) over (partition by camera_id, etype order by occurred_at) is null
                then 1 else 0 end as newgrp
      from a
  ),
  grouped as (
    select *, sum(newgrp) over (partition by camera_id, etype order by occurred_at
                                rows unbounded preceding) as grp
      from marked
  )
  insert into episodes (tenant_id, site_id, camera_id, episode_type, object_class,
                        started_at, ended_at, detection_count, dwell_seconds,
                        confidence, source_activity_ids)
  select tenant_id, site_id, camera_id, etype, max(object_class),
         min(occurred_at), max(occurred_at), count(*),
         extract(epoch from (max(occurred_at) - min(occurred_at))),
         least(1.0, 0.5 + count(*)::numeric/20), array_agg(id order by occurred_at)
    from grouped
   group by tenant_id, site_id, camera_id, etype, grp;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) from public, anon;

-- ---------------------------------------------------------------------
-- Promote incidents from episodes per the SITE's policy (idempotent).
-- after_hours = outside the site's 08:00-19:00 local window (v0; schedule model later).
-- ---------------------------------------------------------------------
create or replace function public.wl_promote_incidents(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows integer; v_tz text;
begin
  select timezone into v_tz from sites where id = p_site_id;
  insert into intel_incidents (tenant_id, site_id, camera_id, incident_type, severity,
                               occurred_at, source_kind, source_id, policy_id, confidence, detail_json)
  select ep.tenant_id, ep.site_id, ep.camera_id, pol.promote_to, pol.severity,
         ep.started_at, 'episode', ep.id, pol.id, ep.confidence,
         jsonb_build_object('policy', pol.name, 'dwell_seconds', ep.dwell_seconds,
                            'detections', ep.detection_count, 'camera', c.name)
    from episodes ep
    join sites s on s.id = ep.site_id
    left join cameras c on c.id = ep.camera_id
    join incident_policies pol on pol.site_id = ep.site_id and pol.enabled
   where ep.site_id = p_site_id and ep.started_at >= p_from and ep.started_at < p_to
     and (pol.match_object_class is null or pol.match_object_class = ep.object_class)
     and (pol.match_episode_type is null or pol.match_episode_type = ep.episode_type)
     and (pol.match_purpose_ilike is null
          or coalesce(c.purpose,'') ilike pol.match_purpose_ilike
          or coalesce(c.name,'') ilike pol.match_purpose_ilike)
     and (pol.min_dwell_seconds is null or ep.dwell_seconds >= pol.min_dwell_seconds)
     and (not pol.after_hours_only
          or (ep.started_at at time zone v_tz)::time < time '08:00'
          or (ep.started_at at time zone v_tz)::time > time '19:00')
  -- Re-derivation rebuilds episode uuids, so match on the incident's stable identity and
  -- REFRESH its provenance pointer + policy mapping. status is deliberately NOT touched, so
  -- an operator's acknowledge/resolve is preserved across a report refresh.
  on conflict (site_id, incident_type, camera_id, occurred_at) do update
    set source_id  = excluded.source_id,
        policy_id  = excluded.policy_id,
        severity   = excluded.severity,
        confidence = excluded.confidence,
        detail_json = excluded.detail_json;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_promote_incidents(uuid,timestamptz,timestamptz) from public, anon;

grant execute on function public.wl_derive_activities(uuid,timestamptz,timestamptz) to service_role;
grant execute on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) to service_role;
grant execute on function public.wl_promote_incidents(uuid,timestamptz,timestamptz) to service_role;

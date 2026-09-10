-- =====================================================================
-- 0073 — Visitor / staff inference (item 1). A GENERIC, multi-factor, configurable layer
-- over journeys (0070) and episodes (0065). It classifies MOVEMENT UNITS, never people:
--
--   probable_visitor | probable_regular_staff | unclassified
--
-- HONESTY (hard rules, enforced by design):
--   * No identity, no names. WatchLog has no cross-camera re-identification, so "recurrence"
--     is a PATTERN recurring across days (same area-set + time-of-day), NOT the same person.
--   * "Long stay" is NOT an absolute employee rule — it is one weighted factor among several.
--   * Ambiguous evidence resolves to `unclassified`, not a guess.
--   * Every row carries confidence, the FULL factor record, source journey/episode/activity
--     ids (provenance), and the config version used.
--
-- Weights/thresholds live in `inference_config` (per-site override or a default row), so the
-- model is tunable without editing product code (feeds report calibration, item 10).
-- =====================================================================

create table if not exists public.inference_config (
  id          uuid primary key default gen_random_uuid(),
  site_id     uuid references public.sites(id) on delete cascade,   -- null = platform default
  version     text not null,
  config      jsonb not null,
  created_at  timestamptz not null default now(),
  unique (site_id, version)
);
alter table public.inference_config enable row level security;

insert into public.inference_config (site_id, version, config) values
 (null, 'inference-v1', jsonb_build_object(
    'long_stay_seconds', 7200, 'short_visit_seconds', 1800, 'recurrence_days_staff', 3, 'margin', 2.0,
    'weights', jsonb_build_object('reaches_management', 2.0, 'long_stay', 1.5, 'recurrence', 2.5,
                                  'within_hours', 0.5, 'reception_only', 2.0, 'short_visit', 1.5,
                                  'single_occurrence', 1.0)))
on conflict (site_id, version) do nothing;

create table if not exists public.entity_inferences (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  site_id       uuid not null references public.sites(id) on delete cascade,
  occurred_at   timestamptz not null,
  classification text not null check (classification in ('probable_visitor','probable_regular_staff','unclassified')),
  confidence    numeric not null default 0,
  unit_kind     text not null,                 -- journey | presence
  factors_json  jsonb not null default '{}'::jsonb,
  source_journey_id uuid,                       -- PROVENANCE (nullable; journeys are rebuilt)
  source_episode_ids uuid[] not null default '{}',
  source_activity_ids uuid[] not null default '{}',
  config_version text not null,
  created_at    timestamptz not null default now()
);
create index if not exists entity_inferences_site_time_idx on public.entity_inferences (site_id, occurred_at desc);
alter table public.entity_inferences enable row level security;

-- ---------------------------------------------------------------------
-- Derive inferences for a window. Re-derivation rebuilds the window (leaf layer).
-- Multi-DAY windows give recurrence its meaning; a single day yields no recurrence signal.
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_entity_inferences(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $$
declare
  v_rows int; v_tz text; v_cfg jsonb; v_ver text;
  w_mgmt numeric; w_long numeric; w_recur numeric; w_hours numeric;
  w_recep numeric; w_short numeric; w_single numeric;
  v_long int; v_short int; v_recur_days int; v_margin numeric;
begin
  select timezone into v_tz from sites where id = p_site_id;
  select config, version into v_cfg, v_ver from inference_config
    where site_id = p_site_id or site_id is null
    order by (site_id is not null) desc limit 1;
  v_long   := coalesce((v_cfg->>'long_stay_seconds')::int, 7200);
  v_short  := coalesce((v_cfg->>'short_visit_seconds')::int, 1800);
  v_recur_days := coalesce((v_cfg->>'recurrence_days_staff')::int, 3);
  v_margin := coalesce((v_cfg->>'margin')::numeric, 2.0);
  w_mgmt   := coalesce((v_cfg->'weights'->>'reaches_management')::numeric, 2.0);
  w_long   := coalesce((v_cfg->'weights'->>'long_stay')::numeric, 1.5);
  w_recur  := coalesce((v_cfg->'weights'->>'recurrence')::numeric, 2.5);
  w_hours  := coalesce((v_cfg->'weights'->>'within_hours')::numeric, 0.5);
  w_recep  := coalesce((v_cfg->'weights'->>'reception_only')::numeric, 2.0);
  w_short  := coalesce((v_cfg->'weights'->>'short_visit')::numeric, 1.5);
  w_single := coalesce((v_cfg->'weights'->>'single_occurrence')::numeric, 1.0);

  delete from entity_inferences e
   where e.site_id = p_site_id and e.occurred_at >= p_from and e.occurred_at < p_to;

  with ctx as (
    select coalesce(reception_camera_ids,'{}') recep, coalesce(management_camera_ids,'{}') mgmt,
           open_time, close_time, coalesce(overnight,false) overnight
      from site_business_context where site_id = p_site_id
  ),
  -- movement units: each journey, plus each presence episode NOT already inside a journey
  units as (
    select 'journey'::text kind, j.id ref_id, j.tenant_id, j.site_id, j.started_at, j.ended_at,
           (select array_agg(distinct ep.camera_id) from episodes ep where ep.id = any(j.episode_ids)) cam_ids,
           j.episode_ids
      from journeys j
     where j.site_id = p_site_id and j.started_at >= p_from and j.started_at < p_to
    union all
    select 'presence', ep.id, ep.tenant_id, ep.site_id, ep.started_at, ep.ended_at,
           array[ep.camera_id], array[ep.id]
      from episodes ep
     where ep.site_id = p_site_id and ep.episode_type = 'presence'
       and ep.started_at >= p_from and ep.started_at < p_to
       and not exists (select 1 from journeys j2 where j2.site_id = p_site_id
                         and j2.started_at >= p_from and j2.started_at < p_to
                         and ep.id = any(j2.episode_ids))
  ),
  feat as (
    select u.*, ctx.recep, ctx.mgmt, ctx.open_time, ctx.close_time, ctx.overnight,
           extract(epoch from (u.ended_at - u.started_at))::numeric dur,
           coalesce(cardinality((select array_agg(distinct cid) from unnest(u.cam_ids) cid where cid is not null)),0) distinct_cams,
           (u.started_at at time zone v_tz)::date local_day,
           (extract(hour from (u.started_at at time zone v_tz))::int / 3) hour_bucket,
           -- purpose set signature (area-set, honest pattern proxy — NOT identity)
           coalesce((select string_agg(distinct coalesce(c.purpose,'?'),'|' order by coalesce(c.purpose,'?'))
                       from cameras c where c.id = any(u.cam_ids)),'?') sig,
           -- reaches a management area (business-context group OR purpose heuristic)
           (u.cam_ids && ctx.mgmt or exists (select 1 from cameras c where c.id = any(u.cam_ids)
                and (c.purpose ilike '%manage%' or c.purpose ilike '%director%' or c.purpose ilike '%admin%'))) reaches_mgmt,
           -- reception-only: every camera is a reception camera (group OR purpose)
           (not exists (select 1 from cameras c where c.id = any(u.cam_ids)
                and not (c.id = any(ctx.recep) or c.purpose ilike '%recept%' or c.purpose ilike '%entrance%|%lobby%'
                         or c.purpose ilike '%lobby%' or c.purpose ilike '%entrance%'))) reception_only,
           case when ctx.open_time is null then null
                when ctx.overnight then ((u.started_at at time zone v_tz)::time >= ctx.open_time
                                         or (u.started_at at time zone v_tz)::time < ctx.close_time)
                else ((u.started_at at time zone v_tz)::time >= ctx.open_time
                      and (u.started_at at time zone v_tz)::time < ctx.close_time) end within_hours
      from units u cross join ctx
  ),
  recur as (
    select sig, hour_bucket, count(distinct local_day) recur_days from feat group by sig, hour_bucket
  ),
  scored as (
    select f.*, r.recur_days,
           ((case when f.reaches_mgmt then w_mgmt else 0 end)
            + (case when f.dur >= v_long then w_long else 0 end)
            + (case when r.recur_days >= v_recur_days then w_recur else 0 end)
            + (case when coalesce(f.within_hours,false) then w_hours else 0 end)) staff_score,
           ((case when f.reception_only and f.distinct_cams <= 1 then w_recep else 0 end)
            + (case when f.dur > 0 and f.dur <= v_short then w_short else 0 end)
            + (case when r.recur_days < 2 then w_single else 0 end)) visitor_score
      from feat f join recur r using (sig, hour_bucket)
  )
  insert into entity_inferences (tenant_id, site_id, occurred_at, classification, confidence,
      unit_kind, factors_json, source_journey_id, source_episode_ids, source_activity_ids, config_version)
  select s.tenant_id, s.site_id, s.started_at,
         case when s.staff_score - s.visitor_score >= v_margin then 'probable_regular_staff'
              when s.visitor_score - s.staff_score >= v_margin then 'probable_visitor'
              else 'unclassified' end,
         round(least(1.0, abs(s.staff_score - s.visitor_score) / (2.0 * v_margin)), 2),
         s.kind,
         jsonb_build_object('reaches_management', s.reaches_mgmt, 'duration_seconds', round(s.dur),
                            'long_stay', s.dur >= v_long, 'short_visit', s.dur > 0 and s.dur <= v_short,
                            'distinct_cameras', s.distinct_cams, 'reception_only', s.reception_only,
                            'within_hours', s.within_hours, 'recurrence_days', s.recur_days,
                            'staff_score', s.staff_score, 'visitor_score', s.visitor_score,
                            'area_signature', s.sig),
         case when s.kind = 'journey' then s.ref_id else null end,
         s.episode_ids,
         coalesce((select array_agg(distinct aid) from episodes ep, unnest(ep.source_activity_ids) aid
                    where ep.id = any(s.episode_ids)), '{}'),    -- activity provenance via the unit's episodes
         v_ver
    from scored s;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_entity_inferences(uuid,timestamptz,timestamptz) from public, anon;
grant execute on function public.wl_derive_entity_inferences(uuid,timestamptz,timestamptz) to service_role;

-- Read summary for a window (counts + rows), for the daily dataset and the portal.
create or replace function public.wl_site_entity_inferences(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'summary', jsonb_build_object(
      'probable_visitor', count(*) filter (where classification='probable_visitor'),
      'probable_regular_staff', count(*) filter (where classification='probable_regular_staff'),
      'unclassified', count(*) filter (where classification='unclassified')),
    'entities', coalesce(jsonb_agg(jsonb_build_object(
        'classification', classification, 'confidence', confidence, 'unit_kind', unit_kind,
        'occurred_at', occurred_at, 'factors', factors_json, 'config_version', config_version)
        order by occurred_at) filter (where true), '[]'::jsonb))
  from entity_inferences
  where site_id = p_site_id and occurred_at >= p_from and occurred_at < p_to;
$$;
revoke all on function public.wl_site_entity_inferences(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_entity_inferences(uuid,timestamptz,timestamptz) to service_role;

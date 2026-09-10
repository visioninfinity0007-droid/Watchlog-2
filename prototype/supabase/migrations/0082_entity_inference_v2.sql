-- =====================================================================
-- 0082 — Visitor/staff inference v2 (final integration pass, item 4).
--
-- Keeps 0073's honesty (recurrence is an area/time PATTERN, not the same person; a recurring
-- pattern is never counted as one identified employee) and adds the signals the requirement
-- asks for: the movement JOURNEY's confidence (0080) now dampens the inference confidence, and
-- every row exposes confidence, the evidence factors, the journey confidence, the monitoring
-- coverage of the window, and the site's calibration state. Results stay
-- probable_visitor / probable_regular_staff / unclassified, and are labelled an ESTIMATED
-- behavioral classification (not an identity) until field calibration.
-- =====================================================================

create or replace function public.wl_derive_entity_inferences(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $$
declare
  v_rows int; v_tz text; v_cfg jsonb; v_ver text;
  w_mgmt numeric; w_long numeric; w_recur numeric; w_hours numeric;
  w_recep numeric; w_short numeric; w_single numeric;
  v_long int; v_short int; v_recur_days int; v_margin numeric;
  v_cov numeric; v_calib text;
begin
  select timezone into v_tz from sites where id = p_site_id;
  select config, version into v_cfg, v_ver from inference_config
    where site_id = p_site_id or site_id is null order by (site_id is not null) desc limit 1;
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

  -- window quality signals fed into confidence
  v_cov := coalesce((wl_site_coverage_report(p_site_id, p_from, p_to)->>'coverage_ratio')::numeric, 1);
  v_calib := case
    when exists (select 1 from inference_config where site_id = p_site_id) then 'tuned'
    when exists (select 1 from report_review_feedback where site_id = p_site_id) then 'reviewed'
    else 'default_uncalibrated' end;

  delete from entity_inferences e
   where e.site_id = p_site_id and e.occurred_at >= p_from and e.occurred_at < p_to;

  with ctx as (
    select coalesce(reception_camera_ids,'{}') recep, coalesce(management_camera_ids,'{}') mgmt,
           open_time, close_time, coalesce(overnight,false) overnight
      from site_business_context where site_id = p_site_id
  ),
  units as (
    select 'journey'::text kind, j.id ref_id, j.tenant_id, j.site_id, j.started_at, j.ended_at,
           (select array_agg(distinct ep.camera_id) from episodes ep where ep.id = any(j.episode_ids)) cam_ids,
           j.episode_ids, j.confidence journey_conf
      from journeys j
     where j.site_id = p_site_id and j.started_at >= p_from and j.started_at < p_to
    union all
    select 'presence', ep.id, ep.tenant_id, ep.site_id, ep.started_at, ep.ended_at,
           array[ep.camera_id], array[ep.id], null::numeric
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
           coalesce((select string_agg(distinct coalesce(c.purpose,'?'),'|' order by coalesce(c.purpose,'?'))
                       from cameras c where c.id = any(u.cam_ids)),'?') sig,
           (u.cam_ids && ctx.mgmt or exists (select 1 from cameras c where c.id = any(u.cam_ids)
                and (c.purpose ilike '%manage%' or c.purpose ilike '%director%' or c.purpose ilike '%admin%'))) reaches_mgmt,
           (not exists (select 1 from cameras c where c.id = any(u.cam_ids)
                and not (c.id = any(ctx.recep) or c.purpose ilike '%recept%'
                         or c.purpose ilike '%lobby%' or c.purpose ilike '%entrance%'))) reception_only,
           case when ctx.open_time is null then null
                when ctx.overnight then ((u.started_at at time zone v_tz)::time >= ctx.open_time
                                         or (u.started_at at time zone v_tz)::time < ctx.close_time)
                else ((u.started_at at time zone v_tz)::time >= ctx.open_time
                      and (u.started_at at time zone v_tz)::time < ctx.close_time) end within_hours
      from units u cross join ctx
  ),
  recur as (select sig, hour_bucket, count(distinct local_day) recur_days from feat group by sig, hour_bucket),
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
         -- confidence = classification margin, DAMPENED by journey confidence + coverage
         round(least(1.0, abs(s.staff_score - s.visitor_score) / (2.0 * v_margin))
               * coalesce(s.journey_conf, 0.6) * v_cov, 2),
         s.kind,
         jsonb_build_object('classification_kind', 'estimated_behavioral',
                            'reaches_management', s.reaches_mgmt, 'duration_seconds', round(s.dur),
                            'long_stay', s.dur >= v_long, 'short_visit', s.dur > 0 and s.dur <= v_short,
                            'distinct_cameras', s.distinct_cams, 'reception_only', s.reception_only,
                            'within_hours', s.within_hours, 'recurrence_days', s.recur_days,
                            'staff_score', s.staff_score, 'visitor_score', s.visitor_score,
                            'area_signature', s.sig,
                            'journey_confidence', s.journey_conf,
                            'monitoring_coverage', v_cov,
                            'calibration_state', v_calib),
         case when s.kind = 'journey' then s.ref_id else null end,
         s.episode_ids,
         coalesce((select array_agg(distinct aid) from episodes ep, unnest(ep.source_activity_ids) aid
                    where ep.id = any(s.episode_ids)), '{}'),
         v_ver
    from scored s;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_derive_entity_inferences(uuid,timestamptz,timestamptz) from public, anon;
grant execute on function public.wl_derive_entity_inferences(uuid,timestamptz,timestamptz) to service_role;

-- Read summary now surfaces the method label + calibration state + coverage at the top.
create or replace function public.wl_site_entity_inferences(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'method', 'estimated behavioral classification (movement patterns, not identities)',
    'calibration_state', (select factors_json->>'calibration_state' from entity_inferences
                            where site_id = p_site_id and occurred_at >= p_from and occurred_at < p_to limit 1),
    'monitoring_coverage', (select (factors_json->>'monitoring_coverage')::numeric from entity_inferences
                            where site_id = p_site_id and occurred_at >= p_from and occurred_at < p_to limit 1),
    'summary', jsonb_build_object(
      'probable_visitor', count(*) filter (where classification='probable_visitor'),
      'probable_regular_staff', count(*) filter (where classification='probable_regular_staff'),
      'unclassified', count(*) filter (where classification='unclassified')),
    'entities', coalesce(jsonb_agg(jsonb_build_object(
        'classification', classification, 'confidence', confidence, 'unit_kind', unit_kind,
        'occurred_at', occurred_at, 'factors', factors_json, 'config_version', config_version)
        order by occurred_at), '[]'::jsonb))
  from entity_inferences
  where site_id = p_site_id and occurred_at >= p_from and occurred_at < p_to;
$$;
revoke all on function public.wl_site_entity_inferences(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_site_entity_inferences(uuid,timestamptz,timestamptz) to service_role;

-- Chai Wala camera analytics-quality contract.
-- Scores glare, overexposure, occlusion, obstruction, camera angle and count/table confidence.
-- Recommendations require repeated evidence and never claim a measured accuracy percentage without human validation.

CREATE OR REPLACE FUNCTION public.wl_restaurant_quality_summary(p_site_id uuid, p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_c record;
  v_cameras jsonb:='[]'::jsonb;
  v_recs jsonb:='[]'::jsonb;
  v_total integer:=0;
  v_dining_scored integer:=0;
begin
  if p_start is null or p_end is null or p_end<=p_start then
    raise exception 'invalid quality window' using errcode='22023';
  end if;

  for v_c in
    with scored as (
      select
        o.camera_id,c.name as camera,o.camera_role,
        o.activity->'analytics_quality' aq
      from public.restaurant_visual_observations o
      join public.cameras c on c.id=o.camera_id
      where o.site_id=p_site_id and o.tenant_id=v_tenant
        and o.captured_at>=p_start and o.captured_at<p_end
        and jsonb_typeof(o.activity->'analytics_quality')='object'
    )
    select
      camera_id,camera,camera_role,
      count(*)::integer samples,
      round(avg(nullif(aq->>'visibility_quality','')::numeric),3) visibility_quality,
      round(avg(nullif(aq->>'people_count_confidence','')::numeric),3) people_count_confidence,
      round(avg(nullif(aq->>'table_tracking_confidence','')::numeric),3) table_tracking_confidence,
      round(avg(nullif(aq->>'glare_level','')::numeric),3) glare_level,
      round(avg(nullif(aq->>'overexposure_level','')::numeric),3) overexposure_level,
      round(avg(nullif(aq->>'occlusion_level','')::numeric),3) occlusion_level,
      round(avg(nullif(aq->>'obstruction_level','')::numeric),3) obstruction_level,
      round(avg(nullif(aq->>'camera_angle_adequacy','')::numeric),3) camera_angle_adequacy,
      round(avg(nullif(aq->>'lighting_uniformity','')::numeric),3) lighting_uniformity,
      count(*) filter(where nullif(aq->>'glare_level','')::numeric>=0.45)::integer glare_frames,
      count(*) filter(where nullif(aq->>'overexposure_level','')::numeric>=0.45)::integer overexposure_frames,
      count(*) filter(where nullif(aq->>'occlusion_level','')::numeric>=0.45)::integer occlusion_frames,
      count(*) filter(where nullif(aq->>'obstruction_level','')::numeric>=0.35)::integer obstruction_frames,
      count(*) filter(where nullif(aq->>'camera_angle_adequacy','')::numeric<=0.55)::integer angle_problem_frames,
      count(*) filter(where camera_role='dining_floor' and nullif(aq->>'people_count_confidence','')::numeric<0.65)::integer low_people_confidence_frames,
      count(*) filter(where camera_role='dining_floor' and nullif(aq->>'table_tracking_confidence','')::numeric<0.65)::integer low_table_confidence_frames,
      coalesce((
        select jsonb_agg(x order by x)
        from (
          select distinct left(b.value,180) x
          from scored s2
          cross join lateral jsonb_array_elements_text(coalesce(s2.aq->'blocked_regions','[]'::jsonb)) b(value)
          where s2.camera_id=scored.camera_id and coalesce(b.value,'')<>''
          limit 8
        ) q
      ),'[]'::jsonb) blocked_regions
    from scored
    group by camera_id,camera,camera_role
    order by camera_role,camera
  loop
    v_total:=v_total+v_c.samples;
    if v_c.camera_role='dining_floor' then v_dining_scored:=v_dining_scored+v_c.samples; end if;

    v_cameras:=v_cameras||jsonb_build_array(jsonb_build_object(
      'camera_id',v_c.camera_id,
      'camera',v_c.camera,
      'role',v_c.camera_role,
      'scored_frames',v_c.samples,
      'visibility_quality',v_c.visibility_quality,
      'people_count_confidence',v_c.people_count_confidence,
      'table_tracking_confidence',v_c.table_tracking_confidence,
      'glare_level',v_c.glare_level,
      'overexposure_level',v_c.overexposure_level,
      'occlusion_level',v_c.occlusion_level,
      'obstruction_level',v_c.obstruction_level,
      'camera_angle_adequacy',v_c.camera_angle_adequacy,
      'lighting_uniformity',v_c.lighting_uniformity,
      'issue_frames',jsonb_build_object(
        'glare',v_c.glare_frames,
        'overexposure',v_c.overexposure_frames,
        'occlusion',v_c.occlusion_frames,
        'obstruction',v_c.obstruction_frames,
        'camera_angle',v_c.angle_problem_frames,
        'low_people_count_confidence',v_c.low_people_confidence_frames,
        'low_table_tracking_confidence',v_c.low_table_confidence_frames
      ),
      'blocked_regions',v_c.blocked_regions
    ));

    if v_c.samples>=3 and (
      v_c.glare_frames::numeric/v_c.samples>=0.20
      or v_c.overexposure_frames::numeric/v_c.samples>=0.20
    ) then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity',case when greatest(v_c.glare_frames,v_c.overexposure_frames)::numeric/v_c.samples>=0.40 then 'high' else 'medium' end,
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Direct glare / overexposure is reducing usable visual detail',
        'evidence',greatest(v_c.glare_frames,v_c.overexposure_frames)||' of '||v_c.samples||' scored frames showed strong glare or overexposure',
        'recommendation','Re-angle the camera or redirect/shield the direct light source so bright bulbs are not aimed into the lens. Verify exposure again after dark before trusting counts.',
        'metrics_impacted',case when v_c.camera_role='dining_floor'
          then jsonb_build_array('visible diner count','table occupancy','table tracking','estimated covers')
          else jsonb_build_array('visual activity classification') end
      ));
    end if;

    if v_c.samples>=3 and v_c.occlusion_frames::numeric/v_c.samples>=0.20 then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity',case when v_c.occlusion_frames::numeric/v_c.samples>=0.40 then 'high' else 'medium' end,
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Repeated occlusion is hiding people or table areas',
        'evidence',v_c.occlusion_frames||' of '||v_c.samples||' scored frames had high occlusion',
        'recommendation','Raise or shift the camera angle to reduce overlap between diners/tables and foreground objects. Keep the important floor/table zones visible from above rather than through people or furniture.',
        'metrics_impacted',case when v_c.camera_role='dining_floor'
          then jsonb_build_array('visible diner count','occupied tables','party size','service timing')
          else jsonb_build_array('visual activity classification') end
      ));
    end if;

    if v_c.samples>=3 and v_c.obstruction_frames::numeric/v_c.samples>=0.15 then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity',case when v_c.obstruction_frames::numeric/v_c.samples>=0.35 then 'high' else 'medium' end,
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Persistent object obstruction is reducing analytics coverage',
        'evidence',v_c.obstruction_frames||' of '||v_c.samples||' scored frames had meaningful obstruction',
        'recommendation','Remove or relocate the persistent blocking object where practical, or shift the camera enough to clear the blocked region while preserving the same business area.',
        'metrics_impacted',case when v_c.camera_role='dining_floor'
          then jsonb_build_array('visible diner count','table occupancy','table utilization')
          else jsonb_build_array('visual activity classification') end,
        'blocked_regions',v_c.blocked_regions
      ));
    end if;

    if v_c.samples>=3 and v_c.angle_problem_frames::numeric/v_c.samples>=0.25 then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity','medium',
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Camera angle is repeatedly weak for the intended analytics role',
        'evidence',v_c.angle_problem_frames||' of '||v_c.samples||' scored frames had low angle adequacy',
        'recommendation',case when v_c.camera_role='dining_floor'
          then 'Tilt or reposition the camera to see the full dining zone and more table surfaces with less perspective overlap. Preserve stable visibility of the calibrated table anchors.'
          else 'Reposition the camera so the primary operating zone is centered and not clipped at the frame edge.' end,
        'metrics_impacted',case when v_c.camera_role='dining_floor'
          then jsonb_build_array('visible diner count','table tracking','estimated covers','service timing')
          else jsonb_build_array('visual activity classification') end
      ));
    end if;

    if v_c.camera_role='dining_floor' and v_c.samples>=3
       and v_c.low_people_confidence_frames::numeric/v_c.samples>=0.25 then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity','high',
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Visible-diner counting confidence is repeatedly low',
        'evidence',v_c.low_people_confidence_frames||' of '||v_c.samples||' scored dining frames had people-count confidence below 0.65',
        'recommendation','Treat customer-count charts as low-confidence until glare, angle and occlusion issues are corrected, then run a manual count validation sample before publishing an accuracy percentage.',
        'metrics_impacted',jsonb_build_array('visible diner count','peak demand by hour','estimated covers')
      ));
    end if;

    if v_c.camera_role='dining_floor' and v_c.samples>=3
       and v_c.low_table_confidence_frames::numeric/v_c.samples>=0.25 then
      v_recs:=v_recs||jsonb_build_array(jsonb_build_object(
        'severity','medium',
        'camera_id',v_c.camera_id,'camera',v_c.camera,'role',v_c.camera_role,
        'issue','Movable-table tracking is repeatedly uncertain',
        'evidence',v_c.low_table_confidence_frames||' of '||v_c.samples||' scored dining frames had table-tracking confidence below 0.65',
        'recommendation','Review the calibrated table anchors and zone boundaries. Because tables are movable, keep anchor zones broad enough for normal movement and use combined_group when adjacent tables are joined for one party.',
        'metrics_impacted',jsonb_build_array('occupied tables','table utilization','table sessions','served sessions','observed time to food')
      ));
    end if;
  end loop;

  return jsonb_build_object(
    'schema','restaurant-analytics-quality-v1',
    'scored_frames',v_total,
    'dining_scored_frames',v_dining_scored,
    'camera_quality',v_cameras,
    'recommendations',v_recs,
    'accuracy_validation',jsonb_build_object(
      'status','not_calibrated_against_human_ground_truth',
      'can_publish_accuracy_percentage',false,
      'note','Model confidence and image quality are not a measured accuracy percentage. Validate representative Floor 1 and Floor 2 frames against human counts before publishing a customer-count accuracy claim.'
    ),
    'measurement_note','Quality scores describe visible image/geometry conditions in analyzed frames. Recommendations require repeated evidence and do not imply that a physical fix has already been made.'
  );
end $function$


revoke execute on function public.wl_restaurant_quality_summary(uuid,timestamptz,timestamptz) from public,anon;
grant execute on function public.wl_restaurant_quality_summary(uuid,timestamptz,timestamptz) to authenticated,service_role;

CREATE OR REPLACE FUNCTION public.wl_restaurant_day(p_site_id uuid, p_date date DEFAULT NULL::date)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text; v_date date; v_local_now timestamp; v_start timestamptz; v_end timestamptz;
  v_hourly jsonb; v_floors jsonb; v_tables jsonb; v_sessions jsonb; v_camera_coverage jsonb;
  v_observations integer; v_table_observations integer; v_business_coverage numeric;
begin
  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  v_type:=coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type<>'restaurant' then return jsonb_build_object('enabled',false,'site_type',v_type); end if;

  v_local_now:=now() at time zone v_site.timezone;
  v_date:=p_date;
  if v_date is null then
    v_date:=v_local_now::date;
    if coalesce(v_ctx.overnight,false) and v_ctx.close_time is not null and v_ctx.open_time is not null
       and v_ctx.close_time<=v_ctx.open_time and v_local_now::time<v_ctx.open_time then
      v_date:=v_date-1;
    end if;
  end if;
  v_start:=(v_date+coalesce(v_ctx.open_time,'00:00'::time)) at time zone v_site.timezone;
  if coalesce(v_ctx.overnight,false) and v_ctx.close_time is not null and v_ctx.open_time is not null
     and v_ctx.close_time<=v_ctx.open_time then
    v_end:=((v_date+1)+v_ctx.close_time) at time zone v_site.timezone;
  else
    v_end:=(v_date+coalesce(v_ctx.close_time,'23:59:59'::time)) at time zone v_site.timezone;
    if v_end<=v_start then v_end:=v_start+interval '1 day'; end if;
  end if;

  select count(*) into v_observations from public.restaurant_visual_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end;
  select count(*) into v_table_observations from public.restaurant_table_observations o
   where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end;

  with hours as (
    select generate_series(v_start,v_end-interval '1 hour',interval '1 hour') hour_start
  ), latest_floor as (
    select distinct on (o.camera_id,date_trunc('minute',o.captured_at))
      date_trunc('minute',o.captured_at) bucket,
      o.camera_id,o.visible_customers,o.occupied_tables
    from public.restaurant_visual_observations o
    where o.site_id=p_site_id and o.tenant_id=v_tenant
      and o.camera_role='dining_floor'
      and o.captured_at>=v_start and o.captured_at<v_end
    order by o.camera_id,date_trunc('minute',o.captured_at),o.captured_at desc
  ), site_minute as (
    select f.bucket,
      sum(f.visible_customers) as visible_customers,
      sum(f.occupied_tables) as occupied_tables,
      count(*) as floor_cameras_observed
    from latest_floor f
    group by f.bucket
    having count(*)=(
      select count(*) from public.restaurant_camera_profiles p
      where p.site_id=p_site_id and p.tenant_id=v_tenant
        and p.enabled and p.analytics_role='dining_floor'
    )
  ), agg as (
    select h.hour_start,
      count(sm.bucket) samples,
      round(avg(sm.visible_customers)::numeric,1) avg_visible_customers,
      max(sm.visible_customers) peak_visible_customers,
      round(avg(sm.occupied_tables)::numeric,1) avg_occupied_tables,
      max(sm.occupied_tables) peak_occupied_tables,
      (
        select round(avg(o.kitchen_load)::numeric,3)
        from public.restaurant_visual_observations o
        where o.site_id=p_site_id and o.tenant_id=v_tenant
          and o.camera_role='kitchen'
          and o.captured_at>=h.hour_start and o.captured_at<h.hour_start+interval '1 hour'
      ) avg_kitchen_load,
      (
        select round(avg(o.handoff_load)::numeric,3)
        from public.restaurant_visual_observations o
        where o.site_id=p_site_id and o.tenant_id=v_tenant
          and o.camera_role='service_handoff'
          and o.captured_at>=h.hour_start and o.captured_at<h.hour_start+interval '1 hour'
      ) avg_handoff_load
    from hours h
    left join site_minute sm
      on sm.bucket>=h.hour_start and sm.bucket<h.hour_start+interval '1 hour'
    group by h.hour_start
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'hour_start',a.hour_start,
    'local_hour',to_char(a.hour_start at time zone v_site.timezone,'HH24:MI'),
    'samples',a.samples,
    'avg_visible_customers',a.avg_visible_customers,
    'peak_visible_customers',a.peak_visible_customers,
    'avg_occupied_tables',a.avg_occupied_tables,
    'peak_occupied_tables',a.peak_occupied_tables,
    'avg_kitchen_load',a.avg_kitchen_load,
    'avg_handoff_load',a.avg_handoff_load,
    'site_total_requires_all_dining_floors',true
  ) order by a.hour_start),'[]'::jsonb) into v_hourly from agg a;

  select coalesce(jsonb_agg(jsonb_build_object(
    'camera_id',f.camera_id,
    'floor',f.floor,
    'samples',f.samples,
    'avg_visible_customers',f.avg_visible_customers,
    'peak_visible_customers',f.peak_visible_customers,
    'avg_occupied_tables',f.avg_occupied_tables,
    'peak_occupied_tables',f.peak_occupied_tables,
    'avg_confidence',f.avg_confidence
  ) order by f.floor),'[]'::jsonb)
  into v_floors
  from (
    select o.camera_id,c.name as floor,count(*) samples,
      round(avg(o.visible_customers)::numeric,1) avg_visible_customers,
      max(o.visible_customers) peak_visible_customers,
      round(avg(o.occupied_tables)::numeric,1) avg_occupied_tables,
      max(o.occupied_tables) peak_occupied_tables,
      round(avg(o.confidence)::numeric,3) avg_confidence
    from public.restaurant_visual_observations o
    join public.cameras c on c.id=o.camera_id
    where o.site_id=p_site_id and o.tenant_id=v_tenant
      and o.camera_role='dining_floor'
      and o.captured_at>=v_start and o.captured_at<v_end
    group by o.camera_id,c.name
  ) f;

  with coverage as (
    select p.camera_id,c.name as camera,p.analytics_role,p.sampling_mode,p.interval_seconds,
      count(o.event_id) as observed_samples,
      case when p.sampling_mode in ('interval','hybrid') and p.interval_seconds is not null
        then greatest(1,ceil(greatest(0,extract(epoch from (least(now(),v_end)-v_start)))/p.interval_seconds)::integer)
        else null end as expected_samples
    from public.restaurant_camera_profiles p
    join public.cameras c on c.id=p.camera_id
    left join public.restaurant_visual_observations o
      on o.camera_id=p.camera_id and o.site_id=p_site_id and o.tenant_id=v_tenant
     and o.captured_at>=v_start and o.captured_at<v_end
    where p.site_id=p_site_id and p.tenant_id=v_tenant and p.enabled
    group by p.camera_id,c.name,p.analytics_role,p.sampling_mode,p.interval_seconds
  )
  select
    coalesce(jsonb_agg(jsonb_build_object(
      'camera_id',camera_id,'camera',camera,'role',analytics_role,
      'sampling_mode',sampling_mode,'interval_seconds',interval_seconds,
      'observed_samples',observed_samples,'expected_samples',expected_samples,
      'coverage_ratio',case when expected_samples is null then null
        else round(least(1.0,observed_samples::numeric/expected_samples),3) end
    ) order by analytics_role,camera),'[]'::jsonb),
    round(avg(case when expected_samples is null then null
      else least(1.0,observed_samples::numeric/expected_samples) end)::numeric,3)
  into v_camera_coverage,v_business_coverage
  from coverage;

  with per_table as (
    select t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order,count(o.event_id) samples,
      count(o.event_id) filter(where o.occupied is true) occupied_samples,
      count(o.event_id) filter(where o.food_present is true) food_samples,
      round(avg(o.customer_count) filter(where o.occupied is true)::numeric,1) avg_party_when_occupied,
      max(o.customer_count) peak_party,round(avg(o.confidence)::numeric,3) avg_confidence
    from public.restaurant_tables t left join public.restaurant_table_observations o
      on o.table_id=t.id and o.captured_at>=v_start and o.captured_at<v_end
    where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active
    group by t.id,t.table_key,t.label,t.camera_id,t.capacity,t.sort_order
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'table_id',p.id,'table_key',p.table_key,'label',p.label,'camera_id',p.camera_id,'capacity',p.capacity,
    'samples',p.samples,'occupied_samples',p.occupied_samples,
    'occupancy_pct',case when p.samples=0 then null else round(100.0*p.occupied_samples/p.samples,1) end,
    'food_present_samples',p.food_samples,'avg_party_when_occupied',p.avg_party_when_occupied,
    'peak_party',p.peak_party,'avg_confidence',p.avg_confidence
  ) order by p.sort_order,p.table_key),'[]'::jsonb) into v_tables from per_table p;

  with ordered as (
    select o.*,lag(o.occupied) over(partition by o.table_id order by o.captured_at) prev_occupied,
      lag(o.captured_at) over(partition by o.table_id order by o.captured_at) prev_at
    from public.restaurant_table_observations o
    where o.site_id=p_site_id and o.tenant_id=v_tenant and o.captured_at>=v_start and o.captured_at<v_end
  ), flagged as (
    select o.*,case when o.occupied is true and (
      coalesce(o.prev_occupied,false)=false or o.prev_at is null or o.captured_at-o.prev_at>interval '20 minutes'
    ) then 1 else 0 end session_start from ordered o
  ), tagged as (
    select f.*,sum(f.session_start) over(partition by f.table_id order by f.captured_at rows unbounded preceding) session_no
    from flagged f
  ), session_rows as (
    select t.table_id,t.session_no,min(t.captured_at) started_at,max(t.captured_at) last_occupied_at,
      min(t.captured_at) filter(where t.food_present is true) first_food_at,max(t.customer_count) peak_party,
      bool_or(t.food_present is true) served,round(avg(t.confidence)::numeric,3) confidence
    from tagged t where t.occupied is true and t.session_no>0 group by t.table_id,t.session_no
  ), named as (
    select s.*,rt.table_key,rt.label,
      extract(epoch from (s.first_food_at-s.started_at))/60.0 time_to_food_min,
      extract(epoch from (s.last_occupied_at-s.started_at))/60.0 observed_dwell_min
    from session_rows s join public.restaurant_tables rt on rt.id=s.table_id
  )
  select jsonb_build_object(
    'count',count(*),'estimated_covers',coalesce(sum(coalesce(n.peak_party,0)),0),
    'served_sessions',count(*) filter(where n.served),
    'avg_observed_time_to_food_minutes',round((avg(n.time_to_food_min) filter(where n.time_to_food_min is not null))::numeric,1),
    'median_observed_time_to_food_minutes',round((percentile_cont(0.5) within group(order by n.time_to_food_min) filter(where n.time_to_food_min is not null))::numeric,1),
    'median_minimum_observed_dwell_minutes',round((percentile_cont(0.5) within group(order by n.observed_dwell_min) filter(where n.observed_dwell_min is not null))::numeric,1),
    'items',coalesce(jsonb_agg(jsonb_build_object(
      'table_key',n.table_key,'label',n.label,'session_no',n.session_no,'started_at',n.started_at,
      'last_occupied_at',n.last_occupied_at,'first_food_at',n.first_food_at,'served',n.served,'peak_party',n.peak_party,
      'observed_time_to_food_minutes',case when n.time_to_food_min is null then null else round(n.time_to_food_min::numeric,1) end,
      'minimum_observed_dwell_minutes',round(n.observed_dwell_min::numeric,1),'confidence',n.confidence
    ) order by n.started_at) filter(where n.table_id is not null),'[]'::jsonb)
  ) into v_sessions from named n;

  return jsonb_build_object(
    'enabled',true,'schema','restaurant-day-v3','service_date',v_date,'timezone',v_site.timezone,
    'window',jsonb_build_object('start',v_start,'end',v_end),
    'hourly',coalesce(v_hourly,'[]'::jsonb),
    'floors',coalesce(v_floors,'[]'::jsonb),
    'tables',coalesce(v_tables,'[]'::jsonb),
    'sessions',coalesce(v_sessions,jsonb_build_object('count',0,'estimated_covers',0,'served_sessions',0,'items','[]'::jsonb)),
    'analytics_quality',public.wl_restaurant_quality_summary(p_site_id,v_start,v_end),
    'data_quality',jsonb_build_object(
      'camera_observations',v_observations,
      'table_observations',v_table_observations,
      'business_analytics_coverage_ratio',v_business_coverage,
      'camera_coverage',coalesce(v_camera_coverage,'[]'::jsonb),
      'has_table_calibration',exists(select 1 from public.restaurant_tables t where t.site_id=p_site_id and t.tenant_id=v_tenant and t.active),
      'measurement_notes',jsonb_build_array(
        'Visible customers are concurrent visible diners, not unique footfall.',
        'Estimated covers sum peak party size across observed table sessions and may be imperfect under occlusion or table movement.',
        'Observed time to food is seated-to-first-food-visible, not POS order-to-serve time.',
        'Minimum observed dwell uses the last occupied observation and can understate the true departure time.',
        'Site-level diner/table totals require a complete same-minute composite across all configured dining-floor cameras.'
      )
    )
  );
end $function$


CREATE OR REPLACE FUNCTION public.wl_restaurant_period(p_site_id uuid, p_days integer DEFAULT 7, p_end_date date DEFAULT NULL::date)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_type text;
  v_current_service_date date;
  v_end_date date;
  v_start_date date;
  v_prev_start date;
  v_prev_end date;
  v_date date;
  v_day jsonb;
  v_days jsonb:='[]'::jsonb;
  v_times jsonb:='[]'::jsonb;
  v_prev_times jsonb:='[]'::jsonb;
  v_day_obs integer;
  v_day_tables integer;
  v_day_covers integer;
  v_day_served integer;
  v_day_sessions integer;
  v_day_peak_visible integer;
  v_day_peak_tables integer;
  v_day_median numeric;
  v_day_coverage numeric;
  v_observed_days integer:=0;
  v_total_covers integer:=0;
  v_total_served integer:=0;
  v_total_sessions integer:=0;
  v_sum_coverage numeric:=0;
  v_prev_observed_days integer:=0;
  v_prev_total_covers integer:=0;
  v_prev_total_served integer:=0;
  v_prev_total_sessions integer:=0;
  v_prev_sum_coverage numeric:=0;
  v_median_time numeric;
  v_prev_median_time numeric;
  v_hour_profile jsonb;
  v_floor_profile jsonb;
  v_table_profile jsonb;
  v_weekday_profile jsonb;
  v_weekly_trend jsonb;
  v_service_distribution jsonb;
  v_busiest_day jsonb;
  v_busiest_hour jsonb;
begin
  if p_days<2 or p_days>31 then
    raise exception 'p_days must be between 2 and 31' using errcode='22023';
  end if;

  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  v_type:=coalesce(v_ctx.site_type,v_site.site_type,'other');
  if v_type<>'restaurant' then return jsonb_build_object('enabled',false,'site_type',v_type); end if;

  v_current_service_date:=(public.wl_restaurant_day(p_site_id,null)->>'service_date')::date;
  v_end_date:=coalesce(p_end_date,v_current_service_date);
  if v_end_date>v_current_service_date then
    raise exception 'period end date cannot be in the future' using errcode='22023';
  end if;
  v_start_date:=v_end_date-(p_days-1);
  v_prev_end:=v_start_date-1;
  v_prev_start:=v_prev_end-(p_days-1);

  v_date:=v_start_date;
  while v_date<=v_end_date loop
    v_day:=public.wl_restaurant_day(p_site_id,v_date);
    v_day_obs:=coalesce((v_day->'data_quality'->>'camera_observations')::integer,0);
    v_day_tables:=coalesce((v_day->'data_quality'->>'table_observations')::integer,0);
    v_day_covers:=coalesce((v_day->'sessions'->>'estimated_covers')::integer,0);
    v_day_served:=coalesce((v_day->'sessions'->>'served_sessions')::integer,0);
    v_day_sessions:=coalesce((v_day->'sessions'->>'count')::integer,0);
    v_day_median:=nullif(v_day->'sessions'->>'median_observed_time_to_food_minutes','')::numeric;
    v_day_coverage:=nullif(v_day->'data_quality'->>'business_analytics_coverage_ratio','')::numeric;

    select max(nullif(h->>'peak_visible_customers','')::integer)
      into v_day_peak_visible
      from jsonb_array_elements(coalesce(v_day->'hourly','[]'::jsonb)) h
     where coalesce((h->>'samples')::integer,0)>0;
    select max(nullif(h->>'peak_occupied_tables','')::integer)
      into v_day_peak_tables
      from jsonb_array_elements(coalesce(v_day->'hourly','[]'::jsonb)) h
     where coalesce((h->>'samples')::integer,0)>0;

    if v_day_obs>0 or v_day_tables>0 then v_observed_days:=v_observed_days+1; end if;
    v_total_covers:=v_total_covers+v_day_covers;
    v_total_served:=v_total_served+v_day_served;
    v_total_sessions:=v_total_sessions+v_day_sessions;
    v_sum_coverage:=v_sum_coverage+coalesce(v_day_coverage,0);

    select v_times || coalesce(jsonb_agg((i->>'observed_time_to_food_minutes')::numeric),'[]'::jsonb)
      into v_times
      from jsonb_array_elements(coalesce(v_day->'sessions'->'items','[]'::jsonb)) i
     where nullif(i->>'observed_time_to_food_minutes','') is not null;

    v_days:=v_days||jsonb_build_array(jsonb_build_object(
      'service_date',v_date,
      'weekday',to_char(v_date,'Dy'),
      'camera_observations',v_day_obs,
      'table_observations',v_day_tables,
      'coverage_ratio',v_day_coverage,
      'peak_visible_diners',v_day_peak_visible,
      'peak_occupied_tables',v_day_peak_tables,
      'estimated_covers',v_day_covers,
      'table_sessions',v_day_sessions,
      'served_sessions',v_day_served,
      'median_observed_time_to_food_minutes',v_day_median,
      'hourly',coalesce(v_day->'hourly','[]'::jsonb),
      'floors',coalesce(v_day->'floors','[]'::jsonb),
      'tables',coalesce(v_day->'tables','[]'::jsonb),
      'camera_coverage',coalesce(v_day->'data_quality'->'camera_coverage','[]'::jsonb)
    ));
    v_date:=v_date+1;
  end loop;

  if jsonb_array_length(v_times)>0 then
    select round((percentile_cont(0.5) within group(order by x))::numeric,1)
      into v_median_time
      from (
        select value::text::numeric x
        from jsonb_array_elements(v_times)
      ) q;
  end if;

  v_date:=v_prev_start;
  while v_date<=v_prev_end loop
    v_day:=public.wl_restaurant_day(p_site_id,v_date);
    v_day_obs:=coalesce((v_day->'data_quality'->>'camera_observations')::integer,0);
    v_day_tables:=coalesce((v_day->'data_quality'->>'table_observations')::integer,0);
    v_day_covers:=coalesce((v_day->'sessions'->>'estimated_covers')::integer,0);
    v_day_served:=coalesce((v_day->'sessions'->>'served_sessions')::integer,0);
    v_day_sessions:=coalesce((v_day->'sessions'->>'count')::integer,0);
    v_day_coverage:=nullif(v_day->'data_quality'->>'business_analytics_coverage_ratio','')::numeric;

    if v_day_obs>0 or v_day_tables>0 then v_prev_observed_days:=v_prev_observed_days+1; end if;
    v_prev_total_covers:=v_prev_total_covers+v_day_covers;
    v_prev_total_served:=v_prev_total_served+v_day_served;
    v_prev_total_sessions:=v_prev_total_sessions+v_day_sessions;
    v_prev_sum_coverage:=v_prev_sum_coverage+coalesce(v_day_coverage,0);

    select v_prev_times || coalesce(jsonb_agg((i->>'observed_time_to_food_minutes')::numeric),'[]'::jsonb)
      into v_prev_times
      from jsonb_array_elements(coalesce(v_day->'sessions'->'items','[]'::jsonb)) i
     where nullif(i->>'observed_time_to_food_minutes','') is not null;

    v_date:=v_date+1;
  end loop;

  if jsonb_array_length(v_prev_times)>0 then
    select round((percentile_cont(0.5) within group(order by x))::numeric,1)
      into v_prev_median_time
      from (
        select value::text::numeric x
        from jsonb_array_elements(v_prev_times)
      ) q;
  end if;

  with rows as (
    select d->>'service_date' service_date,h
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'hourly','[]'::jsonb)) h
    where coalesce((h->>'samples')::integer,0)>0
  ), agg as (
    select h->>'local_hour' local_hour,
      count(distinct service_date) observed_days,
      round(avg(nullif(h->>'avg_visible_customers','')::numeric),1) avg_visible_diners,
      round(avg(nullif(h->>'peak_visible_customers','')::numeric),1) avg_peak_visible_diners,
      max(nullif(h->>'peak_visible_customers','')::integer) max_peak_visible_diners,
      round(avg(nullif(h->>'avg_occupied_tables','')::numeric),1) avg_occupied_tables,
      max(nullif(h->>'peak_occupied_tables','')::integer) max_peak_occupied_tables,
      round(avg(nullif(h->>'avg_kitchen_load','')::numeric),3) avg_kitchen_load,
      round(avg(nullif(h->>'avg_handoff_load','')::numeric),3) avg_handoff_load
    from rows
    group by h->>'local_hour'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'local_hour',local_hour,'observed_days',observed_days,
    'avg_visible_diners',avg_visible_diners,
    'avg_peak_visible_diners',avg_peak_visible_diners,
    'max_peak_visible_diners',max_peak_visible_diners,
    'avg_occupied_tables',avg_occupied_tables,
    'max_peak_occupied_tables',max_peak_occupied_tables,
    'avg_kitchen_load',avg_kitchen_load,
    'avg_handoff_load',avg_handoff_load
  ) order by local_hour),'[]'::jsonb)
  into v_hour_profile from agg;

  with rows as (
    select d->>'service_date' service_date,f
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'floors','[]'::jsonb)) f
    where coalesce((f->>'samples')::integer,0)>0
  ), agg as (
    select f->>'camera_id' camera_id,f->>'floor' floor,
      count(distinct service_date) observed_days,
      sum((f->>'samples')::integer) samples,
      round(avg(nullif(f->>'avg_visible_customers','')::numeric),1) avg_visible_diners,
      max(nullif(f->>'peak_visible_customers','')::integer) peak_visible_diners,
      round(avg(nullif(f->>'avg_occupied_tables','')::numeric),1) avg_occupied_tables,
      max(nullif(f->>'peak_occupied_tables','')::integer) peak_occupied_tables
    from rows
    group by f->>'camera_id',f->>'floor'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'camera_id',camera_id,'floor',floor,'observed_days',observed_days,'samples',samples,
    'avg_visible_diners',avg_visible_diners,'peak_visible_diners',peak_visible_diners,
    'avg_occupied_tables',avg_occupied_tables,'peak_occupied_tables',peak_occupied_tables
  ) order by floor),'[]'::jsonb)
  into v_floor_profile from agg;

  with rows as (
    select d->>'service_date' service_date,t
    from jsonb_array_elements(v_days) d
    cross join lateral jsonb_array_elements(coalesce(d->'tables','[]'::jsonb)) t
    where coalesce((t->>'samples')::integer,0)>0
  ), agg as (
    select t->>'table_key' table_key,max(t->>'label') label,max(t->>'capacity') capacity,
      count(distinct service_date) observed_days,
      sum((t->>'samples')::integer) samples,
      sum((t->>'occupied_samples')::integer) occupied_samples,
      max(nullif(t->>'peak_party','')::integer) peak_party
    from rows
    group by t->>'table_key'
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'table_key',table_key,'label',label,'capacity',capacity,'observed_days',observed_days,
    'samples',samples,'occupied_samples',occupied_samples,
    'occupancy_pct',case when samples=0 then null else round(100.0*occupied_samples/samples,1) end,
    'peak_party',peak_party
  ) order by case when samples=0 then null else 100.0*occupied_samples/samples end desc nulls last,table_key),'[]'::jsonb)
  into v_table_profile from agg;

  with rows as (
    select (d->>'service_date')::date service_date,
      coalesce((d->>'camera_observations')::integer,0) camera_observations,
      coalesce((d->>'estimated_covers')::integer,0) estimated_covers,
      coalesce((d->>'served_sessions')::integer,0) served_sessions,
      nullif(d->>'peak_visible_diners','')::integer peak_visible_diners,
      nullif(d->>'median_observed_time_to_food_minutes','')::numeric median_time,
      nullif(d->>'coverage_ratio','')::numeric coverage_ratio
    from jsonb_array_elements(v_days) d
  ), agg as (
    select extract(isodow from service_date)::integer iso_day,to_char(min(service_date),'Dy') weekday,
      count(*) filter(where camera_observations>0) observed_days,
      round(avg(estimated_covers) filter(where camera_observations>0),1) avg_estimated_covers,
      round(avg(peak_visible_diners) filter(where peak_visible_diners is not null),1) avg_peak_visible_diners,
      round(avg(median_time) filter(where median_time is not null),1) avg_daily_median_time_to_food,
      round(avg(coalesce(coverage_ratio,0)),3) avg_coverage_ratio
    from rows
    group by extract(isodow from service_date)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'iso_day',iso_day,'weekday',weekday,'observed_days',observed_days,
    'avg_estimated_covers',avg_estimated_covers,'avg_peak_visible_diners',avg_peak_visible_diners,
    'avg_daily_median_time_to_food',avg_daily_median_time_to_food,'avg_coverage_ratio',avg_coverage_ratio
  ) order by iso_day),'[]'::jsonb)
  into v_weekday_profile from agg;

  with rows as (
    select (d->>'service_date')::date service_date,
      coalesce((d->>'camera_observations')::integer,0) camera_observations,
      coalesce((d->>'estimated_covers')::integer,0) estimated_covers,
      coalesce((d->>'served_sessions')::integer,0) served_sessions,
      nullif(d->>'peak_visible_diners','')::integer peak_visible_diners,
      nullif(d->>'median_observed_time_to_food_minutes','')::numeric median_time,
      nullif(d->>'coverage_ratio','')::numeric coverage_ratio
    from jsonb_array_elements(v_days) d
  ), agg as (
    select date_trunc('week',service_date)::date week_start,
      min(service_date) first_service_date,max(service_date) last_service_date,
      count(*) filter(where camera_observations>0) observed_days,
      sum(estimated_covers) estimated_covers,
      sum(served_sessions) served_sessions,
      max(peak_visible_diners) peak_visible_diners,
      round(avg(median_time) filter(where median_time is not null),1) avg_daily_median_time_to_food,
      round(avg(coalesce(coverage_ratio,0)),3) avg_coverage_ratio
    from rows
    group by date_trunc('week',service_date)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'week_start',week_start,'first_service_date',first_service_date,'last_service_date',last_service_date,
    'observed_days',observed_days,'estimated_covers',estimated_covers,'served_sessions',served_sessions,
    'peak_visible_diners',peak_visible_diners,'avg_daily_median_time_to_food',avg_daily_median_time_to_food,
    'avg_coverage_ratio',avg_coverage_ratio
  ) order by week_start),'[]'::jsonb)
  into v_weekly_trend from agg;

  select jsonb_build_object(
    'under_15_minutes',count(*) filter(where x<15),
    '15_to_30_minutes',count(*) filter(where x>=15 and x<30),
    '30_to_45_minutes',count(*) filter(where x>=30 and x<45),
    '45_plus_minutes',count(*) filter(where x>=45),
    'sample_sessions',count(*)
  )
  into v_service_distribution
  from (select value::text::numeric x from jsonb_array_elements(v_times)) q;

  select d into v_busiest_day
  from jsonb_array_elements(v_days) d
  where coalesce((d->>'camera_observations')::integer,0)>0
  order by coalesce((d->>'estimated_covers')::integer,0) desc,
           coalesce((d->>'peak_visible_diners')::integer,0) desc,
           d->>'service_date'
  limit 1;

  select h into v_busiest_hour
  from jsonb_array_elements(coalesce(v_hour_profile,'[]'::jsonb)) h
  order by nullif(h->>'avg_peak_visible_diners','')::numeric desc nulls last,
           h->>'local_hour'
  limit 1;

  return jsonb_build_object(
    'enabled',true,
    'schema','restaurant-period-v2',
    'days',p_days,
    'timezone',v_site.timezone,
    'period',jsonb_build_object(
      'start_service_date',v_start_date,'end_service_date',v_end_date,
      'previous_start_service_date',v_prev_start,'previous_end_service_date',v_prev_end
    ),
    'summary',jsonb_build_object(
      'expected_service_days',p_days,
      'observed_service_days',v_observed_days,
      'total_estimated_covers',v_total_covers,
      'avg_estimated_covers_per_observed_day',case when v_observed_days=0 then null else round(v_total_covers::numeric/v_observed_days,1) end,
      'table_sessions',v_total_sessions,
      'served_sessions',v_total_served,
      'median_observed_time_to_food_minutes',v_median_time,
      'avg_coverage_ratio',round(v_sum_coverage/p_days,3),
      'busiest_day',v_busiest_day,
      'busiest_hour',v_busiest_hour
    ),
    'previous_period',jsonb_build_object(
      'expected_service_days',p_days,
      'observed_service_days',v_prev_observed_days,
      'total_estimated_covers',v_prev_total_covers,
      'avg_estimated_covers_per_observed_day',case when v_prev_observed_days=0 then null else round(v_prev_total_covers::numeric/v_prev_observed_days,1) end,
      'table_sessions',v_prev_total_sessions,
      'served_sessions',v_prev_total_served,
      'median_observed_time_to_food_minutes',v_prev_median_time,
      'avg_coverage_ratio',round(v_prev_sum_coverage/p_days,3)
    ),
    'comparison',jsonb_build_object(
      'estimated_covers_delta',v_total_covers-v_prev_total_covers,
      'estimated_covers_pct',case when v_prev_total_covers=0 then null else round(100.0*(v_total_covers-v_prev_total_covers)/v_prev_total_covers,1) end,
      'served_sessions_delta',v_total_served-v_prev_total_served,
      'served_sessions_pct',case when v_prev_total_served=0 then null else round(100.0*(v_total_served-v_prev_total_served)/v_prev_total_served,1) end,
      'median_time_to_food_delta_minutes',case when v_median_time is null or v_prev_median_time is null then null else round(v_median_time-v_prev_median_time,1) end,
      'coverage_delta_points',round(100.0*((v_sum_coverage-v_prev_sum_coverage)/p_days),1)
    ),
    'daily',v_days,
    'hour_profile',coalesce(v_hour_profile,'[]'::jsonb),
    'floor_profile',coalesce(v_floor_profile,'[]'::jsonb),
    'table_profile',coalesce(v_table_profile,'[]'::jsonb),
    'weekday_profile',coalesce(v_weekday_profile,'[]'::jsonb),
    'weekly_trend',coalesce(v_weekly_trend,'[]'::jsonb),
    'service_time_distribution',coalesce(v_service_distribution,'{}'::jsonb),
    'analytics_quality',public.wl_restaurant_quality_summary(
      p_site_id,
      (public.wl_restaurant_day(p_site_id,v_start_date)->'window'->>'start')::timestamptz,
      (public.wl_restaurant_day(p_site_id,v_end_date)->'window'->>'end')::timestamptz
    ),
    'measurement_notes',jsonb_build_array(
      'Visible diners are concurrent visible people on dining-floor cameras, not unique footfall.',
      'Estimated covers and table sessions are camera-derived estimates.',
      'Observed time to food is seated/occupied to first food visible, not POS order-to-serve time.',
      'Period comparisons are only as representative as the reported camera-analysis coverage.',
      'Missing observation periods are missing coverage, not zero business activity.'
    )
  );
end $function$


CREATE OR REPLACE FUNCTION public.wl_generate_daily_report(p_site_id uuid, p_date date DEFAULT NULL::date, p_force boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_site public.sites;
  v_ctx public.site_business_context;
  v_is_restaurant boolean;
  v_local_now timestamp;
  v_date date;
  v_existing uuid;
  v_payload jsonb;
  v_inf text;
  v_id uuid;
  v_rev int;
begin
  select * into v_site from public.sites where id=p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode='22023'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id;
  v_is_restaurant:=coalesce(v_ctx.site_type,v_site.site_type,'other')='restaurant';
  v_local_now:=now() at time zone v_site.timezone;

  if p_date is not null then
    v_date:=p_date;
  elsif v_is_restaurant
    and coalesce(v_ctx.overnight,false)
    and v_ctx.open_time is not null
    and v_ctx.close_time is not null
    and v_ctx.close_time<=v_ctx.open_time
    and v_local_now::time<v_ctx.open_time then
      v_date:=v_local_now::date-1;
  else
    v_date:=v_local_now::date;
  end if;

  select id into v_existing
    from public.report_snapshots
   where site_id=p_site_id and report_date=v_date;
  if v_existing is not null and not p_force then
    return (select jsonb_build_object(
      'report_id',id,'frozen',true,'revision',revision,
      'generated_at',generated_at,'payload',payload)
      from public.report_snapshots where id=v_existing);
  end if;

  v_payload:=public.wl_daily_intelligence(p_site_id,v_date,true);
  if v_is_restaurant then
    v_payload:=v_payload||jsonb_build_object(
      'restaurant',public.wl_restaurant_day(p_site_id,v_date)
    );
  end if;

  select version into v_inf
    from public.inference_config
   where site_id=p_site_id or site_id is null
   order by (site_id is not null) desc
   limit 1;

  insert into public.report_snapshots(
    tenant_id,site_id,report_date,payload,payload_schema,versions,coverage_ratio
  )
  values(
    v_site.tenant_id,p_site_id,v_date,v_payload,v_payload->>'schema',
    jsonb_build_object(
      'inference',coalesce(v_inf,'inference-v1'),
      'journeys','topology-v2',
      'day_state','state-machine-v1',
      'intelligence',v_payload->>'schema',
      'restaurant',case when v_is_restaurant then 'restaurant-day-v3' else null end
    ),
    (v_payload->'coverage'->>'coverage_ratio')::numeric
  )
  on conflict(site_id,report_date) do update
     set payload=excluded.payload,
         payload_schema=excluded.payload_schema,
         versions=excluded.versions,
         coverage_ratio=excluded.coverage_ratio,
         generated_at=now(),
         revision=public.report_snapshots.revision+1,
         delivery_status='pending',
         pdf_sha256=null,
         pdf_bytes=null
  returning id,revision into v_id,v_rev;

  return jsonb_build_object(
    'report_id',v_id,'frozen',false,'revision',v_rev,
    'generated_at',now(),'payload',v_payload
  );
end $function$


CREATE OR REPLACE FUNCTION public.wl_ai_context(p_site_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_site sites;
  v_diag jsonb;
  v_ctx jsonb;
  v_onboarding jsonb;
  v_recent jsonb;
  v_camera_rows jsonb;
begin
  select * into v_site from sites where id=p_site_id and tenant_id=v_tenant;
  v_diag := wl_my_site_diagnosis(p_site_id);
  v_ctx := wl_my_site_context(p_site_id);
  v_onboarding := wl_onboarding_status(p_site_id);

  select coalesce(jsonb_agg(to_jsonb(r) order by r.device_ts desc),'[]'::jsonb) into v_recent
    from (
      select e.id as event_id,e.event_type,e.device_ts,e.received_at,
             case
               when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
                 then 'Camera '||coalesce(c.physical_channel,c.channel)
               else c.name
             end as camera,
             coalesce(c.physical_channel,c.channel) as channel,
             coalesce(e.payload->>'source','live') as source,
             case lower(trim(coalesce(e.payload->>'recovered','false')))
               when 'true' then true when 't' then true when '1' then true when 'yes' then true
               else false
             end as recovered
        from events e
        left join cameras c on c.id=e.camera_id
       where e.site_id=p_site_id
       order by e.device_ts desc
       limit 20
    ) r;

  select coalesce(jsonb_agg(jsonb_build_object(
      'id',c.id,
      'channel',coalesce(c.physical_channel,c.channel),
      'name',case
        when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
          then 'Camera '||coalesce(c.physical_channel,c.channel)
        else c.name
      end,
      'purpose',c.purpose,
      'monitor',c.is_configured,
      'analytics_enabled',coalesce(c.analytics_enabled,false),
      'health_state',coalesce(h.health_state::text,'unknown'),
      'recording_state',coalesce(h.recording_state::text,'unknown')
    ) order by
      case when coalesce(c.physical_channel,c.channel) ~ '^[0-9]+$'
           then coalesce(c.physical_channel,c.channel)::int else 2147483647 end,
      coalesce(c.physical_channel,c.channel)
    ),'[]'::jsonb) into v_camera_rows
    from cameras c
    left join camera_health h on h.camera_id=c.id
   where c.site_id=p_site_id
     and coalesce(c.is_canonical,true);

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v5',
    'generated_at',now(),
    'site',jsonb_build_object('id',v_site.id,'name',v_site.name,'timezone',v_site.timezone),
    'business_context',v_ctx,
    'onboarding',v_onboarding,
    'recorder',v_diag->'recorder',
    'connectivity',v_diag->'connectivity',
    'capabilities',v_diag->'capabilities',
    'capability_known',v_diag->'capability_known',
    'cameras',v_camera_rows,
    'faults',v_diag->'faults',
    'coverage',v_diag->'coverage',
    'permissions',v_diag->'tiers',
    'recent_events',v_recent,
    'safety',jsonb_build_object(
      'recorder_credentials_leave_site',false,
      'recorder_writes_require_approval',true,
      'unknown_capability_must_not_be_assumed',true)
  );
end $function$


update public.site_business_context
set reporting_prefs=
  jsonb_set(
    jsonb_set(
      jsonb_set(
        reporting_prefs,
        '{restaurant_intelligence_context,analytics_quality}',
        ($ctx$
{"summary_order": ["coverage and whether the service day is sufficiently observed", "analytics quality: glare, occlusion, obstruction, camera angle and count/table confidence", "customer demand signal: visible diners and occupied tables by hour/floor", "table utilization and estimated sessions/covers", "service timing: served sessions and observed time to food", "handoff and kitchen pressure", "operational/security exceptions", "practical improvement opportunities supported by repeated evidence"], "report_windows": {"today": {"label": "Today", "purpose": "Latest/current Chai Wala service-day operating report.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "operational/security exceptions", "management reading"], "primary_questions": ["How busy was the restaurant by hour and floor?", "How many calibrated tables were occupied and served?", "What was the observed time to food?", "Where was kitchen or service-handoff pressure visible?", "Was monitoring coverage sufficient to trust the picture?", "Were there any operational or security exceptions?"]}, "yesterday": {"label": "Yesterday", "purpose": "Completed prior Chai Wala service-day review with restaurant operations plus frozen security/evidence report when available.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "management reading", "security/evidence report"], "primary_questions": ["What happened during the completed service day?", "Which hours and floors carried the strongest visible demand?", "How were tables utilized and how many sessions were visibly served?", "How did observed time to food behave?", "Were there service, coverage or security exceptions requiring follow-up?"]}, "last_7_days": {"label": "Last 7 days", "purpose": "Short-term operations pattern report with comparison to the previous seven service days.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "service-day trend", "demand by hour", "floor comparison", "table utilization ranking", "observed service-time distribution", "repeated operational patterns", "management reading and practical actions"], "primary_questions": ["Which service days and hours were busiest?", "How did estimated covers and served sessions change versus the prior seven days?", "Which floor and tables carried more demand?", "Is observed time to food repeatedly slow at particular times?", "Are kitchen/handoff pressure and dining demand aligned?", "Is coverage comparable enough to act on the trend?"]}, "last_30_days": {"label": "Last 30 days", "purpose": "Management trend report for recurring demand, layout, service and operating-pattern decisions.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "weekly trend", "weekday pattern", "demand by hour", "floor comparison", "high- and low-utilization tables", "observed service-time distribution", "recurring operational/security patterns", "management improvement opportunities"], "primary_questions": ["What changed versus the previous thirty service days?", "Which weeks, weekdays and hours repeatedly carry demand?", "Which floor and calibrated tables are consistently high or low utilization?", "How stable is observed time to food across the month?", "Are recurring service-pressure patterns visible?", "Which improvement opportunities are supported by repeated evidence and adequate coverage?"]}}, "analytics_quality": {"rules": ["Do not publish a customer-count accuracy percentage until representative Floor 1 and Floor 2 frames have been manually counted and compared with WatchLog.", "Repeated glare or overexposure should trigger a lighting/camera-angle recommendation.", "Repeated occlusion or obstruction should trigger a camera-height, angle or obstruction-removal recommendation.", "Repeated low table-tracking confidence should trigger table-anchor/zone recalibration guidance.", "Recommendations require repeated evidence across frames; a single poor frame is not enough.", "When analytics quality is poor, surface the limitation before operational recommendations."], "scores": {"glare_level": "0-1, higher is worse", "occlusion_level": "0-1, higher is worse", "obstruction_level": "0-1, higher is worse", "overexposure_level": "0-1, higher is worse", "visibility_quality": "0-1, higher is better", "lighting_uniformity": "0-1, higher is better", "camera_angle_adequacy": "0-1, higher is better", "people_count_confidence": "0-1, dining floors only; higher is better", "table_tracking_confidence": "0-1, dining floors only; higher is better"}, "purpose": "Measure whether the camera view is good enough for the intended business analytics before trusting counts or trends."}}
$ctx$::jsonb)->'analytics_quality',
        true
      ),
      '{restaurant_intelligence_context,response_policy,summary_order}',
      ($ctx$
{"summary_order": ["coverage and whether the service day is sufficiently observed", "analytics quality: glare, occlusion, obstruction, camera angle and count/table confidence", "customer demand signal: visible diners and occupied tables by hour/floor", "table utilization and estimated sessions/covers", "service timing: served sessions and observed time to food", "handoff and kitchen pressure", "operational/security exceptions", "practical improvement opportunities supported by repeated evidence"], "report_windows": {"today": {"label": "Today", "purpose": "Latest/current Chai Wala service-day operating report.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "operational/security exceptions", "management reading"], "primary_questions": ["How busy was the restaurant by hour and floor?", "How many calibrated tables were occupied and served?", "What was the observed time to food?", "Where was kitchen or service-handoff pressure visible?", "Was monitoring coverage sufficient to trust the picture?", "Were there any operational or security exceptions?"]}, "yesterday": {"label": "Yesterday", "purpose": "Completed prior Chai Wala service-day review with restaurant operations plus frozen security/evidence report when available.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "management reading", "security/evidence report"], "primary_questions": ["What happened during the completed service day?", "Which hours and floors carried the strongest visible demand?", "How were tables utilized and how many sessions were visibly served?", "How did observed time to food behave?", "Were there service, coverage or security exceptions requiring follow-up?"]}, "last_7_days": {"label": "Last 7 days", "purpose": "Short-term operations pattern report with comparison to the previous seven service days.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "service-day trend", "demand by hour", "floor comparison", "table utilization ranking", "observed service-time distribution", "repeated operational patterns", "management reading and practical actions"], "primary_questions": ["Which service days and hours were busiest?", "How did estimated covers and served sessions change versus the prior seven days?", "Which floor and tables carried more demand?", "Is observed time to food repeatedly slow at particular times?", "Are kitchen/handoff pressure and dining demand aligned?", "Is coverage comparable enough to act on the trend?"]}, "last_30_days": {"label": "Last 30 days", "purpose": "Management trend report for recurring demand, layout, service and operating-pattern decisions.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "weekly trend", "weekday pattern", "demand by hour", "floor comparison", "high- and low-utilization tables", "observed service-time distribution", "recurring operational/security patterns", "management improvement opportunities"], "primary_questions": ["What changed versus the previous thirty service days?", "Which weeks, weekdays and hours repeatedly carry demand?", "Which floor and calibrated tables are consistently high or low utilization?", "How stable is observed time to food across the month?", "Are recurring service-pressure patterns visible?", "Which improvement opportunities are supported by repeated evidence and adequate coverage?"]}}, "analytics_quality": {"rules": ["Do not publish a customer-count accuracy percentage until representative Floor 1 and Floor 2 frames have been manually counted and compared with WatchLog.", "Repeated glare or overexposure should trigger a lighting/camera-angle recommendation.", "Repeated occlusion or obstruction should trigger a camera-height, angle or obstruction-removal recommendation.", "Repeated low table-tracking confidence should trigger table-anchor/zone recalibration guidance.", "Recommendations require repeated evidence across frames; a single poor frame is not enough.", "When analytics quality is poor, surface the limitation before operational recommendations."], "scores": {"glare_level": "0-1, higher is worse", "occlusion_level": "0-1, higher is worse", "obstruction_level": "0-1, higher is worse", "overexposure_level": "0-1, higher is worse", "visibility_quality": "0-1, higher is better", "lighting_uniformity": "0-1, higher is better", "camera_angle_adequacy": "0-1, higher is better", "people_count_confidence": "0-1, dining floors only; higher is better", "table_tracking_confidence": "0-1, dining floors only; higher is better"}, "purpose": "Measure whether the camera view is good enough for the intended business analytics before trusting counts or trends."}}
$ctx$::jsonb)->'summary_order',
      true
    ),
    '{restaurant_intelligence_context,report_windows}',
    ($ctx$
{"summary_order": ["coverage and whether the service day is sufficiently observed", "analytics quality: glare, occlusion, obstruction, camera angle and count/table confidence", "customer demand signal: visible diners and occupied tables by hour/floor", "table utilization and estimated sessions/covers", "service timing: served sessions and observed time to food", "handoff and kitchen pressure", "operational/security exceptions", "practical improvement opportunities supported by repeated evidence"], "report_windows": {"today": {"label": "Today", "purpose": "Latest/current Chai Wala service-day operating report.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "operational/security exceptions", "management reading"], "primary_questions": ["How busy was the restaurant by hour and floor?", "How many calibrated tables were occupied and served?", "What was the observed time to food?", "Where was kitchen or service-handoff pressure visible?", "Was monitoring coverage sufficient to trust the picture?", "Were there any operational or security exceptions?"]}, "yesterday": {"label": "Yesterday", "purpose": "Completed prior Chai Wala service-day review with restaurant operations plus frozen security/evidence report when available.", "sections": ["service-day coverage and data quality", "analytics quality and improvement recommendations", "headline KPIs", "hourly dining demand", "floor comparison", "table utilization", "observed service timing", "kitchen and handoff pressure", "management reading", "security/evidence report"], "primary_questions": ["What happened during the completed service day?", "Which hours and floors carried the strongest visible demand?", "How were tables utilized and how many sessions were visibly served?", "How did observed time to food behave?", "Were there service, coverage or security exceptions requiring follow-up?"]}, "last_7_days": {"label": "Last 7 days", "purpose": "Short-term operations pattern report with comparison to the previous seven service days.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "service-day trend", "demand by hour", "floor comparison", "table utilization ranking", "observed service-time distribution", "repeated operational patterns", "management reading and practical actions"], "primary_questions": ["Which service days and hours were busiest?", "How did estimated covers and served sessions change versus the prior seven days?", "Which floor and tables carried more demand?", "Is observed time to food repeatedly slow at particular times?", "Are kitchen/handoff pressure and dining demand aligned?", "Is coverage comparable enough to act on the trend?"]}, "last_30_days": {"label": "Last 30 days", "purpose": "Management trend report for recurring demand, layout, service and operating-pattern decisions.", "sections": ["period coverage", "analytics quality and improvement recommendations", "headline KPIs", "previous-period comparison", "weekly trend", "weekday pattern", "demand by hour", "floor comparison", "high- and low-utilization tables", "observed service-time distribution", "recurring operational/security patterns", "management improvement opportunities"], "primary_questions": ["What changed versus the previous thirty service days?", "Which weeks, weekdays and hours repeatedly carry demand?", "Which floor and calibrated tables are consistently high or low utilization?", "How stable is observed time to food across the month?", "Are recurring service-pressure patterns visible?", "Which improvement opportunities are supported by repeated evidence and adequate coverage?"]}}, "analytics_quality": {"rules": ["Do not publish a customer-count accuracy percentage until representative Floor 1 and Floor 2 frames have been manually counted and compared with WatchLog.", "Repeated glare or overexposure should trigger a lighting/camera-angle recommendation.", "Repeated occlusion or obstruction should trigger a camera-height, angle or obstruction-removal recommendation.", "Repeated low table-tracking confidence should trigger table-anchor/zone recalibration guidance.", "Recommendations require repeated evidence across frames; a single poor frame is not enough.", "When analytics quality is poor, surface the limitation before operational recommendations."], "scores": {"glare_level": "0-1, higher is worse", "occlusion_level": "0-1, higher is worse", "obstruction_level": "0-1, higher is worse", "overexposure_level": "0-1, higher is worse", "visibility_quality": "0-1, higher is better", "lighting_uniformity": "0-1, higher is better", "camera_angle_adequacy": "0-1, higher is better", "people_count_confidence": "0-1, dining floors only; higher is better", "table_tracking_confidence": "0-1, dining floors only; higher is better"}, "purpose": "Measure whether the camera view is good enough for the intended business analytics before trusting counts or trends."}}
$ctx$::jsonb)->'report_windows',
    true
  ),
updated_at=now()
where site_id='1a1fab32-10b3-4082-b61c-180ec04c758c';

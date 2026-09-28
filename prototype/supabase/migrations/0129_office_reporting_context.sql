-- Reusable office reporting and business-day semantics.
-- Yesterday means latest completed configured working day, not calendar yesterday.

CREATE OR REPLACE FUNCTION public.wl_my_last_completed_business_date(p_site_id uuid, p_reference timestamp with time zone DEFAULT now())
 RETURNS date
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_local timestamp;
  v_candidate date;
  v_end_local timestamp;
  v_days integer[];
  v_open time;
  v_close time;
  v_overnight boolean;
  i integer;
begin
  select * into v_site
  from public.sites
  where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  select * into v_ctx
  from public.site_business_context
  where site_id=p_site_id and tenant_id=v_tenant;

  v_local:=p_reference at time zone coalesce(v_site.timezone,'UTC');
  v_days:=coalesce(v_ctx.working_days,array[1,2,3,4,5,6,7]);
  v_open:=coalesce(v_ctx.open_time,'00:00'::time);
  v_close:=coalesce(v_ctx.close_time,'23:59:59'::time);
  v_overnight:=coalesce(v_ctx.overnight,false) or v_close<=v_open;

  for i in 0..31 loop
    v_candidate:=v_local::date-i;
    if extract(isodow from v_candidate)::integer=any(v_days) then
      if v_overnight then
        v_end_local:=(v_candidate+1)::timestamp+v_close;
      else
        v_end_local:=v_candidate::timestamp+v_close;
      end if;
      if v_end_local<=v_local then
        return v_candidate;
      end if;
    end if;
  end loop;

  return null;
end $function$


CREATE OR REPLACE FUNCTION public.wl_office_brief(p_site_id uuid, p_date date)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
with auth as (
  select public.wl_assert_my_site(p_site_id) tenant_id
),
s as (
  select s.id,s.tenant_id,coalesce(s.timezone,'UTC') timezone,
         coalesce(ctx.open_time,'08:00'::time) open_time,
         coalesce(ctx.close_time,'19:00'::time) close_time,
         coalesce(ctx.overnight,false) or coalesce(ctx.close_time,'19:00'::time)<=coalesce(ctx.open_time,'08:00'::time) overnight
  from public.sites s
  join auth a on a.tenant_id=s.tenant_id
  left join public.site_business_context ctx on ctx.site_id=s.id and ctx.tenant_id=s.tenant_id
  where s.id=p_site_id
),
win as (
  select
    (p_date::timestamp+s.open_time) at time zone s.timezone as v_start,
    ((case when s.overnight then (p_date+1)::timestamp else p_date::timestamp end)+s.close_time) at time zone s.timezone as v_end,
    s.timezone,s.open_time,s.close_time,s.overnight
  from s
),
base as (
  select e.camera_id,c.name,c.purpose,e.device_ts,
         (e.device_ts at time zone w.timezone) ts_local
  from public.events e
  join public.cameras c on c.id=e.camera_id
  cross join win w
  where e.site_id=p_site_id
    and e.event_type='person'
    and e.device_ts>=w.v_start and e.device_ts<w.v_end
),
epi as (
  select *,
         case when extract(epoch from (device_ts-lag(device_ts) over w))>600
                   or lag(device_ts) over w is null then 1 else 0 end ne
  from base
  window w as (partition by camera_id order by device_ts)
),
calendar_win as (
  select p_date::timestamp at time zone s.timezone c_start,
         (p_date+1)::timestamp at time zone s.timezone c_end,
         s.timezone,s.open_time,s.close_time,s.overnight
  from s
),
outside as (
  select e.device_ts,(e.device_ts at time zone cw.timezone) ts_local
  from public.events e cross join calendar_win cw
  where e.site_id=p_site_id and e.event_type='person'
    and e.device_ts>=cw.c_start and e.device_ts<cw.c_end
    and case
      when cw.overnight then false
      else (e.device_ts at time zone cw.timezone)::time<cw.open_time
        or (e.device_ts at time zone cw.timezone)::time>=cw.close_time
    end
)
select jsonb_build_object(
  'window',(
    select jsonb_build_object(
      'business_date',p_date,'start',v_start,'end',v_end,
      'open_time',open_time,'close_time',close_time,'overnight',overnight
    ) from win
  ),
  'coverage',(
    select jsonb_build_object(
      'first',to_char(min(ts_local),'HH24:MI'),
      'last',to_char(max(ts_local),'HH24:MI'),
      'person_events',count(*),
      'has_opening_half',coalesce(min(ts_local)::time<(select open_time+interval '2 hours' from win),false),
      'has_closing_half',coalesce(max(ts_local)::time>(select close_time-interval '2 hours' from win),false)
    ) from base
  ),
  'peak_hour',(
    select jsonb_build_object('hour',h,'count',n)
    from (
      select extract(hour from ts_local)::int h,count(*) n
      from base group by 1 order by 2 desc limit 1
    ) z
  ),
  'by_area',(
    select coalesce(jsonb_agg(jsonb_build_object(
      'camera',name,'events',n,'episodes',ep,'first',f,'last',l
    ) order by n desc),'[]'::jsonb)
    from (
      select name,count(*) n,sum(ne) ep,
             to_char(min(ts_local),'HH24:MI') f,to_char(max(ts_local),'HH24:MI') l
      from epi group by name
    ) z
  ),
  'restricted',(
    select coalesce(jsonb_agg(jsonb_build_object(
      'camera',name,'episodes',ep,'last',l
    ) order by name),'[]'::jsonb)
    from (
      select name,sum(ne) ep,to_char(max(ts_local),'HH24:MI') l
      from epi
      where purpose ilike '%armory%' or purpose ilike '%restrict%' or name ilike '%armory%'
      group by name
    ) z
  ),
  'after_hours_calendar_events',(select count(*) from outside),
  'agent',(
    select case when max(last_seen_at) is null then null
      else jsonb_build_object('last_seen',max(last_seen_at),'online',max(last_seen_at)>now()-interval '15 minutes')
    end
    from public.agents where site_id=p_site_id
  ),
  'monitoring_coverage',public.wl_site_coverage_report(
    p_site_id,(select v_start from win),(select v_end from win)
  )
);
$function$


CREATE OR REPLACE FUNCTION public.wl_office_period(p_site_id uuid, p_days integer DEFAULT 7, p_working_only boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
  v_tenant uuid:=public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_ctx public.site_business_context;
  v_days integer[];
  v_local_date date;
  v_cursor date;
  v_end_date date;
  v_start_date date;
  v_prev_start date;
  v_prev_end date;
  v_current jsonb:='[]'::jsonb;
  v_previous jsonb:='[]'::jsonb;
  v_d jsonb;
  v_row jsonb;
  v_count integer:=0;
  v_prev_count integer:=0;
  v_cov_sum numeric:=0;
  v_prev_cov_sum numeric:=0;
  v_observed integer:=0;
  v_prev_observed integer:=0;
  v_incidents integer:=0;
  v_prev_incidents integer:=0;
  v_critical integer:=0;
  v_prev_critical integer:=0;
  v_after integer:=0;
  v_prev_after integer:=0;
  v_activity integer:=0;
  v_prev_activity integer:=0;
  v_cov numeric;
  v_working boolean;
  v_local_now timestamp;
begin
  if p_days<2 or p_days>31 then
    raise exception 'p_days must be between 2 and 31' using errcode='22023';
  end if;

  select * into v_site from public.sites where id=p_site_id and tenant_id=v_tenant;
  if v_site.id is null then raise exception 'not authorized for this site' using errcode='42501'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id and tenant_id=v_tenant;
  if coalesce(v_ctx.site_type,v_site.site_type,'other')<>'office' then
    return jsonb_build_object('enabled',false,'site_type',coalesce(v_ctx.site_type,v_site.site_type,'other'));
  end if;

  v_days:=coalesce(v_ctx.working_days,array[1,2,3,4,5]);
  v_local_now:=now() at time zone coalesce(v_site.timezone,'UTC');
  if p_working_only then
    v_cursor:=public.wl_my_last_completed_business_date(p_site_id,now());
  else
    v_cursor:=v_local_now::date-1;
  end if;
  v_end_date:=v_cursor;

  while v_count<p_days and v_cursor>v_end_date-90 loop
    v_working:=extract(isodow from v_cursor)::integer=any(v_days);
    if (not p_working_only) or v_working then
      v_d:=public.wl_my_daily_intelligence(p_site_id,v_cursor);
      v_cov:=coalesce((v_d->'coverage'->>'coverage_ratio')::numeric,0);
      v_row:=jsonb_build_object(
        'date',v_cursor,
        'working_day',v_working,
        'coverage_ratio',v_cov,
        'incidents_total',coalesce((v_d->'attention'->>'incidents_total')::integer,0),
        'critical',coalesce((v_d->'attention'->>'critical')::integer,0),
        'warning',coalesce((v_d->'attention'->>'warning')::integer,0),
        'info',coalesce((v_d->'attention'->>'info')::integer,0),
        'after_hours_count',coalesce((v_d->'after_hours'->>'count')::integer,0),
        'activity_detections',coalesce((v_d->'office'->'coverage'->>'person_events')::integer,0),
        'peak_activity_hour',v_d->'office'->'peak_hour'->>'hour',
        'peak_activity_events',coalesce((v_d->'office'->'peak_hour'->>'count')::integer,0),
        'opening_at',v_d->'day_boundaries'->>'opening_at',
        'closing_at',v_d->'day_boundaries'->>'closing_at',
        'boundary_confidence',nullif(v_d->'day_boundaries'->>'confidence','')::numeric,
        'partial_day',coalesce((v_d->'meta'->>'partial_day')::boolean,false)
      );
      v_current:=jsonb_build_array(v_row)||v_current;
      v_count:=v_count+1;
      if v_count=1 then v_start_date:=v_cursor; else v_start_date:=least(v_start_date,v_cursor); end if;
      v_cov_sum:=v_cov_sum+v_cov;
      if v_cov>0 then v_observed:=v_observed+1; end if;
      v_incidents:=v_incidents+coalesce((v_d->'attention'->>'incidents_total')::integer,0);
      v_critical:=v_critical+coalesce((v_d->'attention'->>'critical')::integer,0);
      v_after:=v_after+coalesce((v_d->'after_hours'->>'count')::integer,0);
      v_activity:=v_activity+coalesce((v_d->'office'->'coverage'->>'person_events')::integer,0);
    end if;
    v_cursor:=v_cursor-1;
  end loop;

  v_prev_end:=v_cursor;
  while v_prev_count<p_days and v_cursor>v_prev_end-90 loop
    v_working:=extract(isodow from v_cursor)::integer=any(v_days);
    if (not p_working_only) or v_working then
      v_d:=public.wl_my_daily_intelligence(p_site_id,v_cursor);
      v_cov:=coalesce((v_d->'coverage'->>'coverage_ratio')::numeric,0);
      v_row:=jsonb_build_object(
        'date',v_cursor,
        'working_day',v_working,
        'coverage_ratio',v_cov,
        'incidents_total',coalesce((v_d->'attention'->>'incidents_total')::integer,0),
        'critical',coalesce((v_d->'attention'->>'critical')::integer,0),
        'after_hours_count',coalesce((v_d->'after_hours'->>'count')::integer,0),
        'activity_detections',coalesce((v_d->'office'->'coverage'->>'person_events')::integer,0)
      );
      v_previous:=jsonb_build_array(v_row)||v_previous;
      v_prev_count:=v_prev_count+1;
      if v_prev_count=1 then v_prev_start:=v_cursor; else v_prev_start:=least(v_prev_start,v_cursor); end if;
      v_prev_cov_sum:=v_prev_cov_sum+v_cov;
      if v_cov>0 then v_prev_observed:=v_prev_observed+1; end if;
      v_prev_incidents:=v_prev_incidents+coalesce((v_d->'attention'->>'incidents_total')::integer,0);
      v_prev_critical:=v_prev_critical+coalesce((v_d->'attention'->>'critical')::integer,0);
      v_prev_after:=v_prev_after+coalesce((v_d->'after_hours'->>'count')::integer,0);
      v_prev_activity:=v_prev_activity+coalesce((v_d->'office'->'coverage'->>'person_events')::integer,0);
    end if;
    v_cursor:=v_cursor-1;
  end loop;

  return jsonb_build_object(
    'enabled',true,
    'schema','office-period-v1',
    'window_type',case when p_working_only then 'completed_working_days' else 'completed_calendar_days' end,
    'period',jsonb_build_object(
      'start_date',v_start_date,'end_date',v_end_date,
      'previous_start_date',v_prev_start,'previous_end_date',v_prev_end
    ),
    'summary',jsonb_build_object(
      'days',v_count,'observed_days',v_observed,
      'avg_coverage_ratio',case when v_count=0 then null else round(v_cov_sum/v_count,3) end,
      'incidents_total',v_incidents,'critical_total',v_critical,
      'after_hours_total',v_after,'activity_detections',v_activity
    ),
    'previous_period',jsonb_build_object(
      'days',v_prev_count,'observed_days',v_prev_observed,
      'avg_coverage_ratio',case when v_prev_count=0 then null else round(v_prev_cov_sum/v_prev_count,3) end,
      'incidents_total',v_prev_incidents,'critical_total',v_prev_critical,
      'after_hours_total',v_prev_after,'activity_detections',v_prev_activity
    ),
    'comparison',jsonb_build_object(
      'coverage_delta_points',case when v_count=0 or v_prev_count=0 then null else round(100*((v_cov_sum/v_count)-(v_prev_cov_sum/v_prev_count)),1) end,
      'incidents_delta',v_incidents-v_prev_incidents,
      'critical_delta',v_critical-v_prev_critical,
      'after_hours_delta',v_after-v_prev_after,
      'activity_detections_delta',v_activity-v_prev_activity
    ),
    'daily',v_current,
    'measurement_notes',jsonb_build_array(
      'Activity detections are camera detections, not unique people.',
      'Missing monitoring coverage is missing evidence, not zero office activity.',
      'Role-specific office conclusions require confirmed physical camera mapping.',
      'Opening/closing times may be low-confidence when entrance mapping or coverage is incomplete.'
    )
  );
end $function$


-- Keep active office tenants aligned with the governed office harness.
update public.site_business_context
set reporting_prefs = {"daily":true,"weekly":false,"monthly":true,"camera_context":{"1":{"name":"Entrance Corridor","role":"primary internal entrance/corridor access view","watch_for":["arrival and departure activity","after-hours movement","unusual corridor dwell"]},"2":{"name":"Reception","role":"reception and visitor waiting area","watch_for":["visitor arrivals","reception waiting/dwell","reception occupancy","unattended reception periods"]},"3":{"name":"Private Office","role":"private office / management-sensitive area","watch_for":["office occupancy","unusual access","after-hours presence","prolonged unusual activity"]},"4":{"name":"Office Interior","role":"general internal office area","watch_for":["office activity level","busy and quiet periods","after-hours presence"]},"5":{"name":"Manager Office","role":"manager/management office","watch_for":["management-office occupancy","unusual access","after-hours presence"]},"6":{"name":"Parking","role":"office parking / vehicle activity area","watch_for":["vehicle arrival and departure activity","unusual prolonged vehicle presence","after-hours parking activity"]},"7":{"name":"Internal Corridor","role":"internal movement corridor","watch_for":["movement flow","after-hours activity","unusual dwell"]},"8":{"name":"External Entrance","role":"external office entrance and perimeter access","watch_for":["arrival/departure flow","after-hours access","unusual entrance dwell","external access activity"]}},"ai_context_note":"Treat this as the HASCO Steel Head Office. Focus on reception/visitor flow, entrance and corridor activity, management-office access, parking activity, after-hours exceptions and surveillance reliability. Do not infer employee identity, visitor identity, employment status or wrongdoing from appearance alone.","report_layout_profile":"office_ops_v1","owner_insight_priorities":["office opening and closing activity","visitor arrival and reception waiting","after-hours activity in private and manager offices","entrance and corridor access patterns","parking and vehicle activity","unusual dwell or access","camera, recorder, recording and monitoring health"],"office_intelligence_context":{"schema":"office-intelligence-context-v1","purpose":"Shared office business meaning for customer AI and management reporting.","working_day":{"open":"08:00","close":"19:00","days_iso":[1,2,3,4,5],"yesterday_rule":"Use the latest completed configured working day; skip weekends rather than using midnight-to-midnight yesterday.","last_7_days_rule":"Use the last 7 completed configured working days.","last_30_days_rule":"Use the last 30 completed calendar days and separate working-day from non-working-day activity."},"report_windows":{"today":{"label":"Today","purpose":"Current operating-day status and attention."},"yesterday":{"label":"Yesterday","purpose":"Latest completed configured working-day report."},"last_7_days":{"label":"Last 7 days","purpose":"Patterns across the last 7 completed working days."},"last_30_days":{"label":"Last 30 days","purpose":"Management trend across 30 completed calendar days with working/non-working days separated."}},"camera_identity":{"canonical_only":true,"mapping_status":"confirmed_from_current_configuration","physical_camera_count":8},"response_policy":{"never_infer":["identity","demographics","intent","employee productivity","wrongdoing from appearance"],"summary_order":["coverage and whether evidence is sufficient","security incidents and attention","opening/closing and office activity","entrance/reception activity","management-office and restricted activity","after-hours presence and unusual dwell","parking/perimeter activity","monitoring reliability and camera quality","practical evidence-based recommendations"],"language_rules":["Lead with what management needs to know, not internal pipeline terminology.","Call detector counts activity detections, not unique people.","Treat missing monitoring as missing evidence, not zero activity.","Recommendations require repeated evidence or a confirmed configuration/health gap."]},"metric_definitions":{"visitor_flow":{"rule":"Use reception/entrance context; detector events are not unique visitors.","class":"observed-derived"},"closing_activity":{"class":"observed-derived","caveat":"confidence depends on entrance mapping and coverage"},"opening_activity":{"class":"observed-derived","caveat":"confidence depends on entrance mapping and coverage"},"visible_presence":{"class":"observed","never_call":"unique people"},"activity_detections":{"class":"observed-derived","never_call":"unique people"},"monitoring_coverage":{"class":"measured","definition":"Verified LIVE/RECOVERED/UNVERIFIED monitoring coverage."},"after_hours_presence":{"class":"observed","definition":"Visible presence outside configured working hours."}}}}::jsonb,
    updated_at=now()
where site_id='4e258665-c2b3-492e-b88d-10791c3754dc'::uuid;

update public.site_business_context
set reporting_prefs = {"daily":true,"weekly":false,"monthly":true,"ai_context_note":"Treat this as the Al-Khalid Security Services office. The recorder has 8 physical cameras; legacy ONVIF stream-profile duplicates are hidden and must not be presented as additional cameras. Prioritize office opening/closing, visitor/reception movement, management-office activity, restricted-area access, admin/entrance activity and surveillance reliability, but do not assign Reception, Directors Office, Armory Gate or Admin Entrance to a specific camera until that physical view has been re-confirmed.","camera_mapping_note":"WatchLog now exposes 8 physical cameras. The previous 16 ONVIF MainStream/SubStream rows are retained only as hidden historical transport profiles. Legacy role rules on physical Cameras 1 and 2 conflicted across stream profiles and were disabled until the actual views are re-confirmed.","camera_mapping_status":"physical_mapping_repaired","report_layout_profile":"office_ops_v1","known_operational_areas":["Reception","Directors Office","Armory Gate","Admin Entrance"],"owner_insight_priorities":["office opening and closing activity","reception and visitor flow","director and management office after-hours presence","armory/restricted-area access","admin entrance activity","unusual dwell or access outside business hours","camera, recorder, recording and monitoring health"],"office_intelligence_context":{"schema":"office-intelligence-context-v1","purpose":"Shared office business meaning for customer AI and management reporting.","working_day":{"open":"08:00","close":"19:00","days_iso":[1,2,3,4,5],"yesterday_rule":"Use the latest completed configured working day; skip weekends rather than using midnight-to-midnight yesterday.","last_7_days_rule":"Use the last 7 completed configured working days.","last_30_days_rule":"Use the last 30 calendar days and separate working-day from non-working-day activity."},"report_windows":{"today":{"label":"Today","purpose":"Current operating-day status and attention."},"yesterday":{"label":"Yesterday","purpose":"Latest completed configured working-day report."},"last_7_days":{"label":"Last 7 days","purpose":"Patterns across the last 7 completed working days."},"last_30_days":{"label":"Last 30 days","purpose":"Management trend across 30 calendar days with working/non-working days separated."}},"camera_identity":{"rule":"Do not bind a business area to a specific camera until its current physical view is visually re-confirmed. Legacy ONVIF stream-profile labels conflicted.","canonical_only":true,"mapping_status":"needs_physical_view_reconfirmation","physical_camera_count":8,"known_operational_areas":["Reception","Directors Office","Armory Gate","Admin Entrance"],"legacy_transport_profiles_hidden":true},"response_policy":{"never_infer":["identity","demographics","intent","employee productivity","restricted-area role from an unconfirmed camera"],"summary_order":["coverage and whether evidence is sufficient","security incidents and attention","opening/closing and general office activity","confirmed entrance/reception/management/restricted activity only where mapped","after-hours presence and unusual dwell","monitoring reliability and camera/mapping quality","practical evidence-based recommendations"],"language_rules":["Lead with what management needs to know, not internal pipeline terminology.","State mapping uncertainty before role-specific conclusions.","Call detector counts activity detections, not unique people.","Treat missing monitoring as missing evidence, not zero activity.","Recommendations require repeated evidence or a confirmed configuration/health gap."]},"metric_definitions":{"visitor_flow":{"rule":"Only report reception/visitor flow when reception/entrance mapping is confirmed; never call detector events unique visitors.","class":"observed-derived"},"closing_activity":{"class":"observed-derived","caveat":"low confidence when entrance mapping or monitoring coverage is incomplete"},"opening_activity":{"class":"observed-derived","caveat":"low confidence when entrance mapping or monitoring coverage is incomplete"},"visible_presence":{"class":"observed","never_call":"unique people"},"monitoring_coverage":{"class":"measured","definition":"Verified recorder/camera/snapshot/analysis coverage for the report window."},"after_hours_presence":{"class":"observed","definition":"Visible presence outside configured working hours."},"restricted_area_activity":{"rule":"Only report as restricted-area activity when the physical camera mapping is confirmed.","class":"observed"}}}}::jsonb,
    updated_at=now()
where site_id='588cb40a-3325-4d0f-8ca7-5eee7eb6e443'::uuid;

revoke execute on function public.wl_my_last_completed_business_date(uuid,timestamptz) from public,anon;
grant execute on function public.wl_my_last_completed_business_date(uuid,timestamptz) to authenticated,service_role;
revoke execute on function public.wl_office_period(uuid,integer,boolean) from public,anon;
grant execute on function public.wl_office_period(uuid,integer,boolean) to authenticated,service_role;

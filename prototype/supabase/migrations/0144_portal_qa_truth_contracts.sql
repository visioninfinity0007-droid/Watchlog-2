
-- 0144: Portal QA truth contracts.
-- Additive/compatible: no historical data rewrite and no camera/site mapping rewrite.

create or replace function public.wl_analytics_valid_purpose(p text)
returns boolean
language sql
immutable
set search_path to 'public','pg_temp'
as $function$
  select p in (
    'entrance_exit','main_gate','reception','checkout_till','loading_bay',
    'warehouse_floor','perimeter','restricted_area','parking','office_floor',
    'school_gate','corridor','custom',
    -- Existing production purpose vocabulary retained for safe editing.
    'entrance','general','management','queue','restricted'
  )
$function$;

create or replace function public.wl_analytics_catalog()
returns jsonb
language sql
stable
set search_path to 'public'
as $function$
  select jsonb_build_object(
    'site_types', jsonb_build_array(
      jsonb_build_object('key','retail','label','Retail store'),
      jsonb_build_object('key','restaurant','label','Restaurant / food service'),
      jsonb_build_object('key','warehouse_logistics','label','Warehouse / logistics'),
      jsonb_build_object('key','manufacturing','label','Factory / manufacturing'),
      jsonb_build_object('key','office_commercial','label','Office / commercial building'),
      jsonb_build_object('key','school_campus','label','School / campus'),
      jsonb_build_object('key','parking_yard','label','Parking / yard'),
      jsonb_build_object('key','residential_community','label','Residential / gated community'),
      jsonb_build_object('key','custom','label','Custom')
    ),
    'purposes', jsonb_build_array(
      jsonb_build_object('key','entrance_exit','label','Entrance / Exit'),
      jsonb_build_object('key','main_gate','label','Main Gate'),
      jsonb_build_object('key','reception','label','Reception'),
      jsonb_build_object('key','checkout_till','label','Checkout / Till'),
      jsonb_build_object('key','loading_bay','label','Loading Bay'),
      jsonb_build_object('key','warehouse_floor','label','Warehouse Floor'),
      jsonb_build_object('key','perimeter','label','Perimeter'),
      jsonb_build_object('key','restricted_area','label','Restricted Area'),
      jsonb_build_object('key','parking','label','Parking'),
      jsonb_build_object('key','office_floor','label','Office Floor'),
      jsonb_build_object('key','school_gate','label','School Gate'),
      jsonb_build_object('key','corridor','label','Corridor'),
      -- Existing production purpose vocabulary. These are aliases/compatible values,
      -- not a request to rewrite stored camera mappings.
      jsonb_build_object('key','entrance','label','Entrance / Exit'),
      jsonb_build_object('key','general','label','General area'),
      jsonb_build_object('key','management','label','Management / office'),
      jsonb_build_object('key','queue','label','Queue / service point'),
      jsonb_build_object('key','restricted','label','Restricted area'),
      jsonb_build_object('key','custom','label','Custom')
    ),
    'analytics', jsonb_build_array(
      jsonb_build_object('key','visitor_flow','label','Visitor Flow','rule_type','line_crossing','classes',jsonb_build_array('person'),'geometry','line'),
      jsonb_build_object('key','vehicle_flow','label','Vehicle Flow','rule_type','line_crossing','classes',jsonb_build_array('car','motorcycle'),'geometry','line'),
      jsonb_build_object('key','boundary_monitoring','label','Boundary Monitoring','rule_type','line_crossing','classes',jsonb_build_array('person','car','motorcycle'),'geometry','line'),
      jsonb_build_object('key','zone_activity','label','Zone Activity','rule_type','zone_entry','classes',jsonb_build_array('person','car','motorcycle'),'geometry','polygon'),
      jsonb_build_object('key','dwell','label','Dwell / Time in Zone','rule_type','zone_dwell','classes',jsonb_build_array('person'),'geometry','polygon'),
      jsonb_build_object('key','checkout_activity','label','Checkout Activity','rule_type','occupancy','classes',jsonb_build_array('person'),'geometry','polygon','note','Measures people present in the checkout zone, not completed transactions.'),
      jsonb_build_object('key','after_hours','label','After-Hours Activity','rule_type','schedule_activity','classes',jsonb_build_array('person','car','motorcycle'),'geometry','none')
    ),
    'always_on',jsonb_build_array(
      jsonb_build_object('key','site_health','label','Site Health','note','Always on. No monitoring rule is required.')
    ),
    'packs', jsonb_build_object(
      'retail', jsonb_build_array('visitor_flow','checkout_activity','after_hours'),
      'restaurant', jsonb_build_array('visitor_flow','zone_activity','dwell','after_hours'),
      'warehouse_logistics', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours'),
      'manufacturing', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours'),
      'office_commercial', jsonb_build_array('visitor_flow','dwell','after_hours'),
      'school_campus', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'parking_yard', jsonb_build_array('vehicle_flow','boundary_monitoring','zone_activity','after_hours'),
      'residential_community', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'custom', jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours')
    ),
    'purpose_recommendations', jsonb_build_object(
      'entrance_exit',jsonb_build_array('visitor_flow','after_hours'),
      'main_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'reception',jsonb_build_array('visitor_flow','dwell','after_hours'),
      'checkout_till',jsonb_build_array('checkout_activity','after_hours'),
      'loading_bay',jsonb_build_array('vehicle_flow','zone_activity','dwell','after_hours'),
      'warehouse_floor',jsonb_build_array('zone_activity','dwell','after_hours'),
      'perimeter',jsonb_build_array('boundary_monitoring','after_hours'),
      'restricted_area',jsonb_build_array('zone_activity','dwell','after_hours'),
      'parking',jsonb_build_array('vehicle_flow','zone_activity','after_hours'),
      'office_floor',jsonb_build_array('zone_activity','after_hours'),
      'school_gate',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','after_hours'),
      'corridor',jsonb_build_array('visitor_flow','after_hours'),
      'entrance',jsonb_build_array('visitor_flow','after_hours'),
      'general',jsonb_build_array('zone_activity','dwell'),
      'management',jsonb_build_array('zone_activity','after_hours'),
      'queue',jsonb_build_array('dwell','zone_activity'),
      'restricted',jsonb_build_array('zone_activity','dwell','after_hours'),
      'custom',jsonb_build_array('visitor_flow','vehicle_flow','boundary_monitoring','zone_activity','dwell','after_hours')
    )
  )
$function$;

create or replace function public.wl_notifications(
  p_site_id uuid default null::uuid,
  p_limit integer default 50
)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public','pg_temp'
as $function$
declare
  v_tenant uuid := wl_my_tenant();
  v_user uuid := auth.uid();
  v_limit int := least(greatest(coalesce(p_limit,50),1),200);
begin
  if v_tenant is null or v_user is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;

  if p_site_id is not null and not exists(
    select 1 from sites s where s.id=p_site_id and s.tenant_id=v_tenant
  ) then
    raise exception 'site not in your account' using errcode='42501';
  end if;

  return coalesce((
    with feed as (
      select
        'incident'::text as source_kind,
        i.id::text as source_id,
        i.site_id,
        s.name as site_name,
        coalesce(i.incident_started_at,i.occurred_at,i.opened_at) as created_at,
        coalesce(nullif(i.severity,''),'attention') as severity,
        initcap(replace(coalesce(nullif(i.incident_type,''),'Incident'),'_',' ')) as title,
        case
          when c.name is not null then 'Activity needs review on '||c.name||'.'
          else 'Activity at this site needs review.'
        end as body,
        '/incidents/?site='||i.site_id::text as href
      from operations_incidents i
      join sites s on s.id=i.site_id
      left join cameras c on c.id=i.camera_id
      where i.tenant_id=v_tenant
        and (p_site_id is null or i.site_id=p_site_id)
        and coalesce(i.incident_started_at,i.occurred_at,i.opened_at) >= now()-interval '90 days'

      union all

      select
        'report',
        r.id::text,
        r.site_id,
        s.name,
        r.generated_at,
        'info',
        'Daily report ready',
        'The WatchLog report for '||to_char(r.report_date,'FMDD FMMonth YYYY')||' is ready.',
        '/reports/?view=yesterday&date='||r.report_date::text||'&site='||r.site_id::text
      from report_snapshots r
      join sites s on s.id=r.site_id
      where r.tenant_id=v_tenant
        and (p_site_id is null or r.site_id=p_site_id)
        and r.generated_at >= now()-interval '90 days'

      union all

      select
        'health',
        f.id::text,
        f.site_id,
        s.name,
        f.opened_at,
        coalesce(nullif(f.severity,''),'warning'),
        case
          when f.camera_id is not null and c.name is not null
            then c.name||' needs attention'
          else 'Site health needs attention'
        end,
        initcap(replace(coalesce(nullif(f.fault_type,''),nullif(f.reason_code,''),'Monitoring issue'),'_',' ')),
        '/site-health/?site='||f.site_id::text
      from operational_faults f
      join sites s on s.id=f.site_id
      left join cameras c on c.id=f.camera_id
      where f.tenant_id=v_tenant
        and f.state<>'resolved'
        and (p_site_id is null or f.site_id=p_site_id)
        and f.opened_at >= now()-interval '90 days'
    ),
    ranked as (
      select feed.*, (nr.id is not null) as is_read
      from feed
      left join notification_reads nr
        on nr.tenant_id=v_tenant
       and nr.user_id=v_user
       and nr.source_kind=feed.source_kind
       and nr.source_id=feed.source_id
      order by feed.created_at desc
      limit v_limit
    )
    select jsonb_agg(jsonb_build_object(
      'kind',source_kind,
      'id',source_id,
      'site_id',site_id,
      'site_name',site_name,
      'created_at',created_at,
      'severity',severity,
      'title',title,
      'body',body,
      'href',href,
      'read',is_read
    ) order by created_at desc)
    from ranked
  ),'[]'::jsonb);
end
$function$;

create or replace function public.wl_my_latest_report_snapshot(p_site_id uuid)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public','pg_temp'
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
begin
  return (
    select jsonb_build_object(
      'report_id',r.id,
      'report_date',r.report_date,
      'revision',r.revision,
      'payload',r.payload,
      'versions',r.versions,
      'generated_at',r.generated_at,
      'delivery_status',r.delivery_status,
      'coverage_ratio',r.coverage_ratio,
      'pdf_available',r.pdf_sha256 is not null
    )
    from public.report_snapshots r
    where r.site_id=p_site_id
      and r.tenant_id=v_tenant
    order by r.report_date desc, r.revision desc, r.generated_at desc
    limit 1
  );
end
$function$;

revoke all on function public.wl_my_latest_report_snapshot(uuid) from public, anon;
grant execute on function public.wl_my_latest_report_snapshot(uuid) to authenticated, service_role;

create or replace function public.wl_owner_site_truth(p_site_id uuid)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public','pg_temp'
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_diag jsonb;
  v_latest_report jsonb;
  v_connection jsonb;
  v_camera_truth jsonb;
  v_security jsonb;
begin
  select * into v_site
  from public.sites
  where id=p_site_id and tenant_id=v_tenant;

  if v_site.id is null then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  v_diag := public.wl_my_site_diagnosis(p_site_id);
  v_latest_report := public.wl_my_latest_report_snapshot(p_site_id);

  with real_agents as (
    select a.*
    from public.agents a
    where a.site_id=p_site_id
      and a.tenant_id=v_tenant
      and coalesce(a.device_driver,'') <> 'recorder-push'
  ),
  ranked as (
    select a.*,
           row_number() over(order by a.enrolled_at desc nulls last,a.id desc) as rn
    from real_agents a
  ),
  authoritative as (
    select *
    from ranked
    where coalesce(v_site.multi_agent_enabled,false) or rn=1
  )
  select jsonb_build_object(
    'authoritative_registrations',count(*),
    'reporting_now',count(*) filter(where last_seen_at>now()-interval '3 minutes'),
    'last_seen_at',max(last_seen_at),
    'state',case
      when count(*)=0 then 'not_connected'
      when count(*) filter(where last_seen_at>now()-interval '3 minutes')=count(*) then 'connected'
      when count(*) filter(where last_seen_at>now()-interval '3 minutes')>0 then 'partial'
      else 'disconnected'
    end
  )
  into v_connection
  from authoritative;

  select jsonb_build_object(
    'monitored_total',count(*),
    'health_operational',count(*) filter(where lower(coalesce(h.health_state::text,'unknown'))='operational'),
    'health_attention',count(*) filter(where lower(coalesce(h.health_state::text,'unknown')) in ('degraded','offline')),
    'health_unknown',count(*) filter(where lower(coalesce(h.health_state::text,'unknown')) not in ('operational','degraded','offline')),
    'recording_confirmed',count(*) filter(where lower(coalesce(h.recording_state::text,'unknown'))='recording'),
    'recording_attention',count(*) filter(where lower(coalesce(h.recording_state::text,'unknown')) in ('not_recording','storage_fault')),
    'recording_unknown',count(*) filter(where lower(coalesce(h.recording_state::text,'unknown')) not in ('recording','not_recording','storage_fault'))
  )
  into v_camera_truth
  from public.cameras c
  left join public.camera_health h on h.camera_id=c.id
  where c.site_id=p_site_id
    and c.tenant_id=v_tenant
    and c.is_configured
    and coalesce(c.is_canonical,true);

  select jsonb_build_object(
    'open_review_required',count(*) filter(
      where coalesce(i.review_required,false)
        and coalesce(i.lifecycle_state,'open') not in ('resolved','closed')
    ),
    'last_7d_review_required',count(*) filter(
      where coalesce(i.review_required,false)
        and coalesce(i.incident_started_at,i.occurred_at,i.opened_at)>=now()-interval '7 days'
    ),
    'last_7d_total',count(*) filter(
      where coalesce(i.incident_started_at,i.occurred_at,i.opened_at)>=now()-interval '7 days'
    )
  )
  into v_security
  from public.operations_incidents i
  where i.site_id=p_site_id
    and i.tenant_id=v_tenant;

  return jsonb_build_object(
    'schema','owner-site-truth-v1',
    'generated_at',now(),
    'site',jsonb_build_object(
      'id',v_site.id,
      'name',v_site.name,
      'timezone',v_site.timezone,
      'site_type',v_site.site_type
    ),
    'connection',coalesce(v_connection,'{}'::jsonb),
    'monitoring_coverage',v_diag->'coverage',
    'camera_truth',coalesce(v_camera_truth,'{}'::jsonb),
    'security',coalesce(v_security,'{}'::jsonb),
    'latest_report',v_latest_report,
    'truth_rules',jsonb_build_object(
      'unknown_is_zero',false,
      'camera_health_implies_recording',false,
      'completed_service_day_implies_saved_report',false,
      'report_observation_implies_incident',false
    )
  );
end
$function$;

revoke all on function public.wl_owner_site_truth(uuid) from public, anon;
grant execute on function public.wl_owner_site_truth(uuid) to authenticated, service_role;

create or replace function public.wl_restaurant_day_truth(
  p_site_id uuid,
  p_date date default null::date
)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public','pg_temp'
as $function$
declare
  v_tenant uuid := public.wl_assert_my_site(p_site_id);
  v_day jsonb;
  v_supported boolean;
  v_sessions jsonb;
begin
  v_day := public.wl_restaurant_day(p_site_id,p_date);

  if coalesce((v_day->>'enabled')::boolean,false) is not true then
    return v_day;
  end if;

  v_supported :=
    coalesce((v_day->'data_quality'->>'camera_observations')::integer,0)>0
    or coalesce((v_day->'data_quality'->>'table_observations')::integer,0)>0;

  v_day := jsonb_set(
    v_day,
    '{data_quality,business_figures_supported}',
    to_jsonb(v_supported),
    true
  );

  if not v_supported then
    v_sessions := coalesce(v_day->'sessions','{}'::jsonb)
      || jsonb_build_object(
        'count',null,
        'estimated_covers',null,
        'served_sessions',null,
        'avg_observed_time_to_food_minutes',null,
        'median_observed_time_to_food_minutes',null,
        'median_minimum_observed_dwell_minutes',null,
        'items','[]'::jsonb,
        'support_reason','No restaurant observations were recorded in this service-day window.'
      );
    v_day := jsonb_set(v_day,'{sessions}',v_sessions,true);
  end if;

  return v_day;
end
$function$;

revoke all on function public.wl_restaurant_day_truth(uuid,date) from public, anon;
grant execute on function public.wl_restaurant_day_truth(uuid,date) to authenticated, service_role;

create or replace function public.wl_portal_overview(p_days integer default 7)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public'
as $function$
declare
  v_tenant uuid := wl_my_tenant();
  v_days   int  := least(greatest(coalesce(p_days, 7), 1), 90);
  v_from   timestamptz := now() - make_interval(days => v_days);
begin
  if v_tenant is null then
    return jsonb_build_object('tenant', null);
  end if;

  return jsonb_build_object(
    'tenant', (
      select jsonb_build_object('id',t.id,'name',t.name)
      from tenants t where t.id=v_tenant
    ),
    'window_days',v_days,

    'sites', (
      select coalesce(jsonb_agg(jsonb_build_object(
        'id',s.id,'name',s.name,'timezone',s.timezone
      ) order by s.name),'[]'::jsonb)
      from sites s where s.tenant_id=v_tenant
    ),

    'agents', (
      with ranked as (
        select a.*,s.name as site_name,coalesce(s.multi_agent_enabled,false) as multi_agent_enabled,
               row_number() over(
                 partition by a.site_id
                 order by a.enrolled_at desc nulls last,a.id desc
               ) as rn
        from agents a
        join sites s on s.id=a.site_id
        where a.tenant_id=v_tenant
          and coalesce(a.device_driver,'')<>'recorder-push'
      ),
      authoritative as (
        select * from ranked where multi_agent_enabled or rn=1
      )
      select coalesce(jsonb_agg(jsonb_build_object(
        'agent_id',a.id,
        'site',a.site_name,
        'hostname',a.hostname,
        'last_seen_at',a.last_seen_at,
        'agent_version',a.agent_version,
        'device_vendor',a.device_vendor,
        'device_model',a.device_model,
        'device_driver',a.device_driver,
        'event_count',(select count(*) from events e where e.agent_id=a.id),
        'last_event_at',(select max(e.device_ts) from events e where e.agent_id=a.id)
      ) order by a.last_seen_at desc nulls last),'[]'::jsonb)
      from authoritative a
    ),

    'totals', (
      select jsonb_build_object(
        'events',count(*),
        'cameras',(select count(*) from cameras c where c.tenant_id=v_tenant),
        'sites',(select count(*) from sites s where s.tenant_id=v_tenant)
      )
      from events
      where tenant_id=v_tenant and device_ts>=v_from
    ),

    'by_type', (
      select coalesce(jsonb_agg(jsonb_build_object('event_type',et,'count',n) order by n desc),'[]'::jsonb)
      from (
        select event_type et,count(*) n
        from events
        where tenant_id=v_tenant and device_ts>=v_from
        group by event_type
      ) x
    ),

    'by_camera', (
      select coalesce(jsonb_agg(jsonb_build_object('camera',cam,'site',st,'count',n) order by n desc),'[]'::jsonb)
      from (
        select coalesce(c.name,'unassigned') cam,s.name st,count(*) n
        from events e
        left join cameras c on c.id=e.camera_id
        left join sites s on s.id=e.site_id
        where e.tenant_id=v_tenant and e.device_ts>=v_from
        group by 1,2
      ) x
    ),

    'by_hour', (
      select coalesce(jsonb_agg(jsonb_build_object('hour',h,'count',n) order by h),'[]'::jsonb)
      from (
        select extract(hour from e.device_ts at time zone s.timezone)::int h,count(*) n
        from events e
        join sites s on s.id=e.site_id
        where e.tenant_id=v_tenant and e.device_ts>=v_from
        group by 1
      ) y
    ),

    'recent', (
      select coalesce(jsonb_agg(to_jsonb(r) order by r.received_at desc),'[]'::jsonb)
      from (
        select e.id as event_id,e.device_ts,e.received_at,e.event_type,
               c.name as camera,s.name as site,(sn.event_id is not null) as has_snapshot
        from events e
        left join cameras c on c.id=e.camera_id
        left join sites s on s.id=e.site_id
        left join snapshots sn on sn.event_id=e.id
        where e.tenant_id=v_tenant
        order by e.received_at desc
        limit 20
      ) r
    ),

    'health',jsonb_build_object(
      'offline_agents',(
        with ranked as (
          select a.*,s.name as site_name,coalesce(s.multi_agent_enabled,false) as multi_agent_enabled,
                 row_number() over(
                   partition by a.site_id
                   order by a.enrolled_at desc nulls last,a.id desc
                 ) as rn
          from agents a
          join sites s on s.id=a.site_id
          where a.tenant_id=v_tenant
            and coalesce(a.device_driver,'')<>'recorder-push'
        ),
        authoritative as (
          select * from ranked where multi_agent_enabled or rn=1
        )
        select coalesce(jsonb_agg(jsonb_build_object(
          'hostname',a.hostname,'site',a.site_name,'last_seen_at',a.last_seen_at
        )),'[]'::jsonb)
        from authoritative a
        where a.last_seen_at is null or a.last_seen_at<now()-interval '30 minutes'
      ),
      'silent_cameras',(
        select coalesce(jsonb_agg(jsonb_build_object(
          'camera',q.camera,'site',q.site,
          'hours_silent',round(extract(epoch from now()-q.last_at)/3600.0,1)
        )),'[]'::jsonb)
        from (
          select c.name as camera,s.name as site,
                 coalesce(max(e.device_ts),c.created_at) as last_at
          from cameras c
          join sites s on s.id=c.site_id
          left join events e on e.camera_id=c.id
          where c.tenant_id=v_tenant
          group by c.id,c.name,s.name,c.created_at
          having coalesce(max(e.device_ts),c.created_at)<now()-interval '24 hours'
        ) q
      ),
      'faults_24h',(
        select coalesce(jsonb_agg(jsonb_build_object(
          'event_type',et,'camera',cam,'count',n
        ) order by n desc),'[]'::jsonb)
        from (
          select e.event_type et,coalesce(c.name,'unassigned') cam,count(*) n
          from events e
          left join cameras c on c.id=e.camera_id
          where e.tenant_id=v_tenant
            and e.device_ts>now()-interval '24 hours'
            and e.event_type in ('video_loss','tamper','disk_error','disk_full')
          group by 1,2
        ) f
      )
    ),

    'open_codes',(
      select coalesce(jsonb_agg(jsonb_build_object(
        'code',ec.code,'site',s.name,'expires_at',ec.expires_at
      )),'[]'::jsonb)
      from enrollment_codes ec
      join sites s on s.id=ec.site_id
      where ec.tenant_id=v_tenant
        and ec.used_at is null
        and ec.expires_at>now()
    )
  );
end
$function$;

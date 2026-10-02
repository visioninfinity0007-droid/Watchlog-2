-- Align Chai Wala tenant meaning across customer AI, restaurant reporting and vision extraction.
-- The business-intelligence context is semantic guidance; observed values still come only from analyzed evidence.
-- restaurant-day-v2 adds role-gated metrics, complete-floor site totals, floor summaries and per-camera coverage.

update public.restaurant_camera_profiles p
set interval_seconds = case p.analytics_role
  when 'dining_floor' then 60
  when 'service_handoff' then 90
  when 'kitchen' then 120
  when 'cash_counter' then 180
  else p.interval_seconds
end,
updated_at=now()
where p.site_id='1a1fab32-10b3-4082-b61c-180ec04c758c'
  and p.analytics_role in ('dining_floor','service_handoff','kitchen','cash_counter');

update public.site_business_context
set reporting_prefs=coalesce(reporting_prefs,'{}'::jsonb) || $ctx$
{"ai_context_note": "Chai Wala - Chota Bukhari is a late-night outdoor dine-in restaurant/cafe. Treat 4:00 PM to 4:00 AM as WatchLog's canonical overnight service window unless the owner changes the configured hours. Floor 1 and Floor 2 are the only customer seating cameras and contain movable tables that may be combined for larger parties. Shop Front is a waiter/service handoff counter, not the customer entrance. Cash Counter is a tight till/staff workstation view; customer queue length is not visually reliable there. Kitchen is back-of-house operations. Back Entrance is staff/service access, not customer footfall. Office View and Office Camera are security/management views, not continuous business-performance cameras. For restaurant reporting, use concurrent visible diners, calibrated table occupancy, estimated table sessions/covers, food-visible served sessions, observed seated-to-first-food-visible timing, table utilization, and relative kitchen/handoff pressure. Always distinguish observed, estimated, and unsupported metrics. Do not infer unique footfall, sales, revenue, order accuracy, food quality, staff identity, demographics, confirmed fire, or medical incidents from camera evidence alone. Use actual coverage before claiming a complete service-day picture.", "restaurant_analytics": {"schema": "restaurant-vision-v2", "capture_policy": {"service_access": "event", "kitchen_seconds": 120, "office_security": "event", "cash_counter_seconds": 180, "dining_floor_seconds": 60, "service_handoff_seconds": 90}, "table_identity": "camera-specific calibrated table anchors with dynamic combining for adjacent movable tables", "footfall_reason": "No current camera provides a clean customer-entry counting line. Use visible diners and estimated table sessions/covers instead.", "capture_policy_status": "configured target only; actual observation density must be read from coverage/sample counts and must not be assumed", "service_time_definition": "first visibly occupied/seated evidence to first food-visible evidence; not POS order-to-serve", "customer_footfall_available": false}, "owner_insight_priorities": ["visible diner activity and peak periods on Floor 1 and Floor 2", "occupied tables and table utilization by floor and by calibrated table", "estimated table sessions and estimated covers, clearly labelled as camera-derived estimates", "observed seated-to-first-food-visible timing and slow-service patterns", "served versus occupied table sessions based on visible food presence", "waiter pickup and service-handoff pressure at Shop Front", "cash-counter activity and unattended periods; never claim customer queue length from this view", "kitchen activity continuity, relative operational pressure and visible safety concerns", "back-entrance service/delivery activity and unusual late-night access", "office occupancy and unusual access as security context", "after-hours presence and security exceptions", "camera, recording, recorder and visual-analysis coverage quality"], "restaurant_intelligence_context": {"schema": "restaurant-intelligence-context-v2", "purpose": "Shared business meaning for WatchLog customer responses, structured visual extraction and management reporting.", "calibration": {"capacity_note": "Tables are movable and can be combined. Nominal anchor capacity is calibration metadata, not a guaranteed seat count.", "dynamic_combining": true, "table_identity_rule": "Use table_key/anchor identity within each camera. When adjacent movable tables are visibly joined for one party, preserve each table row and assign the same combined_group.", "total_table_anchors": 23, "floor_1_table_anchors": 13, "floor_2_table_anchors": 10, "nominal_anchor_capacity": 4}, "service_day": {"days_iso": [1, 2, 3, 4, 5, 6, 7], "date_rule": "The service day is named by the date on which the 4:00 PM opening occurs. After midnight and before the next 4:00 PM opening, business reporting still belongs to the previous service date.", "overnight": true, "canonical_open": "16:00", "canonical_close": "04:00", "hours_authority": "Use the configured WatchLog service window for analytics. Public listing hours are informational only and must not override configured hours."}, "vision_policy": {"no_identity": true, "coverage_rule": "Never extrapolate missing periods as zero activity. Missing analysis is missing coverage.", "no_demographics": true, "confidence_scope": "Confidence applies to the visible observation in that frame, not to business outcomes beyond the frame.", "null_when_uncertain": true, "use_calibrated_table_keys": true, "no_sales_or_revenue_inference": true, "no_emotion_or_intent_inference": true, "preserve_dynamic_combined_groups": true}, "response_policy": {"never_infer": ["sales", "revenue", "transaction count", "order accuracy", "food quality", "staff identity", "customer identity", "customer demographics", "unique customer count", "true customer footfall", "confirmed fire", "medical diagnosis"], "summary_order": ["coverage and whether the service day is sufficiently observed", "customer demand signal: visible diners and occupied tables by hour/floor", "table utilization and estimated sessions/covers", "service timing: served sessions and observed time to food", "handoff and kitchen pressure", "operational/security exceptions", "practical improvement opportunities supported by repeated evidence"], "language_rules": ["Say visible diners, not footfall, unless a clean entry-counter capability is later configured.", "Say estimated covers or estimated table sessions when values are camera-derived.", "Say observed time to food, not order-to-serve time.", "State when coverage is partial before comparing hours, floors or service speed.", "Separate direct observations from estimates and from unsupported metrics.", "Do not convert relative pressure scores into order volumes or staffing productivity.", "Do not describe a single frame as a trend; trends require repeated observations."], "improvement_topics": ["shift staff attention toward repeatedly high-demand hours/floors", "investigate tables/areas with persistently low utilization when coverage is adequate", "investigate repeated long observed time-to-food sessions", "align kitchen and Shop Front staffing when dining demand and handoff pressure repeatedly diverge", "review recurring unattended counter periods during active service", "review recurring congestion or access exceptions", "improve camera placement or add an entry counting line if true footfall is required"]}, "evidence_classes": {"observed": "Directly visible in one or more analyzed frames.", "estimated": "Derived from a sequence of observed frames or calibrated table sessions and must be labelled estimated/observed.", "unsupported": "Not reliably measurable from the current camera geometry or without POS/entry-counter data."}, "business_identity": {"branch": "Chota Bukhari, DHA Phase 6, Karachi", "back_of_house": ["Kitchen", "Shop Front", "Cash Counter", "Back Entrance"], "business_type": "late-night chai cafe and casual restaurant", "service_style": "waiter-led outdoor dine-in with takeaway, delivery and car-side service", "customer_areas": ["Floor 1", "Floor 2"], "security_management_views": ["Office View", "Office Camera"]}, "camera_role_policy": {"kitchen": {"cameras": ["Kitchen"], "truth_boundary": "Operational and safety observations only; do not infer food quality, order accuracy, health diagnosis or confirmed fire.", "primary_outputs": ["staff_count", "kitchen_load", "active_station_pressure", "visible_output", "congestion", "cleaning_reset"]}, "cash_counter": {"cameras": ["Cash Counter"], "truth_boundary": "Customer queue is mostly outside frame; do not estimate queue length, sales, transaction count or revenue.", "primary_outputs": ["counter_active", "staff_count", "counter_interactions", "unattended_periods"]}, "dining_floor": {"cameras": ["Floor 1", "Floor 2"], "vision_focus": "Track calibrated tables, party size, visible food, staff presence, clearing/reset state and dynamic table combining. Prefer null over guessing under occlusion.", "primary_outputs": ["visible_diners", "occupied_tables", "table_utilization_pct", "estimated_table_sessions", "estimated_covers", "served_table_sessions", "observed_time_to_food_minutes", "minimum_observed_dwell_minutes"]}, "service_access": {"cameras": ["Back Entrance"], "truth_boundary": "Staff/service access only; not customer footfall.", "primary_outputs": ["service_access", "delivery_activity", "vehicle_dwell", "after_hours_movement"]}, "office_security": {"cameras": ["Office View", "Office Camera"], "truth_boundary": "Security/event context only; exclude from restaurant customer-volume and service-performance metrics.", "primary_outputs": ["presence", "unusual_access", "after_hours_presence"]}, "service_handoff": {"cameras": ["Shop Front"], "truth_boundary": "This is not the customer entrance and must not produce footfall.", "primary_outputs": ["staff_count", "handoff_load", "ready_item_dwell", "staff_waiting", "service_congestion"]}}, "metric_definitions": {"handoff_load": {"unit": "relative score 0-1", "class": "estimated", "definition": "Relative visible service-handoff pressure based on pickup activity, waiting staff, ready-item dwell and congestion.", "never_call": "customer queue length or exact order backlog", "camera_roles": ["service_handoff"]}, "kitchen_load": {"unit": "relative score 0-1", "class": "estimated", "definition": "Relative visible operational pressure based on staff activity, station use, congestion and visible output.", "never_call": "orders per hour, production quantity, food quality or staff productivity", "camera_roles": ["kitchen"]}, "counter_active": {"unit": "boolean/period", "class": "observed", "definition": "Whether the till/staff workstation is visibly attended or active.", "never_call": "customer queue length, transaction count, sales or revenue"}, "visible_diners": {"unit": "people", "class": "observed", "report_as": ["hourly average", "hourly peak", "service-day peak"], "definition": "Concurrent customers visibly present on dining-floor cameras at a sample time.", "never_call": "unique footfall or unique customers", "camera_roles": ["dining_floor"]}, "occupied_tables": {"unit": "tables", "class": "observed", "report_as": ["hourly average", "hourly peak", "per-table occupancy"], "definition": "Calibrated table anchors visibly occupied at a sample time.", "camera_roles": ["dining_floor"]}, "estimated_covers": {"unit": "diners", "class": "estimated", "caveat": "May be imperfect when people are occluded, move tables, or combine/split parties.", "definition": "Peak visible party size summed once per estimated table session.", "never_call": "unique customer count or POS covers"}, "customer_footfall": {"class": "unsupported", "reason": "No current camera provides a clean customer-entry counting line.", "available": false, "replacement_metrics": ["visible_diners", "estimated_table_sessions", "estimated_covers"]}, "served_table_sessions": {"unit": "sessions", "class": "observed-derived", "definition": "Estimated table sessions in which food becomes visibly present at least once.", "never_call": "completed orders, correct orders or paid orders"}, "table_utilization_pct": {"unit": "percent", "class": "estimated", "caveat": "Coverage gaps and occlusion reduce representativeness.", "definition": "Occupied valid table observations divided by valid observations for that calibrated table during the service window."}, "estimated_table_sessions": {"unit": "sessions", "class": "estimated", "caveat": "Table movement, occlusion and long sampling gaps can split or merge sessions incorrectly.", "definition": "A session starts when a calibrated table transitions from empty/unknown to occupied, or after a sufficiently long observation gap before occupancy resumes.", "session_gap_rule_minutes": 20}, "observed_time_to_food_minutes": {"unit": "minutes", "class": "observed-derived", "definition": "Elapsed time from first visibly occupied/seated evidence for a table session to first frame where food is visibly present.", "never_call": "POS order-to-serve time or kitchen ticket time", "interpretation": "Use medians and distributions when enough sessions exist; flag slow patterns only when supported by multiple sessions and adequate coverage."}, "minimum_observed_dwell_minutes": {"unit": "minutes", "class": "observed-derived", "caveat": "A lower bound; true departure may occur after the last sampled frame.", "definition": "Elapsed time from first occupied observation to the last observed occupied frame in a session."}}, "daily_report_structure": {"sections": ["service-day coverage and data quality", "hourly visible diner and occupied-table pattern", "floor and table utilization", "estimated sessions/covers and served-session pattern", "observed time-to-food distribution", "kitchen and handoff pressure", "operational/security exceptions", "evidence-based improvement actions"], "headline_kpis": ["service-day coverage", "peak visible diners", "peak occupied tables", "estimated covers", "served table sessions", "median observed time to food"]}}}
$ctx$::jsonb,
updated_at=now()
where site_id='1a1fab32-10b3-4082-b61c-180ec04c758c';

CREATE OR REPLACE FUNCTION public.wl_extract_restaurant_visual_observation()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
declare
  v_profile public.restaurant_camera_profiles;
  v_rest jsonb;
  v_row jsonb;
  v_table public.restaurant_tables;
  v_visible integer;
  v_staff integer;
  v_occ integer;
  v_served integer;
  v_kitchen numeric;
  v_handoff numeric;
  v_conf numeric;
  v_counter boolean;
begin
  if new.status <> 'done' or new.analysis is null or jsonb_typeof(new.analysis) <> 'object' then
    return new;
  end if;

  select * into v_profile
    from public.restaurant_camera_profiles
   where camera_id=new.camera_id
     and site_id=new.site_id
     and tenant_id=new.tenant_id
     and enabled;
  if v_profile.camera_id is null then return new; end if;

  v_rest := new.analysis->'restaurant';
  if (v_rest is null or jsonb_typeof(v_rest)<>'object')
     and jsonb_typeof(new.analysis->'business')='object' then
    v_rest := new.analysis->'business'->'restaurant';
  end if;
  if v_rest is null or jsonb_typeof(v_rest)<>'object' then return new; end if;

  v_visible := case when coalesce(v_rest->>'visible_customers','') ~ '^\d+$'
                    then (v_rest->>'visible_customers')::integer end;
  v_staff := case when coalesce(v_rest->>'staff_count','') ~ '^\d+$'
                  then (v_rest->>'staff_count')::integer end;
  v_occ := case when coalesce(v_rest->>'occupied_tables','') ~ '^\d+$'
                then (v_rest->>'occupied_tables')::integer end;
  v_served := case when coalesce(v_rest->>'served_tables','') ~ '^\d+$'
                   then (v_rest->>'served_tables')::integer end;
  v_kitchen := case when coalesce(v_rest->>'kitchen_load','') ~ '^(0(\.\d+)?|1(\.0+)?)$'
                    then (v_rest->>'kitchen_load')::numeric end;
  v_handoff := case when coalesce(v_rest->>'handoff_load','') ~ '^(0(\.\d+)?|1(\.0+)?)$'
                    then (v_rest->>'handoff_load')::numeric end;
  v_conf := case when coalesce(v_rest->>'confidence','') ~ '^(0(\.\d+)?|1(\.0+)?)$'
                 then (v_rest->>'confidence')::numeric end;
  v_counter := case
    when lower(coalesce(v_rest->>'counter_active','')) in ('true','1','yes') then true
    when lower(coalesce(v_rest->>'counter_active','')) in ('false','0','no') then false
  end;

  -- Defense in depth: a business metric may only originate from a camera role
  -- that can visually support it.
  if v_profile.analytics_role <> 'dining_floor' then
    v_visible := null;
    v_occ := null;
    v_served := null;
  end if;
  if v_profile.analytics_role <> 'kitchen' then v_kitchen := null; end if;
  if v_profile.analytics_role <> 'service_handoff' then v_handoff := null; end if;
  if v_profile.analytics_role <> 'cash_counter' then v_counter := null; end if;

  insert into public.restaurant_visual_observations(
    event_id,tenant_id,site_id,camera_id,captured_at,camera_role,
    visible_customers,staff_count,occupied_tables,served_tables,
    kitchen_load,handoff_load,counter_active,confidence,activity,schema_version,updated_at
  ) values (
    new.event_id,new.tenant_id,new.site_id,new.camera_id,new.captured_at,v_profile.analytics_role,
    v_visible,v_staff,v_occ,v_served,v_kitchen,v_handoff,v_counter,
    v_conf,v_rest,
    coalesce(nullif(v_rest->>'schema_version',''),'restaurant-vision-v2'),now()
  )
  on conflict(event_id) do update set
    camera_role=excluded.camera_role,
    visible_customers=excluded.visible_customers,
    staff_count=excluded.staff_count,
    occupied_tables=excluded.occupied_tables,
    served_tables=excluded.served_tables,
    kitchen_load=excluded.kitchen_load,
    handoff_load=excluded.handoff_load,
    counter_active=excluded.counter_active,
    confidence=excluded.confidence,
    activity=excluded.activity,
    schema_version=excluded.schema_version,
    updated_at=now();

  if v_profile.analytics_role='dining_floor'
     and jsonb_typeof(v_rest->'tables')='array' then
    for v_row in select value from jsonb_array_elements(v_rest->'tables')
    loop
      if jsonb_typeof(v_row)<>'object' or coalesce(v_row->>'table_key','')='' then continue; end if;
      select * into v_table
        from public.restaurant_tables
       where site_id=new.site_id
         and camera_id=new.camera_id
         and table_key=v_row->>'table_key'
         and active
       limit 1;
      if v_table.id is null then continue; end if;

      insert into public.restaurant_table_observations(
        event_id,table_id,tenant_id,site_id,camera_id,captured_at,
        occupied,customer_count,food_present,drinks_present,staff_present,clearing_state,
        combined_group,visibility_quality,confidence
      ) values (
        new.event_id,v_table.id,new.tenant_id,new.site_id,new.camera_id,new.captured_at,
        case when lower(coalesce(v_row->>'occupied','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'occupied','')) in ('false','0','no') then false end,
        case when coalesce(v_row->>'customer_count','') ~ '^\d+$'
             then (v_row->>'customer_count')::integer end,
        case when lower(coalesce(v_row->>'food_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'food_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'drinks_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'drinks_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'staff_present','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'staff_present','')) in ('false','0','no') then false end,
        case when lower(coalesce(v_row->>'clearing_state','')) in ('true','1','yes') then true
             when lower(coalesce(v_row->>'clearing_state','')) in ('false','0','no') then false end,
        nullif(left(coalesce(v_row->>'combined_group',''),80),''),
        case when coalesce(v_row->>'visibility_quality','') ~ '^(0(\.\d+)?|1(\.0+)?)$'
             then (v_row->>'visibility_quality')::numeric end,
        case when coalesce(v_row->>'confidence','') ~ '^(0(\.\d+)?|1(\.0+)?)$'
             then (v_row->>'confidence')::numeric end
      )
      on conflict(event_id,table_id) do update set
        occupied=excluded.occupied,
        customer_count=excluded.customer_count,
        food_present=excluded.food_present,
        drinks_present=excluded.drinks_present,
        staff_present=excluded.staff_present,
        clearing_state=excluded.clearing_state,
        combined_group=excluded.combined_group,
        visibility_quality=excluded.visibility_quality,
        confidence=excluded.confidence;
    end loop;
  end if;

  return new;
exception when others then
  -- Restaurant extraction must never block canonical visual-review completion.
  return new;
end $function$;


CREATE OR REPLACE FUNCTION public.wl_vision_claim_snapshots_v2(p_limit integer DEFAULT 2, p_worker_id text DEFAULT NULL::text, p_provider_external boolean DEFAULT true)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare v_out jsonb;
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;

  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
      join public.snapshots s on s.event_id=r.event_id
      join public.events ev on ev.id=s.event_id
      join public.cameras c on c.id=s.camera_id
      left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id and rp.enabled
     where coalesce(c.is_canonical,true)
       and (
         (r.status='pending' and r.next_attempt_at<=now())
         or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
         or (r.status='processing' and r.lease_until<now() and r.attempts<5)
       )
       and (
         not coalesce(p_provider_external,true)
         or exists (
           select 1 from public.ai_site_egress_policy ep
            where ep.site_id=s.site_id and ep.external_egress_allowed=true
         )
       )
       and not (
         rp.sampling_mode='event'
         and coalesce(ev.payload->>'source','')='periodic_snapshot'
       )
     order by r.next_attempt_at,r.captured_at
     for update of r skip locked
     limit least(greatest(coalesce(p_limit,2),1),4)
  ),
  claimed as (
    update public.snapshot_visual_reviews r
       set status='processing',
           attempts=r.attempts+1,
           lease_until=now()+interval '4 minutes',
           worker_id=left(coalesce(p_worker_id,'edge-vision-worker'),120),
           last_error=null,
           updated_at=now()
      from picked p
     where r.event_id=p.event_id
     returning r.event_id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'event_id',s.event_id,
      'tenant_id',s.tenant_id,
      'site_id',s.site_id,
      'camera_id',s.camera_id,
      'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
      'channel',coalesce(c.physical_channel,c.channel),
      'camera_purpose',coalesce(c.purpose,'general'),
      'captured_at',s.captured_at,
      'timezone',coalesce(si.timezone,'Asia/Karachi'),
      'site_type',coalesce(b.site_type,si.site_type,'other'),
      'business_context',jsonb_build_object(
        'site_type',coalesce(b.site_type,si.site_type,'other'),
        'open_time',b.open_time,
        'close_time',b.close_time,
        'overnight',coalesce(b.overnight,false),
        'working_days',coalesce(to_jsonb(b.working_days),'[]'::jsonb),
        'camera_context',coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
        'owner_insight_priorities',coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
        'ai_context_note',coalesce(b.reporting_prefs->>'ai_context_note',''),
        'restaurant_intelligence_context',coalesce(b.reporting_prefs->'restaurant_intelligence_context','{}'::jsonb),
        'restaurant_analytics',case
          when coalesce(b.site_type,si.site_type,'other')='restaurant' and rp.enabled then
            jsonb_build_object(
              'enabled',true,
              'camera_role',rp.analytics_role,
              'sampling_mode',rp.sampling_mode,
              'interval_seconds',rp.interval_seconds,
              'config',rp.config,
              'tables',coalesce((
                select jsonb_agg(jsonb_build_object(
                  'table_key',rt.table_key,'label',rt.label,'capacity',rt.capacity,
                  'tracking_mode',rt.tracking_mode,'anchor',rt.anchor,'roi',rt.roi,
                  'can_combine',rt.can_combine
                ) order by rt.sort_order,rt.table_key)
                from public.restaurant_tables rt
                where rt.camera_id=s.camera_id and rt.site_id=s.site_id and rt.active
              ),'[]'::jsonb),
              'output_contract',jsonb_build_object(
                'top_level_key','restaurant',
                'schema_version','restaurant-vision-v1',
                'truth_rules',jsonb_build_array(
                  'Count only visible people; do not infer unique identity.',
                  'visible_customers means currently visible customers, not unique footfall.',
                  'food_present means visible food on a table; do not infer order correctness or food quality.',
                  'Do not infer sales, revenue, staff identity, health diagnosis, or customer demographics.',
                  'Use null when a requested field is not visually defensible.'
                ),
                'fields',jsonb_build_array(
                  'visible_customers','staff_count','occupied_tables','served_tables',
                  'kitchen_load','handoff_load','counter_active','confidence','tables'
                ),
                'table_fields',jsonb_build_array(
                  'table_key','occupied','customer_count','food_present','drinks_present',
                  'staff_present','clearing_state','combined_group','visibility_quality','confidence'
                )
              )
            )
          else null
        end
      ),
      'content_type',s.content_type,
      'bytes',s.bytes,
      'image_b64',encode(s.image,'base64')
    ) order by s.captured_at),'[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshot_visual_reviews r on r.event_id=q.event_id
    join public.snapshots s on s.event_id=q.event_id
    join public.sites si on si.id=s.site_id
    left join public.site_business_context b on b.site_id=s.site_id
    left join public.cameras c on c.id=s.camera_id
    left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id
   where coalesce(c.is_canonical,true);

  return coalesce(v_out,'[]'::jsonb);
end $function$;


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
    'enabled',true,'schema','restaurant-day-v2','service_date',v_date,'timezone',v_site.timezone,
    'window',jsonb_build_object('start',v_start,'end',v_end),
    'hourly',coalesce(v_hourly,'[]'::jsonb),
    'floors',coalesce(v_floors,'[]'::jsonb),
    'tables',coalesce(v_tables,'[]'::jsonb),
    'sessions',coalesce(v_sessions,jsonb_build_object('count',0,'estimated_covers',0,'served_sessions',0,'items','[]'::jsonb)),
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
end $function$;


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
      'restaurant',case when v_is_restaurant then 'restaurant-day-v2' else null end
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
end $function$;


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
    'facts_version','watchlog-ai-context-v4',
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
end $function$;


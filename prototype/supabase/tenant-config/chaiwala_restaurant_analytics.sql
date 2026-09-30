-- Chai Wala / Chota Bukhari restaurant calibration.
-- Deliberately tenant-specific; the reusable schema is migration 0120.
-- Safe to re-run after camera IDs change because cameras are resolved by name.

do $$
declare
  v_tenant uuid;
  v_site uuid;
  v_f1 uuid; v_front uuid; v_office1 uuid; v_back uuid;
  v_cash uuid; v_f2 uuid; v_kitchen uuid; v_office2 uuid;
begin
  select id into v_tenant from public.tenants where lower(name)='chaiwala' limit 1;
  if v_tenant is null then raise exception 'chaiwala tenant not found'; end if;

  select id into v_site from public.sites
   where tenant_id=v_tenant and name='Chai Wala - Chota Bukhari' limit 1;
  if v_site is null then raise exception 'Chai Wala - Chota Bukhari site not found'; end if;

  select id into v_f1 from public.cameras where site_id=v_site and name='Floor 1' and coalesce(is_canonical,true) limit 1;
  select id into v_front from public.cameras where site_id=v_site and name='Shop Front' and coalesce(is_canonical,true) limit 1;
  select id into v_office1 from public.cameras where site_id=v_site and name='Office View' and coalesce(is_canonical,true) limit 1;
  select id into v_back from public.cameras where site_id=v_site and name='Back Entrance' and coalesce(is_canonical,true) limit 1;
  select id into v_cash from public.cameras where site_id=v_site and name='Cash Counter' and coalesce(is_canonical,true) limit 1;
  select id into v_f2 from public.cameras where site_id=v_site and name='Floor 2' and coalesce(is_canonical,true) limit 1;
  select id into v_kitchen from public.cameras where site_id=v_site and name='Kitchen' and coalesce(is_canonical,true) limit 1;
  select id into v_office2 from public.cameras where site_id=v_site and name='Office Camera' and coalesce(is_canonical,true) limit 1;

  if v_f1 is null or v_front is null or v_office1 is null or v_back is null
     or v_cash is null or v_f2 is null or v_kitchen is null or v_office2 is null then
    raise exception 'one or more Chai Wala cameras are missing';
  end if;

  insert into public.restaurant_camera_profiles
    (camera_id,tenant_id,site_id,analytics_role,sampling_mode,interval_seconds,enabled,config)
  values
    (v_f1,v_tenant,v_site,'dining_floor','interval',60,true,
      '{"table_tracking_mode":"anchor_match","supports_dynamic_combining":true,"measure":["visible_customers","table_occupancy","food_present","staff_visits","table_sessions","reset_state"]}'),
    (v_f2,v_tenant,v_site,'dining_floor','interval',60,true,
      '{"table_tracking_mode":"anchor_match","supports_dynamic_combining":true,"measure":["visible_customers","table_occupancy","food_present","staff_visits","table_sessions","reset_state"]}'),
    (v_kitchen,v_tenant,v_site,'kitchen','interval',120,true,
      '{"measure":["staff_count","active_station_pressure","kitchen_load","visible_output","congestion","cleaning_reset"],"safety_advisory_only":true}'),
    (v_front,v_tenant,v_site,'service_handoff','hybrid',90,true,
      '{"measure":["staff_count","handoff_load","ready_item_dwell","staff_waiting","service_congestion"],"not_customer_entrance":true}'),
    (v_cash,v_tenant,v_site,'cash_counter','hybrid',180,true,
      '{"measure":["counter_active","staff_count","counter_interactions","unattended_periods"],"queue_visibility":"poor","do_not_report_customer_queue_length":true}'),
    (v_back,v_tenant,v_site,'service_access','event',null,true,
      '{"measure":["service_access","delivery_activity","vehicle_dwell","after_hours_movement"],"not_customer_footfall":true}'),
    (v_office1,v_tenant,v_site,'office_security','event',null,true,
      '{"measure":["presence","unusual_access","after_hours_presence"],"business_analytics":false}'),
    (v_office2,v_tenant,v_site,'office_security','event',null,true,
      '{"measure":["presence","unusual_access","after_hours_presence"],"business_analytics":false}')
  on conflict(camera_id) do update set
    analytics_role=excluded.analytics_role,
    sampling_mode=excluded.sampling_mode,
    interval_seconds=excluded.interval_seconds,
    enabled=excluded.enabled,
    config=excluded.config,
    updated_at=now();

  delete from public.restaurant_tables where site_id=v_site;

  insert into public.restaurant_tables
    (tenant_id,site_id,camera_id,table_key,label,capacity,tracking_mode,anchor,roi,can_combine,sort_order)
  values
    (v_tenant,v_site,v_f1,'F1-01','Floor 1 · Front centre',4,'anchor_match','{"x":0.36,"y":0.82,"radius":0.13}','{}',true,1),
    (v_tenant,v_site,v_f1,'F1-02','Floor 1 · Left foreground',4,'anchor_match','{"x":0.18,"y":0.61,"radius":0.11}','{}',true,2),
    (v_tenant,v_site,v_f1,'F1-03','Floor 1 · Right foreground',4,'anchor_match','{"x":0.73,"y":0.56,"radius":0.12}','{}',true,3),
    (v_tenant,v_site,v_f1,'F1-04','Floor 1 · Mid left',4,'anchor_match','{"x":0.29,"y":0.43,"radius":0.10}','{}',true,4),
    (v_tenant,v_site,v_f1,'F1-05','Floor 1 · Mid centre',4,'anchor_match','{"x":0.45,"y":0.35,"radius":0.10}','{}',true,5),
    (v_tenant,v_site,v_f1,'F1-06','Floor 1 · Mid right',4,'anchor_match','{"x":0.58,"y":0.43,"radius":0.10}','{}',true,6),
    (v_tenant,v_site,v_f1,'F1-07','Floor 1 · Upper left',4,'anchor_match','{"x":0.24,"y":0.27,"radius":0.09}','{}',true,7),
    (v_tenant,v_site,v_f1,'F1-08','Floor 1 · Upper centre',4,'anchor_match','{"x":0.42,"y":0.25,"radius":0.09}','{}',true,8),
    (v_tenant,v_site,v_f1,'F1-09','Floor 1 · Upper right',4,'anchor_match','{"x":0.62,"y":0.23,"radius":0.09}','{}',true,9),
    (v_tenant,v_site,v_f1,'F1-10','Floor 1 · Back left',4,'anchor_match','{"x":0.15,"y":0.16,"radius":0.08}','{}',true,10),
    (v_tenant,v_site,v_f1,'F1-11','Floor 1 · Back centre-left',4,'anchor_match','{"x":0.34,"y":0.15,"radius":0.08}','{}',true,11),
    (v_tenant,v_site,v_f1,'F1-12','Floor 1 · Back centre-right',4,'anchor_match','{"x":0.53,"y":0.14,"radius":0.08}','{}',true,12),
    (v_tenant,v_site,v_f1,'F1-13','Floor 1 · Back right',4,'anchor_match','{"x":0.72,"y":0.16,"radius":0.08}','{}',true,13),
    (v_tenant,v_site,v_f2,'F2-01','Floor 2 · Front left',4,'anchor_match','{"x":0.23,"y":0.57,"radius":0.11}','{}',true,101),
    (v_tenant,v_site,v_f2,'F2-02','Floor 2 · Front centre',4,'anchor_match','{"x":0.34,"y":0.59,"radius":0.11}','{}',true,102),
    (v_tenant,v_site,v_f2,'F2-03','Floor 2 · Front right',4,'anchor_match','{"x":0.45,"y":0.56,"radius":0.11}','{}',true,103),
    (v_tenant,v_site,v_f2,'F2-04','Floor 2 · Left side',4,'anchor_match','{"x":0.13,"y":0.39,"radius":0.09}','{}',true,104),
    (v_tenant,v_site,v_f2,'F2-05','Floor 2 · Upper left',4,'anchor_match','{"x":0.36,"y":0.27,"radius":0.09}','{}',true,105),
    (v_tenant,v_site,v_f2,'F2-06','Floor 2 · Upper centre',4,'anchor_match','{"x":0.55,"y":0.27,"radius":0.09}','{}',true,106),
    (v_tenant,v_site,v_f2,'F2-07','Floor 2 · Upper right',4,'anchor_match','{"x":0.72,"y":0.29,"radius":0.09}','{}',true,107),
    (v_tenant,v_site,v_f2,'F2-08','Floor 2 · Back left',4,'anchor_match','{"x":0.29,"y":0.13,"radius":0.08}','{}',true,108),
    (v_tenant,v_site,v_f2,'F2-09','Floor 2 · Back centre',4,'anchor_match','{"x":0.53,"y":0.13,"radius":0.08}','{}',true,109),
    (v_tenant,v_site,v_f2,'F2-10','Floor 2 · Back right',4,'anchor_match','{"x":0.78,"y":0.14,"radius":0.08}','{}',true,110);

  update public.site_business_context
     set reporting_prefs=coalesce(reporting_prefs,'{}'::jsonb) || $ctx$
{"ai_context_note": "Chai Wala - Chota Bukhari is a late-night outdoor dine-in restaurant/cafe. Treat 4:00 PM to 4:00 AM as WatchLog's canonical overnight service window unless the owner changes the configured hours. Floor 1 and Floor 2 are the only customer seating cameras and contain movable tables that may be combined for larger parties. Shop Front is a waiter/service handoff counter, not the customer entrance. Cash Counter is a tight till/staff workstation view; customer queue length is not visually reliable there. Kitchen is back-of-house operations. Back Entrance is staff/service access, not customer footfall. Office View and Office Camera are security/management views, not continuous business-performance cameras. For restaurant reporting, use concurrent visible diners, calibrated table occupancy, estimated table sessions/covers, food-visible served sessions, observed seated-to-first-food-visible timing, table utilization, and relative kitchen/handoff pressure. Always distinguish observed, estimated, and unsupported metrics. Do not infer unique footfall, sales, revenue, order accuracy, food quality, staff identity, demographics, confirmed fire, or medical incidents from camera evidence alone. Use actual coverage before claiming a complete service-day picture.", "restaurant_analytics": {"schema": "restaurant-vision-v3", "capture_policy": {"service_access": "event", "kitchen_seconds": 120, "office_security": "event", "cash_counter_seconds": 180, "dining_floor_seconds": 60, "service_handoff_seconds": 90}, "table_identity": "camera-specific calibrated table anchors with dynamic combining for adjacent movable tables", "footfall_reason": "No current camera provides a clean customer-entry counting line. Use visible diners and estimated table sessions/covers instead.", "capture_policy_status": "configured target only; actual observation density must be read from coverage/sample counts and must not be assumed", "service_time_definition": "first visibly occupied/seated evidence to first food-visible evidence; not POS order-to-serve", "customer_footfall_available": false}, "owner_insight_priorities": ["visible diner activity and peak periods on Floor 1 and Floor 2", "occupied tables and table utilization by floor and by calibrated table", "estimated table sessions and estimated covers, clearly labelled as camera-derived estimates", "observed seated-to-first-food-visible timing and slow-service patterns", "served versus occupied table sessions based on visible food presence", "waiter pickup and service-handoff pressure at Shop Front", "cash-counter activity and unattended periods; never claim customer queue length from this view", "kitchen activity continuity, relative operational pressure and visible safety concerns", "back-entrance service/delivery activity and unusual late-night access", "office occupancy and unusual access as security context", "after-hours presence and security exceptions", "camera, recording, recorder and visual-analysis coverage quality"], "restaurant_intelligence_context": {"schema": "restaurant-intelligence-context-v2", "purpose": "Shared business meaning for WatchLog customer responses, structured visual extraction and management reporting.", "calibration": {"capacity_note": "Tables are movable and can be combined. Nominal anchor capacity is calibration metadata, not a guaranteed seat count.", "dynamic_combining": true, "table_identity_rule": "Use table_key/anchor identity within each camera. When adjacent movable tables are visibly joined for one party, preserve each table row and assign the same combined_group.", "total_table_anchors": 23, "floor_1_table_anchors": 13, "floor_2_table_anchors": 10, "nominal_anchor_capacity": 4}, "service_day": {"days_iso": [1, 2, 3, 4, 5, 6, 7], "date_rule": "The service day is named by the date on which the 4:00 PM opening occurs. After midnight and before the next 4:00 PM opening, business reporting still belongs to the previous service date.", "yesterday_rule": "Yesterday means the latest completed configured Chai Wala service day, not midnight-to-midnight calendar yesterday.", "overnight": true, "canonical_open": "16:00", "canonical_close": "04:00", "hours_authority": "Use the configured WatchLog service window for analytics. Public listing hours are informational only and must not override configured hours."}, "vision_policy": {"no_identity": true, "coverage_rule": "Never extrapolate missing periods as zero activity. Missing analysis is missing coverage.", "no_demographics": true, "confidence_scope": "Confidence applies to the visible observation in that frame, not to business outcomes beyond the frame.", "null_when_uncertain": true, "use_calibrated_table_keys": true, "no_sales_or_revenue_inference": true, "no_emotion_or_intent_inference": true, "preserve_dynamic_combined_groups": true}, "response_policy": {"never_infer": ["sales", "revenue", "transaction count", "order accuracy", "food quality", "staff identity", "customer identity", "customer demographics", "unique customer count", "true customer footfall", "confirmed fire", "medical diagnosis"], "summary_order": ["coverage and whether the service day is sufficiently observed", "customer demand signal: visible diners and occupied tables by hour/floor", "table utilization and estimated sessions/covers", "service timing: served sessions and observed time to food", "handoff and kitchen pressure", "operational/security exceptions", "practical improvement opportunities supported by repeated evidence"], "language_rules": ["Say visible diners, not footfall, unless a clean entry-counter capability is later configured.", "Say estimated covers or estimated table sessions when values are camera-derived.", "Say observed time to food, not order-to-serve time.", "State when coverage is partial before comparing hours, floors or service speed.", "Separate direct observations from estimates and from unsupported metrics.", "Do not convert relative pressure scores into order volumes or staffing productivity.", "Do not describe a single frame as a trend; trends require repeated observations."], "improvement_topics": ["shift staff attention toward repeatedly high-demand hours/floors", "investigate tables/areas with persistently low utilization when coverage is adequate", "investigate repeated long observed time-to-food sessions", "align kitchen and Shop Front staffing when dining demand and handoff pressure repeatedly diverge", "review recurring unattended counter periods during active service", "review recurring congestion or access exceptions", "improve camera placement or add an entry counting line if true footfall is required"]}, "evidence_classes": {"observed": "Directly visible in one or more analyzed frames.", "estimated": "Derived from a sequence of observed frames or calibrated table sessions and must be labelled estimated/observed.", "unsupported": "Not reliably measurable from the current camera geometry or without POS/entry-counter data."}, "business_identity": {"branch": "Chota Bukhari, DHA Phase 6, Karachi", "back_of_house": ["Kitchen", "Shop Front", "Cash Counter", "Back Entrance"], "business_type": "late-night chai cafe and casual restaurant", "service_style": "waiter-led outdoor dine-in with takeaway, delivery and car-side service", "customer_areas": ["Floor 1", "Floor 2"], "security_management_views": ["Office View", "Office Camera"]}, "camera_role_policy": {"kitchen": {"cameras": ["Kitchen"], "truth_boundary": "Operational and safety observations only; do not infer food quality, order accuracy, health diagnosis or confirmed fire.", "primary_outputs": ["staff_count", "kitchen_load", "active_station_pressure", "visible_output", "congestion", "cleaning_reset"]}, "cash_counter": {"cameras": ["Cash Counter"], "truth_boundary": "Customer queue is mostly outside frame; do not estimate queue length, sales, transaction count or revenue.", "primary_outputs": ["counter_active", "staff_count", "counter_interactions", "unattended_periods"]}, "dining_floor": {"cameras": ["Floor 1", "Floor 2"], "vision_focus": "Track calibrated tables, party size, visible food, staff presence, clearing/reset state and dynamic table combining. Prefer null over guessing under occlusion.", "primary_outputs": ["visible_diners", "occupied_tables", "table_utilization_pct", "estimated_table_sessions", "estimated_covers", "served_table_sessions", "observed_time_to_food_minutes", "minimum_observed_dwell_minutes"]}, "service_access": {"cameras": ["Back Entrance"], "truth_boundary": "Staff/service access only; not customer footfall.", "primary_outputs": ["service_access", "delivery_activity", "vehicle_dwell", "after_hours_movement"]}, "office_security": {"cameras": ["Office View", "Office Camera"], "truth_boundary": "Security/event context only; exclude from restaurant customer-volume and service-performance metrics.", "primary_outputs": ["presence", "unusual_access", "after_hours_presence"]}, "service_handoff": {"cameras": ["Shop Front"], "truth_boundary": "This is not the customer entrance and must not produce footfall.", "primary_outputs": ["staff_count", "handoff_load", "ready_item_dwell", "staff_waiting", "service_congestion"]}}, "metric_definitions": {"handoff_load": {"unit": "relative score 0-1", "class": "estimated", "definition": "Relative visible service-handoff pressure based on pickup activity, waiting staff, ready-item dwell and congestion.", "never_call": "customer queue length or exact order backlog", "camera_roles": ["service_handoff"]}, "kitchen_load": {"unit": "relative score 0-1", "class": "estimated", "definition": "Relative visible operational pressure based on staff activity, station use, congestion and visible output.", "never_call": "orders per hour, production quantity, food quality or staff productivity", "camera_roles": ["kitchen"]}, "counter_active": {"unit": "boolean/period", "class": "observed", "definition": "Whether the till/staff workstation is visibly attended or active.", "never_call": "customer queue length, transaction count, sales or revenue"}, "visible_diners": {"unit": "people", "class": "observed", "report_as": ["hourly average", "hourly peak", "service-day peak"], "definition": "Concurrent customers visibly present on dining-floor cameras at a sample time.", "never_call": "unique footfall or unique customers", "camera_roles": ["dining_floor"]}, "occupied_tables": {"unit": "tables", "class": "observed", "report_as": ["hourly average", "hourly peak", "per-table occupancy"], "definition": "Calibrated table anchors visibly occupied at a sample time.", "camera_roles": ["dining_floor"]}, "estimated_covers": {"unit": "diners", "class": "estimated", "caveat": "May be imperfect when people are occluded, move tables, or combine/split parties.", "definition": "Peak visible party size summed once per estimated table session.", "never_call": "unique customer count or POS covers"}, "customer_footfall": {"class": "unsupported", "reason": "No current camera provides a clean customer-entry counting line.", "available": false, "replacement_metrics": ["visible_diners", "estimated_table_sessions", "estimated_covers"]}, "served_table_sessions": {"unit": "sessions", "class": "observed-derived", "definition": "Estimated table sessions in which food becomes visibly present at least once.", "never_call": "completed orders, correct orders or paid orders"}, "table_utilization_pct": {"unit": "percent", "class": "estimated", "caveat": "Coverage gaps and occlusion reduce representativeness.", "definition": "Occupied valid table observations divided by valid observations for that calibrated table during the service window."}, "estimated_table_sessions": {"unit": "sessions", "class": "estimated", "caveat": "Table movement, occlusion and long sampling gaps can split or merge sessions incorrectly.", "definition": "A session starts when a calibrated table transitions from empty/unknown to occupied, or after a sufficiently long observation gap before occupancy resumes.", "session_gap_rule_minutes": 20}, "observed_time_to_food_minutes": {"unit": "minutes", "class": "observed-derived", "definition": "Elapsed time from first visibly occupied/seated evidence for a table session to first frame where food is visibly present.", "never_call": "POS order-to-serve time or kitchen ticket time", "interpretation": "Use medians and distributions when enough sessions exist; flag slow patterns only when supported by multiple sessions and adequate coverage."}, "minimum_observed_dwell_minutes": {"unit": "minutes", "class": "observed-derived", "caveat": "A lower bound; true departure may occur after the last sampled frame.", "definition": "Elapsed time from first occupied observation to the last observed occupied frame in a session."}}, "daily_report_structure": {"sections": ["service-day coverage and data quality", "hourly visible diner and occupied-table pattern", "floor and table utilization", "estimated sessions/covers and served-session pattern", "observed time-to-food distribution", "kitchen and handoff pressure", "operational/security exceptions", "evidence-based improvement actions"], "headline_kpis": ["service-day coverage", "peak visible diners", "peak occupied tables", "estimated covers", "served table sessions", "median observed time to food"]}}}
$ctx$::jsonb,
         updated_at=now()
   where site_id=v_site and tenant_id=v_tenant;

  update public.site_business_context
     set reporting_prefs=jsonb_set(
       reporting_prefs,
       '{report_layout_profile}',
       '"chaiwala_restaurant_ops_v1"'::jsonb,
       true
     ),
     updated_at=now()
   where site_id=v_site and tenant_id=v_tenant;

  update public.site_business_context
     set reporting_prefs=jsonb_set(
       coalesce(reporting_prefs,'{}'::jsonb),
       '{restaurant_intelligence_context,monitoring_truth}',
       '{"fully_monitored_rule":"A completed service day is fully monitored only when the configured 4:00 PM-4:00 AM service window has no unrecovered/unverified monitoring time.","historical_authority":"For a completed service day, service-day monitoring coverage and the completed visual review/saved report outrank generic calendar-day coverage or an empty event index.","empty_event_index_rule":"An empty event index does not mean no retained evidence when reviewed snapshots or a saved report exist for that service day.","coverage_language":"Unverified time means WatchLog cannot confirm what happened in that period. Never describe it as no activity."}'::jsonb,
       true
     ),
     updated_at=now()
   where site_id=v_site and tenant_id=v_tenant;

  update public.sites
     set analytics_config_version=analytics_config_version+1
   where id=v_site;
end $$;

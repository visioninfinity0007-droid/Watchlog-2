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
    (v_kitchen,v_tenant,v_site,'kitchen','interval',60,true,
      '{"measure":["staff_count","active_station_pressure","kitchen_load","visible_output","congestion","cleaning_reset"],"safety_advisory_only":true}'),
    (v_front,v_tenant,v_site,'service_handoff','hybrid',60,true,
      '{"measure":["staff_count","handoff_load","ready_item_dwell","staff_waiting","service_congestion"],"not_customer_entrance":true}'),
    (v_cash,v_tenant,v_site,'cash_counter','hybrid',60,true,
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
     set reporting_prefs=jsonb_set(
       jsonb_set(
         reporting_prefs,
         '{ai_context_note}',
         to_jsonb('This is the Chai Wala Chota Bukhari late-night restaurant branch. Floor 1 and Floor 2 are outdoor dine-in seating areas with movable tables that can be combined for larger parties. Shop Front is the waiter/service handoff counter, not the main customer entrance. Cash Counter is a tight till/workstation view; customer queue length is not reliably visible. Camera 3 and Camera 8 are office/management views and should be treated as security/event cameras rather than continuous business-analytics cameras. Kitchen is a restricted back-of-house operational view. Back Entrance is a staff/service access point, not a customer-footfall camera. Report concurrent visible diners, estimated table sessions, observed seated-to-food-visible timing, table utilization and operational pressure with confidence/coverage. Do not infer sales, revenue, order accuracy, staff identity, food quality, unique customer counts, customer demographics, confirmed fire, or medical incidents from camera evidence alone.'::text),
         true
       ),
       '{restaurant_analytics}',
       '{"schema":"restaurant-vision-v1","customer_footfall_available":false,"footfall_reason":"No current camera provides a clean customer-entry counting line.","table_identity":"camera-specific anchor match with dynamic combining","service_time_definition":"seated-to-first-food-visible; not POS order-to-serve","capture_policy":{"dining_floor_seconds":60,"kitchen_seconds":60,"service_handoff_seconds":60,"cash_counter_seconds":60,"service_access":"event","office_security":"event"}}',
       true
     ),
     updated_at=now()
   where site_id=v_site and tenant_id=v_tenant;

  update public.sites
     set analytics_config_version=analytics_config_version+1
   where id=v_site;
end $$;

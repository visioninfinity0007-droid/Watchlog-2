-- =====================================================================
-- WatchLog — tenant-scoped data for the portal
-- =====================================================================
-- IMPORTANT CORRECTION.
--
-- wl_fleet(), wl_analytics(), wl_site_health(), wl_recent_events() and
-- wl_get_snapshot() were written for the single-tenant prototype viewer.
-- They are SECURITY DEFINER, granted to `anon`, and they return EVERY
-- tenant's rows. With one tenant that was harmless. With paying
-- customers it is a cross-tenant data leak.
--
-- This migration adds portal equivalents that filter by the signed-in
-- user's own tenant, and are granted to `authenticated` only.
--
-- The old anon-granted functions are LEFT IN PLACE deliberately: the
-- deployed prototype viewer at :18080 still uses them and would break.
-- They must be revoked before any second tenant exists - see
-- REVOKE block at the bottom, commented, ready to run.
-- =====================================================================

-- ---------------------------------------------------------------------
-- One round trip for the whole dashboard. Four separate calls meant four
-- HTTP requests on every 10-second refresh.
-- ---------------------------------------------------------------------
create or replace function public.wl_portal_overview(p_days int default 7)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days   int  := least(greatest(coalesce(p_days, 7), 1), 90);
  v_from   timestamptz := now() - make_interval(days => v_days);
begin
  if v_tenant is null then
    return jsonb_build_object('tenant', null);
  end if;

  return jsonb_build_object(
    'tenant', (select jsonb_build_object('id', t.id, 'name', t.name)
                 from tenants t where t.id = v_tenant),
    'window_days', v_days,

    'sites', (
      select coalesce(jsonb_agg(jsonb_build_object(
                'id', s.id, 'name', s.name, 'timezone', s.timezone) order by s.name),
              '[]'::jsonb)
        from sites s where s.tenant_id = v_tenant),

    'agents', (
      select coalesce(jsonb_agg(jsonb_build_object(
                'agent_id', a.id, 'site', s.name, 'hostname', a.hostname,
                'last_seen_at', a.last_seen_at, 'agent_version', a.agent_version,
                'device_vendor', a.device_vendor, 'device_model', a.device_model,
                'device_driver', a.device_driver,
                'event_count', (select count(*) from events e where e.agent_id = a.id),
                'last_event_at', (select max(e.device_ts) from events e where e.agent_id = a.id)
              ) order by a.last_seen_at desc nulls last), '[]'::jsonb)
        from agents a join sites s on s.id = a.site_id
       where a.tenant_id = v_tenant),

    'totals', (
      select jsonb_build_object(
        'events', count(*),
        'cameras', (select count(*) from cameras c where c.tenant_id = v_tenant),
        'sites',   (select count(*) from sites s where s.tenant_id = v_tenant))
        from events where tenant_id = v_tenant and device_ts >= v_from),

    'by_type', (
      select coalesce(jsonb_agg(jsonb_build_object('event_type', et, 'count', n)
                                order by n desc), '[]'::jsonb)
        from (select event_type et, count(*) n from events
               where tenant_id = v_tenant and device_ts >= v_from
               group by event_type) x),

    'by_camera', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'camera', cam, 'site', st, 'count', n) order by n desc), '[]'::jsonb)
        from (select coalesce(c.name, 'unassigned') cam, s.name st, count(*) n
                from events e
                left join cameras c on c.id = e.camera_id
                left join sites   s on s.id = e.site_id
               where e.tenant_id = v_tenant and e.device_ts >= v_from
               group by 1, 2) x),

    'by_hour', (
      select coalesce(jsonb_agg(jsonb_build_object('hour', h, 'count', n)
                                order by h), '[]'::jsonb)
        from (select extract(hour from e.device_ts at time zone s.timezone)::int h,
                     count(*) n
                from events e join sites s on s.id = e.site_id
               where e.tenant_id = v_tenant and e.device_ts >= v_from
               group by 1) y),

    'recent', (
      select coalesce(jsonb_agg(to_jsonb(r) order by r.received_at desc), '[]'::jsonb)
        from (select e.id as event_id, e.device_ts, e.received_at, e.event_type,
                     c.name as camera, s.name as site,
                     (sn.event_id is not null) as has_snapshot
                from events e
                left join cameras   c  on c.id = e.camera_id
                left join sites     s  on s.id = e.site_id
                left join snapshots sn on sn.event_id = e.id
               where e.tenant_id = v_tenant
               order by e.received_at desc limit 20) r),

    'health', jsonb_build_object(
      'offline_agents', (
        select coalesce(jsonb_agg(jsonb_build_object(
                 'hostname', a.hostname, 'site', s.name,
                 'last_seen_at', a.last_seen_at)), '[]'::jsonb)
          from agents a join sites s on s.id = a.site_id
         where a.tenant_id = v_tenant
           and (a.last_seen_at is null or a.last_seen_at < now() - interval '30 minutes')),
      -- Aggregate first, build the JSON second. jsonb_agg() wrapped
      -- around max() is a nested aggregate and Postgres rejects it.
      'silent_cameras', (
        select coalesce(jsonb_agg(jsonb_build_object(
                 'camera', q.camera, 'site', q.site,
                 'hours_silent', round(extract(epoch from now() - q.last_at) / 3600.0, 1))
               ), '[]'::jsonb)
          from (select c.name as camera, s.name as site,
                       coalesce(max(e.device_ts), c.created_at) as last_at
                  from cameras c
                  join sites s on s.id = c.site_id
                  left join events e on e.camera_id = c.id
                 where c.tenant_id = v_tenant
                 group by c.id, c.name, s.name, c.created_at
                having coalesce(max(e.device_ts), c.created_at)
                       < now() - interval '24 hours') q),
      'faults_24h', (
        select coalesce(jsonb_agg(jsonb_build_object(
                 'event_type', et, 'camera', cam, 'count', n) order by n desc), '[]'::jsonb)
          from (select e.event_type et, coalesce(c.name, 'unassigned') cam, count(*) n
                  from events e left join cameras c on c.id = e.camera_id
                 where e.tenant_id = v_tenant
                   and e.device_ts > now() - interval '24 hours'
                   and e.event_type in ('video_loss','tamper','disk_error','disk_full')
                 group by 1, 2) f)
    ),

    'open_codes', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'code', ec.code, 'site', s.name, 'expires_at', ec.expires_at)), '[]'::jsonb)
        from enrollment_codes ec join sites s on s.id = ec.site_id
       where ec.tenant_id = v_tenant and ec.used_at is null and ec.expires_at > now())
  );
end
$$;

-- ---------------------------------------------------------------------
-- Snapshot fetch, scoped. The prototype's wl_get_snapshot would happily
-- return any tenant's image to anyone holding the publishable key.
-- ---------------------------------------------------------------------
create or replace function public.wl_portal_snapshot(p_event_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
           'event_id', s.event_id,
           'content_type', s.content_type,
           'image_b64', encode(s.image, 'base64'))
    from snapshots s
   where s.event_id = p_event_id
     and s.tenant_id = wl_my_tenant()
$$;

revoke all on function public.wl_portal_overview(int)     from public, anon;
revoke all on function public.wl_portal_snapshot(bigint)  from public, anon;
grant execute on function public.wl_portal_overview(int)    to authenticated;
grant execute on function public.wl_portal_snapshot(bigint) to authenticated;

-- ---------------------------------------------------------------------
-- RUN THIS BEFORE THE SECOND TENANT EXISTS.
--
-- It closes the prototype's anon-readable, all-tenant surface. It will
-- break the standalone viewer at 161.97.175.15:18080, which is why it is
-- not run automatically - that viewer is currently the live demo.
--
--   revoke execute on function public.wl_fleet()            from anon;
--   revoke execute on function public.wl_recent_events(int) from anon;
--   revoke execute on function public.wl_analytics(int)     from anon;
--   revoke execute on function public.wl_site_health(int)   from anon;
--   revoke execute on function public.wl_daily_report(uuid,date) from anon;
--   revoke execute on function public.wl_get_snapshot(bigint)    from anon;
--   revoke execute on function public.wl_prune_snapshots(int,int) from anon;
-- ---------------------------------------------------------------------

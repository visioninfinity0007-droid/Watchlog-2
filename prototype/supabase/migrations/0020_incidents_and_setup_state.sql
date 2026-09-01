-- =====================================================================
-- 0020 — a first-class Incidents query, and a cloud-derived setup state.
--
-- wl_incidents(): filterable incident history for the portal's Incidents
-- page (by window, site, type), newest first, flagged for a snapshot.
--
-- wl_sites() gains `setup_state` + `online` + `events` so onboarding and
-- Settings can SHOW a site progress from "waiting for the site PC" through
-- enrollment -> recorder connected -> cameras discovered -> ready. The
-- state is derived entirely from what the agent already reports to the
-- cloud (enrollment, heartbeat, camera sync, events). No recorder
-- credential ever passes through the portal.
--
-- Read-only. Rollback: restore the 0019 wl_sites body; drop wl_incidents.
-- =====================================================================

create or replace function public.wl_incidents(
  p_days int default 7, p_site uuid default null, p_type text default null)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days   int  := least(greatest(coalesce(p_days, 7), 1), 90);
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(x) from (
      select jsonb_build_object(
               'event_id', e.id,
               'site', s.name,
               'site_id', e.site_id,
               'camera', c.name,
               'event_type', e.event_type,
               'device_ts', e.device_ts,
               'has_snapshot', exists (select 1 from snapshots sn where sn.event_id = e.id)
             ) as x
        from events e
        join sites s on s.id = e.site_id
        left join cameras c on c.id = e.camera_id
       where e.tenant_id = v_tenant
         and e.device_ts > now() - make_interval(days => v_days)
         and (p_site is null or e.site_id = p_site)
         and (p_type is null or e.event_type = p_type)
       order by e.device_ts desc
       limit 300) t), '[]'::jsonb);
end $$;

revoke all on function public.wl_incidents(int, uuid, text) from public, anon;
grant execute on function public.wl_incidents(int, uuid, text) to authenticated;


create or replace function public.wl_sites()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
             'id', s.id,
             'name', s.name,
             'timezone', s.timezone,
             'agents',  agg.agents,
             'online',  coalesce(agg.online, false),
             'cameras', agg.cameras,
             'events',  agg.events,
             'open_code', (select e.code from enrollment_codes e
                            where e.site_id = s.id and e.used_at is null
                              and e.expires_at > now()
                            order by e.created_at desc limit 1),
             'last_event', agg.last_event,
             'setup_state',
               case
                 when agg.agents = 0 then 'awaiting_agent'
                 when coalesce(agg.online,false) and agg.cameras > 0 and agg.events > 0 then 'ready'
                 when agg.cameras > 0 then 'cameras_discovered'
                 when agg.has_recorder then 'recorder_connected'
                 else 'enrolled'
               end)
             order by s.name)
      from sites s
      cross join lateral (
        select
          (select count(*) from agents a where a.site_id = s.id) as agents,
          (select bool_or(a.last_seen_at > now() - interval '3 min')
             from agents a where a.site_id = s.id) as online,
          (select bool_or(a.device_model is not null or a.device_vendor is not null)
             from agents a where a.site_id = s.id) as has_recorder,
          (select count(*) from cameras c where c.site_id = s.id) as cameras,
          (select count(*) from events e where e.site_id = s.id) as events,
          (select max(e.device_ts) from events e where e.site_id = s.id) as last_event
      ) agg
     where s.tenant_id = v_tenant), '[]'::jsonb);
end $$;

revoke all on function public.wl_sites() from public, anon;
grant execute on function public.wl_sites() to authenticated;

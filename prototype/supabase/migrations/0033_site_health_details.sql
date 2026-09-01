-- =====================================================================
-- 0033 - Detailed Site Health read model
--
-- The customer Site Health surface needs two details that the original
-- wl_portal_overview summary does not provide reliably:
--   1) every camera's last reported activity, not only the cameras already
--      classified as silent;
--   2) fault rows carrying their site identity, so per-site health counts do
--      not have to infer a site from an aggregate that omitted it.
--
-- Read-only, tenant-scoped, authenticated only. This is recorder/event health,
-- not a promise of live alarm monitoring. A quiet camera may simply have no
-- motion, so the API names the signal `last_activity_at` rather than pretending
-- it is a continuous camera heartbeat.
-- =====================================================================

create or replace function public.wl_site_health_details(p_days int default 7)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days int := least(greatest(coalesce(p_days, 7), 1), 90);
begin
  if v_tenant is null then
    return jsonb_build_object('cameras','[]'::jsonb,'faults','[]'::jsonb);
  end if;

  return jsonb_build_object(
    'cameras', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', c.id,
        'site_id', s.id,
        'site', s.name,
        'channel', c.channel,
        'camera', coalesce(nullif(c.name,''), 'Camera ' || c.channel),
        'last_activity_at', q.last_activity_at,
        'hours_since_activity', case
          when q.last_activity_at is null then null
          else round(extract(epoch from (now() - q.last_activity_at)) / 3600.0, 1)
        end,
        'activity_state', case
          when q.last_activity_at is null then 'never'
          when q.last_activity_at < now() - interval '24 hours' then 'silent'
          else 'active'
        end
      ) order by s.name, c.channel)
      from cameras c
      join sites s on s.id = c.site_id
      left join lateral (
        select max(e.device_ts) as last_activity_at
          from events e
         where e.camera_id = c.id
           and e.tenant_id = v_tenant
      ) q on true
      where c.tenant_id = v_tenant
    ), '[]'::jsonb),

    'faults', coalesce((
      select jsonb_agg(jsonb_build_object(
        'event_id', e.id,
        'site_id', s.id,
        'site', s.name,
        'camera_id', c.id,
        'camera', coalesce(nullif(c.name,''), 'Unassigned camera'),
        'event_type', e.event_type,
        'device_ts', e.device_ts
      ) order by e.device_ts desc)
      from events e
      join sites s on s.id = e.site_id
      left join cameras c on c.id = e.camera_id
      where e.tenant_id = v_tenant
        and e.device_ts >= now() - make_interval(days => v_days)
        and e.event_type in ('video_loss','tamper','disk_error','disk_full','offline')
    ), '[]'::jsonb)
  );
end
$$;

revoke all on function public.wl_site_health_details(int) from public, anon;
grant execute on function public.wl_site_health_details(int) to authenticated;

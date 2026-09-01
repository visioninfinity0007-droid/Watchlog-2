-- =====================================================================
-- 0034 - Enrollment-code read authorization
--
-- Enrollment codes are single-use credentials. 0032 already restricts code
-- creation to owner/admin, but wl_sites() still returned an unused live code to
-- every tenant member. A Viewer is read-only and does not need the secret.
--
-- Keep `has_open_code` visible to every member for honest setup state, while
-- returning the actual `open_code` value only to Owner/Admin.
-- =====================================================================

create or replace function public.wl_sites()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_can_manage boolean := coalesce(wl_my_role() in ('owner','admin'), false);
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
             'has_open_code', agg.open_code is not null,
             'open_code', case when v_can_manage then agg.open_code else null end,
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
          (select max(e.device_ts) from events e where e.site_id = s.id) as last_event,
          (select ec.code from enrollment_codes ec
            where ec.site_id = s.id
              and ec.tenant_id = v_tenant
              and ec.used_at is null
              and ec.expires_at > now()
            order by ec.created_at desc limit 1) as open_code
      ) agg
     where s.tenant_id = v_tenant), '[]'::jsonb);
end $$;

revoke all on function public.wl_sites() from public, anon;
grant execute on function public.wl_sites() to authenticated;

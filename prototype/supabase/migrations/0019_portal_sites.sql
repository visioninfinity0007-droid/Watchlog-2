-- =====================================================================
-- 0019 — wl_sites(): the tenant's sites for the portal.
--
-- The portal's Settings and Reports surfaces need the list of a tenant's
-- sites with their ids (to add a site, issue an enrollment code, or narrow
-- a report recipient to one site). Until now the only site data reached the
-- portal inside wl_portal_overview's agent rows, without site ids. This is
-- a small read, tenant-scoped like everything else, authenticated only.
--
-- Read-only. Rollback: drop function public.wl_sites().
-- =====================================================================

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
             'agents',  (select count(*) from agents a where a.site_id = s.id),
             'cameras', (select count(*) from cameras c where c.site_id = s.id),
             'open_code', (select e.code from enrollment_codes e
                            where e.site_id = s.id and e.used_at is null
                              and e.expires_at > now()
                            order by e.created_at desc limit 1),
             'last_event', (select max(ev.device_ts) from events ev where ev.site_id = s.id))
             order by s.name)
      from sites s
     where s.tenant_id = v_tenant), '[]'::jsonb);
end $$;

revoke all on function public.wl_sites() from public, anon;
grant execute on function public.wl_sites() to authenticated;

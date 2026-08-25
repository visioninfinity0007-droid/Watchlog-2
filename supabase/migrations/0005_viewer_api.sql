-- =====================================================================
-- WatchLog prototype — the viewer API
-- =====================================================================
-- Two read-only SECURITY DEFINER functions so viewer/index.html can show
-- fleet health with the PUBLISHABLE key.
--
-- Read access is granted through functions rather than anon RLS policies
-- so that no table is ever directly reachable: the surface is exactly
-- these two calls, with no arbitrary filtering and no path to
-- enrollment_codes or agent_key_hash.
--
-- PROTOTYPE ONLY. Anyone holding the publishable key — which is public by
-- design — can read this synthetic event data. The delivered Milestone 1
-- reads through an authenticated dashboard session scoped by tenant_id.
-- Do not apply this migration to a database holding real AKSS data.
--
-- Roll back with:
--   drop function if exists public.wl_recent_events(int);
--   drop function if exists public.wl_fleet();
-- =====================================================================

create or replace function public.wl_fleet()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_agg(to_jsonb(f) order by f.last_seen_at desc nulls last),
                  '[]'::jsonb)
    from v_agent_fleet f
$$;

create or replace function public.wl_recent_events(p_limit int default 25)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_agg(to_jsonb(e) order by e.received_at desc), '[]'::jsonb)
    from (
      select ev.device_ts, ev.agent_ts, ev.received_at, ev.event_type,
             ev.device_event_id, c.name as camera, s.name as site
        from events ev
        left join cameras c on c.id = ev.camera_id
        left join sites   s on s.id = ev.site_id
       order by ev.received_at desc
       limit least(greatest(coalesce(p_limit, 25), 1), 200)
    ) e
$$;

revoke all on function public.wl_fleet()             from public;
revoke all on function public.wl_recent_events(int)  from public;

grant execute on function public.wl_fleet()            to anon, authenticated;
grant execute on function public.wl_recent_events(int) to anon, authenticated;

-- 0159 - The 0005/0007 viewer RPCs are tenant-scoped (repo only until approved).
--
-- Security defect (found by the 5.1.1 capability audit, confirmed read-only on production
-- 2026-10-07): wl_get_snapshot(bigint) and wl_recent_events(int) are SECURITY DEFINER with
-- no tenant filter and EXECUTE granted to `authenticated` (0010 revoked only `anon`).
-- Any signed-in user of any tenant could read another tenant's CCTV still by event id
-- (ids are sequential) and list every tenant's latest events with camera and site names.
--
-- Change: both keep their signatures, grants and output shape, and now return only rows of
-- tenants the caller is an active member of (wl_is_member, the predicate every portal RPC
-- uses). A snapshot of another tenant returns null, exactly as a missing snapshot does, so
-- the id space reveals nothing. Nothing else changes.

create or replace function public.wl_get_snapshot(p_event_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  select jsonb_build_object(
           'event_id',     s.event_id,
           'content_type', s.content_type,
           'bytes',        s.bytes,
           'captured_at',  s.captured_at,
           'image_b64',    encode(s.image, 'base64'))
    from snapshots s
   where s.event_id = p_event_id
     and public.wl_is_member(s.tenant_id)
$function$;

create or replace function public.wl_recent_events(p_limit int default 25)
returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  select coalesce(jsonb_agg(to_jsonb(e) order by e.received_at desc), '[]'::jsonb)
    from (
      select ev.id as event_id, ev.device_ts, ev.agent_ts, ev.received_at,
             ev.event_type, ev.device_event_id,
             c.name as camera, s.name as site,
             (sn.event_id is not null) as has_snapshot
        from events ev
        left join cameras   c  on c.id = ev.camera_id
        left join sites     s  on s.id = ev.site_id
        left join snapshots sn on sn.event_id = ev.id
       where public.wl_is_member(ev.tenant_id)
       order by ev.received_at desc
       limit least(greatest(coalesce(p_limit, 25), 1), 200)
    ) e
$function$;

revoke all on function public.wl_get_snapshot(bigint) from public, anon;
grant execute on function public.wl_get_snapshot(bigint) to authenticated;
revoke all on function public.wl_recent_events(int) from public, anon;
grant execute on function public.wl_recent_events(int) to authenticated;

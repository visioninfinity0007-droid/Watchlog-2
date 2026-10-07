-- 0165 - Fault, restore and raw recorder signals are never people activity (repo only until approved).
--
-- 5.1.2 Agents report new native event types: video_restore, tamper_end, camera_disconnect,
-- camera_reconnect, alarm_input_end, recorder_restart, plus vendor codes WatchLog has no
-- meaning for, stored raw with payload.unmapped = true. wl_derive_activities (0147) turns every
-- event outside four fault types into an activity, and wl_derive_episodes (0147) turns every
-- activity outside ('video_loss','tamper') into a 'presence' episode, which feeds site day
-- state, journeys, daily intelligence and incident promotion: a 03:00 recorder restart would
-- read as after-hours presence. Dahua recorder-scoped alarm inputs already leaked this way.
--
-- Change (both functions restated from 0147; nothing else differs):
--   * wl_event_not_activity(type, camera, payload): restores/ends/reconnects/restarts, any
--     recorder-scoped event and any unmapped raw code are not activity;
--   * camera_disconnect is a camera fault like video_loss, and a fault episode.
-- Existing event types keep their current meaning.

create or replace function public.wl_event_not_activity(p_type text, p_camera uuid, p_payload jsonb)
returns boolean
language sql
immutable
set search_path = public
as $function$
  select coalesce(p_type, '') in ('video_restore', 'tamper_end', 'camera_reconnect',
                                  'alarm_input_end', 'recorder_restart')
      or coalesce(p_payload->>'unmapped', 'false') = 'true'
      or (coalesce(p_payload->>'recorder_scoped', 'false') = 'true'
          and coalesce(p_type, '') not in ('disk_error', 'disk_full'))
$function$;

revoke all on function public.wl_event_not_activity(text, uuid, jsonb) from public, anon, authenticated;

create or replace function public.wl_derive_activities(p_site_id uuid, p_from timestamp with time zone, p_to timestamp with time zone)
returns integer
language plpgsql
security definer
set search_path to 'public'
as $function$
declare v_rows integer;
begin
  insert into activities (tenant_id, site_id, camera_id, activity_type, object_class, semantic,
                          occurred_at, source_event_id, metadata_json)
  select s.tenant_id, e.site_id, e.camera_id,
         case when e.event_type in ('video_loss','tamper','disk_error','disk_full',
                                    'camera_disconnect') then 'camera_fault'
              else e.event_type end,
         e.event_type,
         coalesce(nullif(c.purpose,''), 'unspecified') || ':' || e.event_type,
         e.device_ts, e.id,
         jsonb_build_object('camera_name', c.name, 'camera_purpose', c.purpose)
    from events e
    join sites s on s.id = e.site_id
    left join cameras c on c.id = e.camera_id
   where e.site_id = p_site_id and e.device_ts >= p_from and e.device_ts < p_to
     and not public.wl_event_is_recorder_scoped_disk(e.event_type, e.camera_id, e.payload)
     and not public.wl_event_not_activity(e.event_type, e.camera_id, e.payload)
  on conflict (source_event_id, activity_type) do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end $function$;

create or replace function public.wl_derive_episodes(p_site_id uuid, p_from timestamp with time zone, p_to timestamp with time zone, p_gap_seconds integer default 600)
returns integer
language plpgsql
security definer
set search_path to 'public'
as $function$
declare v_rows integer;
begin
  delete from episodes e
   where e.site_id = p_site_id and e.started_at >= p_from and e.started_at < p_to;
  with a as (
    select ac.id, ac.tenant_id, ac.site_id, ac.camera_id, ac.object_class, ac.occurred_at,
           case when ac.object_class in ('video_loss','tamper','camera_disconnect') then 'video_loss'
                else 'presence' end as etype
      from activities ac
      left join events ev on ev.id = ac.source_event_id
     where ac.site_id = p_site_id and ac.occurred_at >= p_from and ac.occurred_at < p_to
       and not public.wl_event_is_recorder_scoped_disk(ac.object_class, ac.camera_id, ev.payload)
       and not public.wl_event_not_activity(ac.object_class, ac.camera_id, ev.payload)
  ),
  marked as (
    select *,
           case when extract(epoch from (occurred_at - lag(occurred_at)
                       over (partition by camera_id, etype order by occurred_at))) > p_gap_seconds
                  or lag(occurred_at) over (partition by camera_id, etype order by occurred_at) is null
                then 1 else 0 end as newgrp
      from a
  ),
  grouped as (
    select *, sum(newgrp) over (partition by camera_id, etype order by occurred_at
                                rows unbounded preceding) as grp
      from marked
  )
  insert into episodes (tenant_id, site_id, camera_id, episode_type, object_class,
                        started_at, ended_at, detection_count, dwell_seconds,
                        confidence, source_activity_ids)
  select tenant_id, site_id, camera_id, etype, max(object_class),
         min(occurred_at), max(occurred_at), count(*),
         extract(epoch from (max(occurred_at) - min(occurred_at))),
         least(1.0, 0.5 + count(*)::numeric/20), array_agg(id order by occurred_at)
    from grouped
   group by tenant_id, site_id, camera_id, etype, grp;
  get diagnostics v_rows = row_count;
  return v_rows;
end $function$;

revoke all on function public.wl_derive_activities(uuid,timestamptz,timestamptz) from public, anon;
grant execute on function public.wl_derive_activities(uuid,timestamptz,timestamptz) to service_role;
revoke all on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) to service_role;

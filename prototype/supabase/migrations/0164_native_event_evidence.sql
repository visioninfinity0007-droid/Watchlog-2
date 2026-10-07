-- 0164 - Native-event evidence package: timing (-15 s / event / +30 s), attribution, leases.
--
-- Principle: a clip from the wrong camera or the wrong time is worse than a clean failure.
-- Every evidence artifact carries verifiable camera, recorder and time provenance.
--
-- 1. Native-event clips (site switch, default OFF). When a qualifying recorder event
--    (person, vehicle, line_crossing, intrusion, tamper, alarm_input by default; per-site list)
--    is inserted for a camera, an AFTER INSERT trigger on events creates one incident clip
--    request T-15 s .. T+30 s, source 'native_event', bound to the event, its camera AND its
--    recorder. Only when sites.native_event_clips_enabled is true (default false), so applying
--    this migration changes nothing until a site opts in (wl_set_native_event_clips).
--    - The event's recorder must be the camera's recorder; otherwise no clip (never guessed).
--    - Recovered/archive events and events delivered more than 15 minutes late get no
--      automatic clip (an owner can still request one).
--    - Per camera at most one automatic clip per 2 minutes: a later event whose window still
--      fits one pending <= 60 s request is merged into it; any other is skipped.
--    wl_ingest_events is NOT redefined.
-- 2. Claim guard: wl_agent_claim_clip_requests (0150 body) hands out a request only once its
--    post-roll has been recorded (end_at <= now() - 10 s). Two attribution additions:
--    a request stamped with a recorder is handed out only while its camera is still on that
--    recorder (otherwise it fails cleanly), and an event clip carries the event's
--    payload clock_source (JSON null when the event has none), the hand-off the Agent's
--    _get_clip has waited for since 5.0.28 (test_incident_clip_clock.py).
-- 3. Lease: the 0142 stale-clip finalizer's lease becomes 420 s (default and floor), above
--    the Hikvision worst case (120 s lock wait + 90 s export + 2 x 60 s remux = 330 s) plus
--    upload margin. Its pg_cron job is rescheduled with 420. The Agent releases its own
--    in-flight evidence at startup (wl_agent_release_inflight_evidence, 0150, unchanged).
-- 4. Provenance:
--    - incident_clip_requests.recorder_id: the recorder the clip is taken from, stamped at
--      insert (from the event for native-event clips, from the camera otherwise).
--    - snapshots.capture_source + real captured_at: an Agent that reports the real capture
--      time of an event still in the event payload (snapshot_captured_at) gets it stored as
--      captured_at, with capture_source 'live_after_event'; events.device_ts stays the event
--      time. Older Agents keep captured_at = device_ts, labelled 'event_time'. Archive frames
--      and periodic stills are labelled 'recorder_archive' / 'periodic'. The payload carries
--      the time because wl_ingest_events copies payload verbatim; its body is not redefined.
--    - wl_agent_complete_clip (0040 body) recomputes sha256 over the stored chunks in
--      sequence order and fails the clip (chunks deleted) when it differs from the Agent's.

-- ---------------------------------------------------------------------
-- A. Schema
-- ---------------------------------------------------------------------
alter table public.sites
  add column if not exists native_event_clips_enabled boolean not null default false;
alter table public.sites
  add column if not exists native_event_clip_types text[] not null
    default array['person','vehicle','line_crossing','intrusion','tamper','alarm_input']::text[];

alter table public.incident_clip_requests
  add column if not exists recorder_id uuid
    references public.recorders(id) on delete set null;

-- 'native_event' joins the 0058 sources. The 0058 check was declared inline, so its name is
-- generated; every check on incident_clip_requests that names `source` is replaced.
do $$
declare v_name text;
begin
  for v_name in
    select c.conname
      from pg_constraint c
     where c.conrelid = 'public.incident_clip_requests'::regclass
       and c.contype = 'c'
       and pg_get_constraintdef(c.oid) ~* '\msource\M'
  loop
    execute format('alter table public.incident_clip_requests drop constraint %I', v_name);
  end loop;
end $$;
alter table public.incident_clip_requests
  add constraint incident_clip_requests_source_check
  check (source in ('user','rule','native_event'));

create index if not exists incident_clip_native_camera_idx
  on public.incident_clip_requests(camera_id, start_at desc)
  where source = 'native_event';

alter table public.snapshots
  add column if not exists capture_source text not null default 'event_time';
alter table public.snapshots
  drop constraint if exists snapshots_capture_source_check;
-- NOT VALID: existing rows all hold the default; new rows are checked. No table scan.
alter table public.snapshots
  add constraint snapshots_capture_source_check
  check (capture_source in ('event_time','live_after_event','recorder_archive','periodic'))
  not valid;

-- ---------------------------------------------------------------------
-- B. Clip request recorder stamp (provenance for every source).
-- ---------------------------------------------------------------------
create or replace function public.wl_incident_clip_stamp_recorder()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  if new.recorder_id is null and new.camera_id is not null then
    select c.recorder_id into new.recorder_id
      from public.cameras c
     where c.id=new.camera_id
       and c.tenant_id=new.tenant_id
       and c.site_id=new.site_id;
  end if;
  return new;
end
$function$;

revoke all on function public.wl_incident_clip_stamp_recorder()
  from public,anon,authenticated;

drop trigger if exists trg_incident_clip_stamp_recorder on public.incident_clip_requests;
create trigger trg_incident_clip_stamp_recorder
before insert on public.incident_clip_requests
for each row execute function public.wl_incident_clip_stamp_recorder();

-- ---------------------------------------------------------------------
-- C. Native-event clip creation.
-- ---------------------------------------------------------------------
create or replace function public.wl_native_event_clip_request()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_enabled boolean;
  v_types text[];
  v_camera_recorder uuid;
  v_start timestamptz := new.device_ts - interval '15 seconds';
  v_end timestamptz := new.device_ts + interval '30 seconds';
  v_near public.incident_clip_requests;
begin
  select s.native_event_clips_enabled,s.native_event_clip_types
    into v_enabled,v_types
    from public.sites s
   where s.id=new.site_id
     and s.tenant_id=new.tenant_id;
  if not coalesce(v_enabled,false)
     or not (new.event_type = any(coalesce(v_types,'{}'::text[]))) then
    return null;
  end if;

  -- A recovered archive event is history, not a live incident.
  if coalesce(new.payload->>'source','')='recorder_archive'
     or coalesce(new.payload->>'recovered','')='true' then
    return null;
  end if;
  -- Only a (near) live event: a long-spooled backlog must not flood the recorder.
  if new.device_ts < now() - interval '15 minutes'
     or new.device_ts > now() + interval '5 minutes' then
    return null;
  end if;

  -- Attribution: the camera must be on the recorder that raised the event.
  select c.recorder_id into v_camera_recorder
    from public.cameras c
   where c.id=new.camera_id
     and c.tenant_id=new.tenant_id
     and c.site_id=new.site_id;
  if new.recorder_id is null
     or v_camera_recorder is null
     or v_camera_recorder<>new.recorder_id then
    return null;
  end if;

  -- Never let evidence bookkeeping fail the event ingest itself.
  begin
    perform pg_advisory_xact_lock(
      hashtextextended('wl_native_event_clip:'||new.camera_id::text,0)
    );

    select r.* into v_near
      from public.incident_clip_requests r
     where r.camera_id=new.camera_id
       and r.tenant_id=new.tenant_id
       and r.source='native_event'
       and r.start_at > v_start - interval '120 seconds'
       and r.start_at < v_start + interval '120 seconds'
     order by (r.status='pending') desc,
              abs(extract(epoch from (r.start_at - v_start))),
              r.requested_at
     limit 1
     for update;

    if v_near.id is not null then
      -- Rate limit: one automatic clip per camera per 2 minutes. A still-pending request
      -- whose window can cover this event too (<= 60 s in all) is widened; otherwise the
      -- event is skipped.
      if v_near.status='pending'
         and v_near.recorder_id is not distinct from new.recorder_id
         and greatest(v_near.end_at,v_end) - least(v_near.start_at,v_start)
             <= interval '60 seconds' then
        update public.incident_clip_requests
           set start_at=least(v_near.start_at,v_start),
               end_at=greatest(v_near.end_at,v_end)
         where id=v_near.id;
      end if;
      return null;
    end if;

    insert into public.incident_clip_requests(
      tenant_id,event_id,site_id,camera_id,recorder_id,source,start_at,end_at
    ) values (
      new.tenant_id,new.id,new.site_id,new.camera_id,new.recorder_id,'native_event',
      v_start,v_end
    )
    on conflict do nothing;
  exception when others then
    raise warning 'native event clip request skipped for event %: %', new.id, sqlerrm;
  end;
  return null;
end
$function$;

revoke all on function public.wl_native_event_clip_request()
  from public,anon,authenticated;

drop trigger if exists trg_native_event_clip_request on public.events;
create trigger trg_native_event_clip_request
after insert on public.events
for each row
when (new.camera_id is not null)
execute function public.wl_native_event_clip_request();

-- Owner/Admin switch. Default OFF; the type list may be narrowed or restored.
create or replace function public.wl_set_native_event_clips(
  p_site_id uuid,
  p_enabled boolean,
  p_event_types text[] default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_tenant uuid := public.wl_require_role(array['owner','admin']);
  v_allowed text[] := array['person','vehicle','line_crossing','intrusion','tamper','alarm_input']::text[];
  v_types text[];
begin
  if not exists(
    select 1 from public.sites where id=p_site_id and tenant_id=v_tenant
  ) then
    raise exception 'not your site' using errcode='42501';
  end if;
  if p_event_types is not null then
    select coalesce(array_agg(distinct t order by t),'{}'::text[]) into v_types
      from unnest(p_event_types) t;
    if cardinality(v_types)=0 or not (v_types <@ v_allowed) then
      raise exception 'event types must be chosen from %', array_to_string(v_allowed,', ')
        using errcode='22023';
    end if;
  end if;

  update public.sites
     set native_event_clips_enabled=coalesce(p_enabled,false),
         native_event_clip_types=coalesce(v_types,native_event_clip_types)
   where id=p_site_id
  returning native_event_clip_types into v_types;

  return jsonb_build_object(
    'site_id',p_site_id,
    'native_event_clips_enabled',coalesce(p_enabled,false),
    'native_event_clip_types',to_jsonb(v_types)
  );
end
$function$;

revoke all on function public.wl_set_native_event_clips(uuid,boolean,text[])
  from public,anon;
grant execute on function public.wl_set_native_event_clips(uuid,boolean,text[])
  to authenticated;

-- ---------------------------------------------------------------------
-- D. Snapshot capture provenance (wl_ingest_events body unchanged).
-- ---------------------------------------------------------------------
create or replace function public.wl_snapshot_capture_provenance()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_payload jsonb;
  v_at timestamptz;
begin
  select e.payload into v_payload
    from public.events e
   where e.id=new.event_id;
  if v_payload is null then
    return new;
  end if;

  if coalesce(v_payload->>'source','')='periodic_snapshot' then
    new.capture_source := 'periodic';
    return new;
  end if;
  if coalesce(v_payload->>'source','')='recorder_archive'
     or coalesce(v_payload->>'recovered','')='true' then
    new.capture_source := 'recorder_archive';
    return new;
  end if;

  if nullif(v_payload->>'snapshot_captured_at','') is not null then
    begin
      v_at := (v_payload->>'snapshot_captured_at')::timestamptz;
    exception when others then
      v_at := null;
    end;
    if v_at is not null
       and v_at <= now() + interval '5 minutes'
       and v_at >= now() - interval '7 days' then
      new.captured_at := v_at;
      new.capture_source := 'live_after_event';
      return new;
    end if;
  end if;

  new.capture_source := 'event_time';
  return new;
end
$function$;

revoke all on function public.wl_snapshot_capture_provenance()
  from public,anon,authenticated;

drop trigger if exists trg_snapshot_capture_provenance on public.snapshots;
create trigger trg_snapshot_capture_provenance
before insert on public.snapshots
for each row execute function public.wl_snapshot_capture_provenance();

-- ---------------------------------------------------------------------
-- E. Claim guard: 0150 body + post-roll recorded + recorder attribution + clock_source.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_clip_requests(
  p_agent_id uuid,
  p_agent_key text,
  p_limit integer default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),2);
  v_result jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  delete from public.incident_clip_chunks c
   using public.incident_clip_requests r
   where c.request_id=r.id
     and r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.expires_at<=now();

  update public.incident_clip_requests
     set status='expired'
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and expires_at<=now()
     and status in ('pending','processing','ready');

  -- A request that can never reach a recorder input (camera gone, or a blank
  -- channel) fails now. It is neither left pending for 24 h, blocking a new
  -- request, nor handed to the Agent without a channel (MNVR-033).
  with unroutable as (
    select r.id
      from public.incident_clip_requests r
     where r.site_id=v_agent.site_id
       and r.tenant_id=v_agent.tenant_id
       and r.status='pending'
       and r.expires_at>now()
       and not exists (
         select 1
           from public.cameras c
          where c.id=r.camera_id
            and c.tenant_id=v_agent.tenant_id
            and c.site_id=v_agent.site_id
            and c.recorder_id is not null
            and nullif(btrim(c.channel),'') is not null
       )
     for update of r skip locked
  )
  update public.incident_clip_requests r
     set status='failed',
         error_message='WatchLog could not confirm which recorder input this camera uses, so recorded footage was not retrieved.',
         completed_at=now()
    from unroutable u
   where r.id=u.id;

  -- 0164: a request stamped with a recorder is only ever exported from that recorder.
  -- If its camera now belongs to another recorder the request fails cleanly instead of
  -- returning footage of another recorder's input.
  with moved as (
    select r.id
      from public.incident_clip_requests r
      join public.cameras c
        on c.id=r.camera_id
       and c.tenant_id=v_agent.tenant_id
       and c.site_id=v_agent.site_id
     where r.site_id=v_agent.site_id
       and r.tenant_id=v_agent.tenant_id
       and r.status='pending'
       and r.expires_at>now()
       and r.recorder_id is not null
       and c.recorder_id is distinct from r.recorder_id
     for update of r skip locked
  )
  update public.incident_clip_requests r
     set status='failed',
         error_message='This camera is no longer connected through the recorder that recorded the incident, so recorded footage was not retrieved.',
         completed_at=now()
    from moved m
   where r.id=m.id;

  -- started_at is the processing lease the 0142 finalizer
  -- (wl_finalize_stale_incident_clips) measures; every claim sets it.
  -- 0164: only once the post-roll exists on the recorder (end_at at least 10 s ago).
  with picked as (
    select r.id
      from public.incident_clip_requests r
      join public.cameras c
        on c.id=r.camera_id
       and c.tenant_id=v_agent.tenant_id
       and c.site_id=v_agent.site_id
       and c.recorder_id is not null
       and nullif(btrim(c.channel),'') is not null
     where r.site_id=v_agent.site_id
       and r.tenant_id=v_agent.tenant_id
       and r.status='pending'
       and r.expires_at>now()
       and r.end_at <= now() - interval '10 seconds'
       and (r.recorder_id is null or r.recorder_id=c.recorder_id)
     order by r.requested_at
     for update of r skip locked
     limit v_limit
  ),
  claimed as (
    update public.incident_clip_requests r
       set status='processing',
           claimed_by_agent_id=v_agent.id,
           started_at=now(),
           error_message=null
      from picked p
     where r.id=p.id
    returning r.*
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'request_id',r.id,
        'event_id',r.event_id,
        'camera_id',r.camera_id,
        'recorder_id',c.recorder_id,
        'channel',c.channel,
        'start_at',r.start_at,
        'end_at',r.end_at
      )
      || case
           when r.event_id is not null
           then jsonb_build_object('clock_source',e.payload->'clock_source')
           else '{}'::jsonb
         end
      order by r.requested_at
    ),
    '[]'::jsonb
  )
  into v_result
  from claimed r
  join public.cameras c
    on c.id=r.camera_id
   and c.tenant_id=v_agent.tenant_id
   and c.site_id=v_agent.site_id
   and c.recorder_id is not null
  left join public.events e
    on e.id=r.event_id
   and e.tenant_id=v_agent.tenant_id;

  return v_result;
end
$function$;

revoke all on function public.wl_agent_claim_clip_requests(
  uuid,text,integer
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_clip_requests(
  uuid,text,integer
) to anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- F. Stale-clip lease: 0142 body with the interval raised to 420 s.
-- ---------------------------------------------------------------------
create or replace function public.wl_finalize_stale_incident_clips(
  p_stale_seconds integer default 420
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_stale_seconds integer := least(greatest(coalesce(p_stale_seconds, 420), 420), 3600);
  v_count integer := 0;
begin
  with stale as (
    select r.id
      from public.incident_clip_requests r
     where r.status = 'processing'
       and r.started_at is not null
       and r.started_at < now() - make_interval(secs => v_stale_seconds)
     for update skip locked
  ), removed_chunks as (
    delete from public.incident_clip_chunks c
     using stale s
     where c.request_id = s.id
    returning c.request_id
  ), finalized as (
    update public.incident_clip_requests r
       set status = 'failed',
           error_message = 'Footage retrieval did not complete. Please retry.',
           completed_at = now(),
           claimed_by_agent_id = null
      from stale s
     where r.id = s.id
    returning r.id
  )
  select count(*) into v_count from finalized;

  return v_count;
end
$$;

comment on function public.wl_finalize_stale_incident_clips(integer) is
  'WatchLog 0164: fail abandoned processing clip requests after a 420 s lease (driver worst case 330 s + upload margin) and delete partial bytes';

revoke all on function public.wl_finalize_stale_incident_clips(integer)
  from public, anon, authenticated;
grant execute on function public.wl_finalize_stale_incident_clips(integer)
  to service_role;

do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid)
      from cron.job
     where jobname = 'watchlog-finalize-stale-incident-clips';
    perform cron.schedule(
      'watchlog-finalize-stale-incident-clips',
      '*/2 * * * *',
      'select public.wl_finalize_stale_incident_clips(420)'
    );
  end if;
exception when others then
  raise notice 'stale incident clip scheduling skipped: %', sqlerrm;
end $$;

-- ---------------------------------------------------------------------
-- G. Completion: 0040 body + server-side sha256 over the stored chunks.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_complete_clip(
  p_agent_id uuid,p_agent_key text,p_request_id uuid,
  p_content_type text,p_file_extension text,p_sha256 text,p_total_bytes int
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_request public.incident_clip_requests;
  v_stored bigint;
  v_chunks int;
  v_server_sha text;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select * into v_request from public.incident_clip_requests
   where id=p_request_id and tenant_id=v_agent.tenant_id and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id and status='processing';
  if v_request.id is null then raise exception 'clip request not claimed by this agent' using errcode='42501'; end if;
  if p_content_type not in ('video/x-dav','video/mp4','application/octet-stream') then raise exception 'unsupported clip content type'; end if;
  if p_file_extension not in ('dav','mp4') then raise exception 'unsupported clip file extension'; end if;
  if p_sha256 !~ '^[0-9a-f]{64}$' then raise exception 'invalid clip checksum'; end if;
  if p_total_bytes < 1 or p_total_bytes > 33554432 then raise exception 'invalid clip size'; end if;
  select coalesce(sum(bytes),0),count(*) into v_stored,v_chunks
    from public.incident_clip_chunks where request_id=p_request_id;
  if v_chunks < 1 or v_stored <> p_total_bytes then raise exception 'clip upload is incomplete'; end if;

  -- 0164: the server verifies the bytes it will serve. A mismatch is a failed clip, not
  -- an error: the request leaves 'processing' and its bytes are removed.
  select encode(sha256(string_agg(data,''::bytea order by sequence_no)),'hex')
    into v_server_sha
    from public.incident_clip_chunks where request_id=p_request_id;
  if v_server_sha is distinct from p_sha256 then
    delete from public.incident_clip_chunks where request_id=p_request_id;
    update public.incident_clip_requests
       set status='failed',
           error_message='The recorded footage could not be verified. Please request it again.',
           completed_at=now()
     where id=p_request_id;
    return jsonb_build_object('ok',false,'status','failed','reason','checksum_mismatch');
  end if;

  update public.incident_clip_requests
     set status='ready',content_type=p_content_type,file_extension=p_file_extension,
         bytes=p_total_bytes,sha256=p_sha256,error_message=null,
         completed_at=now(),expires_at=now()+interval '24 hours'
   where id=p_request_id;
  return jsonb_build_object('ok',true,'status','ready','bytes',p_total_bytes,'chunks',v_chunks);
end
$$;

revoke all on function public.wl_agent_complete_clip(uuid,text,uuid,text,text,text,int) from public;
grant execute on function public.wl_agent_complete_clip(uuid,text,uuid,text,text,text,int) to anon,authenticated;

-- =====================================================================
-- 0040 - On-demand incident footage retrieval
--
-- WatchLog does NOT continuously upload recorder video. A signed-in tenant
-- Owner/Admin may explicitly request a short evidence window around one
-- incident. The site agent retrieves that window from the local recorder and
-- uploads it in bounded chunks. Clips expire quickly and are independent of
-- the permanent incident/event record.
--
-- This is a transport/request layer, not a claim that every recorder supports
-- playback export. Unsupported devices fail truthfully with status
-- 'unsupported'. No recorder URL or credential is stored in these tables.
-- =====================================================================

create table if not exists public.incident_clip_requests (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  event_id             bigint not null references public.events(id) on delete cascade,
  site_id              uuid not null references public.sites(id) on delete cascade,
  camera_id            uuid references public.cameras(id) on delete set null,
  requested_by         uuid not null references auth.users(id) on delete restrict,
  claimed_by_agent_id  uuid references public.agents(id) on delete set null,
  start_at             timestamptz not null,
  end_at               timestamptz not null,
  status               text not null default 'pending'
                       check (status in ('pending','processing','ready','unsupported','failed','expired')),
  content_type         text,
  file_extension       text,
  bytes                integer,
  sha256               text,
  error_message        text,
  requested_at         timestamptz not null default now(),
  started_at           timestamptz,
  completed_at         timestamptz,
  expires_at           timestamptz not null default now() + interval '24 hours',
  constraint incident_clip_window_check check (end_at > start_at and end_at - start_at <= interval '60 seconds'),
  constraint incident_clip_bytes_check check (bytes is null or (bytes > 0 and bytes <= 33554432)),
  constraint incident_clip_sha_check check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  constraint incident_clip_extension_check check (file_extension is null or file_extension in ('dav','mp4')),
  constraint incident_clip_content_type_check check (
    content_type is null or content_type in ('video/x-dav','video/mp4','application/octet-stream')
  )
);

create index if not exists incident_clip_tenant_event_idx
  on public.incident_clip_requests(tenant_id, event_id, requested_at desc);
create index if not exists incident_clip_pending_site_idx
  on public.incident_clip_requests(site_id, requested_at)
  where status = 'pending';
create unique index if not exists incident_clip_one_active_per_event_idx
  on public.incident_clip_requests(event_id)
  where status in ('pending','processing');

create table if not exists public.incident_clip_chunks (
  request_id uuid not null references public.incident_clip_requests(id) on delete cascade,
  sequence_no integer not null check (sequence_no between 0 and 127),
  data bytea not null,
  bytes integer not null check (bytes > 0 and bytes <= 786432),
  primary key (request_id, sequence_no)
);

alter table public.incident_clip_requests enable row level security;
alter table public.incident_clip_chunks enable row level security;
revoke all on table public.incident_clip_requests from public, anon, authenticated;
revoke all on table public.incident_clip_chunks from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- Customer request. Retrieving footage consumes recorder/uplink resources,
-- therefore it is an Owner/Admin action rather than a passive Viewer read.
-- ---------------------------------------------------------------------
create or replace function public.wl_request_incident_clip(
  p_event_id bigint,
  p_pre_seconds int default 10,
  p_post_seconds int default 20
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_event public.events;
  v_id uuid;
  v_pre int := least(greatest(coalesce(p_pre_seconds,10),0),30);
  v_post int := least(greatest(coalesce(p_post_seconds,20),1),30);
  v_existing public.incident_clip_requests;
begin
  select * into v_event
    from public.events
   where id=p_event_id and tenant_id=v_tenant;
  if v_event.id is null then
    raise exception 'incident not found in your account' using errcode='42501';
  end if;
  if v_event.camera_id is null then
    raise exception 'incident has no camera to retrieve footage from';
  end if;
  if v_pre + v_post > 60 then
    raise exception 'incident footage window cannot exceed 60 seconds';
  end if;

  select * into v_existing
    from public.incident_clip_requests
   where event_id=p_event_id
     and status in ('pending','processing','ready')
     and expires_at > now()
   order by requested_at desc limit 1;
  if v_existing.id is not null then
    return jsonb_build_object(
      'request_id',v_existing.id,'status',v_existing.status,
      'start_at',v_existing.start_at,'end_at',v_existing.end_at,
      'existing',true
    );
  end if;

  insert into public.incident_clip_requests
    (tenant_id,event_id,site_id,camera_id,requested_by,start_at,end_at)
  values
    (v_tenant,v_event.id,v_event.site_id,v_event.camera_id,auth.uid(),
     v_event.device_ts - make_interval(secs=>v_pre),
     v_event.device_ts + make_interval(secs=>v_post))
  returning id into v_id;

  return jsonb_build_object(
    'request_id',v_id,'status','pending',
    'start_at',v_event.device_ts - make_interval(secs=>v_pre),
    'end_at',v_event.device_ts + make_interval(secs=>v_post),
    'existing',false
  );
end
$$;

revoke all on function public.wl_request_incident_clip(bigint,int,int) from public,anon;
grant execute on function public.wl_request_incident_clip(bigint,int,int) to authenticated;

-- ---------------------------------------------------------------------
-- Customer status/manifest. No media bytes are returned here.
-- ---------------------------------------------------------------------
create or replace function public.wl_incident_clip_status(p_event_id bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_row public.incident_clip_requests;
begin
  if v_tenant is null then return null; end if;
  select * into v_row
    from public.incident_clip_requests
   where tenant_id=v_tenant and event_id=p_event_id
   order by requested_at desc limit 1;
  if v_row.id is null then return null; end if;
  return jsonb_build_object(
    'request_id',v_row.id,
    'status',case when v_row.expires_at <= now() and v_row.status='ready' then 'expired' else v_row.status end,
    'start_at',v_row.start_at,'end_at',v_row.end_at,
    'content_type',v_row.content_type,'file_extension',v_row.file_extension,
    'bytes',v_row.bytes,'sha256',v_row.sha256,
    'error_message',v_row.error_message,
    'requested_at',v_row.requested_at,'completed_at',v_row.completed_at,
    'expires_at',v_row.expires_at,
    'chunks',(select count(*) from public.incident_clip_chunks c where c.request_id=v_row.id)
  );
end
$$;

revoke all on function public.wl_incident_clip_status(bigint) from public,anon;
grant execute on function public.wl_incident_clip_status(bigint) to authenticated;

create or replace function public.wl_incident_clip_chunk(p_request_id uuid,p_sequence_no int)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_request public.incident_clip_requests;
  v_chunk public.incident_clip_chunks;
begin
  if v_tenant is null then return null; end if;
  select * into v_request
    from public.incident_clip_requests
   where id=p_request_id and tenant_id=v_tenant and status='ready' and expires_at>now();
  if v_request.id is null then return null; end if;
  select * into v_chunk
    from public.incident_clip_chunks
   where request_id=p_request_id and sequence_no=p_sequence_no;
  if v_chunk.request_id is null then return null; end if;
  return jsonb_build_object(
    'request_id',p_request_id,'sequence_no',v_chunk.sequence_no,
    'bytes',v_chunk.bytes,'data_b64',encode(v_chunk.data,'base64')
  );
end
$$;

revoke all on function public.wl_incident_clip_chunk(uuid,int) from public,anon;
grant execute on function public.wl_incident_clip_chunk(uuid,int) to authenticated;

-- ---------------------------------------------------------------------
-- Agent claim/upload API. The agent can claim requests only for its site.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_clip_requests(
  p_agent_id uuid,p_agent_key text,p_limit int default 1
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit,1),1),2);
  v_result jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  -- A data-modifying CTE (claimed) must be at the TOP LEVEL of the statement;
  -- Postgres rejects a modifying WITH nested inside coalesce((WITH ... SELECT ...)).
  -- Run it as a top-level SELECT ... INTO and coalesce the aggregate instead.
  with picked as (
    select r.id
      from public.incident_clip_requests r
     where r.site_id=v_agent.site_id
       and r.tenant_id=v_agent.tenant_id
       and r.status='pending'
       and r.expires_at>now()
     order by r.requested_at
     for update skip locked
     limit v_limit
  ), claimed as (
    update public.incident_clip_requests r
       set status='processing',claimed_by_agent_id=v_agent.id,started_at=now(),error_message=null
      from picked p
     where r.id=p.id
    returning r.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'request_id',r.id,'event_id',r.event_id,'camera_id',r.camera_id,
    'channel',c.channel,'start_at',r.start_at,'end_at',r.end_at
  ) order by r.requested_at), '[]'::jsonb)
    into v_result
    from claimed r
    join public.cameras c on c.id=r.camera_id and c.site_id=v_agent.site_id;
  return v_result;
end
$$;

revoke all on function public.wl_agent_claim_clip_requests(uuid,text,int) from public;
grant execute on function public.wl_agent_claim_clip_requests(uuid,text,int) to anon,authenticated;

create or replace function public.wl_agent_upload_clip_chunk(
  p_agent_id uuid,p_agent_key text,p_request_id uuid,p_sequence_no int,p_data_b64 text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_request public.incident_clip_requests;
  v_data bytea;
  v_total bigint;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  select * into v_request from public.incident_clip_requests
   where id=p_request_id and tenant_id=v_agent.tenant_id and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id and status='processing';
  if v_request.id is null then raise exception 'clip request not claimed by this agent' using errcode='42501'; end if;
  if p_sequence_no not between 0 and 127 then raise exception 'invalid clip chunk sequence'; end if;
  begin
    v_data := decode(p_data_b64,'base64');
  exception when others then
    raise exception 'invalid clip chunk encoding';
  end;
  if octet_length(v_data) < 1 or octet_length(v_data) > 786432 then
    raise exception 'clip chunk exceeds allowed size';
  end if;

  insert into public.incident_clip_chunks(request_id,sequence_no,data,bytes)
  values(p_request_id,p_sequence_no,v_data,octet_length(v_data))
  on conflict(request_id,sequence_no) do update
    set data=excluded.data,bytes=excluded.bytes;

  select coalesce(sum(bytes),0) into v_total
    from public.incident_clip_chunks where request_id=p_request_id;
  if v_total > 33554432 then
    delete from public.incident_clip_chunks where request_id=p_request_id;
    update public.incident_clip_requests
       set status='failed',error_message='Incident footage exceeded the 32 MB pilot limit',completed_at=now()
     where id=p_request_id;
    raise exception 'incident footage exceeds the 32 MB pilot limit';
  end if;
  return jsonb_build_object('ok',true,'sequence_no',p_sequence_no,'stored_bytes',v_total);
end
$$;

revoke all on function public.wl_agent_upload_clip_chunk(uuid,text,uuid,int,text) from public;
grant execute on function public.wl_agent_upload_clip_chunk(uuid,text,uuid,int,text) to anon,authenticated;

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

create or replace function public.wl_agent_fail_clip(
  p_agent_id uuid,p_agent_key text,p_request_id uuid,p_reason text,p_unsupported boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_status text := case when coalesce(p_unsupported,false) then 'unsupported' else 'failed' end;
begin
  v_agent := wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;
  delete from public.incident_clip_chunks where request_id=p_request_id;
  update public.incident_clip_requests
     set status=v_status,error_message=left(coalesce(nullif(btrim(p_reason),''),'Recorder could not provide this footage'),300),completed_at=now()
   where id=p_request_id and tenant_id=v_agent.tenant_id and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id and status='processing';
  if not found then raise exception 'clip request not claimed by this agent' using errcode='42501'; end if;
  return jsonb_build_object('ok',true,'status',v_status);
end
$$;

revoke all on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) from public;
grant execute on function public.wl_agent_fail_clip(uuid,text,uuid,text,boolean) to anon,authenticated;

-- ---------------------------------------------------------------------
-- Extend incident list with the latest clip state and event provenance.
-- ---------------------------------------------------------------------
create or replace function public.wl_incidents(
  p_days int default 7,p_site uuid default null,p_type text default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days int := least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then return '[]'::jsonb; end if;
  return coalesce((
    select jsonb_agg(x) from (
      select jsonb_build_object(
        'event_id',e.id,'site',s.name,'site_id',e.site_id,
        'camera',c.name,'camera_id',e.camera_id,
        'event_type',e.event_type,'device_ts',e.device_ts,
        'has_snapshot',exists(select 1 from public.snapshots sn where sn.event_id=e.id),
        'event_source',coalesce(e.payload->>'source','recorder_event'),
        'native_ai',coalesce((e.payload->>'native_ai')::boolean,false),
        'clip_status',(select case when r.expires_at<=now() and r.status='ready' then 'expired' else r.status end
                       from public.incident_clip_requests r where r.event_id=e.id
                       order by r.requested_at desc limit 1)
      ) as x
      from public.events e
      join public.sites s on s.id=e.site_id
      left join public.cameras c on c.id=e.camera_id
      where e.tenant_id=v_tenant
        and e.device_ts>now()-make_interval(days=>v_days)
        and (p_site is null or e.site_id=p_site)
        and (p_type is null or e.event_type=p_type)
      order by e.device_ts desc limit 300
    ) q
  ),'[]'::jsonb);
end
$$;

revoke all on function public.wl_incidents(int,uuid,text) from public,anon;
grant execute on function public.wl_incidents(int,uuid,text) to authenticated;

-- Remove expired media bytes while keeping the request/audit row and event.
create or replace function public.wl_prune_incident_clips(p_keep_hours int default 24)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_requests int; v_chunks int;
begin
  with expired as (
    update public.incident_clip_requests
       set status='expired'
     where status='ready' and expires_at <= now()
     returning id
  ), removed as (
    delete from public.incident_clip_chunks c
     using expired e where c.request_id=e.id returning 1
  ) select count(*) into v_chunks from removed;

  update public.incident_clip_requests
     set status='expired'
   where status in ('pending','processing')
     and requested_at < now()-make_interval(hours=>greatest(coalesce(p_keep_hours,24),1));
  get diagnostics v_requests=row_count;
  return jsonb_build_object('expired_active_requests',v_requests,'deleted_chunks',v_chunks);
end
$$;

revoke all on function public.wl_prune_incident_clips(int) from public,anon,authenticated;

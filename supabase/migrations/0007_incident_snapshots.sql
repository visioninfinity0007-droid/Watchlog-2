-- =====================================================================
-- WatchLog — incident snapshots
-- =====================================================================
-- A still image captured at the moment of an incident, so a report says
-- "motion, Loading Bay, 02:14" AND shows what was there.
--
-- WHY STILLS AND NOT VIDEO CLIPS
--   A still is ~100-300 KB. A 30-second clip is 5-50 MB. Pulling a clip
--   for every event would saturate a site's uplink and exhaust storage
--   in days. Clips belong on-demand, for one incident an operator has
--   chosen to look at. The driver interface has get_clip() reserved for
--   that; it is unimplemented until it can be tested on real hardware.
--
-- WHY BYTEA AND NOT OBJECT STORAGE
--   Agents authenticate through SECURITY DEFINER functions with the
--   publishable key, not a Supabase Auth session. Object storage would
--   need either a service key in the distributed binary - the exact
--   thing the security model exists to avoid - or a signed-URL minting
--   path that cannot be built from SQL alone.
--   Storing bytes here keeps one auth model and no new attack surface.
--
--   The cost is real and must not be forgotten: this consumes database
--   quota, and the free tier is 500 MB. At ~200 KB a snapshot that is
--   roughly 2,500 images. wl_prune_snapshots() below enforces a ceiling.
--   Production should move to object storage with signed upload URLs
--   issued by a server-side function.
-- =====================================================================

-- ---------------------------------------------------------------------
-- The dedupe key was previously computed inline inside
-- wl_ingest_events. It is now needed in two places, so it lives in one.
-- IMMUTABLE so it can be used anywhere without re-planning.
-- ---------------------------------------------------------------------
create or replace function public.wl_dedupe_key(
  p_site_id  uuid,
  p_channel  text,
  p_event_id text,
  p_device_ts timestamptz,
  p_event_type text
) returns text
language sql
immutable
as $$
  select p_site_id::text || ':' || coalesce(p_channel, '?') || ':' ||
         coalesce(nullif(p_event_id, ''),
                  to_char(p_device_ts at time zone 'UTC',
                          'YYYY-MM-DD"T"HH24:MI:SS"Z"') || ':' || p_event_type)
$$;

-- ---------------------------------------------------------------------
create table if not exists snapshots (
  id           bigint generated always as identity primary key,
  tenant_id    uuid not null references tenants(id) on delete cascade,
  event_id     bigint not null references events(id) on delete cascade,
  site_id      uuid not null references sites(id)   on delete cascade,
  camera_id    uuid references cameras(id) on delete set null,
  image        bytea not null,
  bytes        integer not null,
  content_type text not null default 'image/jpeg',
  captured_at  timestamptz not null default now(),
  -- One image per event. A retry must not store the picture twice.
  constraint snapshots_event_uniq unique (event_id),
  -- 3 MB ceiling: a still that large means something is misconfigured
  -- (a driver returning a clip, or an uncompressed frame).
  constraint snapshots_size_sane check (bytes > 0 and bytes <= 3145728)
);
create index if not exists snapshots_tenant_captured_idx
  on snapshots(tenant_id, captured_at desc);

alter table snapshots enable row level security;

-- ---------------------------------------------------------------------
-- wl_ingest_events — now also stores an optional snapshot per event.
--
-- Replaces the 0004 version. The snapshot is strictly secondary: if the
-- image is missing, malformed or oversized the EVENT still lands. Losing
-- a picture is an inconvenience; losing the incident record is a failure.
-- ---------------------------------------------------------------------
create or replace function public.wl_ingest_events(
  p_agent_id  uuid,
  p_agent_key text,
  p_events    jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent    agents;
  v_received int;
  v_inserted int;
  v_map      jsonb;
  v_snaps    int := 0;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  select count(*) into v_received
    from jsonb_array_elements(coalesce(p_events, '[]'::jsonb));

  with incoming as (
    select
      e->>'channel'                                     as channel,
      coalesce(nullif(e->>'event_type',''), 'unknown')  as event_type,
      nullif(e->>'device_event_id','')                  as device_event_id,
      (e->>'device_ts')::timestamptz                    as device_ts,
      coalesce((e->>'agent_ts')::timestamptz, now())    as agent_ts,
      coalesce(e->'payload', '{}'::jsonb)               as payload
    from jsonb_array_elements(coalesce(p_events, '[]'::jsonb)) e
    where e->>'device_ts' is not null
  ),
  keyed as (
    select i.*, wl_dedupe_key(v_agent.site_id, i.channel, i.device_event_id,
                              i.device_ts, i.event_type) as dedupe_key
      from incoming i
  ),
  deduped as (
    select distinct on (dedupe_key) * from keyed order by dedupe_key, device_ts
  ),
  ins as (
    insert into events (tenant_id, site_id, camera_id, agent_id, event_type,
                        device_event_id, device_ts, agent_ts, dedupe_key, payload)
    select v_agent.tenant_id, v_agent.site_id, c.id, v_agent.id, d.event_type,
           d.device_event_id, d.device_ts, d.agent_ts, d.dedupe_key, d.payload
      from deduped d
      left join cameras c
        on c.site_id = v_agent.site_id and c.channel = d.channel
    on conflict (tenant_id, dedupe_key) do nothing
    returning id, dedupe_key
  )
  select count(*),
         coalesce(jsonb_agg(jsonb_build_object('id', id, 'k', dedupe_key)),
                  '[]'::jsonb)
    into v_inserted, v_map
    from ins;

  -- Attach images to the rows that were actually inserted.
  if v_map <> '[]'::jsonb then
    with supplied as (
      select wl_dedupe_key(v_agent.site_id, e->>'channel',
                           nullif(e->>'device_event_id',''),
                           (e->>'device_ts')::timestamptz,
                           coalesce(nullif(e->>'event_type',''), 'unknown')) as k,
             e->>'channel'      as channel,
             e->>'snapshot_b64' as b64
        from jsonb_array_elements(coalesce(p_events, '[]'::jsonb)) e
       where nullif(e->>'snapshot_b64','') is not null
         and e->>'device_ts' is not null
    ),
    decoded as (
      select distinct on (s.k)
             (m->>'id')::bigint as event_id,
             s.channel,
             decode(s.b64, 'base64') as img
        from jsonb_array_elements(v_map) m
        join supplied s on s.k = m->>'k'
       order by s.k
    ),
    put as (
      insert into snapshots (tenant_id, event_id, site_id, camera_id, image, bytes)
      select v_agent.tenant_id, d.event_id, v_agent.site_id, c.id,
             d.img, octet_length(d.img)
        from decoded d
        left join cameras c
          on c.site_id = v_agent.site_id and c.channel = d.channel
       where octet_length(d.img) between 1 and 3145728
      on conflict (event_id) do nothing
      returning 1
    )
    select count(*) into v_snaps from put;
  end if;

  update agents set last_seen_at = now() where id = v_agent.id;

  return jsonb_build_object(
    'received',  v_received,
    'inserted',  v_inserted,
    'skipped',   v_received - v_inserted,
    'snapshots', v_snaps,
    'server_time', now()
  );
end
$$;

-- ---------------------------------------------------------------------
-- wl_get_snapshot — one image, base64, for the viewer to render inline.
-- ---------------------------------------------------------------------
create or replace function public.wl_get_snapshot(p_event_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
           'event_id',     s.event_id,
           'content_type', s.content_type,
           'bytes',        s.bytes,
           'captured_at',  s.captured_at,
           'image_b64',    encode(s.image, 'base64'))
    from snapshots s
   where s.event_id = p_event_id
$$;

-- ---------------------------------------------------------------------
-- wl_recent_events — now reports whether an image exists, and the id
-- needed to fetch it.
-- ---------------------------------------------------------------------
create or replace function public.wl_recent_events(p_limit int default 25)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
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
       order by ev.received_at desc
       limit least(greatest(coalesce(p_limit, 25), 1), 200)
    ) e
$$;

-- ---------------------------------------------------------------------
-- wl_prune_snapshots — the ceiling that keeps this from eating the
-- database. Images age out; the EVENTS they belong to are never touched,
-- so incident history survives even after the pictures are gone.
-- ---------------------------------------------------------------------
create or replace function public.wl_prune_snapshots(
  p_keep_days int default 14,
  p_max_rows  int default 5000
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_by_age int; v_by_count int;
begin
  delete from snapshots
   where captured_at < now() - make_interval(days => greatest(p_keep_days, 1));
  get diagnostics v_by_age = row_count;

  delete from snapshots
   where id in (select id from snapshots
                 order by captured_at desc
                offset greatest(p_max_rows, 100));
  get diagnostics v_by_count = row_count;

  return jsonb_build_object('deleted_by_age', v_by_age,
                            'deleted_by_count', v_by_count,
                            'remaining', (select count(*) from snapshots),
                            'bytes_stored', (select coalesce(sum(bytes),0) from snapshots));
end
$$;

revoke all on function public.wl_get_snapshot(bigint)     from public;
revoke all on function public.wl_prune_snapshots(int,int)  from public;
revoke all on function public.wl_dedupe_key(uuid,text,text,timestamptz,text) from public;

grant execute on function public.wl_get_snapshot(bigint)    to anon, authenticated;
grant execute on function public.wl_prune_snapshots(int,int) to anon, authenticated;

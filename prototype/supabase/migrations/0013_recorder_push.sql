-- =====================================================================
-- Recorder-push ("PC-free") ingest.
--
-- The problem this answers: a site with no always-on PC. The agent needs
-- SOMETHING on the LAN running 24/7, and the recorder will not run our
-- code. But a modern recorder CAN be told to POST an event + snapshot to
-- an HTTP endpoint on every alarm. So for these sites we make the NVR do
-- the dialling: it pushes events out to us, exactly as the agent does.
-- Still outbound-only, still nothing exposed inbound.
--
-- How auth works here, and why it is safe:
--   * Each PC-free site gets a random PUSH TOKEN. The recorder (via a
--     small translator service that speaks the NVR's native alarm format)
--     posts to us carrying that token. The token is the credential.
--   * The token maps to a VIRTUAL AGENT - a normal agents row with
--     device_driver = 'recorder-push'. So a push site behaves like any
--     other site everywhere else (health, events, snapshots, the portal),
--     with no special cases downstream.
--   * wl_ingest_push authenticates by the token inside the function body
--     (SECURITY DEFINER), the same pattern as wl_enroll / wl_heartbeat,
--     which is why it is granted to anon. No table is reachable directly.
--
-- What this deliberately does NOT give you: on-site false-alarm
-- filtering. The agent's YOLO filter runs on a PC; a bare recorder push
-- has no edge compute, so events arrive unfiltered and we would filter
-- them in the cloud. Recorder-push is the "no-PC / lite" path; the full
-- experience still wants the agent. That trade-off is intentional and
-- should be stated to the customer.
-- =====================================================================

create table if not exists public.push_sources (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  -- The virtual agent this token feeds. One push source == one agent row,
  -- so everything downstream treats it like any other agent.
  agent_id     uuid not null references public.agents(id) on delete cascade,
  token        text not null unique
                 default replace(gen_random_uuid()::text,'-','')
                       || replace(gen_random_uuid()::text,'-',''),
  enabled      boolean not null default true,
  created_at   timestamptz not null default now(),
  last_push_at timestamptz
);

create index if not exists push_sources_tenant_idx
  on public.push_sources (tenant_id);

alter table public.push_sources enable row level security;

-- Members may see that a push source exists, but NOT its token - the
-- token is a secret, surfaced once at issue time and thereafter only
-- through the owner-only function below.
create policy portal_read_push_sources on public.push_sources
  for select using (public.wl_is_member(tenant_id));


-- ---------------------------------------------------------------------
-- Issue a push token for a site (owner/admin only). Creates the virtual
-- agent and returns the token ONCE. Re-issuing rotates the token and
-- disables the old one.
-- ---------------------------------------------------------------------
create or replace function public.wl_issue_push_token(p_site_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant  uuid := wl_require_role(array['owner', 'admin']);
  v_site    sites;
  v_agent   uuid;
  v_token   text;
begin
  select * into v_site from sites where id = p_site_id and tenant_id = v_tenant;
  if v_site.id is null then
    raise exception 'that site does not belong to your account';
  end if;

  -- Rotate: disable any existing source for this site.
  update push_sources set enabled = false where site_id = p_site_id;

  -- A virtual agent to own the pushed events. agent_key_hash is required
  -- but never used - this agent authenticates by push token, not by key.
  insert into agents (tenant_id, site_id, agent_key_hash, hostname,
                      device_driver, last_seen_at)
  values (v_tenant, p_site_id, md5(gen_random_uuid()::text || gen_random_uuid()::text),
          'Recorder push', 'recorder-push', now())
  returning id into v_agent;

  insert into push_sources (tenant_id, site_id, agent_id)
  values (v_tenant, p_site_id, v_agent)
  returning token into v_token;

  return jsonb_build_object(
    'ok', true,
    'token', v_token,
    'note', 'Configure the recorder to POST events to the WatchLog push '
            || 'endpoint with this token. Shown once - keep it safe.');
end $$;


-- ---------------------------------------------------------------------
-- Ingest a batch pushed on behalf of a recorder. Token-authenticated.
-- The body is IDENTICAL to wl_ingest_events once the (tenant, site,
-- agent) is resolved - same dedupe, same insert, same snapshot attach -
-- so a push site and an agent site are indistinguishable downstream.
-- ---------------------------------------------------------------------
create or replace function public.wl_ingest_push(p_token text, p_events jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent    agents;
  v_src      push_sources;
  v_received int;
  v_inserted int;
  v_map      jsonb;
  v_snaps    int := 0;
begin
  select p.* into v_src from push_sources p
   where p.token = btrim(p_token) and p.enabled;
  if v_src.id is null then
    raise exception 'push source not recognised' using errcode = '28000';
  end if;
  select * into v_agent from agents where id = v_src.agent_id;

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
             (m->>'id')::bigint as event_id, s.channel,
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
  update push_sources set last_push_at = now() where id = v_src.id;

  return jsonb_build_object('received', v_received, 'inserted', v_inserted,
                            'skipped', v_received - v_inserted,
                            'snapshots', v_snaps, 'server_time', now());
end $$;


-- Portal: list a tenant's push sources (no token exposed).
create or replace function public.wl_push_sources()
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
             'id', p.id, 'site', s.name, 'enabled', p.enabled,
             'created_at', p.created_at, 'last_push_at', p.last_push_at)
             order by p.created_at desc)
      from push_sources p join sites s on s.id = p.site_id
     where p.tenant_id = v_tenant), '[]'::jsonb);
end $$;


-- Grants: push ingest is anon (token-authenticated inside), the rest is
-- authenticated only.
revoke all on function public.wl_issue_push_token(uuid) from public, anon;
revoke all on function public.wl_push_sources()        from public, anon;
grant execute on function public.wl_issue_push_token(uuid) to authenticated;
grant execute on function public.wl_push_sources()        to authenticated;
grant execute on function public.wl_ingest_push(text, jsonb) to anon, authenticated;

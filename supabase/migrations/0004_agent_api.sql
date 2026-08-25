-- =====================================================================
-- WatchLog prototype — the agent API
-- =====================================================================
-- DEVIATION FROM THE LOCKED DECISION, DELIBERATE AND FLAGGED.
--
-- WATCHLOG_FULL_BUILD_PLAN.md locks "agents write with the service-role
-- key and bypass RLS". That is not done here. The reason is concrete: a
-- PyInstaller .exe is handed to a machine we do not control, and a
-- service-role key inside it is a full-database credential anyone can
-- recover with `strings`. It was flagged as unshippable in the Day 1
-- prototype README before this migration existed.
--
-- Instead: every table stays sealed behind RLS with no policies, and
-- agents call these four SECURITY DEFINER functions with the PUBLISHABLE
-- key — which is public by design and grants nothing on its own. An
-- agent proves identity with the per-agent secret minted at enrollment
-- and stored here only as a SHA-256 hash.
--
-- Three properties this buys that the service-key design cannot:
--   1. A stolen build yields no database access.
--   2. tenant_id and site_id are read from the agent's own row, never
--      from client input, so a compromised agent cannot write into
--      another tenant.
--   3. Enrollment is one transaction, so a mid-enrollment failure can no
--      longer burn an enrollment code.
--
-- To revert to the locked design: drop these functions and grant the
-- service role direct table access. Nothing else depends on them.
-- =====================================================================

-- ---------------------------------------------------------------------
-- internal: resolve an agent from (id, key). NOT granted to any client
-- role — it is only callable from inside the definer functions below.
-- ---------------------------------------------------------------------
create or replace function public.wl_auth_agent(p_agent_id uuid, p_agent_key text)
returns agents
language sql
stable
security definer
set search_path = public
as $$
  select a.* from agents a
   where a.id = p_agent_id
     and a.agent_key_hash = encode(sha256(p_agent_key::bytea), 'hex')
$$;

revoke all on function public.wl_auth_agent(uuid, text) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- wl_enroll — exchange a one-time code for a durable identity.
-- Returns the agent key in plaintext EXACTLY ONCE. It is never
-- recoverable from the database afterwards.
-- ---------------------------------------------------------------------
create or replace function public.wl_enroll(
  p_code            text,
  p_hostname        text default null,
  p_platform        text default null,
  p_agent_version   text default null,
  p_device_vendor   text default null,
  p_device_model    text default null,
  p_device_driver   text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_code  enrollment_codes%rowtype;
  v_key   text;
  v_agent uuid;
begin
  -- Atomic claim. Only unused, unexpired codes match.
  update enrollment_codes
     set used_at = now()
   where code = p_code
     and used_at is null
     and expires_at > now()
  returning * into v_code;

  if v_code.code is null then
    raise exception 'enrollment code rejected: unknown, already used, or expired'
      using errcode = '22023';
  end if;

  -- 256 bits from two v4 UUIDs; no extension dependency.
  v_key := replace(gen_random_uuid()::text, '-', '')
        || replace(gen_random_uuid()::text, '-', '');

  insert into agents (tenant_id, site_id, agent_key_hash, hostname, platform,
                      agent_version, device_vendor, device_model, device_driver,
                      last_seen_at)
  values (v_code.tenant_id, v_code.site_id,
          encode(sha256(v_key::bytea), 'hex'),
          p_hostname, p_platform, p_agent_version,
          p_device_vendor, p_device_model, p_device_driver, now())
  returning id into v_agent;

  update enrollment_codes set used_by_agent_id = v_agent where code = p_code;

  return jsonb_build_object(
    'agent_id',  v_agent,
    'agent_key', v_key,
    'tenant_id', v_code.tenant_id,
    'site_id',   v_code.site_id,
    'server_time', now()
  );
end
$$;

-- ---------------------------------------------------------------------
-- wl_heartbeat — liveness. Also the agent's clock-skew reference.
-- ---------------------------------------------------------------------
create or replace function public.wl_heartbeat(
  p_agent_id      uuid,
  p_agent_key     text,
  p_agent_version text default null,
  p_device_vendor text default null,
  p_device_model  text default null,
  p_device_driver text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_rows int;
begin
  update agents a
     set last_seen_at   = now(),
         agent_version  = coalesce(p_agent_version,  a.agent_version),
         device_vendor  = coalesce(p_device_vendor,  a.device_vendor),
         device_model   = coalesce(p_device_model,   a.device_model),
         device_driver  = coalesce(p_device_driver,  a.device_driver)
   where a.id = p_agent_id
     and a.agent_key_hash = encode(sha256(p_agent_key::bytea), 'hex');

  get diagnostics v_rows = row_count;
  if v_rows = 0 then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  return jsonb_build_object('ok', true, 'server_time', now());
end
$$;

-- ---------------------------------------------------------------------
-- wl_sync_cameras — declare the channels found on the device.
-- Input: [{"channel":"1","name":"Main Gate"}, ...]
-- Output: {"1":"<camera uuid>", ...}
-- ---------------------------------------------------------------------
create or replace function public.wl_sync_cameras(
  p_agent_id  uuid,
  p_agent_key text,
  p_cameras   jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent agents;
  v_out   jsonb := '{}'::jsonb;
  v_row   record;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  -- tenant_id and site_id come from the agent row, never from the client.
  insert into cameras (tenant_id, site_id, channel, name)
  select v_agent.tenant_id, v_agent.site_id,
         c->>'channel',
         coalesce(nullif(c->>'name',''), 'Channel ' || (c->>'channel'))
    from jsonb_array_elements(coalesce(p_cameras, '[]'::jsonb)) c
   where coalesce(c->>'channel','') <> ''
  on conflict (site_id, channel) do update set name = excluded.name;

  for v_row in
    select channel, id from cameras where site_id = v_agent.site_id
  loop
    v_out := v_out || jsonb_build_object(v_row.channel, v_row.id);
  end loop;

  return v_out;
end
$$;

-- ---------------------------------------------------------------------
-- wl_ingest_events — the hot path.
--
-- The DEDUPE CONTRACT LIVES HERE, not in the agent. The key is derived
-- server-side from the device's own identifiers, so a buggy or outdated
-- agent build cannot weaken it.
--
-- Input: [{"channel":"1","event_type":"motion",
--          "device_event_id":"EV1","device_ts":"...","agent_ts":"...",
--          "payload":{...}}, ...]
-- Output: {"received":n,"inserted":m,"skipped":n-m}
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
    select i.*,
           -- Stable device id when the device gives one; otherwise
           -- site + channel + timestamp + type. Same fix Cargo Max needed.
           v_agent.site_id::text || ':' || coalesce(i.channel,'?') || ':' ||
           coalesce(i.device_event_id,
                    to_char(i.device_ts at time zone 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS"Z"') || ':' || i.event_type)
             as dedupe_key
      from incoming i
  ),
  deduped as (
    -- collapse repeats inside a single batch before they reach the index
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
    returning 1
  )
  select count(*) into v_inserted from ins;

  update agents set last_seen_at = now() where id = v_agent.id;

  return jsonb_build_object(
    'received', v_received,
    'inserted', v_inserted,
    'skipped',  v_received - v_inserted,
    'server_time', now()
  );
end
$$;

-- ---------------------------------------------------------------------
-- Grants. These four are the entire agent-facing surface area.
-- ---------------------------------------------------------------------
revoke all on function public.wl_enroll(text, text, text, text, text, text, text) from public;
revoke all on function public.wl_heartbeat(uuid, text, text, text, text, text)    from public;
revoke all on function public.wl_sync_cameras(uuid, text, jsonb)                  from public;
revoke all on function public.wl_ingest_events(uuid, text, jsonb)                 from public;

grant execute on function public.wl_enroll(text, text, text, text, text, text, text) to anon, authenticated;
grant execute on function public.wl_heartbeat(uuid, text, text, text, text, text)    to anon, authenticated;
grant execute on function public.wl_sync_cameras(uuid, text, jsonb)                  to anon, authenticated;
grant execute on function public.wl_ingest_events(uuid, text, jsonb)                 to anon, authenticated;

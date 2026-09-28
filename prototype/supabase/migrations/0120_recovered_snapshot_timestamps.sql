-- Preserve the original footage timestamp on recovered/live event snapshots.
-- wl_ingest_events already stores event.device_ts; snapshot rows must use the same
-- timestamp so recovered archive frames appear in the historical timeline and the
-- visual-review queue inherits the correct captured_at.
create or replace function public.wl_ingest_events(
  p_agent_id uuid,
  p_agent_key text,
  p_events jsonb
) returns jsonb
language plpgsql
security definer
set search_path=public
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
    returning id, dedupe_key, device_ts
  )
  select count(*),
         coalesce(jsonb_agg(jsonb_build_object('id', id, 'k', dedupe_key, 'ts', device_ts)),
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
             e->>'snapshot_b64' as b64,
             (e->>'device_ts')::timestamptz as captured_at
        from jsonb_array_elements(coalesce(p_events, '[]'::jsonb)) e
       where nullif(e->>'snapshot_b64','') is not null
         and e->>'device_ts' is not null
    ),
    decoded as (
      select distinct on (s.k)
             (m->>'id')::bigint as event_id,
             s.channel,
             s.captured_at,
             decode(s.b64, 'base64') as img
        from jsonb_array_elements(v_map) m
        join supplied s on s.k = m->>'k'
       order by s.k
    ),
    put as (
      insert into snapshots (
        tenant_id, event_id, site_id, camera_id, image, bytes, captured_at
      )
      select v_agent.tenant_id, d.event_id, v_agent.site_id, c.id,
             d.img, octet_length(d.img), d.captured_at
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

revoke all on function public.wl_ingest_events(uuid,text,jsonb) from public;
grant execute on function public.wl_ingest_events(uuid,text,jsonb) to anon, authenticated;

-- 0146 - Multi-recorder identity foundation.
--
-- REPO ONLY until separately approved for production.
--
-- Adds first-class recorder identity between Site and Camera while preserving:
--   * existing camera UUIDs/history,
--   * one site-level Agent authority,
--   * legacy single-recorder Agent camera/capability sync.
--
-- Multi-recorder invariant:
--   camera channel is unique only inside one recorder, never across a site.
--
-- This migration deliberately does NOT yet rewrite health/evidence/archive/Site Control.
-- Those layers consume recorder_id in later gated phases.

create table if not exists public.recorders (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  local_key text not null,
  display_name text not null,
  vendor text null,
  model text null,
  driver text null,
  firmware text null,
  identity_fingerprint text null,
  is_primary boolean not null default false,
  is_configured boolean not null default true,
  capabilities jsonb null,
  capabilities_at timestamptz null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint recorders_local_key_nonblank check (btrim(local_key) <> ''),
  constraint recorders_display_name_nonblank check (btrim(display_name) <> ''),
  constraint recorders_site_local_key_uniq unique (site_id, local_key),
  constraint recorders_id_tenant_site_uniq unique (id, tenant_id, site_id)
);

create unique index if not exists recorders_one_configured_primary_per_site_idx
  on public.recorders(site_id)
  where is_primary and is_configured;

create index if not exists recorders_tenant_site_idx
  on public.recorders(tenant_id, site_id, is_configured);

alter table public.recorders enable row level security;
revoke all on table public.recorders from public, anon, authenticated;
grant select on table public.recorders to authenticated;

drop policy if exists portal_read_recorders on public.recorders;
create policy portal_read_recorders on public.recorders
  for select to authenticated
  using (public.wl_is_member(tenant_id));

-- Existing sites are single-recorder by architecture. Create one deterministic
-- default recorder for each site that already has cameras or a non-push Agent.
-- Recorder vendor/model/driver are copied only from the current non-push Agent;
-- they remain reported facts, not capability proof.
insert into public.recorders(
  tenant_id, site_id, local_key, display_name,
  vendor, model, driver,
  is_primary, is_configured,
  capabilities, capabilities_at
)
select
  s.tenant_id,
  s.id,
  'legacy-default',
  s.name || ' Recorder',
  a.device_vendor,
  a.device_model,
  a.device_driver,
  true,
  true,
  s.capabilities,
  s.capabilities_at
from public.sites s
left join lateral (
  select ax.*
    from public.agents ax
   where ax.id = public.wl_current_site_agent(s.id)
) a on true
where (
  exists (select 1 from public.cameras c where c.site_id = s.id)
  or a.id is not null
)
and not exists (
  select 1 from public.recorders r where r.site_id = s.id
)
on conflict (site_id, local_key) do nothing;

alter table public.cameras
  add column if not exists recorder_id uuid;

-- Preserve every camera UUID: attach rows in place to the site's primary
-- recorder; do not insert/delete/re-key cameras.
update public.cameras c
   set recorder_id = r.id
  from public.recorders r
 where c.recorder_id is null
   and r.site_id = c.site_id
   and r.tenant_id = c.tenant_id
   and r.is_primary
   and r.is_configured;

do $$
begin
  if exists (
    select 1 from public.cameras c
     where c.recorder_id is null
  ) then
    raise exception '0146 backfill failed: camera without deterministic recorder';
  end if;

  if exists (
    select 1
      from public.cameras c
      join public.recorders r on r.id = c.recorder_id
     where r.site_id is distinct from c.site_id
        or r.tenant_id is distinct from c.tenant_id
  ) then
    raise exception '0146 backfill failed: camera/recorder lineage mismatch';
  end if;
end
$$;

alter table public.cameras
  alter column recorder_id set not null;

alter table public.cameras
  drop constraint if exists cameras_recorder_lineage_fkey;

alter table public.cameras
  add constraint cameras_recorder_lineage_fkey
  foreign key (recorder_id, tenant_id, site_id)
  references public.recorders(id, tenant_id, site_id)
  on delete restrict;

alter table public.cameras
  drop constraint if exists cameras_recorder_channel_uniq;

alter table public.cameras
  add constraint cameras_recorder_channel_uniq
  unique (recorder_id, channel);

create index if not exists cameras_site_recorder_idx
  on public.cameras(site_id, recorder_id);

-- Compatibility for existing trusted/internal inserts that predate recorder_id.
-- If the site is still unambiguously single-recorder, assign (or lazily create)
-- its default recorder before NOT NULL/FK checks. Once multiple recorder rows
-- exist, recorder_id becomes mandatory and the insert fails closed.
-- Every recorder creator takes the per-site recorder-registry lock before it
-- reads the site's recorders, so concurrent first contacts cannot both create
-- a "first" recorder. 0154 makes a site's first recorder its continuity owner.
create or replace function public.wl_camera_assign_default_recorder()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_site public.sites;
  v_count integer;
  v_recorder public.recorders;
begin
  if new.recorder_id is not null then
    return new;
  end if;

  select * into v_site
    from public.sites
   where id=new.site_id and tenant_id=new.tenant_id;
  if v_site.id is null then
    raise exception 'camera site/tenant lineage invalid' using errcode='42501';
  end if;

  perform pg_advisory_xact_lock(
    hashtext('wl_site_recorder_registry'), hashtext(new.site_id::text)
  );

  select count(*) into v_count
    from public.recorders
   where site_id=new.site_id and tenant_id=new.tenant_id;

  if v_count=0 then
    insert into public.recorders(
      tenant_id,site_id,local_key,display_name,
      is_primary,is_configured,capabilities,capabilities_at
    ) values (
      new.tenant_id,new.site_id,'legacy-default',v_site.name || ' Recorder',
      true,true,v_site.capabilities,v_site.capabilities_at
    )
    returning * into v_recorder;
  elsif v_count=1 then
    select * into v_recorder
      from public.recorders
     where site_id=new.site_id and tenant_id=new.tenant_id
     limit 1;
    if not coalesce(v_recorder.is_configured,false) then
      raise exception 'site recorder is not configured' using errcode='42501';
    end if;
  else
    raise exception 'recorder_id required for multi-recorder site'
      using errcode='42501';
  end if;

  new.recorder_id := v_recorder.id;
  return new;
end
$function$;

revoke all on function public.wl_camera_assign_default_recorder()
  from public, anon, authenticated, service_role;

drop trigger if exists trg_camera_assign_default_recorder on public.cameras;
create trigger trg_camera_assign_default_recorder
before insert on public.cameras
for each row
when (new.recorder_id is null)
execute function public.wl_camera_assign_default_recorder();

-- Site authority must not follow whichever Agent wrote last. Before 0146 the
-- current Agent was simply the newest last_seen_at, so a superseded Agent that
-- keeps running (an old PC left on after a replacement was enrolled) took the
-- site back with every heartbeat or legacy ingest, and the replacement's
-- recorder-aware RPCs failed the authority assertion below. An Agent is now
-- skipped while a later-enrolled, non-push Agent of the same site is online
-- (seen within 3 minutes, the fleet "online" threshold). Failover is kept: once
-- the replacement goes offline the older Agent's own reports make it current
-- again, and the replacement takes over as soon as it reports. last_seen_at
-- stays truthful for every Agent. Lease authority on multi-Agent sites and the
-- 0110 recorder-push exclusion are unchanged.
create or replace function public.wl_current_site_agent(p_site_id uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $function$
  with site_cfg as (
    select coalesce(multi_agent_enabled, false) as multi_agent_enabled
      from public.sites where id = p_site_id
  ), live_lease as (
    select l.holder_agent_id
      from public.site_agent_leases l, site_cfg s
     where s.multi_agent_enabled
       and l.site_id = p_site_id
       and l.holder_agent_id is not null
       and l.lease_expires_at > now()
     limit 1
  ), newest as (
    select a.id
      from public.agents a
     where a.site_id = p_site_id
       and coalesce(a.device_driver, '') <> 'recorder-push'
       and (
         coalesce((select s.multi_agent_enabled from site_cfg s), false)
         or not exists (
           select 1
             from public.agents n
            where n.site_id = a.site_id
              and coalesce(n.device_driver, '') <> 'recorder-push'
              and n.enrolled_at > a.enrolled_at
              and n.last_seen_at > now() - interval '3 minutes'
         )
       )
     order by a.last_seen_at desc nulls last,
              a.enrolled_at desc nulls last,
              a.id desc
     limit 1
  )
  select coalesce((select holder_agent_id from live_lease),
                  (select id from newest));
$function$;

-- Preserve the 0103 ACL exactly.
revoke execute on function public.wl_current_site_agent(uuid)
  from public, anon, authenticated;
grant execute on function public.wl_current_site_agent(uuid) to service_role;

-- Owner-only authority assertion for NEW recorder-aware Agent paths.
-- Legacy singleton RPCs retain their historical compatibility behavior, while
-- any explicit recorder path must be driven by the deterministic current site
-- Agent. With wl_current_site_agent above, a stale enrolled Agent cannot revive
-- itself by heartbeating or writing health/events and advancing last_seen_at.
create or replace function public.wl_assert_current_agent_authority(
  p_agent_id uuid,
  p_site_id uuid
) returns void
language plpgsql
stable
set search_path = public
as $function$
begin
  if public.wl_current_site_agent(p_site_id) is distinct from p_agent_id then
    raise exception 'agent is not current site authority'
      using errcode='42501';
  end if;
end
$function$;

revoke all on function public.wl_assert_current_agent_authority(uuid,uuid)
  from public,anon,authenticated,service_role;


-- Owner-only helper used after a wrapper has authenticated an Agent.
-- It lazily creates the legacy default recorder for a new single-recorder site.
-- Once a site has more than one recorder row, the legacy path fails closed.
create or replace function public.wl_legacy_recorder_for_agent(p_agent_id uuid)
returns uuid
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_site public.sites;
  v_count integer;
  v_recorder public.recorders;
begin
  select * into v_agent from public.agents where id = p_agent_id;
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  select * into v_site from public.sites where id = v_agent.site_id and tenant_id = v_agent.tenant_id;
  if v_site.id is null then
    raise exception 'agent site not found' using errcode='42501';
  end if;

  perform pg_advisory_xact_lock(
    hashtext('wl_site_recorder_registry'), hashtext(v_agent.site_id::text)
  );

  select count(*) into v_count
    from public.recorders
   where site_id = v_agent.site_id
     and tenant_id = v_agent.tenant_id
     and is_configured;

  if v_count = 0 then
    insert into public.recorders(
      tenant_id, site_id, local_key, display_name,
      vendor, model, driver,
      is_primary, is_configured,
      capabilities, capabilities_at
    ) values (
      v_agent.tenant_id, v_agent.site_id, 'legacy-default',
      v_site.name || ' Recorder',
      v_agent.device_vendor, v_agent.device_model, v_agent.device_driver,
      true, true,
      v_site.capabilities, v_site.capabilities_at
    )
    returning * into v_recorder;
    return v_recorder.id;
  end if;

  if v_count <> 1 then
    raise exception 'legacy recorder path is ambiguous for multi-recorder site'
      using errcode='42501';
  end if;

  select * into v_recorder
    from public.recorders
   where site_id = v_agent.site_id
     and tenant_id = v_agent.tenant_id
     and is_configured
   limit 1;

  return v_recorder.id;
end
$function$;

revoke all on function public.wl_legacy_recorder_for_agent(uuid)
  from public, anon, authenticated, service_role;

-- Internal camera-sync implementation. Caller must already have authenticated
-- and authorized tenant/site/recorder lineage.
create or replace function public.wl_sync_recorder_cameras_core(
  p_tenant_id uuid,
  p_site_id uuid,
  p_recorder_id uuid,
  p_cameras jsonb
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_out jsonb := '{}'::jsonb;
  v_row record;
  v_legacy_profiles boolean := false;
begin
  if not exists (
    select 1 from public.recorders r
     where r.id = p_recorder_id
       and r.tenant_id = p_tenant_id
       and r.site_id = p_site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this site'
      using errcode='42501';
  end if;

  select exists(
    select 1
      from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
     where coalesce(c->>'name','') ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
  ) into v_legacy_profiles;

  if v_legacy_profiles then
    with parsed as (
      select c,
             c->>'channel' as transport_channel,
             coalesce(
               substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
               c->>'channel'
             ) as physical_channel,
             row_number() over (
               partition by coalesce(
                 substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
                 c->>'channel'
               )
               order by case when (c->>'channel') ~ '^[0-9]+$'
                             then (c->>'channel')::int else -1 end desc,
                        c->>'channel' desc
             ) as rn
        from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
       where coalesce(c->>'channel','') <> ''
    )
    insert into public.cameras(
      tenant_id,site_id,recorder_id,channel,physical_channel,name,
      is_configured,analytics_enabled,is_canonical
    )
    select
      p_tenant_id,
      p_site_id,
      p_recorder_id,
      transport_channel,
      physical_channel,
      case when rn=1 then 'Camera '||physical_channel
           else coalesce(nullif(c->>'name',''),'Profile '||transport_channel) end,
      coalesce((c->>'is_configured')::boolean,false),
      (rn=1),
      (rn=1)
      from parsed
    on conflict(recorder_id,channel) do update
      set physical_channel=excluded.physical_channel,
          name=case
            when cameras.is_canonical and cameras.is_configured then cameras.name
            else excluded.name
          end,
          is_canonical=excluded.is_canonical,
          analytics_enabled=case
            when excluded.is_canonical then cameras.analytics_enabled
            else false
          end,
          is_configured=case
            when excluded.is_canonical then cameras.is_configured
            else false
          end;

    for v_row in
      select channel,id
        from public.cameras
       where recorder_id=p_recorder_id
         and channel in (
           select c->>'channel'
             from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
         )
    loop
      v_out := v_out || jsonb_build_object(v_row.channel,v_row.id);
    end loop;
    return v_out;
  end if;

  update public.cameras
     set channel='legacy-profile-'||channel
   where recorder_id=p_recorder_id
     and physical_channel is not null
     and channel !~ '^legacy-profile-'
     and exists (
       select 1 from public.cameras x
        where x.id=cameras.id
          and (
            x.name ~* '^Camera [0-9]+$'
            or x.name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
            or exists (
              select 1 from public.cameras y
               where y.recorder_id=x.recorder_id
                 and y.physical_channel=x.physical_channel
                 and y.id<>x.id
            )
          )
     );

  update public.cameras
     set channel=physical_channel,
         name=case
           when name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
             then 'Camera '||physical_channel
           else name
         end
   where recorder_id=p_recorder_id
     and is_canonical
     and physical_channel is not null
     and channel ~ '^legacy-profile-';

  update public.cameras
     set is_canonical=false,
         is_configured=false,
         analytics_enabled=false
   where recorder_id=p_recorder_id
     and channel ~ '^legacy-profile-';

  insert into public.cameras(
    tenant_id,site_id,recorder_id,channel,physical_channel,name,is_configured,is_canonical
  )
  select
    p_tenant_id,p_site_id,p_recorder_id,
    c->>'channel',c->>'channel',
    coalesce(nullif(c->>'name',''),'Camera '||(c->>'channel')),
    coalesce((c->>'is_configured')::boolean,false),true
  from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
  where coalesce(c->>'channel','')<>''
  on conflict(recorder_id,channel) do update
    set physical_channel=excluded.physical_channel,
        name=case when cameras.is_configured then cameras.name else excluded.name end,
        is_canonical=true;

  for v_row in
    select channel,id
      from public.cameras
     where recorder_id=p_recorder_id
       and coalesce(is_canonical,true)
  loop
    v_out := v_out || jsonb_build_object(v_row.channel,v_row.id);
  end loop;
  return v_out;
end
$function$;

revoke all on function public.wl_sync_recorder_cameras_core(uuid,uuid,uuid,jsonb)
  from public, anon, authenticated, service_role;

-- New Agent path: sync recorder identities. It is additive: omission never
-- disables/deletes a recorder. Removal/disable is a separate governed action.
create or replace function public.wl_sync_recorders(
  p_agent_id uuid,
  p_agent_key text,
  p_recorders jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_item jsonb;
  v_local_key text;
  v_primary boolean;
  v_configured boolean;
  v_recorder public.recorders;
  v_existing_primary public.recorders;
  v_out jsonb := '{}'::jsonb;
  v_primary_in_payload integer;
  v_existing integer;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  if p_recorders is null or jsonb_typeof(p_recorders) <> 'array' then
    raise exception 'recorders must be a JSON array' using errcode='22023';
  end if;

  if exists (
    select 1
      from jsonb_array_elements(p_recorders) r
     group by btrim(coalesce(r->>'local_key',''))
    having count(*) > 1
  ) then
    raise exception 'duplicate recorder local_key in payload' using errcode='22023';
  end if;

  select count(*) into v_primary_in_payload
    from jsonb_array_elements(p_recorders) r
   where coalesce((r->>'is_primary')::boolean,false);

  if v_primary_in_payload > 1 then
    raise exception 'at most one recorder may be primary' using errcode='22023';
  end if;

  select count(*) into v_existing
    from public.recorders
   where tenant_id=v_agent.tenant_id and site_id=v_agent.site_id;

  if v_existing=0 and jsonb_array_length(p_recorders)>0 and v_primary_in_payload<>1 then
    raise exception 'first recorder sync requires exactly one primary recorder'
      using errcode='22023';
  end if;

  for v_item in select value from jsonb_array_elements(p_recorders)
  loop
    v_local_key := btrim(coalesce(v_item->>'local_key',''));
    if v_local_key='' then
      raise exception 'recorder local_key required' using errcode='22023';
    end if;

    v_primary := coalesce((v_item->>'is_primary')::boolean,false);
    v_configured := coalesce((v_item->>'is_configured')::boolean,true);

    select * into v_recorder
      from public.recorders
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and local_key=v_local_key;

    -- Adopt the pre-0146 default recorder when the upgraded Agent identifies
    -- its original singleton recorder with a stable local key.
    if v_recorder.id is null and v_primary then
      select * into v_existing_primary
        from public.recorders
       where tenant_id=v_agent.tenant_id
         and site_id=v_agent.site_id
         and is_primary
         and is_configured
       limit 1;

      if v_existing_primary.id is not null
         and v_existing_primary.local_key='legacy-default'
         and not exists (
           select 1 from public.recorders
            where site_id=v_agent.site_id and local_key=v_local_key
         )
      then
        update public.recorders
           set local_key=v_local_key,
               display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
               vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
               model=coalesce(nullif(btrim(v_item->>'model'),''),model),
               driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
               firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
               identity_fingerprint=coalesce(nullif(btrim(v_item->>'identity_fingerprint'),''),identity_fingerprint),
               is_configured=v_configured,
               updated_at=now()
         where id=v_existing_primary.id
         returning * into v_recorder;
      end if;
    end if;

    if v_recorder.id is null then
      if v_primary and exists (
        select 1 from public.recorders
         where site_id=v_agent.site_id and is_primary and is_configured
      ) then
        raise exception 'site already has a configured primary recorder'
          using errcode='23505';
      end if;

      insert into public.recorders(
        tenant_id,site_id,local_key,display_name,
        vendor,model,driver,firmware,identity_fingerprint,
        is_primary,is_configured
      ) values (
        v_agent.tenant_id,v_agent.site_id,v_local_key,
        coalesce(nullif(btrim(v_item->>'display_name'),''),'Recorder'),
        nullif(btrim(v_item->>'vendor'),''),
        nullif(btrim(v_item->>'model'),''),
        nullif(btrim(v_item->>'driver'),''),
        nullif(btrim(v_item->>'firmware'),''),
        nullif(btrim(v_item->>'identity_fingerprint'),''),
        v_primary,v_configured
      )
      returning * into v_recorder;
    else
      update public.recorders
         set display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
             vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
             model=coalesce(nullif(btrim(v_item->>'model'),''),model),
             driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
             firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
             identity_fingerprint=coalesce(nullif(btrim(v_item->>'identity_fingerprint'),''),identity_fingerprint),
             is_configured=v_configured,
             updated_at=now()
       where id=v_recorder.id
       returning * into v_recorder;
    end if;

    v_out := v_out || jsonb_build_object(v_local_key,v_recorder.id);
  end loop;

  return v_out;
end
$function$;

revoke all on function public.wl_sync_recorders(uuid,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.wl_sync_recorders(uuid,text,jsonb) to anon;

-- New explicit camera path for multi-recorder Agents.
create or replace function public.wl_sync_recorder_cameras(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_cameras jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  if not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  return public.wl_sync_recorder_cameras_core(
    v_agent.tenant_id,v_agent.site_id,p_recorder_id,p_cameras
  );
end
$function$;

revoke all on function public.wl_sync_recorder_cameras(uuid,text,uuid,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.wl_sync_recorder_cameras(uuid,text,uuid,jsonb) to anon;

-- Preserve legacy single-recorder Agent API. It lazily creates/resolves exactly
-- one recorder; after a second recorder exists it fails closed rather than
-- treating site+channel as globally unique.
create or replace function public.wl_sync_cameras(
  p_agent_id uuid,
  p_agent_key text,
  p_cameras jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_recorder_id uuid;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  v_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);

  return public.wl_sync_recorder_cameras_core(
    v_agent.tenant_id,v_agent.site_id,v_recorder_id,p_cameras
  );
end
$function$;

revoke all on function public.wl_sync_cameras(uuid,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.wl_sync_cameras(uuid,text,jsonb)
  to anon, authenticated, service_role;

-- Preserve legacy capability sync for true single-recorder sites and mirror the
-- effective payload onto that recorder. Once a site has multiple recorder rows,
-- the ambiguous legacy capability path fails closed.
create or replace function public.wl_sync_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_effective jsonb;
  v_recorder_id uuid;
begin
  v_agent := public.wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;

  v_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  v_effective := public.wl_overlay_camera_truth(v_agent.site_id, p_capabilities);

  update public.sites
     set capabilities = v_effective,
         capabilities_at = now()
   where id = v_agent.site_id;

  update public.recorders
     set capabilities = v_effective,
         capabilities_at = now(),
         updated_at = now()
   where id = v_recorder_id
     and tenant_id = v_agent.tenant_id
     and site_id = v_agent.site_id;

  return jsonb_build_object(
    'ok', true,
    'recorder_id', v_recorder_id,
    'channels', coalesce(jsonb_array_length(v_effective->'channels'), 0)
  );
end
$function$;

-- Preserve the pre-0146 legacy ACL exactly.
revoke all on function public.wl_sync_capabilities(uuid,text,jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.wl_sync_capabilities(uuid,text,jsonb)
  to public, anon, authenticated, service_role;

-- Event provenance must also become recorder-aware before two collectors can run.
-- Keep recorder_id nullable for historical/non-agent rows, but every new Agent ingest
-- resolves one explicit recorder deterministically.
alter table public.events
  add column if not exists recorder_id uuid;

-- Existing camera-linked history inherits recorder provenance without changing event IDs.
update public.events e
   set recorder_id = c.recorder_id
  from public.cameras c
 where e.recorder_id is null
   and e.camera_id = c.id
   and c.recorder_id is not null;

alter table public.events
  drop constraint if exists events_recorder_lineage_fkey;

alter table public.events
  add constraint events_recorder_lineage_fkey
  foreign key (recorder_id, tenant_id, site_id)
  references public.recorders(id, tenant_id, site_id)
  on delete restrict;

create index if not exists events_recorder_device_ts_idx
  on public.events(recorder_id, device_ts desc)
  where recorder_id is not null;

-- Preserve historical dedupe continuity for the original/primary recorder:
-- primary ch1 keeps the old site:ch1 namespace. Additional recorders namespace
-- their channel with recorder UUID so Recorder A ch1 and Recorder B ch1 cannot collide.
create or replace function public.wl_recorder_event_dedupe_key(
  p_site_id uuid,
  p_recorder_id uuid,
  p_channel text,
  p_event_id text,
  p_device_ts timestamptz,
  p_event_type text
) returns text
language sql
stable
set search_path = public
as $function$
  select public.wl_dedupe_key(
    p_site_id,
    case
      when exists (
        select 1 from public.recorders r
         where r.id=p_recorder_id
           and r.site_id=p_site_id
           and r.is_primary
      ) then p_channel
      else p_recorder_id::text || ':' || coalesce(p_channel,'?')
    end,
    p_event_id,
    p_device_ts,
    p_event_type
  )
$function$;

revoke all on function public.wl_recorder_event_dedupe_key(
  uuid,uuid,text,text,timestamptz,text
) from public, anon, authenticated, service_role;

-- Recorder-aware replacement of the existing Agent ingest function.
-- Legacy payloads without recorder_id still work while the site has exactly one
-- recorder. Once multiple recorder rows exist, missing recorder_id fails closed.
create or replace function public.wl_ingest_events(
  p_agent_id uuid,
  p_agent_key text,
  p_events jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_received int;
  v_inserted int;
  v_map jsonb;
  v_snaps int := 0;
  v_legacy_recorder_id uuid := null;
begin
  v_agent := public.wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if p_events is null or jsonb_typeof(p_events) <> 'array' then
    raise exception 'events must be a JSON array' using errcode='22023';
  end if;

  -- Explicit recorder identity is the new multi-recorder path and is allowed
  -- only from the current site Agent. Legacy rows without recorder_id keep the
  -- pre-5.1 compatibility path below.
  if exists (
    select 1 from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is not null
  ) then
    perform public.wl_assert_current_agent_authority(
      v_agent.id,v_agent.site_id
    );
  end if;

  select count(*) into v_received
    from jsonb_array_elements(p_events);

  if exists (
    select 1 from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is null
  ) then
    v_legacy_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);
  end if;

  if exists (
    select 1
      from jsonb_array_elements(p_events) e
     where nullif(e->>'recorder_id','') is not null
       and not exists (
         select 1 from public.recorders r
          where r.id=(e->>'recorder_id')::uuid
            and r.tenant_id=v_agent.tenant_id
            and r.site_id=v_agent.site_id
            and r.is_configured
       )
  ) then
    raise exception 'event recorder not configured for this agent site'
      using errcode='42501';
  end if;

  with incoming as (
    select
      coalesce(nullif(e->>'recorder_id','')::uuid, v_legacy_recorder_id) as recorder_id,
      e->>'channel'                                     as channel,
      coalesce(nullif(e->>'event_type',''), 'unknown')  as event_type,
      nullif(e->>'device_event_id','')                  as device_event_id,
      (e->>'device_ts')::timestamptz                    as device_ts,
      coalesce((e->>'agent_ts')::timestamptz, now())    as agent_ts,
      coalesce(e->'payload', '{}'::jsonb)               as payload
    from jsonb_array_elements(p_events) e
    where e->>'device_ts' is not null
  ),
  keyed as (
    select i.*,
           public.wl_recorder_event_dedupe_key(
             v_agent.site_id,i.recorder_id,i.channel,i.device_event_id,
             i.device_ts,i.event_type
           ) as dedupe_key
      from incoming i
  ),
  deduped as (
    select distinct on (dedupe_key) *
      from keyed
     order by dedupe_key, device_ts
  ),
  ins as (
    insert into public.events(
      tenant_id,site_id,recorder_id,camera_id,agent_id,event_type,
      device_event_id,device_ts,agent_ts,dedupe_key,payload
    )
    select
      v_agent.tenant_id,
      v_agent.site_id,
      d.recorder_id,
      c.id,
      v_agent.id,
      d.event_type,
      d.device_event_id,
      d.device_ts,
      d.agent_ts,
      d.dedupe_key,
      d.payload
      from deduped d
      left join public.cameras c
        on c.site_id=v_agent.site_id
       and c.tenant_id=v_agent.tenant_id
       and c.recorder_id=d.recorder_id
       and c.channel=d.channel
    on conflict (tenant_id,dedupe_key) do nothing
    returning id,dedupe_key,device_ts
  )
  select count(*),
         coalesce(
           jsonb_agg(jsonb_build_object('id',id,'k',dedupe_key,'ts',device_ts)),
           '[]'::jsonb
         )
    into v_inserted,v_map
    from ins;

  if v_map <> '[]'::jsonb then
    with supplied as (
      select
        coalesce(nullif(e->>'recorder_id','')::uuid, v_legacy_recorder_id) as recorder_id,
        public.wl_recorder_event_dedupe_key(
          v_agent.site_id,
          coalesce(nullif(e->>'recorder_id','')::uuid, v_legacy_recorder_id),
          e->>'channel',
          nullif(e->>'device_event_id',''),
          (e->>'device_ts')::timestamptz,
          coalesce(nullif(e->>'event_type',''),'unknown')
        ) as k,
        e->>'channel' as channel,
        e->>'snapshot_b64' as b64,
        (e->>'device_ts')::timestamptz as captured_at
        from jsonb_array_elements(p_events) e
       where nullif(e->>'snapshot_b64','') is not null
         and e->>'device_ts' is not null
    ),
    decoded as (
      select distinct on (s.k)
             (m->>'id')::bigint as event_id,
             s.recorder_id,
             s.channel,
             s.captured_at,
             decode(s.b64,'base64') as img
        from jsonb_array_elements(v_map) m
        join supplied s on s.k=m->>'k'
       order by s.k
    ),
    put as (
      insert into public.snapshots(
        tenant_id,event_id,site_id,camera_id,image,bytes,captured_at
      )
      select
        v_agent.tenant_id,
        d.event_id,
        v_agent.site_id,
        c.id,
        d.img,
        octet_length(d.img),
        d.captured_at
        from decoded d
        left join public.cameras c
          on c.site_id=v_agent.site_id
         and c.tenant_id=v_agent.tenant_id
         and c.recorder_id=d.recorder_id
         and c.channel=d.channel
       where octet_length(d.img) between 1 and 3145728
      on conflict (event_id) do nothing
      returning 1
    )
    select count(*) into v_snaps from put;
  end if;

  update public.agents set last_seen_at=now() where id=v_agent.id;

  return jsonb_build_object(
    'received',v_received,
    'inserted',v_inserted,
    'skipped',v_received-v_inserted,
    'snapshots',v_snaps,
    'server_time',now()
  );
end
$function$;

-- The new recorder/channel key is now authoritative. Keeping the old site-level
-- unique constraint would still prevent Recorder A ch1 + Recorder B ch1.
alter table public.cameras
  drop constraint if exists cameras_site_channel_uniq;

-- Recorder push (0013/0108/0110) still has one site-level token, sends only a
-- channel, and resolved cameras by site+channel. With site+channel no longer
-- unique, an alarm on a multi-recorder site would attach to an arbitrary
-- recorder's camera with recorder_id NULL (and its still could land on another
-- camera). Until push identity is recorder-scoped, push fails closed on a site
-- with more than one configured recorder; on a single-recorder site it keeps
-- working and is attributed to that one recorder. ACLs from 0013/0108/0110 are
-- kept: create or replace preserves them.
create or replace function public.wl_push_recorder_for_site(
  p_tenant_id uuid,
  p_site_id uuid
) returns uuid
language plpgsql
stable
set search_path = public
as $function$
declare
  v_count integer;
  v_recorder_id uuid;
begin
  select count(*) into v_count
    from public.recorders r
   where r.tenant_id=p_tenant_id
     and r.site_id=p_site_id
     and r.is_configured;

  if v_count > 1 then
    raise exception 'recorder push is not available for a site with more than one recorder'
      using errcode='42501';
  end if;

  -- NULL when the site has no recorder yet: events then resolve no camera.
  select r.id into v_recorder_id
    from public.recorders r
   where r.tenant_id=p_tenant_id
     and r.site_id=p_site_id
     and r.is_configured;

  return v_recorder_id;
end
$function$;

revoke all on function public.wl_push_recorder_for_site(uuid,uuid)
  from public, anon, authenticated, service_role;

-- The single configured recorder is the site's continuity owner (0154), so the
-- historical site:channel dedupe key is unchanged and still matches that
-- recorder's Agent events.
create or replace function public.wl_ingest_push(p_token text, p_events jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent       public.agents;
  v_src         public.push_sources;
  v_recorder_id uuid;
  v_received    int;
  v_inserted    int;
  v_map         jsonb;
  v_snaps       int := 0;
begin
  select p.* into v_src from public.push_sources p
   where p.token = btrim(p_token) and p.enabled;
  if v_src.id is null then
    raise exception 'push source not recognised' using errcode = '28000';
  end if;
  select * into v_agent from public.agents where id = v_src.agent_id;

  v_recorder_id := public.wl_push_recorder_for_site(v_agent.tenant_id, v_agent.site_id);

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
    select i.*, public.wl_dedupe_key(v_agent.site_id, i.channel, i.device_event_id,
                                     i.device_ts, i.event_type) as dedupe_key
      from incoming i
  ),
  deduped as (
    select distinct on (dedupe_key) * from keyed order by dedupe_key, device_ts
  ),
  ins as (
    insert into public.events (tenant_id, site_id, recorder_id, camera_id, agent_id,
                               event_type, device_event_id, device_ts, agent_ts,
                               dedupe_key, payload)
    select v_agent.tenant_id, v_agent.site_id, v_recorder_id, c.id, v_agent.id,
           d.event_type, d.device_event_id, d.device_ts, d.agent_ts,
           d.dedupe_key, d.payload
      from deduped d
      left join public.cameras c
        on c.site_id = v_agent.site_id
       and c.recorder_id = v_recorder_id
       and c.channel = d.channel
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
      select public.wl_dedupe_key(v_agent.site_id, e->>'channel',
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
      insert into public.snapshots (tenant_id, event_id, site_id, camera_id, image, bytes)
      select v_agent.tenant_id, d.event_id, v_agent.site_id, c.id,
             d.img, octet_length(d.img)
        from decoded d
        left join public.cameras c
          on c.site_id = v_agent.site_id
         and c.recorder_id = v_recorder_id
         and c.channel = d.channel
       where octet_length(d.img) between 1 and 3145728
      on conflict (event_id) do nothing
      returning 1
    )
    select count(*) into v_snaps from put;
  end if;

  update public.agents set last_seen_at = now() where id = v_agent.id;
  update public.push_sources set last_push_at = now() where id = v_src.id;

  return jsonb_build_object('received', v_received, 'inserted', v_inserted,
                            'skipped', v_received - v_inserted,
                            'snapshots', v_snaps, 'server_time', now());
end
$function$;

create or replace function public.wl_push_liveness(p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_src public.push_sources;
begin
  select * into v_src from public.push_sources where token = p_token and enabled;
  if v_src.id is null then
    raise exception 'push token not recognised' using errcode = '28000';
  end if;

  perform public.wl_push_recorder_for_site(v_src.tenant_id, v_src.site_id);

  update public.push_sources set last_push_at = now() where id = v_src.id;
  update public.agents set last_seen_at = now() where id = v_src.agent_id;

  return jsonb_build_object('ok', true, 'recorded_at', now());
end
$function$;

create or replace function public.wl_agent_issue_push_token(
  p_agent_id  uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent     public.agents;
  v_new_agent uuid;
  v_token     text;
begin
  select * into v_agent
    from public.agents a
   where a.id = p_agent_id
     and a.agent_key_hash = encode(sha256(p_agent_key::bytea), 'hex');

  if v_agent.id is null then
    raise exception 'agent authentication failed' using errcode = '28000';
  end if;

  if coalesce(v_agent.device_driver, '') = 'recorder-push' then
    raise exception 'a recorder-push agent cannot issue push tokens'
      using errcode = '42501';
  end if;

  -- Neither reuse nor mint a site-level token for a multi-recorder site.
  perform public.wl_push_recorder_for_site(v_agent.tenant_id, v_agent.site_id);

  -- Reuse this site's live push source if it already has one.
  select ps.token into v_token
    from public.push_sources ps
   where ps.site_id = v_agent.site_id
     and ps.enabled
   order by ps.created_at desc
   limit 1;

  if v_token is null then
    -- A virtual agent to own the pushed events. agent_key_hash is required
    -- but never used: this agent authenticates by push token, not by key.
    insert into public.agents (tenant_id, site_id, agent_key_hash, hostname,
                               device_driver, last_seen_at)
    values (v_agent.tenant_id, v_agent.site_id,
            md5(gen_random_uuid()::text || gen_random_uuid()::text),
            'Recorder push', 'recorder-push', now())
    returning id into v_new_agent;

    insert into public.push_sources (tenant_id, site_id, agent_id)
    values (v_agent.tenant_id, v_agent.site_id, v_new_agent)
    returning token into v_token;
  end if;

  return jsonb_build_object(
    'ok', true,
    'token', v_token,
    'site_id', v_agent.site_id,
    'note', 'Point the recorder at the WatchLog push bridge with this token.');
end
$function$;

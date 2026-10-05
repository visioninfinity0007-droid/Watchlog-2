-- 0154 - Separate operator primary from immutable legacy continuity ownership.
--
-- STACKED AFTER 0153. REPO ONLY until separately approved.
--
-- Why:
--   Multi-recorder 5.1 originally used recorders.is_primary for two unrelated jobs:
--     1) operator/current preferred primary designation; and
--     2) immutable ownership of pre-multi-recorder spool/health/dedupe continuity.
--   Allowing an operator to promote Recorder B would therefore move Recorder A's
--   historical namespace/state semantics to B. That can duplicate or misattribute
--   evidence.
--
-- Contract:
--   * is_primary MAY move between configured recorders;
--   * continuity_owner is immutable after rollout/adoption;
--   * exactly one recorder per site owns continuity whenever the site has recorders;
--   * a site's first configured recorder becomes its owner (backfill below for
--     existing sites; insert trigger for sites whose first recorder appears later);
--   * legacy event dedupe uses continuity_owner, never is_primary;
--   * Agent recorder sync may update is_primary/is_configured but never continuity_owner;
--   * a same-site reinstall (fresh local id) re-adopts the continuity recorder and
--     flags the previous installation's secondaries as needing re-add, instead of
--     creating duplicate recorders; a recorder's known serial is never
--     overwritten (wl_sync_recorders below).

alter table public.recorders
  add column if not exists continuity_owner boolean not null default false;

-- 0154 is applied before primary reassignment exists in production. The current
-- primary is therefore the only safe deterministic legacy-continuity owner.
with missing as (
  select r.site_id
    from public.recorders r
   group by r.site_id
  having count(*) filter (where r.continuity_owner) = 0
)
update public.recorders r
   set continuity_owner = true,
       updated_at = now()
  from missing m
 where r.site_id = m.site_id
   and r.is_primary;

do $$
begin
  if exists (
    select 1
      from public.recorders r
     group by r.site_id
    having count(*) filter (where r.continuity_owner) <> 1
  ) then
    raise exception '0154 continuity backfill failed: each recorder site needs exactly one continuity owner';
  end if;
end
$$;

create unique index if not exists recorders_one_continuity_owner_per_site_idx
  on public.recorders(site_id)
  where continuity_owner;

alter table public.recorders
  drop constraint if exists recorders_continuity_owner_configured_check;

alter table public.recorders
  add constraint recorders_continuity_owner_configured_check
  check (not continuity_owner or is_configured);

-- The backfill above only covers sites that already had recorders. A site whose
-- first recorder appears later (a new enrollment, a lazy legacy-default from a
-- 5.0.x sync/ingest or camera insert, or a first wl_sync_recorders) has no owner
-- to inherit. The first configured recorder created for such a site becomes its
-- owner, so a lazily created legacy-default keeps the legacy site:channel
-- dedupe namespace. Creators hold the same per-site recorder-registry lock, so
-- concurrent first contacts cannot both see an owner-less site.
create or replace function public.wl_recorder_assign_continuity_owner()
returns trigger
language plpgsql
security definer
set search_path = public
as $function$
begin
  if new.continuity_owner or not new.is_configured then
    return new;
  end if;

  perform pg_advisory_xact_lock(
    hashtext('wl_site_recorder_registry'), hashtext(new.site_id::text)
  );

  if not exists (
    select 1 from public.recorders r
     where r.site_id=new.site_id
       and r.continuity_owner
  ) then
    new.continuity_owner := true;
  end if;

  return new;
end
$function$;

revoke all on function public.wl_recorder_assign_continuity_owner()
  from public,anon,authenticated,service_role;

drop trigger if exists trg_recorder_assign_continuity_owner on public.recorders;
create trigger trg_recorder_assign_continuity_owner
before insert on public.recorders
for each row
execute function public.wl_recorder_assign_continuity_owner();

-- Preserve the old event namespace ONLY for the immutable continuity owner.
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
           and r.continuity_owner
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
) from public,anon,authenticated,service_role;

-- Same-site reinstall. Uninstall removes the Agent identity and the local
-- recorder registry, so a reinstall enrolls a new Agent and Setup stages the
-- recorder under a fresh local id. Looking recorders up by local id alone then
-- forked the site: a new recorder with no history, the continuity recorder's
-- cameras orphaned, and the old secondaries silently offline. Two facts make
-- re-adoption deterministic:
--   * bound_agent_id     the Agent whose wl_sync_recorders last named the row;
--   * readd_required_at  set on a configured secondary that the site's current
--                        installation has not re-added since it re-adopted the
--                        continuity recorder. It stays configured (its cameras
--                        stay expected, so coverage never hides them) and is
--                        listed by wl_multi_recorder_agent_contract.
alter table public.recorders
  add column if not exists bound_agent_id uuid,
  add column if not exists readd_required_at timestamptz;

alter table public.recorders
  drop constraint if exists recorders_bound_agent_fkey;

alter table public.recorders
  add constraint recorders_bound_agent_fkey
  foreign key (bound_agent_id) references public.agents(id)
  on delete set null;

-- Recorder identity proof for re-adoption: the Agent's identity_fingerprint
-- ("serial:<serial>" when the recorder reported one), compared case- and
-- whitespace-insensitively. Never inferred from vendor or model.
create or replace function public.wl_recorder_fingerprint_norm(p_fingerprint text)
returns text
language sql
immutable
set search_path = public
as $function$
  select nullif(
    upper(regexp_replace(btrim(coalesce(p_fingerprint,'')), '^serial:\s*', 'serial:', 'i')),
    ''
  )
$function$;

revoke all on function public.wl_recorder_fingerprint_norm(text)
  from public,anon,authenticated,service_role;

-- Recorder sync is a desired-state registry sync. A non-empty payload must name
-- exactly one CONFIGURED preferred primary. The function clears old preferred
-- primary flags first, then applies the payload atomically inside the RPC
-- transaction. continuity_owner is never accepted from or modified by the Agent.
-- The payload primary is applied first, so on a site without recorders it is the
-- first recorder created (and so the continuity owner), and on a legacy singleton
-- site it is the item that adopts legacy-default, whatever the payload order.
--
-- Re-adoption (same-site reinstall, or the same Agent losing its registry): when
-- the payload does not name the continuity recorder and its primary's local id
-- is new to the site, that primary IS the re-staged continuity recorder. The
-- continuity row takes the new local id; its UUID, cameras, history and
-- continuity ownership stay. It fails closed (42501, nothing changes) when the
-- payload proves otherwise (the primary's identity fingerprint is another
-- recorder of the site, the primary's and the continuity recorder's serials are
-- both known and differ, or another new row carries the continuity recorder's
-- fingerprint), or when the caller is an earlier installation than the one
-- that last bound the continuity recorder. Remaining presumption: when either
-- serial is unknown (the primary reported none, or the continuity recorder
-- never did), nothing can disprove identity at re-adoption, so the re-staged
-- primary is presumed to be the continuity recorder. Setup should therefore
-- stage the continuity recorder as the first primary after a reinstall and send
-- its serial whenever the recorder reports one. A later sync that proves the
-- presumption wrong cannot rewrite history: for every recorder, a configured
-- item whose known identity fingerprint differs from the recorder's recorded
-- one fails closed (42501, nothing changes), and a recorded fingerprint is only
-- ever filled, never replaced. Disabling such a recorder still works.
-- Configured secondaries the payload
-- does not name are flagged readd_required_at after a re-adoption, or whenever
-- an earlier-enrolled Agent bound them. A new local id whose identity
-- fingerprint matches exactly one recorder of the site that the payload does
-- not name re-adopts that recorder (re-adding the same physical recorder);
-- without such proof it is a new recorder.
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
  v_fingerprint text;
  v_recorder public.recorders;
  v_continuity public.recorders;
  v_out jsonb := '{}'::jsonb;
  v_primary_in_payload integer;
  v_existing integer;
  v_payload_keys text[];
  v_primary_item jsonb;
  v_readopt boolean := false;
  v_matches integer;
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
     where btrim(coalesce(r->>'local_key',''))=''
  ) then
    raise exception 'recorder local_key required' using errcode='22023';
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

  if jsonb_array_length(p_recorders)>0 and v_primary_in_payload<>1 then
    raise exception 'recorder registry requires exactly one primary recorder'
      using errcode='22023';
  end if;

  if exists (
    select 1 from jsonb_array_elements(p_recorders) r
     where coalesce((r->>'is_primary')::boolean,false)
       and not coalesce((r->>'is_configured')::boolean,true)
  ) then
    raise exception 'primary recorder must be configured'
      using errcode='22023';
  end if;

  -- Serialize with the lazy legacy creators and other syncs for this site
  -- before reading its registry.
  perform pg_advisory_xact_lock(
    hashtext('wl_site_recorder_registry'), hashtext(v_agent.site_id::text)
  );

  select count(*) into v_existing
    from public.recorders
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id;

  -- The existing migration-created/adopted continuity row is immutable.
  -- 5.1 also keeps it configured: safe retirement needs a future quiesce+drain
  -- workflow so legacy spool/health evidence cannot be stranded.
  select * into v_continuity
    from public.recorders
   where tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and continuity_owner
   limit 1;

  select coalesce(array_agg(btrim(r->>'local_key')),'{}'::text[])
    into v_payload_keys
    from jsonb_array_elements(p_recorders) r;

  select r into v_primary_item
    from jsonb_array_elements(p_recorders) r
   where coalesce((r->>'is_primary')::boolean,false)
   limit 1;

  -- Re-adoption is decided once, before anything changes.
  if v_primary_item is not null
     and v_continuity.id is not null
     and not (v_continuity.local_key = any(v_payload_keys))
     and not exists (
       select 1 from public.recorders x
        where x.tenant_id=v_agent.tenant_id
          and x.site_id=v_agent.site_id
          and x.local_key=btrim(v_primary_item->>'local_key')
     )
  then
    if exists (
      select 1 from public.recorders x
       where x.tenant_id=v_agent.tenant_id
         and x.site_id=v_agent.site_id
         and x.id<>v_continuity.id
         and public.wl_recorder_fingerprint_norm(x.identity_fingerprint)
             = public.wl_recorder_fingerprint_norm(v_primary_item->>'identity_fingerprint')
    ) or exists (
      select 1 from jsonb_array_elements(p_recorders) r
       where not coalesce((r->>'is_primary')::boolean,false)
         and public.wl_recorder_fingerprint_norm(r->>'identity_fingerprint')
             = public.wl_recorder_fingerprint_norm(v_continuity.identity_fingerprint)
         and not exists (
           select 1 from public.recorders x
            where x.tenant_id=v_agent.tenant_id
              and x.site_id=v_agent.site_id
              and x.local_key=btrim(r->>'local_key')
         )
    ) or (
      -- Both serials known and different: a different physical recorder.
      public.wl_recorder_fingerprint_norm(v_primary_item->>'identity_fingerprint') is not null
      and public.wl_recorder_fingerprint_norm(v_continuity.identity_fingerprint) is not null
      and public.wl_recorder_fingerprint_norm(v_primary_item->>'identity_fingerprint')
          <> public.wl_recorder_fingerprint_norm(v_continuity.identity_fingerprint)
    ) then
      raise exception 'recorder registry does not include this site''s continuity recorder'
        using errcode='42501';
    end if;

    if v_continuity.bound_agent_id is not null
       and v_continuity.bound_agent_id<>v_agent.id
       and exists (
         select 1 from public.agents b
          where b.id=v_continuity.bound_agent_id
            and b.enrolled_at>v_agent.enrolled_at
       )
    then
      raise exception 'this site''s recorders were registered by a newer WatchLog installation'
        using errcode='42501';
    end if;

    v_readopt := true;
  end if;

  -- Remove preferred-primary designation before applying the complete desired
  -- primary state. This is safe inside the same RPC transaction.
  if jsonb_array_length(p_recorders)>0 then
    update public.recorders
       set is_primary=false,
           updated_at=now()
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and is_primary;
  end if;

  for v_item in
    select r.value
      from jsonb_array_elements(p_recorders) with ordinality r(value,ord)
     order by coalesce((r.value->>'is_primary')::boolean,false) desc, r.ord
  loop
    v_local_key := btrim(coalesce(v_item->>'local_key',''));
    v_primary := coalesce((v_item->>'is_primary')::boolean,false);
    v_configured := coalesce((v_item->>'is_configured')::boolean,true);
    v_fingerprint := public.wl_recorder_fingerprint_norm(v_item->>'identity_fingerprint');

    select * into v_recorder
      from public.recorders
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and local_key=v_local_key;

    if v_recorder.id is not null
       and v_continuity.id is not null
       and v_recorder.id=v_continuity.id
       and not v_configured
    then
      raise exception 'continuity recorder cannot be disabled in contract v4'
        using errcode='42501';
    end if;

    -- First upgraded sync adopts the migration-created legacy-default row.
    -- Adoption is keyed by immutable continuity ownership, not current primary.
    if v_recorder.id is null
       and v_continuity.id is not null
       and v_continuity.local_key='legacy-default'
       and v_existing=1
       and not v_configured
    then
      raise exception 'continuity recorder cannot be disabled in contract v4'
        using errcode='42501';
    end if;

    if v_recorder.id is null
       and v_continuity.id is not null
       and v_continuity.local_key='legacy-default'
       and v_existing=1
       and not exists (
         select 1 from public.recorders
          where site_id=v_agent.site_id
            and local_key=v_local_key
       )
    then
      update public.recorders
         set local_key=v_local_key,
             display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
             vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
             model=coalesce(nullif(btrim(v_item->>'model'),''),model),
             driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
             firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
             identity_fingerprint=coalesce(
               identity_fingerprint,
               nullif(btrim(v_item->>'identity_fingerprint'),'')
             ),
             is_primary=v_primary,
             is_configured=v_configured,
             updated_at=now()
       where id=v_continuity.id
       returning * into v_recorder;
      v_continuity := v_recorder;
    end if;

    -- Same-site reinstall: the re-staged primary re-adopts the continuity row.
    if v_recorder.id is null and v_primary and v_readopt then
      update public.recorders
         set local_key=v_local_key,
             updated_at=now()
       where id=v_continuity.id
       returning * into v_recorder;
      v_continuity := v_recorder;
    end if;

    -- Re-adding a recorder the site already has: proven by its fingerprint.
    if v_recorder.id is null and v_fingerprint is not null then
      select count(*) into v_matches
        from public.recorders x
       where x.tenant_id=v_agent.tenant_id
         and x.site_id=v_agent.site_id
         and not x.continuity_owner
         and not (x.local_key = any(v_payload_keys))
         and public.wl_recorder_fingerprint_norm(x.identity_fingerprint)=v_fingerprint;

      if v_matches>1 then
        raise exception 'recorder identity is ambiguous for this site'
          using errcode='42501';
      end if;

      if v_matches=1 then
        update public.recorders x
           set local_key=v_local_key,
               updated_at=now()
         where x.tenant_id=v_agent.tenant_id
           and x.site_id=v_agent.site_id
           and not x.continuity_owner
           and not (x.local_key = any(v_payload_keys))
           and public.wl_recorder_fingerprint_norm(x.identity_fingerprint)=v_fingerprint
        returning x.* into v_recorder;
      end if;
    end if;

    -- A recorder's known identity never changes. A configured item whose known
    -- identity fingerprint differs from the recorder's recorded one is another
    -- physical recorder: refuse rather than graft it onto this recorder's
    -- UUID, cameras and history. Disabling such a recorder stays possible
    -- (nothing is grafted); its recorded fingerprint is kept either way.
    if v_recorder.id is not null
       and v_configured
       and v_fingerprint is not null
       and public.wl_recorder_fingerprint_norm(v_recorder.identity_fingerprint) is not null
       and public.wl_recorder_fingerprint_norm(v_recorder.identity_fingerprint)<>v_fingerprint
    then
      raise exception 'this recorder''s serial number differs from the one registered for it; add it as a new recorder'
        using errcode='42501';
    end if;

    if v_recorder.id is null then
      -- continuity_owner is left to trg_recorder_assign_continuity_owner: true
      -- only when this is the site's first configured recorder.
      insert into public.recorders(
        tenant_id,site_id,local_key,display_name,
        vendor,model,driver,firmware,identity_fingerprint,
        is_primary,is_configured,bound_agent_id
      ) values (
        v_agent.tenant_id,v_agent.site_id,v_local_key,
        coalesce(nullif(btrim(v_item->>'display_name'),''),'Recorder'),
        nullif(btrim(v_item->>'vendor'),''),
        nullif(btrim(v_item->>'model'),''),
        nullif(btrim(v_item->>'driver'),''),
        nullif(btrim(v_item->>'firmware'),''),
        nullif(btrim(v_item->>'identity_fingerprint'),''),
        v_primary,v_configured,v_agent.id
      )
      returning * into v_recorder;
    else
      update public.recorders
         set display_name=coalesce(nullif(btrim(v_item->>'display_name'),''),display_name),
             vendor=coalesce(nullif(btrim(v_item->>'vendor'),''),vendor),
             model=coalesce(nullif(btrim(v_item->>'model'),''),model),
             driver=coalesce(nullif(btrim(v_item->>'driver'),''),driver),
             firmware=coalesce(nullif(btrim(v_item->>'firmware'),''),firmware),
             identity_fingerprint=coalesce(
               identity_fingerprint,
               nullif(btrim(v_item->>'identity_fingerprint'),'')
             ),
             is_primary=v_primary,
             is_configured=v_configured,
             bound_agent_id=v_agent.id,
             readd_required_at=null,
             updated_at=now()
       where id=v_recorder.id
       returning * into v_recorder;
    end if;

    v_out := v_out || jsonb_build_object(v_local_key,v_recorder.id);
  end loop;

  -- The previous installation's secondaries stay configured and visible until
  -- this installation re-adds them (or disables them): every unnamed one after
  -- a re-adoption, and otherwise those an earlier-enrolled Agent bound (Setup
  -- may reuse the continuity recorder's old local id, so no re-adoption runs).
  -- An unnamed recorder this Agent bound itself is left alone: omission alone
  -- never changes a recorder.
  update public.recorders r
     set readd_required_at=coalesce(r.readd_required_at,now()),
         updated_at=now()
   where r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.is_configured
     and not r.continuity_owner
     and not (r.local_key = any(v_payload_keys))
     and r.readd_required_at is null
     and (
       v_readopt
       or exists (
         select 1 from public.agents b
          where b.id=r.bound_agent_id
            and b.id<>v_agent.id
            and b.enrolled_at<v_agent.enrolled_at
       )
     );

  if jsonb_array_length(p_recorders)>0 and (
    select count(*)
      from public.recorders
     where tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and is_primary
       and is_configured
  ) <> 1 then
    raise exception 'site must have exactly one configured primary recorder'
      using errcode='23514';
  end if;

  if exists (
    select 1
      from public.recorders r
     where r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
     group by r.site_id
    having count(*) filter (where r.continuity_owner) <> 1
  ) then
    raise exception 'recorder continuity owner invariant violated'
      using errcode='23514';
  end if;

  return v_out;
end
$function$;

revoke all on function public.wl_sync_recorders(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_sync_recorders(uuid,text,jsonb)
  to anon;


-- Contract v4: runtime fan-out must not activate until immutable continuity
-- ownership exists on both DB and Agent. Additive field: the recorders this
-- installation still has to re-add after a reinstall (id and owner-given name
-- only). Recorder push and re-adoption ship in the same chain as v4, so they
-- need no feature flag.
create or replace function public.wl_multi_recorder_agent_contract(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
stable
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

  return jsonb_build_object(
    'ok',true,
    'version',4,
    'configured_recorders',(
      select count(*)
        from public.recorders r
       where r.tenant_id=v_agent.tenant_id
         and r.site_id=v_agent.site_id
         and r.is_configured
    ),
    'recorders_needing_readd',coalesce((
      select jsonb_agg(
               jsonb_build_object(
                 'id',r.id,
                 'display_name',r.display_name,
                 'since',r.readd_required_at
               )
               order by r.readd_required_at,r.id
             )
        from public.recorders r
       where r.tenant_id=v_agent.tenant_id
         and r.site_id=v_agent.site_id
         and r.is_configured
         and r.readd_required_at is not null
    ),'[]'::jsonb),
    'features',jsonb_build_array(
      'recorders',
      'recorder_cameras',
      'recorder_events',
      'recorder_health',
      'recorder_recovery',
      'recorder_reconciliation',
      'recorder_capabilities',
      'recorder_job_routing',
      'recorder_analytics',
      'recorder_continuity'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(uuid,text)
  to anon;

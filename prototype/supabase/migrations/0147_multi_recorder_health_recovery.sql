-- 0147 - Multi-recorder health and recovery identity.
--
-- STACKED AFTER 0146. REPO ONLY until separately approved.
--
-- Goals:
--   * preserve legacy single-recorder health/recovery RPC signatures;
--   * add recorder-scoped current health + camera/inventory reporting;
--   * isolate recorder recovery intervals so simultaneous NVR gaps do not collide;
--   * fail legacy ambiguous paths closed once a site has multiple recorders;
--   * keep site-wide coverage conservative: recorder-specific recovery does NOT
--     automatically promote a whole-site UNVERIFIED interval to RECOVERED;
--   * keep pre-upgrade recovery intervals legacy (recorder_id NULL) so deployed
--     single-recorder Agents can still claim and complete them and their
--     RECOVERED history keeps counting.
--
-- Deliberately deferred to the next gate:
--   * recorder-aware durable health reconciliation / storage-transition replay;
--   * per-recorder local health-store namespaces;
--   * portal consumption of recorder_health.

create table if not exists public.recorder_health (
  recorder_id uuid not null,
  agent_id uuid not null references public.agents(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  nvr_reachable boolean null,
  nvr_auth_ok boolean null,
  recording_state text not null default 'unknown',
  storage_state text not null default 'unknown',
  reason_code text null,
  last_ok_at timestamptz null,
  last_change_at timestamptz null,
  updated_at timestamptz not null default now(),
  primary key (recorder_id, agent_id),
  constraint recorder_health_recorder_lineage_fkey
    foreign key (recorder_id,tenant_id,site_id)
    references public.recorders(id,tenant_id,site_id)
    on delete restrict
);

create index if not exists recorder_health_site_agent_idx
  on public.recorder_health(site_id,agent_id,updated_at desc);

alter table public.recorder_health enable row level security;
revoke all on table public.recorder_health from public,anon,authenticated;
grant select on table public.recorder_health to authenticated;

drop policy if exists portal_read_recorder_health on public.recorder_health;
create policy portal_read_recorder_health on public.recorder_health
  for select to authenticated
  using (public.wl_is_member(tenant_id));

create table if not exists public.recorder_health_transitions (
  id bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  recorder_id uuid not null,
  agent_id uuid not null references public.agents(id) on delete cascade,
  layer text not null check (layer in ('connectivity','auth','recording','storage')),
  from_state text not null,
  to_state text not null,
  reason_code text null,
  at timestamptz not null default now(),
  meta jsonb not null default '{}'::jsonb,
  legacy_transition_id bigint null unique,
  constraint recorder_health_tx_recorder_lineage_fkey
    foreign key (recorder_id,tenant_id,site_id)
    references public.recorders(id,tenant_id,site_id)
    on delete restrict
);

create index if not exists recorder_health_tx_site_recorder_at_idx
  on public.recorder_health_transitions(site_id,recorder_id,at desc);

alter table public.recorder_health_transitions enable row level security;
revoke all on table public.recorder_health_transitions from public,anon,authenticated;

-- Backfill current legacy NVR health onto the site's default/primary recorder,
-- preserving one row per historical reporting Agent rather than collapsing
-- multi-agent history into a single mutable row.
insert into public.recorder_health(
  recorder_id,agent_id,tenant_id,site_id,
  nvr_reachable,nvr_auth_ok,recording_state,storage_state,reason_code,
  last_ok_at,last_change_at,updated_at
)
select
  r.id,nh.agent_id,nh.tenant_id,nh.site_id,
  nh.nvr_reachable,nh.nvr_auth_ok,nh.recording_state,nh.storage_state,nh.reason_code,
  nh.last_ok_at,nh.last_change_at,nh.updated_at
from public.nvr_health nh
join public.recorders r
  on r.site_id=nh.site_id
 and r.tenant_id=nh.tenant_id
 and r.is_primary
where not exists (
  select 1 from public.recorder_health rh
   where rh.recorder_id=r.id and rh.agent_id=nh.agent_id
)
on conflict (recorder_id,agent_id) do nothing;

-- Preserve the legacy transition ledger as recorder-attributed history where
-- the site has a deterministic primary recorder.
insert into public.recorder_health_transitions(
  tenant_id,site_id,recorder_id,agent_id,layer,
  from_state,to_state,reason_code,at,meta,legacy_transition_id
)
select
  tx.tenant_id,tx.site_id,r.id,tx.agent_id,tx.layer,
  tx.from_state,tx.to_state,tx.reason_code,tx.at,tx.meta,tx.id
from public.nvr_health_transitions tx
join public.recorders r
  on r.site_id=tx.site_id
 and r.tenant_id=tx.tenant_id
 and r.is_primary
where not exists (
  select 1 from public.recorder_health_transitions x
   where x.legacy_transition_id=tx.id
)
on conflict (legacy_transition_id) do nothing;

-- ---------------------------------------------------------------------
-- Internal recorder-health report core.
-- Caller has already authenticated the Agent.
-- ---------------------------------------------------------------------
create or replace function public.wl_report_recorder_health_core(
  p_agent_id uuid,
  p_tenant_id uuid,
  p_site_id uuid,
  p_recorder_id uuid,
  p_report jsonb
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_now timestamptz := now();
  v_nvr jsonb := coalesce(p_report->'nvr','{}'::jsonb);
  v_reachable boolean;
  v_auth_ok boolean;
  v_reason text := coalesce(v_nvr->>'reason','unknown');
  v_enumerated boolean := coalesce((p_report#>>'{channels,enumerated}')::boolean,false);
  v_prev public.recorder_health;
  v_present int := 0;
  v_missing int := 0;
  v_disabled int := 0;
  v_unknown int := 0;
  v_unmapped jsonb;
begin
  if not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=p_tenant_id
       and r.site_id=p_site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  if v_nvr ? 'reachable' and jsonb_typeof(v_nvr->'reachable')='boolean' then
    v_reachable := (v_nvr->>'reachable')::boolean;
  else
    v_reachable := null;
  end if;

  if v_nvr ? 'auth_ok' and jsonb_typeof(v_nvr->'auth_ok')='boolean' then
    v_auth_ok := (v_nvr->>'auth_ok')::boolean;
  else
    v_auth_ok := null;
  end if;

  if v_reason not in (
    'ok','unknown','nvr_unreachable','nvr_auth_failed','agent_unreachable',
    'probe_timeout','stale_frame','video_loss','channel_missing',
    'channel_disabled','storage_fault','not_recording','tamper',
    'disk_error','disk_full'
  ) then
    v_reason := 'unknown';
  end if;

  select * into v_prev
    from public.recorder_health
   where recorder_id=p_recorder_id and agent_id=p_agent_id;

  insert into public.recorder_health as rh(
    recorder_id,agent_id,tenant_id,site_id,
    nvr_reachable,nvr_auth_ok,reason_code,last_ok_at,last_change_at,updated_at
  ) values (
    p_recorder_id,p_agent_id,p_tenant_id,p_site_id,
    v_reachable,v_auth_ok,v_reason,
    case when v_reachable is true and v_auth_ok is true then v_now else null end,
    v_now,v_now
  )
  on conflict (recorder_id,agent_id) do update
     set nvr_reachable=excluded.nvr_reachable,
         nvr_auth_ok=excluded.nvr_auth_ok,
         reason_code=excluded.reason_code,
         last_ok_at=case
           when excluded.nvr_reachable is true and excluded.nvr_auth_ok is true
           then v_now else rh.last_ok_at end,
         last_change_at=case
           when rh.nvr_reachable is distinct from excluded.nvr_reachable
             or rh.nvr_auth_ok is distinct from excluded.nvr_auth_ok
           then v_now else rh.last_change_at end,
         updated_at=v_now;

  if v_prev.recorder_id is null
     or v_prev.nvr_reachable is distinct from v_reachable
     or v_prev.nvr_auth_ok is distinct from v_auth_ok
  then
    insert into public.recorder_health_transitions(
      tenant_id,site_id,recorder_id,agent_id,layer,
      from_state,to_state,reason_code,at
    ) values (
      p_tenant_id,p_site_id,p_recorder_id,p_agent_id,
      case when v_auth_ok is false then 'auth' else 'connectivity' end,
      coalesce(v_prev.reason_code,'unknown'),v_reason,v_reason,v_now
    );
  end if;

  -- Inventory truth is now recorder-scoped.
  with rep as (
    select
      c->>'channel' as channel,
      coalesce((c->>'enabled')::boolean,true) as enabled
    from jsonb_array_elements(coalesce(p_report#>'{channels,reported}','[]'::jsonb)) c
    where coalesce(c->>'channel','')<>''
  ),
  det as (
    select (v_reachable is true and v_auth_ok is true and v_enumerated) as ok
  ),
  derived as (
    select cm.id as camera_id, cm.channel,
           case
             when not (select ok from det) then 'unknown'
             when exists(select 1 from rep x where x.channel=cm.channel and not x.enabled)
               then 'disabled'
             when exists(select 1 from rep x where x.channel=cm.channel)
               then 'present'
             else 'missing'
           end as state
      from public.cameras cm
     where cm.site_id=p_site_id
       and cm.tenant_id=p_tenant_id
       and cm.recorder_id=p_recorder_id
  ),
  prev_inv as (
    select ci.camera_id,ci.inventory_state
      from public.camera_inventory ci
      join public.cameras cm on cm.id=ci.camera_id
     where cm.recorder_id=p_recorder_id
  ),
  tx as (
    insert into public.camera_inventory_transitions(
      tenant_id,site_id,camera_id,from_state,to_state,reason_code
    )
    select
      p_tenant_id,p_site_id,d.camera_id,
      coalesce(pi.inventory_state,'unknown'),d.state,
      case d.state
        when 'missing' then 'channel_missing'
        when 'disabled' then 'channel_disabled'
        when 'unknown' then v_reason
        else 'ok'
      end
      from derived d
      left join prev_inv pi on pi.camera_id=d.camera_id
     where pi.inventory_state is distinct from d.state
    returning 1
  ),
  up as (
    insert into public.camera_inventory as ci(
      camera_id,tenant_id,site_id,inventory_state,reason_code,
      first_seen_at,last_present_at,updated_at
    )
    select
      d.camera_id,p_tenant_id,p_site_id,d.state,
      case d.state
        when 'missing' then 'channel_missing'
        when 'disabled' then 'channel_disabled'
        when 'unknown' then v_reason
        else 'ok'
      end,
      case when d.state='present' then v_now else null end,
      case when d.state='present' then v_now else null end,
      v_now
      from derived d
    on conflict (camera_id) do update
       set inventory_state=excluded.inventory_state,
           reason_code=excluded.reason_code,
           first_seen_at=coalesce(ci.first_seen_at,excluded.first_seen_at),
           last_present_at=case when excluded.inventory_state='present'
                                then v_now else ci.last_present_at end,
           updated_at=v_now
    returning 1
  )
  select
    count(*) filter(where state='present'),
    count(*) filter(where state='missing'),
    count(*) filter(where state='disabled'),
    count(*) filter(where state='unknown')
  into v_present,v_missing,v_disabled,v_unknown
  from derived;

  select coalesce(jsonb_agg(x.channel),'[]'::jsonb)
    into v_unmapped
    from (
      select c->>'channel' as channel
      from jsonb_array_elements(coalesce(p_report#>'{channels,reported}','[]'::jsonb)) c
      where coalesce(c->>'channel','')<>''
        and not exists (
          select 1 from public.cameras cm
           where cm.site_id=p_site_id
             and cm.tenant_id=p_tenant_id
             and cm.recorder_id=p_recorder_id
             and cm.channel=c->>'channel'
        )
    ) x;

  update public.recorders
     set vendor=coalesce(nullif(v_nvr->>'vendor',''),vendor),
         model=coalesce(nullif(v_nvr->>'model',''),model),
         firmware=coalesce(nullif(v_nvr->>'firmware',''),firmware),
         updated_at=v_now
   where id=p_recorder_id and tenant_id=p_tenant_id and site_id=p_site_id;

  update public.agents set last_seen_at=v_now where id=p_agent_id;

  return jsonb_build_object(
    'ok',true,
    'recorder_id',p_recorder_id,
    'nvr_state',coalesce(v_nvr->>'state','unknown'),
    'present',v_present,
    'missing',v_missing,
    'disabled',v_disabled,
    'unknown',v_unknown,
    'unmapped',v_unmapped,
    'server_time',v_now
  );
end
$function$;

revoke all on function public.wl_report_recorder_health_core(
  uuid,uuid,uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

create or replace function public.wl_report_recorder_health(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_report jsonb
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

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  return public.wl_report_recorder_health_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,p_recorder_id,p_report
  );
end
$function$;

revoke all on function public.wl_report_recorder_health(uuid,text,uuid,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_recorder_health(uuid,text,uuid,jsonb)
  to anon;

-- Preserve the deployed Agent signature, but only while recorder identity is
-- unambiguous. On a multi-recorder site this now fails closed rather than
-- applying one recorder's inventory to every camera at the site.
create or replace function public.wl_report_health(
  p_agent_id uuid,
  p_agent_key text,
  p_report jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_recorder_id uuid;
  v_result jsonb;
  v_now timestamptz := now();
  v_nvr jsonb := coalesce(p_report->'nvr','{}'::jsonb);
  v_reachable boolean;
  v_auth_ok boolean;
  v_reason text := coalesce(v_nvr->>'reason','unknown');
  v_prev public.nvr_health;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  -- Asserts that recorder identity is still unambiguous. This is what prevents
  -- an old Agent from applying one NVR's health to a multi-recorder site.
  v_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);

  v_result := public.wl_report_recorder_health_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,v_recorder_id,p_report
  );

  -- Compatibility mirror: existing fault/read-model functions still consume
  -- nvr_health(agent_id). Keep it current for the deployed singleton Agent path
  -- until those readers are deliberately moved to recorder_health.
  if v_nvr ? 'reachable' and jsonb_typeof(v_nvr->'reachable')='boolean' then
    v_reachable := (v_nvr->>'reachable')::boolean;
  else
    v_reachable := null;
  end if;
  if v_nvr ? 'auth_ok' and jsonb_typeof(v_nvr->'auth_ok')='boolean' then
    v_auth_ok := (v_nvr->>'auth_ok')::boolean;
  else
    v_auth_ok := null;
  end if;
  if v_reason not in (
    'ok','unknown','nvr_unreachable','nvr_auth_failed','agent_unreachable',
    'probe_timeout','stale_frame','video_loss','channel_missing',
    'channel_disabled','storage_fault','not_recording','tamper',
    'disk_error','disk_full'
  ) then
    v_reason := 'unknown';
  end if;

  select * into v_prev from public.nvr_health where agent_id=v_agent.id;

  insert into public.nvr_health as nh(
    agent_id,tenant_id,site_id,nvr_reachable,nvr_auth_ok,
    reason_code,last_ok_at,last_change_at,updated_at
  ) values (
    v_agent.id,v_agent.tenant_id,v_agent.site_id,v_reachable,v_auth_ok,
    v_reason,
    case when v_reachable is true and v_auth_ok is true then v_now else null end,
    v_now,v_now
  )
  on conflict (agent_id) do update
     set nvr_reachable=excluded.nvr_reachable,
         nvr_auth_ok=excluded.nvr_auth_ok,
         reason_code=excluded.reason_code,
         last_ok_at=case
           when excluded.nvr_reachable is true and excluded.nvr_auth_ok is true
           then v_now else nh.last_ok_at end,
         last_change_at=case
           when nh.nvr_reachable is distinct from excluded.nvr_reachable
             or nh.nvr_auth_ok is distinct from excluded.nvr_auth_ok
           then v_now else nh.last_change_at end,
         updated_at=v_now;

  if v_prev.agent_id is null
     or v_prev.nvr_reachable is distinct from v_reachable
     or v_prev.nvr_auth_ok is distinct from v_auth_ok
  then
    insert into public.nvr_health_transitions(
      tenant_id,site_id,agent_id,layer,from_state,to_state,reason_code
    ) values (
      v_agent.tenant_id,v_agent.site_id,v_agent.id,
      case when v_auth_ok is false then 'auth' else 'connectivity' end,
      coalesce(v_prev.reason_code,'unknown'),v_reason,v_reason
    );
  end if;

  update public.agents
     set device_vendor=coalesce(nullif(v_nvr->>'vendor',''),device_vendor),
         device_model=coalesce(nullif(v_nvr->>'model',''),device_model),
         last_seen_at=v_now
   where id=v_agent.id;

  return v_result;
end
$function$;

revoke all on function public.wl_report_health(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_health(uuid,text,jsonb)
  to anon,authenticated;

-- ---------------------------------------------------------------------
-- Recorder-scoped camera current health.
-- Durable reconciliation is handled in the next gate; this is the same live
-- current-state contract as wl_report_camera_health, with deterministic mapping.
-- ---------------------------------------------------------------------
create or replace function public.wl_report_recorder_camera_health_core(
  p_agent_id uuid,
  p_tenant_id uuid,
  p_site_id uuid,
  p_recorder_id uuid,
  p_report jsonb
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_now timestamptz := now();
  v_op int := 0;
  v_deg int := 0;
  v_off int := 0;
  v_unk int := 0;
begin
  if not exists (
    select 1 from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=p_tenant_id
       and r.site_id=p_site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  with rep as (
    select
      c->>'channel' as channel,
      lower(coalesce(c->>'health','unknown')) as health,
      lower(coalesce(c->>'reason','unknown')) as reason
    from jsonb_array_elements(coalesce(p_report->'cameras','[]'::jsonb)) c
    where coalesce(c->>'channel','')<>''
  ),
  mapped as (
    select
      cm.id as camera_id,
      case when x.health in ('operational','degraded','offline','unknown')
           then x.health else 'unknown' end as health,
      case when x.reason in (
        'ok','unknown','probe_timeout','stale_frame','video_loss',
        'channel_missing','channel_disabled','nvr_unreachable',
        'nvr_auth_failed','agent_unreachable','storage_fault',
        'not_recording','tamper','disk_error','disk_full'
      ) then x.reason else 'unknown' end as reason
    from rep x
    join public.cameras cm
      on cm.site_id=p_site_id
     and cm.tenant_id=p_tenant_id
     and cm.recorder_id=p_recorder_id
     and cm.channel=x.channel
     and cm.is_configured
  ),
  up as (
    insert into public.camera_health as chh(
      camera_id,tenant_id,site_id,health_state,reason_code,
      last_probe_at,last_ok_at,last_change_at,observed_at,
      last_offline_at,last_recovery_at,updated_at
    )
    select
      m.camera_id,p_tenant_id,p_site_id,m.health,m.reason,
      v_now,
      case when m.health='operational' then v_now else null end,
      v_now,v_now,
      case when m.health='offline' then v_now else null end,
      case when m.health='operational' then v_now else null end,
      v_now
    from mapped m
    on conflict (camera_id) do update
       set health_state=excluded.health_state,
           reason_code=excluded.reason_code,
           last_probe_at=v_now,
           last_ok_at=case when excluded.health_state='operational'
                           then v_now else chh.last_ok_at end,
           last_change_at=case
             when chh.health_state is distinct from excluded.health_state
             then v_now else chh.last_change_at end,
           observed_at=v_now,
           last_offline_at=case
             when excluded.health_state='offline'
              and chh.health_state is distinct from 'offline'
             then v_now else chh.last_offline_at end,
           last_recovery_at=case
             when excluded.health_state='operational'
              and chh.health_state='offline'
             then v_now else chh.last_recovery_at end,
           updated_at=v_now
       where v_now>=coalesce(chh.observed_at,'-infinity'::timestamptz)
    returning 1
  )
  select
    count(*) filter(where health='operational'),
    count(*) filter(where health='degraded'),
    count(*) filter(where health='offline'),
    count(*) filter(where health='unknown')
  into v_op,v_deg,v_off,v_unk
  from mapped;

  update public.agents set last_seen_at=v_now where id=p_agent_id;

  return jsonb_build_object(
    'ok',true,'recorder_id',p_recorder_id,
    'operational',v_op,'degraded',v_deg,'offline',v_off,'unknown',v_unk,
    'server_time',v_now
  );
end
$function$;

revoke all on function public.wl_report_recorder_camera_health_core(
  uuid,uuid,uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

create or replace function public.wl_report_recorder_camera_health(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_report jsonb
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

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );
  return public.wl_report_recorder_camera_health_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,p_recorder_id,p_report
  );
end
$function$;

revoke all on function public.wl_report_recorder_camera_health(uuid,text,uuid,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_recorder_camera_health(uuid,text,uuid,jsonb)
  to anon;

create or replace function public.wl_report_camera_health(
  p_agent_id uuid,
  p_agent_key text,
  p_report jsonb
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
  return public.wl_report_recorder_camera_health_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,v_recorder_id,p_report
  );
end
$function$;

revoke all on function public.wl_report_camera_health(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_camera_health(uuid,text,jsonb)
  to anon,authenticated;

-- ---------------------------------------------------------------------
-- Recorder-scoped recovery identity.
--
-- Existing rows are NOT backfilled. recorder_id NULL means a legacy site-wide
-- interval: deployed 5.0.x Agents may hold one pending or in progress across
-- this deploy, and recovered ones carry RECOVERED coverage history that the
-- site-wide coverage contract counts only while recorder_id is NULL. On a
-- one-recorder site both the legacy and the recorder RPCs below therefore
-- serve NULL rows and that recorder's rows alike; on a multi-recorder site the
-- legacy RPCs fail closed and a NULL row is never attributed to a recorder.
-- ---------------------------------------------------------------------
alter table public.recovery_intervals
  add column if not exists recorder_id uuid;

alter table public.recovery_intervals
  drop constraint if exists recovery_intervals_recorder_lineage_fkey;

alter table public.recovery_intervals
  add constraint recovery_intervals_recorder_lineage_fkey
  foreign key (recorder_id,tenant_id,site_id)
  references public.recorders(id,tenant_id,site_id)
  on delete restrict;

alter table public.recovery_intervals
  drop constraint if exists recovery_intervals_site_id_started_at_ended_at_key;

create unique index if not exists recovery_intervals_recorder_window_uidx
  on public.recovery_intervals(recorder_id,started_at,ended_at)
  where recorder_id is not null;

create unique index if not exists recovery_intervals_legacy_site_window_uidx
  on public.recovery_intervals(site_id,started_at,ended_at)
  where recorder_id is null;

create index if not exists recovery_intervals_recorder_claim_idx
  on public.recovery_intervals(recorder_id,status,updated_at)
  where recorder_id is not null;

create or replace function public.wl_open_recorder_recovery_interval(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_started_at timestamptz,
  p_ended_at timestamptz,
  p_channels text[] default '{}'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_id uuid;
  v_cameras uuid[] := '{}';
  v_channel_count int := 0;
  v_singleton boolean;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

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

  -- p_recorder_id is configured, so "exactly one" means it is the only one and
  -- the site's legacy (NULL recorder) intervals are its work too.
  select count(*)=1 into v_singleton
    from public.recorders r
   where r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.is_configured;

  if p_ended_at<=p_started_at then
    return jsonb_build_object('ok',false,'reason','empty_interval');
  end if;

  -- A recorder interval must name the channels it covers. Without them the
  -- Agent would have to guess the archive target, so refuse (and let the
  -- Agent keep its gap) rather than store a camera-less interval.
  if coalesce(cardinality(p_channels),0)=0
     or exists (
       select 1 from unnest(p_channels) x where coalesce(btrim(x),'')=''
     )
  then
    raise exception 'recovery interval needs at least one recorder channel'
      using errcode='22023';
  end if;

  select coalesce(array_agg(c.id order by c.channel),'{}'::uuid[]), count(*)
    into v_cameras,v_channel_count
    from public.cameras c
   where c.tenant_id=v_agent.tenant_id
     and c.site_id=v_agent.site_id
     and c.recorder_id=p_recorder_id
     and c.channel=any(coalesce(p_channels,'{}'::text[]));

  if v_channel_count <> (
    select count(distinct x) from unnest(coalesce(p_channels,'{}'::text[])) x
  ) then
    raise exception 'recovery channel does not belong to recorder'
      using errcode='42501';
  end if;

  select id into v_id
    from public.recovery_intervals r
   where r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and (
       r.recorder_id=p_recorder_id
       or (v_singleton and r.recorder_id is null)
     )
     and abs(extract(epoch from (r.started_at-p_started_at)))<5
     and abs(extract(epoch from (r.ended_at-p_ended_at)))<5
   limit 1;

  if v_id is not null then
    return jsonb_build_object('ok',true,'duplicate',true,'id',v_id);
  end if;

  insert into public.recovery_intervals(
    tenant_id,site_id,agent_id,recorder_id,
    started_at,ended_at,cameras
  ) values (
    v_agent.tenant_id,v_agent.site_id,v_agent.id,p_recorder_id,
    p_started_at,p_ended_at,v_cameras
  )
  returning id into v_id;

  return jsonb_build_object(
    'ok',true,'id',v_id,'status','pending','recorder_id',p_recorder_id
  );
end
$function$;

revoke all on function public.wl_open_recorder_recovery_interval(
  uuid,text,uuid,timestamptz,timestamptz,text[]
) from public,anon,authenticated,service_role;
grant execute on function public.wl_open_recorder_recovery_interval(
  uuid,text,uuid,timestamptz,timestamptz,text[]
) to anon;

create or replace function public.wl_agent_claim_recorder_recovery(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_limit int default 1,
  p_stale_seconds int default 900
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_out jsonb;
  v_singleton boolean;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

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

  select count(*)=1 into v_singleton
    from public.recorders r
   where r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.is_configured;

  with due as (
    select id
      from public.recovery_intervals
     where (
         recorder_id=p_recorder_id
         or (v_singleton and recorder_id is null)
       )
       and tenant_id=v_agent.tenant_id
       and site_id=v_agent.site_id
       and (
         status='pending'
         or (
           status='in_progress'
           and updated_at<now()-make_interval(secs=>p_stale_seconds)
         )
       )
     order by ended_at desc
     limit greatest(1,p_limit)
     for update skip locked
  ),
  claimed as (
    update public.recovery_intervals r
       set status='in_progress',
           attempts=r.attempts+1,
           agent_id=v_agent.id,
           updated_at=now()
      from due
     where r.id=due.id
    returning
      r.id,r.recorder_id,r.started_at,r.ended_at,
      r.cameras,r.checkpoint,r.attempts
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id',id,'recorder_id',recorder_id,
        'started_at',started_at,'ended_at',ended_at,
        'cameras',to_jsonb(cameras),
        'channels',coalesce((
          select jsonb_agg(cm.channel order by cm.channel)
            from public.cameras cm
           where cm.id=any(claimed.cameras)
        ),'[]'::jsonb),
        'checkpoint',checkpoint,'attempts',attempts
      )
      order by ended_at desc
    ),
    '[]'::jsonb
  )
  into v_out
  from claimed;

  return v_out;
end
$function$;

revoke all on function public.wl_agent_claim_recorder_recovery(
  uuid,text,uuid,int,int
) from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_recorder_recovery(
  uuid,text,uuid,int,int
) to anon;

create or replace function public.wl_complete_recorder_recovery(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_id uuid,
  p_status text,
  p_recovered_count int default 0,
  p_checkpoint jsonb default '{}'::jsonb,
  p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_singleton boolean;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  if p_status not in (
    'pending','in_progress','recovered','partial','unrecoverable'
  ) then
    raise exception 'invalid recovery status' using errcode='22023';
  end if;

  -- A legacy (NULL recorder) row belongs to p_recorder_id only while it is
  -- the site's one configured recorder.
  select count(*)=1
         and bool_or(r.id=p_recorder_id)
    into v_singleton
    from public.recorders r
   where r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id
     and r.is_configured;

  update public.recovery_intervals r
     set status=p_status,
         recovered_count=greatest(r.recovered_count,coalesce(p_recovered_count,0)),
         checkpoint=coalesce(p_checkpoint,r.checkpoint),
         detail=r.detail||coalesce(p_detail,'{}'::jsonb),
         updated_at=now()
   where r.id=p_id
     and (
       r.recorder_id=p_recorder_id
       or (coalesce(v_singleton,false) and r.recorder_id is null)
     )
     and r.tenant_id=v_agent.tenant_id
     and r.site_id=v_agent.site_id;

  if not found then
    return jsonb_build_object('ok',false,'reason','not_found');
  end if;

  return jsonb_build_object(
    'ok',true,'id',p_id,'status',p_status,'recorder_id',p_recorder_id
  );
end
$function$;

revoke all on function public.wl_complete_recorder_recovery(
  uuid,text,uuid,uuid,text,int,jsonb,jsonb
) from public,anon,authenticated,service_role;
grant execute on function public.wl_complete_recorder_recovery(
  uuid,text,uuid,uuid,text,int,jsonb,jsonb
) to anon;

-- Legacy recovery RPCs remain available for deployed single-recorder Agents,
-- but they assert singleton recorder identity first. New legacy rows keep
-- recorder_id NULL so the existing site-wide recovery/coverage semantics and
-- dedupe keys are byte-compatible on a one-recorder site. Dedupe, claim and
-- complete also accept rows of that singleton recorder, so an interval a
-- recorder-aware Agent opened is not stranded if the site rolls back to a
-- legacy Agent.

create or replace function public.wl_open_recovery_interval(
  p_agent_id uuid,
  p_agent_key text,
  p_started_at timestamptz,
  p_ended_at timestamptz,
  p_cameras uuid[] default '{}'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_guard uuid;
  v_id uuid;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  v_guard := public.wl_legacy_recorder_for_agent(v_agent.id);

  if p_ended_at<=p_started_at then
    return jsonb_build_object('ok',false,'reason','empty_interval');
  end if;

  if exists (
    select 1
      from unnest(coalesce(p_cameras,'{}'::uuid[])) cid
     where not exists (
       select 1 from public.cameras c
        where c.id=cid
          and c.tenant_id=v_agent.tenant_id
          and c.site_id=v_agent.site_id
          and c.recorder_id=v_guard
     )
  ) then
    raise exception 'recovery camera does not belong to legacy recorder'
      using errcode='42501';
  end if;

  select id into v_id
    from public.recovery_intervals r
   where r.site_id=v_agent.site_id
     and (r.recorder_id is null or r.recorder_id=v_guard)
     and abs(extract(epoch from (r.started_at-p_started_at)))<5
     and abs(extract(epoch from (r.ended_at-p_ended_at)))<5
   limit 1;

  if v_id is not null then
    return jsonb_build_object('ok',true,'duplicate',true,'id',v_id);
  end if;

  insert into public.recovery_intervals(
    tenant_id,site_id,agent_id,recorder_id,started_at,ended_at,cameras
  ) values (
    v_agent.tenant_id,v_agent.site_id,v_agent.id,null,
    p_started_at,p_ended_at,coalesce(p_cameras,'{}'::uuid[])
  )
  returning id into v_id;

  return jsonb_build_object('ok',true,'id',v_id,'status','pending');
end
$function$;

revoke all on function public.wl_open_recovery_interval(
  uuid,text,timestamptz,timestamptz,uuid[]
) from public,anon,authenticated,service_role;
grant execute on function public.wl_open_recovery_interval(
  uuid,text,timestamptz,timestamptz,uuid[]
) to anon,authenticated;

create or replace function public.wl_agent_claim_recovery(
  p_agent_id uuid,
  p_agent_key text,
  p_limit int default 1,
  p_stale_seconds int default 900
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_guard uuid;
  v_out jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;
  v_guard := public.wl_legacy_recorder_for_agent(v_agent.id);

  with due as (
    select id
      from public.recovery_intervals
     where site_id=v_agent.site_id
       and tenant_id=v_agent.tenant_id
       and (recorder_id is null or recorder_id=v_guard)
       and (
         status='pending'
         or (
           status='in_progress'
           and updated_at<now()-make_interval(secs=>p_stale_seconds)
         )
       )
     order by ended_at desc
     limit greatest(1,p_limit)
     for update skip locked
  ),
  claimed as (
    update public.recovery_intervals r
       set status='in_progress',
           attempts=r.attempts+1,
           agent_id=v_agent.id,
           updated_at=now()
      from due
     where r.id=due.id
    returning
      r.id,r.started_at,r.ended_at,r.cameras,r.checkpoint,r.attempts
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id',id,'started_at',started_at,'ended_at',ended_at,
        'cameras',to_jsonb(cameras),'checkpoint',checkpoint,'attempts',attempts
      )
      order by ended_at desc
    ),
    '[]'::jsonb
  )
  into v_out from claimed;

  return v_out;
end
$function$;

revoke all on function public.wl_agent_claim_recovery(uuid,text,int,int)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_agent_claim_recovery(uuid,text,int,int)
  to anon,authenticated;

create or replace function public.wl_complete_recovery(
  p_agent_id uuid,
  p_agent_key text,
  p_id uuid,
  p_status text,
  p_recovered_count int default 0,
  p_checkpoint jsonb default '{}'::jsonb,
  p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_guard uuid;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;
  v_guard := public.wl_legacy_recorder_for_agent(v_agent.id);

  if p_status not in (
    'pending','in_progress','recovered','partial','unrecoverable'
  ) then
    raise exception 'invalid recovery status' using errcode='22023';
  end if;

  update public.recovery_intervals r
     set status=p_status,
         recovered_count=greatest(r.recovered_count,coalesce(p_recovered_count,0)),
         checkpoint=coalesce(p_checkpoint,r.checkpoint),
         detail=r.detail||coalesce(p_detail,'{}'::jsonb),
         updated_at=now()
   where r.id=p_id
     and r.site_id=v_agent.site_id
     and r.tenant_id=v_agent.tenant_id
     and (r.recorder_id is null or r.recorder_id=v_guard);

  if not found then
    return jsonb_build_object('ok',false,'reason','not_found');
  end if;

  return jsonb_build_object('ok',true,'id',p_id,'status',p_status);
end
$function$;

revoke all on function public.wl_complete_recovery(
  uuid,text,uuid,text,int,jsonb,jsonb
) from public,anon,authenticated,service_role;
grant execute on function public.wl_complete_recovery(
  uuid,text,uuid,text,int,jsonb,jsonb
) to anon,authenticated;


-- ---------------------------------------------------------------------
-- Site-wide coverage remains conservative during the multi-recorder rollout.
--
-- The existing coverage function was written when one Agent == one recorder and
-- treats any recovered interval at the site as recovery of the site-wide gap.
-- A recorder-specific recovery may cover only some cameras, so it MUST NOT
-- upgrade the whole site to RECOVERED. Legacy NULL-recorder intervals keep the
-- exact old behavior for deployed single-recorder Agents. A later governed
-- camera/recorder coverage layer may compose recorder-specific recovery safely.
-- ---------------------------------------------------------------------
create or replace function public.wl_site_coverage_report_classes(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_base jsonb;
  v_wall numeric;
  v_unv numeric;
  v_mon numeric;
  v_rec numeric;
  v_a timestamptz;
  v_b timestamptz;
begin
  v_base := public.wl_site_coverage_report(p_site_id,p_from,p_to);
  v_a := least(p_from,p_to);
  v_b := greatest(p_from,p_to);
  v_wall := coalesce((v_base->>'wall_seconds')::numeric,0);
  v_unv := coalesce((v_base->>'unverified_seconds')::numeric,0);
  v_mon := coalesce((v_base->>'monitored_seconds')::numeric,0);

  with gaps as (
    select tstzrange(
      (x->>'start')::timestamptz,
      (x->>'end')::timestamptz,
      '[)'
    ) r
    from jsonb_array_elements(coalesce(v_base->'gaps','[]'::jsonb)) x
    where (x->>'start') is not null
      and (x->>'end') is not null
      and (x->>'start')::timestamptz<(x->>'end')::timestamptz
  ),
  gmr as (
    select coalesce(range_agg(r),'{}'::tstzmultirange) rr from gaps
  ),
  rec as (
    select tstzrange(
      greatest(ri.started_at,v_a),
      least(ri.ended_at,v_b),
      '[)'
    ) r
    from public.recovery_intervals ri
    where ri.site_id=p_site_id
      and ri.recorder_id is null
      and ri.status='recovered'
      and greatest(ri.started_at,v_a)<least(ri.ended_at,v_b)
  ),
  rmr as (
    select coalesce(range_agg(r),'{}'::tstzmultirange) rr from rec
  ),
  inter as (
    select ((select rr from gmr)*(select rr from rmr)) m
  )
  select coalesce((
    select sum(extract(epoch from (upper(x)-lower(x))))
    from (select unnest((select m from inter)) x) z
  ),0)::numeric
  into v_rec;

  v_rec := least(v_rec,v_unv);

  return v_base||jsonb_build_object(
    'classes',
    jsonb_build_object(
      'live_seconds',round(v_mon,1),
      'recovered_seconds',round(v_rec,1),
      'unverified_seconds',round(greatest(0,v_unv-v_rec),1),
      'live_ratio',case when v_wall>0 then round(v_mon/v_wall,4) else 1 end,
      'recovered_ratio',case when v_wall>0 then round(v_rec/v_wall,4) else 0 end,
      'unverified_ratio',case when v_wall>0
        then round(greatest(0,v_unv-v_rec)/v_wall,4) else 0 end,
      'total_coverage_ratio',case when v_wall>0
        then round((v_mon+v_rec)/v_wall,4) else 1 end
    )
  );
end
$function$;

revoke all on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) from public,anon;
grant execute on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) to authenticated,service_role;

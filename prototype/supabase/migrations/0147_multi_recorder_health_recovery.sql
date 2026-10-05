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
--     RECOVERED history keeps counting;
--   * recorder-scoped recording/storage current proof, so one recorder's
--     proof never lands on another recorder's same-numbered cameras;
--   * recorder-aware operational faults, so an unplugged recorder raises a
--     fault and a missing or frozen nvr_health row hides nothing;
--   * camera-less recorder disk events are recorder storage evidence, never
--     camera activity.
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

-- Present-tense storage proof for this recorder: the recorder-scoped twin of
-- nvr_health.sto_current_* (0089), written only by the current-proof RPCs.
alter table public.recorder_health
  add column if not exists sto_current_state text not null default 'unknown';
alter table public.recorder_health
  add column if not exists sto_current_reason_code text;
alter table public.recorder_health
  add column if not exists sto_current_at timestamptz;
alter table public.recorder_health
  add column if not exists sto_current_evidence text;

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
  last_ok_at,last_change_at,updated_at,
  sto_current_state,sto_current_reason_code,sto_current_at,sto_current_evidence
)
select
  r.id,nh.agent_id,nh.tenant_id,nh.site_id,
  nh.nvr_reachable,nh.nvr_auth_ok,nh.recording_state,nh.storage_state,nh.reason_code,
  nh.last_ok_at,nh.last_change_at,nh.updated_at,
  nh.sto_current_state,nh.sto_current_reason_code,nh.sto_current_at,
  nh.sto_current_evidence
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
  v_unconfigured int := 0;
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
    select cm.id as camera_id, cm.channel, cm.is_configured,
           case
             -- 0085: a slot the operator declared empty stays disabled.
             when not cm.is_configured then 'disabled'
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
    count(*) filter(where state='unknown'),
    count(*) filter(where not is_configured)
  into v_present,v_missing,v_disabled,v_unknown,v_unconfigured
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
    'not_configured',v_unconfigured,
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
-- Recorder-scoped recording/storage current proof (0089 contract).
--
-- 0089's wl_report_recording_storage_current has no recorder: it maps report
-- channels by site+channel and writes storage onto nvr_health(agent). With two
-- recorders sharing channel numbers, Recorder A's archive proof would mark
-- Recorder B's channel 1 recording and the storage state would alternate
-- between recorders. The core maps cameras by recorder+channel and keeps
-- storage proof per recorder; the legacy RPC keeps working only while recorder
-- identity is unambiguous.
-- Internal: the caller has authenticated and authorized the Agent.
-- ---------------------------------------------------------------------
create or replace function public.wl_report_recorder_recording_storage_current_core(
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
  v_storage_state text;
  v_storage_reason text;
  v_recording_evidence text := lower(coalesce(p_report->>'recording_evidence','unknown'));
  v_count int := 0;
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

  v_storage_state := lower(coalesce(p_report#>>'{storage,state}','unknown'));
  if v_storage_state not in ('ok','degraded','fault','unknown') then
    v_storage_state := 'unknown';
  end if;
  v_storage_reason := lower(coalesce(p_report#>>'{storage,reason}','unknown'));
  if v_storage_reason not in ('ok','unknown','storage_fault','disk_error','disk_full',
                              'nvr_unreachable','nvr_auth_failed','agent_unreachable') then
    v_storage_reason := 'unknown';
  end if;

  -- updated_at stays the connectivity report's clock: storage proof alone
  -- must not make a stale reachability look fresh.
  insert into public.recorder_health as rh(
    recorder_id,agent_id,tenant_id,site_id,
    sto_current_state,sto_current_reason_code,sto_current_at,sto_current_evidence
  ) values (
    p_recorder_id,p_agent_id,p_tenant_id,p_site_id,
    v_storage_state,v_storage_reason,v_now,'vendor_status'
  )
  on conflict (recorder_id,agent_id) do update
     set sto_current_state=excluded.sto_current_state,
         sto_current_reason_code=excluded.sto_current_reason_code,
         sto_current_at=excluded.sto_current_at,
         sto_current_evidence=excluded.sto_current_evidence;

  with raw as (
    select r->>'channel' as channel,
           lower(coalesce(r->>'state','unknown')) as raw_state,
           lower(coalesce(r->>'reason','unknown')) as raw_reason
      from jsonb_array_elements(coalesce(p_report#>'{recording,channels}','[]'::jsonb)) r
     where coalesce(r->>'channel','') <> ''
  ), normalized as (
    select c.id as camera_id,
           case
             when not c.is_configured then 'unknown'
             when raw.raw_state = 'recording' and v_recording_evidence <> 'archive_search' then 'unknown'
             when raw.raw_state in ('recording','not_recording','storage_fault','unknown') then raw.raw_state
             else 'unknown'
           end as current_state,
           case
             when not c.is_configured then 'channel_disabled'
             when raw.raw_state = 'recording' and v_recording_evidence <> 'archive_search' then 'unknown'
             when raw.raw_reason in ('ok','unknown','not_recording','storage_fault','channel_missing',
                                     'channel_disabled','nvr_unreachable','nvr_auth_failed',
                                     'agent_unreachable','disk_error','disk_full') then raw.raw_reason
             else 'unknown'
           end as current_reason,
           c.is_configured
      from raw
      join public.cameras c
        on c.site_id = p_site_id
       and c.tenant_id = p_tenant_id
       and c.recorder_id = p_recorder_id
       and c.channel = raw.channel
  ), up as (
    insert into public.camera_health as ch
      (camera_id, tenant_id, site_id,
       rec_current_state, rec_current_reason_code, rec_current_at,
       rec_current_evidence, updated_at)
    select n.camera_id, p_tenant_id, p_site_id,
           n.current_state, n.current_reason, v_now,
           case when n.is_configured then v_recording_evidence else 'inventory' end,
           v_now
      from normalized n
    on conflict (camera_id) do update
       set rec_current_state = excluded.rec_current_state,
           rec_current_reason_code = excluded.rec_current_reason_code,
           rec_current_at = excluded.rec_current_at,
           rec_current_evidence = excluded.rec_current_evidence,
           updated_at = v_now
    returning 1
  )
  select count(*) into v_count from up;

  update public.agents set last_seen_at = v_now where id = p_agent_id;

  return jsonb_build_object('ok', true,
                            'agent_id', p_agent_id,
                            'site_id', p_site_id,
                            'recorder_id', p_recorder_id,
                            'storage_state', v_storage_state,
                            'storage_reason', v_storage_reason,
                            'recording_evidence', v_recording_evidence,
                            'cameras_refreshed', v_count,
                            'server_time', v_now);
end
$function$;

revoke all on function public.wl_report_recorder_recording_storage_current_core(
  uuid,uuid,uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

create or replace function public.wl_report_recorder_recording_storage_current(
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
  v_result jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  perform public.wl_assert_current_agent_authority(
    v_agent.id,v_agent.site_id
  );

  v_result := public.wl_report_recorder_recording_storage_current_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,p_recorder_id,p_report
  );
  perform public.wl_reconcile_site_faults(v_agent.site_id);
  return v_result;
end
$function$;

revoke all on function public.wl_report_recorder_recording_storage_current(
  uuid,text,uuid,jsonb
) from public,anon,authenticated,service_role;
grant execute on function public.wl_report_recorder_recording_storage_current(
  uuid,text,uuid,jsonb
) to anon;

-- Deployed Agent signature (0089). Keeps its not-current-Agent answer, then
-- requires unambiguous recorder identity: on a multi-recorder site it fails
-- closed instead of applying one recorder's proof to every same-numbered
-- channel. nvr_health stays mirrored for the existing singleton readers.
create or replace function public.wl_report_recording_storage_current(
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
  v_current uuid;
  v_recorder_id uuid;
  v_result jsonb;
  v_now timestamptz := now();
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  v_current := public.wl_current_site_agent(v_agent.site_id);
  if v_current is distinct from v_agent.id then
    return jsonb_build_object('ok', false, 'reason', 'not_current_agent',
                              'current_agent_id', v_current);
  end if;

  v_recorder_id := public.wl_legacy_recorder_for_agent(v_agent.id);

  v_result := public.wl_report_recorder_recording_storage_current_core(
    v_agent.id,v_agent.tenant_id,v_agent.site_id,v_recorder_id,p_report
  );

  insert into public.nvr_health as nh
    (agent_id, tenant_id, site_id,
     sto_current_state, sto_current_reason_code, sto_current_at,
     sto_current_evidence, updated_at)
  values
    (v_agent.id, v_agent.tenant_id, v_agent.site_id,
     v_result->>'storage_state', v_result->>'storage_reason', v_now,
     'vendor_status', v_now)
  on conflict (agent_id) do update
     set sto_current_state = excluded.sto_current_state,
         sto_current_reason_code = excluded.sto_current_reason_code,
         sto_current_at = excluded.sto_current_at,
         sto_current_evidence = excluded.sto_current_evidence,
         updated_at = v_now;

  perform public.wl_reconcile_site_faults(v_agent.site_id);
  return v_result;
end
$function$;

revoke all on function public.wl_report_recording_storage_current(uuid,text,jsonb)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_report_recording_storage_current(uuid,text,jsonb)
  to anon,authenticated;

-- ---------------------------------------------------------------------
-- Recorder-scoped disk events.
-- A disk_error/disk_full event is about the recorder's storage, not a camera.
-- Agents mark such events payload.recorder_scoped (Dahua, Hikvision) or
-- payload.recorder_scope (ONVIF) and send no channel, so camera_id is NULL.
-- A disk event that resolved to no camera is recorder-scoped too.
-- ---------------------------------------------------------------------
create or replace function public.wl_event_is_recorder_scoped_disk(
  p_event_type text,
  p_camera_id uuid,
  p_payload jsonb
) returns boolean
language sql
immutable
set search_path = public
as $function$
  select coalesce(p_event_type,'') in ('disk_error','disk_full')
     and (
       p_camera_id is null
       or lower(coalesce(p_payload->>'recorder_scoped','')) = 'true'
       or lower(coalesce(p_payload->>'recorder_scope','')) = 'true'
     )
$function$;

revoke all on function public.wl_event_is_recorder_scoped_disk(text,uuid,jsonb)
  from public,anon,authenticated,service_role;

-- ---------------------------------------------------------------------
-- Recorder-aware operational faults (replaces the 0089 body).
--
-- 0089 derived every recorder fault, and the gate that lets camera faults
-- open, from one nvr_health(agent) row. The recorder RPCs write only
-- recorder_health, so on a multi-recorder site that row is missing (every
-- camera fault suppressed) or frozen at its last singleton value (a permanent
-- false fault, or nothing when Recorder B is unplugged). Now every configured
-- recorder is judged from its own recorder_health row of the current Agent:
--   * multi-recorder site: recorder faults are keyed 'nvr:'||recorder_id and
--     a camera's faults open only while its own recorder is observable;
--   * one-recorder site: the historical 'nvr:'||agent_id keys are kept, so
--     open faults and acknowledgements survive the deploy; recorder_health is
--     preferred and nvr_health is only the fallback for a recorder with no row;
--   * storage: the newest current proof (recorder_health, and nvr_health on a
--     one-recorder site), fresh within 15 minutes, as in 0089. A recorder-
--     scoped disk event from the current Agent is storage evidence for its
--     own recorder while both its device and receive times are inside that
--     window (an archive replay never counts). Only a newer conclusive
--     proof (ok/degraded/fault) supersedes it: a recorder whose storage
--     cannot be read sends 'unknown' proof every cycle, and that must not
--     cancel the only storage evidence it has. The event is fault evidence
--     only: it is not written to recorder_health, so a reader of
--     recorder_health.storage_state alone (0152 wl_my_site_recorders) does
--     not see it and has to consult the open 'storage' fault.
-- UNKNOWN never opens a fault; MISSING/DISABLED cameras stay inventory.
-- An offline camera's fault reason comes from camera_health.reason_code
-- (NEW-L4): only a video-loss signal is critical camera_offline/video_loss;
-- a probe timeout or any other cause is a 'camera_not_verified' warning.
-- The key 'camera:<id>:offline' is unchanged, and an open row follows the
-- current cause.
-- ---------------------------------------------------------------------
create or replace function public.wl_reconcile_site_faults(p_site_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_tenant uuid;
  v_now timestamptz := now();
  v_fresh_cut timestamptz := v_now - interval '15 minutes';
  v_current_agent uuid := public.wl_current_site_agent(p_site_id);
  v_configured int := 0;
  v_desired jsonb;
  v_opened int := 0;
  v_reclassified int := 0;
  v_resolved int := 0;
  v_open_total int := 0;
begin
  select tenant_id into v_tenant from public.sites where id = p_site_id;
  if v_tenant is null then
    return jsonb_build_object('ok', false, 'reason', 'unknown_site');
  end if;

  perform pg_advisory_xact_lock(hashtext('wl_reconcile_site_faults'), hashtext(p_site_id::text));

  select count(*) into v_configured
    from public.recorders r
   where r.site_id = p_site_id
     and r.tenant_id = v_tenant
     and r.is_configured;

  with agent_state as (
    select v_current_agent as agent_id,
           exists (select 1 from public.agent_unreachable_intervals aui
                    where aui.agent_id = v_current_agent
                      and aui.ended_at is null) as agent_down,
           (exists (select 1 from public.nvr_health nh
                     where nh.site_id = p_site_id
                       and nh.agent_id = v_current_agent)
            or exists (select 1 from public.recorder_health rh
                        where rh.site_id = p_site_id
                          and rh.agent_id = v_current_agent)) as reported
     where v_current_agent is not null
  ), legacy as (
    -- The Agent-wide row is attributable only while the site has at most
    -- one configured recorder.
    select nh.nvr_reachable, nh.nvr_auth_ok,
           nh.sto_current_state, nh.sto_current_at
      from public.nvr_health nh
     where nh.site_id = p_site_id
       and nh.agent_id = v_current_agent
       and v_configured <= 1
  ), base as (
    select r.id as recorder_id,
           case when v_configured = 1 then 'nvr:' || v_current_agent::text
                else 'nvr:' || r.id::text end as key_prefix,
           case when rh.recorder_id is not null then rh.nvr_reachable
                else (select l.nvr_reachable from legacy l) end as nvr_reachable,
           case when rh.recorder_id is not null then rh.nvr_auth_ok
                else (select l.nvr_auth_ok from legacy l) end as nvr_auth_ok,
           rh.sto_current_state as rh_storage_state,
           rh.sto_current_at as rh_storage_at
      from public.recorders r
      left join public.recorder_health rh
        on rh.recorder_id = r.id
       and rh.agent_id = v_current_agent
     where r.site_id = p_site_id
       and r.tenant_id = v_tenant
       and r.is_configured
       and v_current_agent is not null
    union all
    -- No configured recorder: the historical Agent-wide row is all there is.
    select null::uuid, 'nvr:' || v_current_agent::text,
           l.nvr_reachable, l.nvr_auth_ok, null::text, null::timestamptz
      from legacy l
     where v_configured = 0
  ), obs as (
    select s.recorder_id, s.key_prefix, s.nvr_reachable, s.nvr_auth_ok,
           case when s.use_event then s.ev_state else s.pr_state end as storage_state,
           case when s.use_event then s.ev_reason else s.pr_reason end as storage_reason,
           case when s.use_event then s.ev_at else s.pr_at end as storage_observed_at
      from (
        select b.recorder_id, b.key_prefix, b.nvr_reachable, b.nvr_auth_ok,
               pr.storage_state as pr_state, pr.storage_reason as pr_reason,
               pr.storage_observed_at as pr_at,
               ev.storage_state as ev_state, ev.storage_reason as ev_reason,
               ev.storage_observed_at as ev_at,
               -- A fresh disk event stands unless a newer (or same-time)
               -- conclusive proof supersedes it; 'unknown' proof never does.
               (ev.storage_observed_at is not null
                and (pr.storage_observed_at is null
                     or coalesce(pr.storage_state,'unknown') not in ('ok','degraded','fault')
                     or pr.storage_observed_at < ev.storage_observed_at)) as use_event
          from base b
          left join lateral (
            select x.storage_state, x.storage_reason, x.storage_observed_at
              from (
                select b.rh_storage_state as storage_state,
                       case when b.rh_storage_state = 'fault'
                            then 'storage_fault' else 'disk_full' end as storage_reason,
                       b.rh_storage_at as storage_observed_at
                 where b.rh_storage_at is not null
                union all
                select l.sto_current_state,
                       case when l.sto_current_state = 'fault'
                            then 'storage_fault' else 'disk_full' end,
                       l.sto_current_at
                  from legacy l
                 where l.sto_current_at is not null
              ) x
             order by x.storage_observed_at desc,
                      case when x.storage_state = 'fault' then 0 else 1 end
             limit 1
          ) pr on true
          left join lateral (
            select case when e.event_type = 'disk_error'
                        then 'fault' else 'degraded' end as storage_state,
                   e.event_type as storage_reason,
                   e.received_at as storage_observed_at
              from public.events e
             where b.recorder_id is not null
               and e.recorder_id = b.recorder_id
               and e.site_id = p_site_id
               and e.agent_id = v_current_agent
               and e.device_ts >= v_fresh_cut
               and e.received_at >= v_fresh_cut
               and public.wl_event_is_recorder_scoped_disk(e.event_type, e.camera_id, e.payload)
             order by e.received_at desc,
                      case when e.event_type = 'disk_error' then 0 else 1 end
             limit 1
          ) ev on true
      ) s
  ), desired as (
    select 'agent:' || a.agent_id::text || ':unreachable' as dedupe_key,
           'agent' as fault_domain, 'agent_unreachable' as fault_type,
           'critical' as severity, 'agent_unreachable' as reason_code,
           null::uuid as camera_id, a.agent_id
      from agent_state a where a.agent_down and a.reported
    union all
    select o.key_prefix || ':unreachable',
           'nvr_connectivity', 'nvr_unreachable', 'critical',
           'nvr_unreachable', null::uuid, a.agent_id
      from obs o cross join agent_state a
     where not a.agent_down and o.nvr_reachable is false
    union all
    select o.key_prefix || ':auth',
           'nvr_auth', 'nvr_auth_failed', 'critical',
           'nvr_auth_failed', null::uuid, a.agent_id
      from obs o cross join agent_state a
     where not a.agent_down and o.nvr_reachable is true and o.nvr_auth_ok is false
    union all
    select o.key_prefix || ':storage', 'storage',
           case when o.storage_state = 'fault' then 'storage_fault' else 'storage_degraded' end,
           case when o.storage_state = 'fault' then 'critical' else 'warning' end,
           o.storage_reason,
           null::uuid, a.agent_id
      from obs o cross join agent_state a
     where not a.agent_down
       and o.nvr_reachable is true
       and o.nvr_auth_ok is not false
       and o.storage_observed_at >= v_fresh_cut
       and o.storage_state in ('fault','degraded')
    union all
    -- The cause comes from camera_health.reason_code. Only a reported
    -- video-loss signal is a critical camera_offline/video_loss fault; a
    -- probe timeout (or any other cause) means WatchLog could not verify
    -- the camera, which is a warning, never video loss.
    select 'camera:' || ch.camera_id::text || ':offline',
           'camera',
           case when ch.reason_code::text = 'video_loss'
                then 'camera_offline' else 'camera_not_verified' end,
           case when ch.reason_code::text = 'video_loss'
                then 'critical' else 'warning' end,
           case when ch.reason_code::text in
                     ('video_loss','probe_timeout','stale_frame','tamper')
                then ch.reason_code::text else 'unknown' end,
           ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
      join obs o on (o.recorder_id = c.recorder_id or o.recorder_id is null)
      cross join agent_state a
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.health_state = 'offline'
       and coalesce(ci.inventory_state,'present') not in ('missing','disabled')
       and not a.agent_down and o.nvr_reachable is true and o.nvr_auth_ok is true
    union all
    select 'camera:' || ch.camera_id::text || ':recording',
           'recording',
           case when ch.rec_current_state = 'storage_fault'
                then 'recording_storage_fault' else 'not_recording' end,
           'warning',
           case when ch.rec_current_state = 'storage_fault'
                then 'storage_fault' else 'not_recording' end,
           ch.camera_id, null::uuid
      from public.camera_health ch
      left join public.camera_inventory ci on ci.camera_id = ch.camera_id
      join public.cameras c on c.id = ch.camera_id
      join obs o on (o.recorder_id = c.recorder_id or o.recorder_id is null)
      cross join agent_state a
     where ch.site_id = p_site_id
       and c.is_configured
       and ch.rec_current_at >= v_fresh_cut
       and ch.rec_current_state in ('not_recording','storage_fault')
       and coalesce(ci.inventory_state,'present') not in ('missing','disabled')
       and not a.agent_down and o.nvr_reachable is true and o.nvr_auth_ok is true
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'dedupe_key',dedupe_key,'fault_domain',fault_domain,
           'fault_type',fault_type,'severity',severity,'reason_code',reason_code,
           'camera_id',camera_id,'agent_id',agent_id)),'[]'::jsonb)
    into v_desired from desired;

  with d as (
    select * from jsonb_to_recordset(v_desired) as x(
      dedupe_key text, fault_domain text, fault_type text,
      severity text, reason_code text, camera_id uuid, agent_id uuid)
  ), ins as (
    insert into public.operational_faults
      (tenant_id,site_id,camera_id,agent_id,fault_domain,fault_type,
       severity,state,reason_code,dedupe_key,opened_at)
    select v_tenant,p_site_id,d.camera_id,d.agent_id,d.fault_domain,d.fault_type,
           d.severity,'open',d.reason_code,d.dedupe_key,v_now from d
    on conflict (dedupe_key) where state <> 'resolved' do nothing
    returning 1
  ) select count(*) into v_opened from ins;

  -- A camera's cause can change while its fault stays open (probe timeout
  -- then video loss, or the reverse). Keep the open row's classification
  -- current under the same key; state, acknowledgement and opened_at stay.
  with d as (
    select * from jsonb_to_recordset(v_desired) as x(
      dedupe_key text, fault_domain text, fault_type text,
      severity text, reason_code text)
  ), upd as (
    update public.operational_faults f
       set fault_type=d.fault_type, severity=d.severity,
           reason_code=d.reason_code
      from d
     where f.site_id=p_site_id and f.state<>'resolved'
       and d.fault_domain='camera'
       and f.dedupe_key=d.dedupe_key
       and (f.fault_type, f.severity, f.reason_code::text)
           is distinct from (d.fault_type, d.severity, d.reason_code)
    returning 1
  ) select count(*) into v_reclassified from upd;

  with res as (
    update public.operational_faults f
       set state='resolved', resolved_at=v_now
     where f.site_id=p_site_id and f.state<>'resolved'
       and not exists(select 1 from jsonb_to_recordset(v_desired) as x(dedupe_key text)
                      where x.dedupe_key=f.dedupe_key)
    returning 1
  ) select count(*) into v_resolved from res;

  select count(*) into v_open_total
    from public.operational_faults where site_id=p_site_id and state<>'resolved';

  return jsonb_build_object('ok',true,'site_id',p_site_id,
                            'current_agent_id',v_current_agent,
                            'configured_recorders',v_configured,
                            'evaluated_at',v_now,'opened',v_opened,
                            'reclassified',v_reclassified,
                            'resolved',v_resolved,'open_total',v_open_total);
end
$function$;

revoke all on function public.wl_reconcile_site_faults(uuid)
  from public,anon,authenticated;

-- The cron sweep also visits sites whose only health rows are recorder_health
-- (a multi-recorder Agent that has not reported a camera yet).
create or replace function public.wl_sweep_faults()
returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  r record;
  res jsonb;
  v_sites int := 0; v_opened int := 0; v_resolved int := 0;
begin
  perform pg_advisory_xact_lock(hashtext('wl_sweep_faults'));
  for r in select distinct site_id from (
             select site_id from public.nvr_health
             union
             select site_id from public.camera_health
             union
             select site_id from public.recorder_health) s loop
    res := public.wl_reconcile_site_faults(r.site_id);
    v_sites := v_sites + 1;
    v_opened := v_opened + coalesce((res->>'opened')::int, 0);
    v_resolved := v_resolved + coalesce((res->>'resolved')::int, 0);
  end loop;
  return jsonb_build_object('ok', true, 'evaluated_at', now(),
    'sites', v_sites, 'opened', v_opened, 'resolved', v_resolved);
end
$function$;

revoke all on function public.wl_sweep_faults() from public,anon,authenticated;

-- ---------------------------------------------------------------------
-- Intelligence pipeline (0065): recorder-scoped disk events are evidence
-- for the recorder's storage fault (above), never camera activity. 0065
-- turned a camera-less disk_error/disk_full into a 'camera_fault' activity
-- with no camera, and wl_derive_episodes then grouped it into a 'presence'
-- episode. Activities
-- already derived from such events are skipped when episodes are rebuilt.
-- Everything else is the 0065 body unchanged.
-- ---------------------------------------------------------------------
create or replace function public.wl_derive_activities(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $function$
declare v_rows integer;
begin
  insert into activities (tenant_id, site_id, camera_id, activity_type, object_class, semantic,
                          occurred_at, source_event_id, metadata_json)
  select s.tenant_id, e.site_id, e.camera_id,
         case when e.event_type in ('video_loss','tamper','disk_error','disk_full') then 'camera_fault'
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
  on conflict (source_event_id, activity_type) do nothing;
  get diagnostics v_rows = row_count;
  return v_rows;
end $function$;
revoke all on function public.wl_derive_activities(uuid,timestamptz,timestamptz) from public, anon;
grant execute on function public.wl_derive_activities(uuid,timestamptz,timestamptz) to service_role;

create or replace function public.wl_derive_episodes(
  p_site_id uuid, p_from timestamptz, p_to timestamptz, p_gap_seconds integer default 600
) returns integer
language plpgsql security definer set search_path = public as $function$
declare v_rows integer;
begin
  delete from episodes e
   where e.site_id = p_site_id and e.started_at >= p_from and e.started_at < p_to;
  with a as (
    select ac.id, ac.tenant_id, ac.site_id, ac.camera_id, ac.object_class, ac.occurred_at,
           case when ac.object_class in ('video_loss','tamper') then 'video_loss'
                else 'presence' end as etype
      from activities ac
      left join events ev on ev.id = ac.source_event_id
     where ac.site_id = p_site_id and ac.occurred_at >= p_from and ac.occurred_at < p_to
       and not public.wl_event_is_recorder_scoped_disk(ac.object_class, ac.camera_id, ev.payload)
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
revoke all on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) from public, anon;
grant execute on function public.wl_derive_episodes(uuid,timestamptz,timestamptz,integer) to service_role;

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
  v_configured_channels jsonb;
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

  -- Deployed 5.0.x Agents open legacy intervals with no cameras: a whole-site
  -- gap. On a one-recorder site that gap covers every configured camera of
  -- the recorder (an unconfigured slot has no camera to recover, 0085), so
  -- such a row is handed out with those channels. A recorder-aware Agent
  -- closes a claim without channels as unrecoverable without reading the
  -- archive, so while the recorder has no configured camera a camera-less
  -- legacy row is not claimed here at all: it stays pending for a later
  -- claim (or a legacy Agent) instead of getting a false final status.
  select jsonb_agg(cm.channel order by cm.channel)
    into v_configured_channels
    from public.cameras cm
   where cm.tenant_id=v_agent.tenant_id
     and cm.site_id=v_agent.site_id
     and cm.recorder_id=p_recorder_id
     and cm.is_configured;

  with due as (
    select id
      from public.recovery_intervals
     where (
         recorder_id=p_recorder_id
         or (
           v_singleton and recorder_id is null
           and (
             coalesce(cardinality(cameras),0)>0
             or v_configured_channels is not null
           )
         )
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
        'channels',case
          when claimed.recorder_id is null
               and coalesce(cardinality(claimed.cameras),0)=0
            then coalesce(v_configured_channels,'[]'::jsonb)
          else coalesce((
            select jsonb_agg(cm.channel order by cm.channel)
              from public.cameras cm
             where cm.id=any(claimed.cameras)
          ),'[]'::jsonb)
        end,
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
  v_single uuid;
begin
  -- A site with exactly one configured recorder keeps the legacy wall-clock
  -- contract, and the recorder RPCs treat that recorder's intervals and the
  -- legacy NULL-recorder intervals as the same work (v_singleton). A
  -- recorder-aware Agent bound to that recorder stores its recovery with the
  -- recorder id, so count it here exactly like a NULL-recorder interval.
  select (array_agg(r.id))[1]
    into v_single
    from public.recorders r
   where r.site_id=p_site_id
     and r.is_configured
  having count(*)=1;

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
      and (ri.recorder_id is null or ri.recorder_id=v_single)
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

-- Keep 0103's ACL: this body has no tenant check (the base report it wraps
-- has none either), so it stays service_role-only. Browser roles get it only
-- in the migration that adds wl_assert_my_site.
revoke all on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated;
grant execute on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) to service_role;

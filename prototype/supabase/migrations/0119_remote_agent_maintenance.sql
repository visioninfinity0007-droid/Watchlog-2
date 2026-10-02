-- 0119_remote_agent_maintenance.sql
-- Secure outbound remote-update control plane.
--
-- Cloud operators may request "apply latest signed release" only. The cloud does
-- not send shell text or arbitrary package URLs to the site. The agent selects
-- the package from its locally configured signed HTTPS manifest.

create table if not exists public.agent_update_requests (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  requested_by uuid null references auth.users(id) on delete set null,
  requested_reason text not null default 'platform maintenance',
  status text not null default 'pending'
    check (status in ('pending','claimed','staged','succeeded','failed','cancelled','expired')),
  claimed_by_agent_id uuid null references public.agents(id) on delete set null,
  current_version text null,
  target_version text null,
  package_sha256 text null,
  detail text null,
  requested_at timestamptz not null default now(),
  claimed_at timestamptz null,
  staged_at timestamptz null,
  completed_at timestamptz null,
  expires_at timestamptz not null default (now() + interval '24 hours')
);

create index if not exists agent_update_requests_site_status_idx
  on public.agent_update_requests(site_id,status,requested_at);

alter table public.agent_update_requests enable row level security;
revoke all on table public.agent_update_requests from anon, authenticated;

create or replace function public.wl_known_capabilities()
returns text[] language sql immutable
set search_path = public
as $$
  select array[
    'operations_runtime',
    'operations_extended_primitives',
    'operations_evidence_still',
    'operations_evidence_clip',
    'archive_processing',
    'multi_agent_fencing',
    'recorder_probe_v2',
    'site_control_runtime',
    'remote_update_v1'
  ]
$$;

create or replace function public.wl_agent_semver_triplet(p_version text)
returns int[]
language plpgsql
immutable
set search_path = public
as $$
declare
  m text[];
begin
  m := regexp_match(coalesce(p_version,''), '^([0-9]+)\\.([0-9]+)\\.([0-9]+)');
  if m is null then return array[0,0,0]; end if;
  return array[m[1]::int,m[2]::int,m[3]::int];
end
$$;

create or replace function public.wl_agent_report_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_clean jsonb;
  v_version int[];
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  v_version := public.wl_agent_semver_triplet(v_agent.agent_version);

  select coalesce(jsonb_agg(distinct c), '[]'::jsonb)
    into v_clean
    from jsonb_array_elements_text(coalesce(p_capabilities,'[]'::jsonb)) c
   where c = any(public.wl_known_capabilities())
     and (
       c not in ('site_control_runtime','remote_update_v1')
       or v_version >= array[5,0,22]
     );

  update public.agents
     set capabilities=v_clean,
         capabilities_reported_at=now(),
         last_seen_at=now()
   where id=v_agent.id;

  return jsonb_build_object('ok',true,'capabilities',v_clean);
end
$$;

create or replace function public.wl_platform_request_agent_update(
  p_site_id uuid,
  p_reason text default 'platform maintenance'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_role text := wl_platform_require(array['platform_owner','platform_admin']);
  v_site public.sites;
  v_agent public.agents;
  v_id uuid;
begin
  select * into v_site from public.sites where id=p_site_id;
  if v_site.id is null then
    raise exception 'site not found' using errcode='22023';
  end if;

  select * into v_agent
    from public.agents
   where id = public.wl_current_site_agent(p_site_id);

  if v_agent.id is null then
    raise exception 'site has no current agent' using errcode='42501';
  end if;
  if not (coalesce(v_agent.capabilities,'[]'::jsonb) ? 'remote_update_v1') then
    raise exception 'current agent does not advertise secure remote update'
      using errcode='42501';
  end if;

  update public.agent_update_requests
     set status='expired', completed_at=now(),
         detail=coalesce(detail,'superseded by a newer platform update request')
   where site_id=p_site_id
     and status in ('pending','claimed')
     and expires_at<=now();

  if exists (
    select 1 from public.agent_update_requests
     where site_id=p_site_id and status in ('pending','claimed','staged')
       and expires_at>now()
  ) then
    raise exception 'an update request is already active for this site'
      using errcode='23505';
  end if;

  insert into public.agent_update_requests(
    tenant_id,site_id,requested_by,requested_reason,status,current_version
  ) values (
    v_site.tenant_id,v_site.id,auth.uid(),
    left(coalesce(nullif(btrim(p_reason),''),'platform maintenance'),500),
    'pending',v_agent.agent_version
  ) returning id into v_id;

  return jsonb_build_object(
    'request_id',v_id,'site_id',p_site_id,'status','pending',
    'current_version',v_agent.agent_version,'role',v_role
  );
end
$$;

create or replace function public.wl_agent_claim_update_request(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_current uuid;
  v_req public.agent_update_requests;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  v_current := public.wl_current_site_agent(v_agent.site_id);
  if v_current is distinct from v_agent.id then
    return null;
  end if;

  update public.agent_update_requests
     set status='expired', completed_at=now(),
         detail=coalesce(detail,'update request expired before claim')
   where site_id=v_agent.site_id
     and status='pending'
     and expires_at<=now();

  update public.agent_update_requests r
     set status='claimed',
         claimed_at=now(),
         claimed_by_agent_id=v_agent.id,
         current_version=coalesce(v_agent.agent_version,r.current_version)
   where r.id = (
     select q.id
       from public.agent_update_requests q
      where q.site_id=v_agent.site_id
        and q.tenant_id=v_agent.tenant_id
        and q.status='pending'
        and q.expires_at>now()
      order by q.requested_at
      for update skip locked
      limit 1
   )
  returning * into v_req;

  if v_req.id is null then return null; end if;

  return jsonb_build_object(
    'request_id',v_req.id,
    'reason',v_req.requested_reason,
    'requested_at',v_req.requested_at,
    'expires_at',v_req.expires_at
  );
end
$$;

create or replace function public.wl_agent_stage_update_request(
  p_agent_id uuid,
  p_agent_key text,
  p_request_id uuid,
  p_target_version text,
  p_sha256 text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_req public.agent_update_requests;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;
  if p_target_version is null or length(btrim(p_target_version))<1 then
    raise exception 'target version required' using errcode='22023';
  end if;
  if coalesce(p_sha256,'') !~ '^[0-9A-Fa-f]{64}$' then
    raise exception 'valid package sha256 required' using errcode='22023';
  end if;

  update public.agent_update_requests
     set status='staged',
         staged_at=now(),
         target_version=left(btrim(p_target_version),80),
         package_sha256=lower(p_sha256),
         detail='signed package staged locally; awaiting launcher handoff'
   where id=p_request_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id
     and status='claimed'
  returning * into v_req;

  if v_req.id is null then
    raise exception 'update request is not claimable by this agent' using errcode='42501';
  end if;
  return jsonb_build_object('ok',true,'request_id',v_req.id,'status',v_req.status);
end
$$;

create or replace function public.wl_agent_complete_update_request(
  p_agent_id uuid,
  p_agent_key text,
  p_request_id uuid,
  p_ok boolean,
  p_detail text default null,
  p_applied_version text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_agent public.agents;
  v_req public.agent_update_requests;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  update public.agent_update_requests
     set status=case when coalesce(p_ok,false) then 'succeeded' else 'failed' end,
         completed_at=now(),
         detail=left(coalesce(nullif(btrim(p_detail),''),
                    case when coalesce(p_ok,false) then 'update completed' else 'update failed' end),500),
         target_version=coalesce(nullif(btrim(p_applied_version),''),target_version)
   where id=p_request_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id
     and claimed_by_agent_id=v_agent.id
     and status in ('claimed','staged')
  returning * into v_req;

  if v_req.id is null then
    raise exception 'update request is not completable by this agent' using errcode='42501';
  end if;

  return jsonb_build_object(
    'ok',true,'request_id',v_req.id,'status',v_req.status,
    'target_version',v_req.target_version
  );
end
$$;

create or replace function public.wl_platform_agent_update_status(
  p_request_id uuid
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
  v_req public.agent_update_requests;
begin
  select * into v_req from public.agent_update_requests where id=p_request_id;
  if v_req.id is null then
    raise exception 'update request not found' using errcode='42704';
  end if;
  return jsonb_build_object(
    'request_id',v_req.id,'tenant_id',v_req.tenant_id,'site_id',v_req.site_id,
    'status',v_req.status,'current_version',v_req.current_version,
    'target_version',v_req.target_version,'detail',v_req.detail,
    'requested_at',v_req.requested_at,'claimed_at',v_req.claimed_at,
    'staged_at',v_req.staged_at,'completed_at',v_req.completed_at,
    'expires_at',v_req.expires_at,'role',v_role
  );
end
$$;

revoke all on function public.wl_platform_request_agent_update(uuid,text) from public,anon;
revoke all on function public.wl_platform_agent_update_status(uuid) from public,anon;
grant execute on function public.wl_platform_request_agent_update(uuid,text) to authenticated;
grant execute on function public.wl_platform_agent_update_status(uuid) to authenticated;

revoke all on function public.wl_agent_claim_update_request(uuid,text) from public,anon,authenticated;
revoke all on function public.wl_agent_stage_update_request(uuid,text,uuid,text,text) from public,anon,authenticated;
revoke all on function public.wl_agent_complete_update_request(uuid,text,uuid,boolean,text,text) from public,anon,authenticated;
grant execute on function public.wl_agent_claim_update_request(uuid,text) to anon;
grant execute on function public.wl_agent_stage_update_request(uuid,text,uuid,text,text) to anon;
grant execute on function public.wl_agent_complete_update_request(uuid,text,uuid,boolean,text,text) to anon;

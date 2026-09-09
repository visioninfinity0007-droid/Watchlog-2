-- ===================================================================
-- 0058 — Operations incident evidence: bounded stills + rule-authorized footage.
--
-- Evidence actions were runtime-wired but truthfully UNSUPPORTED because no bounded transport
-- existed. This closes that WITHOUT giving the agent any freedom to request arbitrary footage:
-- the SERVER stays authoritative over what evidence is permitted. When an incident fires with a
-- capture_still / request_footage action, the emitter creates a server-authorized evidence task
-- bound to THAT incident + camera + timestamp; the agent may only CLAIM authorized work for its
-- own site and upload the result. It can never choose a tenant/site/camera/time itself.
--
--   capture_still   -> a bounded single still (<= 3 MiB), stored with provenance + sha256 + camera
--                      + timestamp linkage. New task type (a still is not a chunked clip).
--   request_footage -> REUSES the 0040/0041 bounded clip transport (claim/upload/complete/fail),
--                      now linkable to an operations incident. No second clip-upload architecture.
--
-- Idempotent: incident cooldown returns the live incident (no new evidence); one active evidence
-- task per incident; a duplicate upload cannot create duplicate evidence; a failed task can be
-- retried safely (bounded attempts); expired work is not resurrected.
-- ===================================================================

-- ---------------------------------------------------------------------
-- A. Bounded incident STILL evidence.
-- ---------------------------------------------------------------------
create table if not exists public.operations_incident_evidence (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           uuid not null references public.tenants(id) on delete cascade,
  site_id             uuid not null references public.sites(id) on delete cascade,
  incident_id         bigint not null references public.operations_incidents(id) on delete cascade,
  camera_id           uuid references public.cameras(id) on delete set null,
  purpose             text,
  occurred_at         timestamptz not null,                 -- the incident moment to capture
  status              text not null default 'pending'
                        check (status in ('pending','processing','ready','unsupported','failed','expired')),
  claimed_by_agent_id uuid references public.agents(id) on delete set null,
  claim_expires_at    timestamptz,
  attempts            int not null default 0,
  image               bytea,
  content_type        text check (content_type is null or content_type in ('image/jpeg','image/png')),
  sha256              text check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  byte_size           int  check (byte_size is null or (byte_size > 0 and byte_size <= 3145728)),
  captured_at         timestamptz,
  provenance          text not null default 'Captured at the incident by the site agent',
  error_message       text,
  requested_at        timestamptz not null default now(),
  completed_at        timestamptz,
  expires_at          timestamptz not null default now() + interval '7 days'
);
create index if not exists operations_incident_evidence_incident_idx
  on public.operations_incident_evidence(incident_id);
create index if not exists operations_incident_evidence_pending_site_idx
  on public.operations_incident_evidence(site_id, requested_at)
  where status in ('pending','processing');
-- at most ONE active-or-ready still per incident (idempotent across retries / incident re-fires)
create unique index if not exists operations_incident_evidence_one_per_incident_idx
  on public.operations_incident_evidence(incident_id)
  where status in ('pending','processing','ready');

alter table public.operations_incident_evidence enable row level security;
revoke all on table public.operations_incident_evidence from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- B. Extend the 0040 clip transport so a bounded clip can belong to an operations incident.
--    event_id becomes optional; exactly one of (event_id, operations_incident_id) is set.
-- ---------------------------------------------------------------------
alter table public.incident_clip_requests alter column event_id drop not null;
alter table public.incident_clip_requests alter column requested_by drop not null;
alter table public.incident_clip_requests
  add column if not exists operations_incident_id bigint references public.operations_incidents(id) on delete cascade;
alter table public.incident_clip_requests
  add column if not exists source text not null default 'user' check (source in ('user','rule'));
alter table public.incident_clip_requests drop constraint if exists incident_clip_link_chk;
alter table public.incident_clip_requests add constraint incident_clip_link_chk check (
  (event_id is not null)::int + (operations_incident_id is not null)::int = 1);
create unique index if not exists incident_clip_one_active_per_incident_idx
  on public.incident_clip_requests(operations_incident_id)
  where operations_incident_id is not null and status in ('pending','processing');

-- ---------------------------------------------------------------------
-- C. Server-side creators (INTERNAL — no grant; only the definer-owned emitter calls them).
--    The agent NEVER creates evidence work; it only fulfils what the server authorised.
-- ---------------------------------------------------------------------
create or replace function public.wl_create_incident_still_request(
  p_incident_id bigint, p_camera_id uuid, p_occurred_at timestamptz, p_purpose text default null
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_inc public.operations_incidents; v_id uuid;
begin
  select * into v_inc from public.operations_incidents where id = p_incident_id;
  if v_inc.id is null or p_camera_id is null then return null; end if;   -- nothing to capture
  insert into public.operations_incident_evidence(tenant_id, site_id, incident_id, camera_id, purpose, occurred_at)
  values (v_inc.tenant_id, v_inc.site_id, v_inc.id, p_camera_id, nullif(p_purpose,''), coalesce(p_occurred_at, v_inc.occurred_at))
  on conflict do nothing                                                -- idempotent: one active per incident
  returning id into v_id;
  return v_id;
end $$;
revoke all on function public.wl_create_incident_still_request(bigint,uuid,timestamptz,text) from public, anon, authenticated;

create or replace function public.wl_create_incident_clip_request(
  p_incident_id bigint, p_camera_id uuid, p_occurred_at timestamptz,
  p_pre_seconds int default 10, p_post_seconds int default 20
) returns uuid language plpgsql security definer set search_path = public as $$
declare v_inc public.operations_incidents; v_pre int := least(greatest(coalesce(p_pre_seconds,10),0),30);
        v_post int := least(greatest(coalesce(p_post_seconds,20),1),30); v_id uuid; v_at timestamptz;
begin
  select * into v_inc from public.operations_incidents where id = p_incident_id;
  if v_inc.id is null or p_camera_id is null then return null; end if;
  v_at := coalesce(p_occurred_at, v_inc.occurred_at);
  insert into public.incident_clip_requests(tenant_id, operations_incident_id, site_id, camera_id, source,
    start_at, end_at)
  values (v_inc.tenant_id, v_inc.id, v_inc.site_id, p_camera_id, 'rule',
    v_at - make_interval(secs => v_pre), v_at + make_interval(secs => v_post))
  on conflict do nothing
  returning id into v_id;
  return v_id;
end $$;
revoke all on function public.wl_create_incident_clip_request(bigint,uuid,timestamptz,int,int) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- D. Agent stills transport — claim / upload / fail. Site-scoped agent auth. The agent is handed
--    the camera + timestamp by the server; it never selects them.
-- ---------------------------------------------------------------------
create or replace function public.wl_agent_claim_incident_stills(
  p_agent_id uuid, p_agent_key text, p_limit int default 1
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_limit int := least(greatest(coalesce(p_limit,1),1),3); v_result jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;

  update public.operations_incident_evidence
     set status = 'expired'
   where tenant_id = v_agent.tenant_id and site_id = v_agent.site_id
     and expires_at <= now() and status in ('pending','processing','ready');

  with picked as (
    select e.id from public.operations_incident_evidence e
     where e.site_id = v_agent.site_id and e.tenant_id = v_agent.tenant_id and e.expires_at > now()
       and (e.status = 'pending' or (e.status = 'processing' and coalesce(e.claim_expires_at, now()) <= now()))
     order by e.requested_at
     for update skip locked
     limit v_limit
  ), claimed as (
    update public.operations_incident_evidence e
       set status = 'processing', claimed_by_agent_id = v_agent.id,
           claim_expires_at = now() + interval '5 minutes', attempts = e.attempts + 1, error_message = null
      from picked p where e.id = p.id
    returning e.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'request_id', e.id, 'incident_id', e.incident_id, 'camera_id', e.camera_id,
    'channel', c.channel, 'occurred_at', e.occurred_at, 'purpose', e.purpose
  ) order by e.requested_at), '[]'::jsonb) into v_result
    from claimed e join public.cameras c on c.id = e.camera_id and c.site_id = v_agent.site_id;
  return v_result;
end $$;
revoke all on function public.wl_agent_claim_incident_stills(uuid,text,int) from public;
grant execute on function public.wl_agent_claim_incident_stills(uuid,text,int) to anon, authenticated;

create or replace function public.wl_agent_upload_incident_still(
  p_agent_id uuid, p_agent_key text, p_request_id uuid, p_image_b64 text,
  p_content_type text, p_sha256 text, p_captured_at timestamptz default now()
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_req public.operations_incident_evidence; v_data bytea;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  select * into v_req from public.operations_incident_evidence
   where id = p_request_id and tenant_id = v_agent.tenant_id and site_id = v_agent.site_id;
  if v_req.id is null then raise exception 'evidence request not for this agent site' using errcode = '42501'; end if;
  if v_req.status = 'ready' then                       -- idempotent: a repeat upload returns the stored one
    return jsonb_build_object('ok', true, 'status', 'ready', 'bytes', v_req.byte_size, 'duplicate', true);
  end if;
  if v_req.claimed_by_agent_id <> v_agent.id or v_req.status <> 'processing' then
    raise exception 'evidence request not claimed by this agent' using errcode = '42501';
  end if;
  if p_content_type not in ('image/jpeg','image/png') then raise exception 'unsupported still content type'; end if;
  if p_sha256 !~ '^[0-9a-f]{64}$' then raise exception 'invalid still checksum'; end if;
  begin v_data := decode(p_image_b64, 'base64');
  exception when others then raise exception 'invalid still encoding'; end;
  if octet_length(v_data) < 1 or octet_length(v_data) > 3145728 then raise exception 'still exceeds the 3 MiB limit'; end if;
  if encode(sha256(v_data), 'hex') <> lower(p_sha256) then raise exception 'still checksum mismatch'; end if;

  update public.operations_incident_evidence
     set status = 'ready', image = v_data, content_type = p_content_type, sha256 = lower(p_sha256),
         byte_size = octet_length(v_data), captured_at = coalesce(p_captured_at, now()),
         completed_at = now(), error_message = null
   where id = p_request_id;
  return jsonb_build_object('ok', true, 'status', 'ready', 'bytes', octet_length(v_data));
end $$;
revoke all on function public.wl_agent_upload_incident_still(uuid,text,uuid,text,text,text,timestamptz) from public;
grant execute on function public.wl_agent_upload_incident_still(uuid,text,uuid,text,text,text,timestamptz) to anon, authenticated;

create or replace function public.wl_agent_fail_incident_still(
  p_agent_id uuid, p_agent_key text, p_request_id uuid, p_reason text, p_unsupported boolean default false
) returns jsonb language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_req public.operations_incident_evidence; v_status text;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode = '28000'; end if;
  select * into v_req from public.operations_incident_evidence
   where id = p_request_id and tenant_id = v_agent.tenant_id and site_id = v_agent.site_id
     and claimed_by_agent_id = v_agent.id and status = 'processing';
  if v_req.id is null then raise exception 'evidence request not claimed by this agent' using errcode = '42501'; end if;
  -- truthful: unsupported is terminal; a transient failure is retriable until the attempt budget runs out
  if p_unsupported then v_status := 'unsupported';
  elsif v_req.attempts >= 3 then v_status := 'failed';
  else v_status := 'pending'; end if;
  update public.operations_incident_evidence
     set status = v_status, claim_expires_at = null, claimed_by_agent_id = null,
         error_message = left(coalesce(p_reason,'evidence capture failed'), 300),
         completed_at = case when v_status in ('unsupported','failed') then now() else null end
   where id = p_request_id;
  return jsonb_build_object('ok', true, 'status', v_status);
end $$;
revoke all on function public.wl_agent_fail_incident_still(uuid,text,uuid,text,boolean) from public;
grant execute on function public.wl_agent_fail_incident_still(uuid,text,uuid,text,boolean) to anon, authenticated;

-- ---------------------------------------------------------------------
-- E. Customer read — evidence for an operations incident (RBAC, tenant-scoped). Never a blank
--    placeholder: each row carries its true status. Media bytes only for a READY still via the
--    dedicated image RPC (kept out of the list payload).
-- ---------------------------------------------------------------------
create or replace function public.wl_operations_incident_evidence(p_incident_id bigint)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_inc public.operations_incidents;
begin
  if v_tenant is null then raise exception 'not signed in' using errcode = '42501'; end if;
  select * into v_inc from public.operations_incidents where id = p_incident_id and tenant_id = v_tenant;
  if v_inc.id is null then raise exception 'incident not in your account' using errcode = '42501'; end if;
  return jsonb_build_object(
    'incident_id', v_inc.id,
    'stills', coalesce((select jsonb_agg(jsonb_build_object(
        'id', e.id, 'status', e.status, 'purpose', e.purpose, 'occurred_at', e.occurred_at,
        'camera_id', e.camera_id, 'captured_at', e.captured_at, 'sha256', e.sha256,
        'byte_size', e.byte_size, 'content_type', e.content_type, 'provenance', e.provenance,
        'has_image', (e.status = 'ready' and e.image is not null), 'error', e.error_message,
        'expires_at', e.expires_at) order by e.requested_at)
      from public.operations_incident_evidence e where e.incident_id = v_inc.id), '[]'::jsonb),
    'clips', coalesce((select jsonb_agg(jsonb_build_object(
        'request_id', r.id, 'status', r.status, 'start_at', r.start_at, 'end_at', r.end_at,
        'bytes', r.bytes, 'sha256', r.sha256, 'content_type', r.content_type, 'source', r.source,
        'file_extension', r.file_extension, 'error', r.error_message, 'expires_at', r.expires_at,
        'chunks', (select count(*) from public.incident_clip_chunks ch where ch.request_id = r.id)) order by r.requested_at)
      from public.incident_clip_requests r where r.operations_incident_id = v_inc.id), '[]'::jsonb)
  );
end $$;
revoke all on function public.wl_operations_incident_evidence(bigint) from public, anon;
grant execute on function public.wl_operations_incident_evidence(bigint) to authenticated;

create or replace function public.wl_operations_incident_still_image(p_still_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_row public.operations_incident_evidence;
begin
  if v_tenant is null then raise exception 'not signed in' using errcode = '42501'; end if;
  select * into v_row from public.operations_incident_evidence where id = p_still_id and tenant_id = v_tenant;
  if v_row.id is null then raise exception 'still not in your account' using errcode = '42501'; end if;
  if v_row.status <> 'ready' or v_row.image is null then
    return jsonb_build_object('status', v_row.status, 'image_b64', null);
  end if;
  return jsonb_build_object('status', 'ready', 'content_type', v_row.content_type,
    'captured_at', v_row.captured_at, 'sha256', v_row.sha256, 'provenance', v_row.provenance,
    'image_b64', encode(v_row.image, 'base64'));
end $$;
revoke all on function public.wl_operations_incident_still_image(uuid) from public, anon;
grant execute on function public.wl_operations_incident_still_image(uuid) to authenticated;

-- ---------------------------------------------------------------------
-- F. Retention prune (evidence bytes are short-lived; the incident record is permanent).
-- ---------------------------------------------------------------------
create or replace function public.wl_prune_incident_evidence(p_keep_hours int default 168)
returns int language plpgsql security definer set search_path = public as $$
declare v_n int;
begin
  update public.operations_incident_evidence set status = 'expired', image = null
   where expires_at <= now() and status in ('pending','processing','ready');
  delete from public.operations_incident_evidence
   where completed_at is not null and completed_at < now() - make_interval(hours => greatest(p_keep_hours,1));
  get diagnostics v_n = row_count;
  return v_n;
end $$;
revoke all on function public.wl_prune_incident_evidence(int) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- G. Wire evidence creation into the emitter: when a NEW incident is inserted and its configured
--    actions are recorded, create the SERVER-authorized evidence tasks. Cooldown-returned
--    incidents take the early-return path above and create no new evidence (idempotent).
--    (Full re-create of the 0049 emitter with the two evidence hooks in the actions loop.)
-- ---------------------------------------------------------------------
create or replace function public.wl_emit_operations_incident(
  p_rule_id uuid, p_camera_id uuid default null, p_object_class text default null,
  p_confidence numeric default null, p_occurred_at timestamptz default now(),
  p_dedupe text default null, p_detail jsonb default '{}'::jsonb
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_rule public.monitoring_rules; v_incident public.operations_incidents; v_existing public.operations_incidents;
  v_dedupe text; v_status text; v_sev text; v_atype text; v_action jsonb;
begin
  select * into v_rule from public.monitoring_rules where id = p_rule_id;
  if v_rule.id is null then raise exception 'monitoring rule % not found', p_rule_id using errcode = '42704'; end if;
  if not v_rule.enabled then return null; end if;
  if v_rule.confidence_min is not null and p_confidence is not null and p_confidence < v_rule.confidence_min then
    return null;
  end if;

  v_dedupe := coalesce(p_dedupe,
    'rule:' || p_rule_id::text || ':cam:' || coalesce(p_camera_id::text,'-') || ':' || coalesce(p_object_class,'-'));

  if coalesce(v_rule.cooldown_seconds,0) > 0 then
    select * into v_existing from public.operations_incidents
      where dedupe_key = v_dedupe and status in ('candidate','open','acknowledged')
        and opened_at > now() - make_interval(secs => v_rule.cooldown_seconds)
      order by opened_at desc limit 1;
    if v_existing.id is not null then return to_jsonb(v_existing); end if;
  end if;

  v_sev := case v_rule.severity when 'incident' then 'critical' when 'attention' then 'attention' else 'info' end;
  v_status := case when v_rule.sensitive or v_rule.review_required then 'candidate' else 'open' end;

  insert into public.operations_incidents (
    tenant_id, site_id, camera_id, agent_id, rule_id, rule_version, incident_type,
    object_class, severity, status, review_required, sensitive, occurred_at, dedupe_key, detail)
  values (
    v_rule.tenant_id, v_rule.site_id, coalesce(p_camera_id, v_rule.camera_id), null,
    v_rule.id, v_rule.rule_version, v_rule.rule_type, p_object_class, v_sev, v_status,
    (v_rule.review_required or v_rule.sensitive), v_rule.sensitive,
    p_occurred_at, v_dedupe, coalesce(p_detail,'{}'::jsonb))
  on conflict (dedupe_key) where status in ('candidate','open','acknowledged') do nothing
  returning * into v_incident;

  if v_incident.id is null then
    select * into v_incident from public.operations_incidents
      where dedupe_key = v_dedupe and status in ('candidate','open','acknowledged')
      order by opened_at desc limit 1;
    return to_jsonb(v_incident);
  end if;

  insert into public.operations_incident_actions (incident_id, tenant_id, action_type, actor, detail)
  values (v_incident.id, v_rule.tenant_id, 'create_incident', 'engine',
          jsonb_build_object('rule_version', v_rule.rule_version, 'confidence', p_confidence));

  for v_action in select * from jsonb_array_elements(coalesce(v_rule.actions,'[]'::jsonb)) loop
    v_atype := v_action->>'type';
    if v_atype in ('capture_still','request_footage','mark_review','include_in_report','escalate_severity','notify') then
      insert into public.operations_incident_actions (incident_id, tenant_id, action_type, actor, detail)
      values (v_incident.id, v_rule.tenant_id, v_atype, 'engine', v_action);
      -- server-authorized bounded evidence task, bound to THIS incident/camera/time
      if v_atype = 'capture_still' then
        perform public.wl_create_incident_still_request(
          v_incident.id, v_incident.camera_id, v_incident.occurred_at, v_action->>'purpose');
      elsif v_atype = 'request_footage' then
        perform public.wl_create_incident_clip_request(
          v_incident.id, v_incident.camera_id, v_incident.occurred_at,
          coalesce((v_action->>'pre_seconds')::int, 10), coalesce((v_action->>'post_seconds')::int, 20));
      end if;
    end if;
  end loop;

  return to_jsonb(v_incident);
end $$;

-- ===================================================================
-- 0049 — Operations Intelligence: a GENERIC, configurable
--   Detection -> Tracking -> Zones/Lines -> Temporal -> Rules -> Incident
--   -> Evidence -> Actions -> Reporting
-- engine. This is a PLATFORM layer, NOT industry- or customer-specific. It
-- provides primitives (presence/absence, zone entry/exit, dwell, occupancy,
-- line crossing, schedule activity, vehicle, queue/wait) that later industry
-- template packs compose purely by CONFIGURATION (a vertical is named ONLY in
-- those packs, never here). No hardcoded verticals or client-specific workflows.
-- Detects conditions and object classes, never identities. Nothing here claims
-- more than tracking quality supports.
--
-- Scope of THIS migration: the server foundation — the configurable rule model
-- (versioned), the incident lifecycle (candidate/open/ack/resolve/dismiss), the
-- configurable + auditable actions, and tenant-safe RBAC read/mutate. The
-- agent-side detection/tracking/temporal evaluation that PRODUCES a firing is a
-- separate concern (requires a future agent release); this defines the config
-- the agent consumes and the incident model it feeds.
-- ===================================================================

-- -------------------------------------------------------------------
-- 1. Extend monitoring_rules with generic primitive parameters + governance.
--    All additive; existing rows keep working (new columns default safely).
-- -------------------------------------------------------------------
alter table public.monitoring_rules
  add column if not exists rule_version     int     not null default 1,
  add column if not exists confidence_min   numeric(4,3),
  add column if not exists cooldown_seconds int,
  add column if not exists occupancy_min    int,
  add column if not exists occupancy_max    int,
  add column if not exists actions          jsonb   not null default '[]'::jsonb,
  add column if not exists evidence_json    jsonb   not null default '{}'::jsonb,
  add column if not exists sensitive        boolean not null default false,
  add column if not exists review_required  boolean not null default false,
  add column if not exists provenance       jsonb   not null default '{}'::jsonb;

-- widen the rule-type vocabulary with the new generic primitives
alter table public.monitoring_rules drop constraint if exists monitoring_rules_type_chk;
alter table public.monitoring_rules add constraint monitoring_rules_type_chk check (
  rule_type in ('line_crossing','zone_entry','zone_exit','zone_dwell','zone_presence',
                'zone_absence','occupancy','schedule_activity','vehicle_activity',
                'queue_wait','health'));

alter table public.monitoring_rules drop constraint if exists monitoring_rules_confidence_chk;
alter table public.monitoring_rules add constraint monitoring_rules_confidence_chk check (
  confidence_min is null or confidence_min between 0 and 1);
alter table public.monitoring_rules drop constraint if exists monitoring_rules_cooldown_chk;
alter table public.monitoring_rules add constraint monitoring_rules_cooldown_chk check (
  cooldown_seconds is null or cooldown_seconds between 0 and 86400);
alter table public.monitoring_rules drop constraint if exists monitoring_rules_occupancy_chk;
alter table public.monitoring_rules add constraint monitoring_rules_occupancy_chk check (
  (occupancy_min is null or occupancy_min >= 0)
  and (occupancy_max is null or occupancy_max >= 0)
  and (occupancy_min is null or occupancy_max is null or occupancy_min <= occupancy_max));
alter table public.monitoring_rules drop constraint if exists monitoring_rules_actions_chk;
alter table public.monitoring_rules add constraint monitoring_rules_actions_chk check (
  jsonb_typeof(actions) = 'array');

-- -------------------------------------------------------------------
-- 2. Immutable per-rule version history. monitoring_rules.config_version is a
--    site-wide counter (tells the agent when to re-pull); rule_version is the
--    per-RULE definition version, so every incident records exactly which rule
--    definition caused it (provenance).
-- -------------------------------------------------------------------
create table if not exists public.monitoring_rule_versions (
  id          bigint generated always as identity primary key,
  rule_id     uuid not null references public.monitoring_rules(id) on delete cascade,
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  version     int  not null,
  definition  jsonb not null,                 -- full immutable snapshot of the rule at this version
  created_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now(),
  constraint monitoring_rule_versions_uniq unique (rule_id, version)
);
create index if not exists monitoring_rule_versions_rule_idx
  on public.monitoring_rule_versions(rule_id, version desc);

-- BEFORE UPDATE: bump rule_version whenever any DEFINITIONAL column changes
-- (a site-wide config_version bump alone must NOT bump the rule version).
create or replace function public.wl_monitoring_rule_bump_version()
returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' and (
       new.name             is distinct from old.name or
       new.rule_type        is distinct from old.rule_type or
       new.object_classes   is distinct from old.object_classes or
       new.geometry_json    is distinct from old.geometry_json or
       new.direction_json   is distinct from old.direction_json or
       new.schedule_id      is distinct from old.schedule_id or
       new.dwell_seconds    is distinct from old.dwell_seconds or
       new.sample_seconds   is distinct from old.sample_seconds or
       new.severity         is distinct from old.severity or
       new.promote_incident is distinct from old.promote_incident or
       new.confidence_min   is distinct from old.confidence_min or
       new.cooldown_seconds is distinct from old.cooldown_seconds or
       new.occupancy_min    is distinct from old.occupancy_min or
       new.occupancy_max    is distinct from old.occupancy_max or
       new.actions          is distinct from old.actions or
       new.evidence_json    is distinct from old.evidence_json or
       new.sensitive        is distinct from old.sensitive or
       new.review_required  is distinct from old.review_required or
       new.enabled          is distinct from old.enabled) then
    new.rule_version := old.rule_version + 1;
    new.updated_at := now();
  end if;
  return new;
end $$;

drop trigger if exists trg_monitoring_rule_bump_version on public.monitoring_rules;
create trigger trg_monitoring_rule_bump_version
  before update on public.monitoring_rules
  for each row execute function public.wl_monitoring_rule_bump_version();

-- AFTER INSERT/UPDATE: snapshot the (possibly new) version. Same version twice
-- is a no-op, so a non-definitional update creates no duplicate snapshot.
create or replace function public.wl_monitoring_rule_snapshot()
returns trigger language plpgsql
security definer set search_path = public as $$
begin
  insert into public.monitoring_rule_versions (rule_id, tenant_id, version, definition, created_by)
  values (new.id, new.tenant_id, new.rule_version, to_jsonb(new), auth.uid())
  on conflict (rule_id, version) do nothing;
  return new;
end $$;

drop trigger if exists trg_monitoring_rule_snapshot on public.monitoring_rules;
create trigger trg_monitoring_rule_snapshot
  after insert or update on public.monitoring_rules
  for each row execute function public.wl_monitoring_rule_snapshot();

-- backfill a v1 snapshot for any rules that predate this migration
insert into public.monitoring_rule_versions (rule_id, tenant_id, version, definition)
select r.id, r.tenant_id, r.rule_version, to_jsonb(r)
  from public.monitoring_rules r
on conflict (rule_id, version) do nothing;

-- -------------------------------------------------------------------
-- 3. Operations incidents — the durable, tenant-scoped, acknowledgeable record
--    a rule produces. DISTINCT from operational_faults (health/reliability) and
--    from analytic_events (the raw firing stream). Sensitive/subjective classes
--    are ASSISTIVE: they open as 'candidate' + review_required, never as an
--    autonomous accusation.
-- -------------------------------------------------------------------
create table if not exists public.operations_incidents (
  id              bigint generated always as identity primary key,
  tenant_id       uuid not null references public.tenants(id) on delete cascade,
  site_id         uuid not null references public.sites(id) on delete cascade,
  camera_id       uuid references public.cameras(id) on delete set null,
  agent_id        uuid references public.agents(id) on delete set null,
  rule_id         uuid references public.monitoring_rules(id) on delete set null,
  rule_version    int,
  incident_type   text not null,                 -- generic, taken from the rule primitive
  object_class    text,
  severity        text not null default 'attention'
                    check (severity in ('info','attention','critical')),
  status          text not null default 'open'
                    check (status in ('candidate','open','acknowledged','resolved','dismissed')),
  review_required boolean not null default false,
  sensitive       boolean not null default false,
  occurred_at     timestamptz not null,
  opened_at       timestamptz not null default now(),
  acknowledged_at timestamptz,
  acknowledged_by uuid references auth.users(id) on delete set null,
  resolved_at     timestamptz,
  resolved_by     uuid references auth.users(id) on delete set null,
  dedupe_key      text not null,
  evidence_json   jsonb not null default '{}'::jsonb,
  detail          jsonb not null default '{}'::jsonb,
  constraint operations_incidents_class_chk check (
    object_class is null or object_class in ('person','car','motorcycle'))
);
create index if not exists operations_incidents_site_status_idx
  on public.operations_incidents(site_id, status);
create index if not exists operations_incidents_tenant_time_idx
  on public.operations_incidents(tenant_id, opened_at desc);
create index if not exists operations_incidents_rule_idx
  on public.operations_incidents(rule_id);
-- one live incident per condition; a resolved/dismissed one frees the key for recurrence
create unique index if not exists operations_incidents_open_uidx
  on public.operations_incidents(dedupe_key)
  where status in ('candidate','open','acknowledged');

-- Auditable trail of the configured ACTIONS the engine applied for an incident.
-- (Lifecycle transitions ack/resolve/dismiss are audited on the incident row
-- itself via the *_by / *_at columns.)
create table if not exists public.operations_incident_actions (
  id           bigint generated always as identity primary key,
  incident_id  bigint not null references public.operations_incidents(id) on delete cascade,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  action_type  text not null check (action_type in
                 ('create_incident','capture_still','request_footage','mark_review',
                  'include_in_report','escalate_severity','notify')),
  actor        text not null default 'engine',
  result       text,
  detail       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists operations_incident_actions_incident_idx
  on public.operations_incident_actions(incident_id, created_at);

-- -------------------------------------------------------------------
-- 4. Engine: emit an incident from a rule firing. Internal (service_role) — a
--    customer can never fabricate an incident directly. Applies the confidence
--    gate, cooldown de-dupe, sensitive->candidate handling, records provenance
--    (rule_version) and the configured actions. No continuous video; footage is
--    only ever a bounded request recorded as an action intent.
-- -------------------------------------------------------------------
create or replace function public.wl_emit_operations_incident(
  p_rule_id     uuid,
  p_camera_id   uuid        default null,
  p_object_class text       default null,
  p_confidence  numeric     default null,
  p_occurred_at timestamptz default now(),
  p_dedupe      text        default null,
  p_detail      jsonb       default '{}'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_rule     public.monitoring_rules;
  v_incident public.operations_incidents;
  v_existing public.operations_incidents;
  v_dedupe   text;
  v_status   text;
  v_sev      text;
  v_atype    text;
  v_action   jsonb;
begin
  select * into v_rule from public.monitoring_rules where id = p_rule_id;
  if v_rule.id is null then
    raise exception 'monitoring rule % not found', p_rule_id using errcode = '42704';
  end if;
  if not v_rule.enabled then
    return null;                                  -- disabled rules never emit
  end if;
  -- confidence gate: never claim beyond what tracking quality supports
  if v_rule.confidence_min is not null and p_confidence is not null
     and p_confidence < v_rule.confidence_min then
    return null;
  end if;

  v_dedupe := coalesce(p_dedupe,
    'rule:' || p_rule_id::text
    || ':cam:' || coalesce(p_camera_id::text, '-')
    || ':' || coalesce(p_object_class, '-'));

  -- cooldown: a repeat of the SAME condition within the window returns the live one
  if coalesce(v_rule.cooldown_seconds, 0) > 0 then
    select * into v_existing from public.operations_incidents
      where dedupe_key = v_dedupe
        and status in ('candidate','open','acknowledged')
        and opened_at > now() - make_interval(secs => v_rule.cooldown_seconds)
      order by opened_at desc limit 1;
    if v_existing.id is not null then
      return to_jsonb(v_existing);
    end if;
  end if;

  v_sev := case v_rule.severity
             when 'incident' then 'critical'
             when 'attention' then 'attention'
             else 'info' end;
  -- sensitive/subjective classifications are ASSISTIVE only: candidate + review
  v_status := case when v_rule.sensitive or v_rule.review_required then 'candidate' else 'open' end;

  insert into public.operations_incidents (
    tenant_id, site_id, camera_id, agent_id, rule_id, rule_version, incident_type,
    object_class, severity, status, review_required, sensitive, occurred_at, dedupe_key, detail)
  values (
    v_rule.tenant_id, v_rule.site_id, coalesce(p_camera_id, v_rule.camera_id), null,
    v_rule.id, v_rule.rule_version, v_rule.rule_type, p_object_class, v_sev, v_status,
    (v_rule.review_required or v_rule.sensitive), v_rule.sensitive,
    p_occurred_at, v_dedupe, coalesce(p_detail, '{}'::jsonb))
  on conflict (dedupe_key) where status in ('candidate','open','acknowledged') do nothing
  returning * into v_incident;

  if v_incident.id is null then
    -- an open incident for this exact condition already exists (dedupe): return it
    select * into v_incident from public.operations_incidents
      where dedupe_key = v_dedupe and status in ('candidate','open','acknowledged')
      order by opened_at desc limit 1;
    return to_jsonb(v_incident);
  end if;

  -- provenance action + each configured action (auditable intent). Unknown action
  -- types are skipped, not stored, so a bad config can never poison the audit trail.
  insert into public.operations_incident_actions (incident_id, tenant_id, action_type, actor, detail)
  values (v_incident.id, v_rule.tenant_id, 'create_incident', 'engine',
          jsonb_build_object('rule_version', v_rule.rule_version, 'confidence', p_confidence));

  for v_action in select * from jsonb_array_elements(coalesce(v_rule.actions, '[]'::jsonb)) loop
    v_atype := v_action->>'type';
    if v_atype in ('capture_still','request_footage','mark_review','include_in_report',
                   'escalate_severity','notify') then
      insert into public.operations_incident_actions (incident_id, tenant_id, action_type, actor, detail)
      values (v_incident.id, v_rule.tenant_id, v_atype, 'engine', v_action);
    end if;
  end loop;

  return to_jsonb(v_incident);
end $$;

-- -------------------------------------------------------------------
-- 5. Customer lifecycle RPCs — RBAC (owner/admin), tenant-scoped, self-auditing
--    via the *_by/*_at columns.
-- -------------------------------------------------------------------
create or replace function public.wl_acknowledge_operations_incident(p_id bigint)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']); v_row public.operations_incidents;
begin
  update public.operations_incidents
     set status = 'acknowledged', acknowledged_at = now(), acknowledged_by = auth.uid()
   where id = p_id and tenant_id = v_tenant and status in ('candidate','open')
   returning * into v_row;
  if v_row.id is null then
    raise exception 'incident not found or not actionable in your account' using errcode = '42704';
  end if;
  return to_jsonb(v_row);
end $$;

create or replace function public.wl_resolve_operations_incident(p_id bigint)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']); v_row public.operations_incidents;
begin
  update public.operations_incidents
     set status = 'resolved', resolved_at = now(), resolved_by = auth.uid()
   where id = p_id and tenant_id = v_tenant and status in ('candidate','open','acknowledged')
   returning * into v_row;
  if v_row.id is null then
    raise exception 'incident not found or not actionable in your account' using errcode = '42704';
  end if;
  return to_jsonb(v_row);
end $$;

-- Dismiss a candidate (e.g. a sensitive/subjective classification a human judged
-- not real). Records the human reason; this is the "no autonomous accusation" path.
create or replace function public.wl_dismiss_operations_incident(p_id bigint, p_reason text default null)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_tenant uuid := wl_require_role(array['owner','admin']); v_row public.operations_incidents;
begin
  update public.operations_incidents
     set status = 'dismissed', resolved_at = now(), resolved_by = auth.uid(),
         detail = detail || jsonb_build_object('dismiss_reason', p_reason, 'dismissed_by', auth.uid())
   where id = p_id and tenant_id = v_tenant and status in ('candidate','open','acknowledged')
   returning * into v_row;
  if v_row.id is null then
    raise exception 'incident not found or not actionable in your account' using errcode = '42704';
  end if;
  return to_jsonb(v_row);
end $$;

-- Tenant-scoped read model with provenance + evidence + applied actions.
create or replace function public.wl_operations_incidents(p_site_id uuid, p_limit int default 100)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant();
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if not exists (select 1 from public.sites s where s.id = p_site_id and s.tenant_id = v_tenant) then
    raise exception 'site not in your account' using errcode = '42501';
  end if;
  return coalesce((
    select jsonb_agg(row_to_json(t)::jsonb order by t.opened_at desc)
    from (
      select i.id, i.incident_type, i.severity, i.status, i.review_required, i.sensitive,
             i.object_class, i.occurred_at, i.opened_at, i.camera_id, i.rule_id, i.rule_version,
             i.evidence_json as evidence,
             (select coalesce(jsonb_agg(jsonb_build_object(
                        'type', a.action_type, 'actor', a.actor, 'at', a.created_at, 'detail', a.detail)
                        order by a.created_at), '[]'::jsonb)
                from public.operations_incident_actions a where a.incident_id = i.id) as actions
        from public.operations_incidents i
       where i.site_id = p_site_id and i.tenant_id = v_tenant
       order by i.opened_at desc
       limit greatest(1, least(coalesce(p_limit,100), 500))
    ) t
  ), '[]'::jsonb);
end $$;

-- -------------------------------------------------------------------
-- 6. RLS + grants. Reads are tenant-scoped; every write goes through the RPCs.
-- -------------------------------------------------------------------
alter table public.monitoring_rule_versions    enable row level security;
alter table public.operations_incidents        enable row level security;
alter table public.operations_incident_actions enable row level security;

drop policy if exists portal_read_rule_versions on public.monitoring_rule_versions;
create policy portal_read_rule_versions on public.monitoring_rule_versions
  for select to authenticated using (public.wl_is_member(tenant_id));

drop policy if exists portal_read_operations_incidents on public.operations_incidents;
create policy portal_read_operations_incidents on public.operations_incidents
  for select to authenticated using (public.wl_is_member(tenant_id));

drop policy if exists portal_read_operations_incident_actions on public.operations_incident_actions;
create policy portal_read_operations_incident_actions on public.operations_incident_actions
  for select to authenticated using (public.wl_is_member(tenant_id));

revoke all on function public.wl_emit_operations_incident(uuid,uuid,text,numeric,timestamptz,text,jsonb)
  from public, anon, authenticated;
grant execute on function public.wl_emit_operations_incident(uuid,uuid,text,numeric,timestamptz,text,jsonb)
  to service_role;

revoke all on function public.wl_acknowledge_operations_incident(bigint) from public, anon;
grant execute on function public.wl_acknowledge_operations_incident(bigint) to authenticated;
revoke all on function public.wl_resolve_operations_incident(bigint) from public, anon;
grant execute on function public.wl_resolve_operations_incident(bigint) to authenticated;
revoke all on function public.wl_dismiss_operations_incident(bigint,text) from public, anon;
grant execute on function public.wl_dismiss_operations_incident(bigint,text) to authenticated;
revoke all on function public.wl_operations_incidents(uuid,int) from public, anon;
grant execute on function public.wl_operations_incidents(uuid,int) to authenticated;

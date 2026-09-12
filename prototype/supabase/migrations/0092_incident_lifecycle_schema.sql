-- =====================================================================
-- 0092 — Temporal operations-incident lifecycle
--
-- Existing `status` remains the HUMAN REVIEW workflow:
-- candidate | open | acknowledged | resolved | dismissed
--
-- New `lifecycle_state` is the PHYSICAL / MACHINE episode lifecycle:
-- active | escalated | ended | evidence_processing | report_ready
--
-- Ending observed activity MUST NOT silently resolve human review.
-- =====================================================================

alter table public.monitoring_rules
  add column if not exists episode_quiet_seconds int not null default 60;

alter table public.monitoring_rules
  drop constraint if exists monitoring_rules_episode_quiet_chk;
alter table public.monitoring_rules
  add constraint monitoring_rules_episode_quiet_chk
  check (episode_quiet_seconds between 5 and 3600);

-- Episode quiet time is definitional, so include it in rule-version provenance.
create or replace function public.wl_monitoring_rule_bump_version()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'UPDATE' and (
       new.name                  is distinct from old.name or
       new.rule_type             is distinct from old.rule_type or
       new.object_classes        is distinct from old.object_classes or
       new.geometry_json         is distinct from old.geometry_json or
       new.direction_json        is distinct from old.direction_json or
       new.schedule_id           is distinct from old.schedule_id or
       new.dwell_seconds         is distinct from old.dwell_seconds or
       new.sample_seconds        is distinct from old.sample_seconds or
       new.severity              is distinct from old.severity or
       new.promote_incident      is distinct from old.promote_incident or
       new.confidence_min        is distinct from old.confidence_min or
       new.cooldown_seconds      is distinct from old.cooldown_seconds or
       new.episode_quiet_seconds is distinct from old.episode_quiet_seconds or
       new.occupancy_min         is distinct from old.occupancy_min or
       new.occupancy_max         is distinct from old.occupancy_max or
       new.actions               is distinct from old.actions or
       new.evidence_json         is distinct from old.evidence_json or
       new.sensitive             is distinct from old.sensitive or
       new.review_required       is distinct from old.review_required or
       new.enabled               is distinct from old.enabled
  ) then
    new.rule_version := old.rule_version + 1;
    new.updated_at := now();
  end if;
  return new;
end $$;

alter table public.operations_incidents
  add column if not exists lifecycle_state text,
  add column if not exists base_dedupe_key text,
  add column if not exists incident_started_at timestamptz,
  add column if not exists last_activity_at timestamptz,
  add column if not exists incident_ended_at timestamptz,
  add column if not exists evidence_window_start timestamptz,
  add column if not exists evidence_window_end timestamptz,
  add column if not exists lifecycle_confidence numeric,
  add column if not exists summary_json jsonb not null default '{}'::jsonb;

alter table public.operations_incidents
  drop constraint if exists operations_incidents_lifecycle_check;
alter table public.operations_incidents
  add constraint operations_incidents_lifecycle_check
  check (lifecycle_state is null or lifecycle_state in
    ('active','escalated','ended','evidence_processing','report_ready'));

alter table public.operations_incidents
  drop constraint if exists operations_incidents_lifecycle_confidence_check;
alter table public.operations_incidents
  add constraint operations_incidents_lifecycle_confidence_check
  check (lifecycle_confidence is null or lifecycle_confidence between 0 and 1);

-- Backfill without changing review semantics.
update public.operations_incidents
   set lifecycle_state = coalesce(
         lifecycle_state,
         case
           when status in ('resolved','dismissed') then 'report_ready'
           else 'active'
         end
       ),
       base_dedupe_key = coalesce(base_dedupe_key, dedupe_key),
       incident_started_at = coalesce(incident_started_at, occurred_at, opened_at),
       last_activity_at = coalesce(last_activity_at, occurred_at, opened_at),
       evidence_window_start = coalesce(
         evidence_window_start,
         coalesce(incident_started_at, occurred_at, opened_at) - interval '15 seconds'
       )
 where lifecycle_state is null
    or base_dedupe_key is null
    or incident_started_at is null
    or last_activity_at is null
    or evidence_window_start is null;

alter table public.operations_incidents
  alter column lifecycle_state set default 'active',
  alter column lifecycle_state set not null;

create index if not exists operations_incidents_lifecycle_idx
  on public.operations_incidents(site_id, lifecycle_state, last_activity_at desc);

create index if not exists operations_incidents_base_idx
  on public.operations_incidents(site_id, base_dedupe_key, incident_started_at desc);

-- The existing dedupe_key index keeps every episode row unique in the review
-- workflow. This second index guarantees only ONE active physical episode for a
-- condition identity, even when the previous episode still awaits operator review.
create unique index if not exists operations_incidents_one_active_base_uidx
  on public.operations_incidents(base_dedupe_key)
  where lifecycle_state in ('active','escalated');

create table if not exists public.operations_incident_lifecycle_events (
  id bigint generated always as identity primary key,
  incident_id bigint not null
    references public.operations_incidents(id) on delete cascade,
  tenant_id uuid not null
    references public.tenants(id) on delete cascade,
  event_type text not null
    check (event_type in ('opened','touched','escalated','ended',
                          'evidence_processing','report_ready')),
  occurred_at timestamptz not null default now(),
  detail jsonb not null default '{}'::jsonb
);
create index if not exists operations_incident_lifecycle_events_incident_idx
  on public.operations_incident_lifecycle_events(incident_id, occurred_at);

alter table public.operations_incident_lifecycle_events enable row level security;
revoke all on table public.operations_incident_lifecycle_events
  from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- Dedicated episode quiet-time setter. Existing governance RPC signatures stay
-- backward-compatible.
-- ---------------------------------------------------------------------
create or replace function public.wl_set_rule_episode_policy(
  p_rule_id uuid,
  p_episode_quiet_seconds int
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_rule public.monitoring_rules;
  v_quiet int := least(greatest(coalesce(p_episode_quiet_seconds,60),5),3600);
begin
  update public.monitoring_rules
     set episode_quiet_seconds = v_quiet
   where id = p_rule_id and tenant_id = v_tenant
  returning * into v_rule;

  if v_rule.id is null then
    raise exception 'rule not found in your account' using errcode='42501';
  end if;

  return jsonb_build_object(
    'ok', true,
    'rule_id', v_rule.id,
    'rule_version', v_rule.rule_version,
    'episode_quiet_seconds', v_rule.episode_quiet_seconds
  );
end $$;

revoke all on function public.wl_set_rule_episode_policy(uuid,int)
  from public, anon;
grant execute on function public.wl_set_rule_episode_policy(uuid,int)
  to authenticated, service_role;

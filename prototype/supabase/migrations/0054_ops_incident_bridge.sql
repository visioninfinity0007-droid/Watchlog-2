-- ===================================================================
-- 0054 — Live bridge: promotable analytic_events -> 0049 operations_incidents.
--
-- The agent's local rule engine already reports firings as analytic_events via the
-- EXISTING wl_ingest_analytic_events path. Rather than a parallel reporting path, this
-- trigger promotes the EXCEPTION firings into operations incidents. A rule is an
-- exception when it is configured as one: severity attention/incident, or
-- promote_incident, or sensitive/review_required. Measurement-only firings stay as
-- analytic_events and never raise an incident.
--
-- The 0049 emitter (wl_emit_operations_incident) applies the confidence gate, cooldown
-- de-dupe, sensitive->candidate handling, configured actions and rule_version PROVENANCE;
-- this trigger only forwards the firing and tags detail.source='live' so LIVE WatchLog
-- observation stays distinct from recorder-native events and archive-reprocessed results
-- (the latter live in archive_scan_results, 0051, with their own provenance label).
-- ===================================================================

create or replace function public.wl_analytic_event_to_ops_incident()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare v_rule public.monitoring_rules;
begin
  select * into v_rule from public.monitoring_rules where id = new.monitoring_rule_id;
  if v_rule.id is null then
    return new;                       -- unmapped measurement (e.g. ad-hoc) — nothing to promote
  end if;
  if v_rule.severity in ('attention','incident')
     or v_rule.promote_incident or v_rule.sensitive or v_rule.review_required then
    perform public.wl_emit_operations_incident(
      v_rule.id,
      new.camera_id,
      new.object_class,
      nullif(new.metadata_json->>'confidence', '')::numeric,   -- null => not confidence-gated (e.g. absence)
      new.occurred_at,
      null,                                                    -- let the emitter compute the cooldown dedupe key
      jsonb_build_object('source', 'live', 'event_type', new.event_type,
                         'analytic_event_id', new.id, 'track_key', new.track_key));
  end if;
  return new;
end $$;

drop trigger if exists trg_analytic_event_to_ops_incident on public.analytic_events;
create trigger trg_analytic_event_to_ops_incident
  after insert on public.analytic_events
  for each row execute function public.wl_analytic_event_to_ops_incident();

-- =====================================================================
-- 0075 — Critical-alert delivery: health-transition -> incident bridge + retry-aware claim.
--
-- Completes the chain the alert engine (0068) started, so a camera going into VideoLoss (or
-- recovering) becomes a deliverable alert without spamming:
--
--   camera_health_transition (fires ONLY on state change, 0045)
--        -> intel_incident (camera_offline / camera_recovered), deduped on stable identity
--        -> wl_claim_alerts (dedup per incident+channel, retry-aware)
--        -> transport (report-runner) -> wl_mark_alert_sent
--
-- "No spam" is structural: a persisting VideoLoss produces NO new transition, hence no new
-- incident, hence no new alert. Recovery is a single transition -> a single recovery alert.
-- Retry is explicit: a FAILED dispatch is re-claimable; a SENT one never is.
-- =====================================================================

-- Health transitions are a legitimate incident source.
alter table public.intel_incidents drop constraint if exists intel_incidents_source_kind_check;
alter table public.intel_incidents add constraint intel_incidents_source_kind_check
  check (source_kind in ('episode','activity','event','health_transition'));

-- ---------------------------------------------------------------------
-- Bridge camera health transitions in a window into incidents (idempotent).
-- offline -> camera_offline (critical if the camera is configured critical, else warning);
-- offline->operational -> camera_recovered (info). Deterministic source id per transition.
-- ---------------------------------------------------------------------
create or replace function public.wl_bridge_health_to_incidents(
  p_site_id uuid, p_from timestamptz, p_to timestamptz
) returns integer
language plpgsql security definer set search_path = public as $$
declare v_rows int; v_crit uuid[];
begin
  select coalesce(critical_camera_ids,'{}') into v_crit from site_business_context where site_id = p_site_id;
  if v_crit is null then v_crit := '{}'; end if;

  insert into intel_incidents (tenant_id, site_id, camera_id, incident_type, severity, occurred_at,
                               source_kind, source_id, detail_json)
  select t.tenant_id, t.site_id, t.camera_id,
         case when t.to_state = 'offline' then 'camera_offline' else 'camera_recovered' end,
         case when t.to_state = 'offline'
              then (case when t.camera_id = any(v_crit) then 'critical' else 'warning' end)
              else 'info' end,
         t.at, 'health_transition', md5('health:' || t.id::text)::uuid,
         jsonb_build_object('reason', t.reason_code, 'from', t.from_state, 'to', t.to_state,
                            'transition_id', t.id, 'camera', c.name)
    from camera_health_transitions t
    left join cameras c on c.id = t.camera_id
   where t.site_id = p_site_id and t.at >= p_from and t.at < p_to
     and (t.to_state = 'offline' or (t.from_state = 'offline' and t.to_state = 'operational'))
  on conflict (site_id, incident_type, camera_id, occurred_at) do update
    set severity = excluded.severity, detail_json = excluded.detail_json, source_id = excluded.source_id;
  get diagnostics v_rows = row_count;
  return v_rows;
end $$;
revoke all on function public.wl_bridge_health_to_incidents(uuid,timestamptz,timestamptz) from public, anon, authenticated;
grant execute on function public.wl_bridge_health_to_incidents(uuid,timestamptz,timestamptz) to service_role;

-- ---------------------------------------------------------------------
-- Retry-aware claim (supersedes 0068's body). A FAILED dispatch is re-claimable (retry);
-- a SENT or in-flight CLAIMED one is not (no double-send). Still atomic + severity-gated.
-- ---------------------------------------------------------------------
create or replace function public.wl_claim_alerts(
  p_site_id uuid, p_channel text, p_min_severity text default 'warning', p_limit int default 50
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_rank int; v_out jsonb;
begin
  v_rank := case lower(p_min_severity) when 'critical' then 0 when 'info' then 2 else 1 end;
  with cand as (
    select ii.*
      from intel_incidents ii
     where ii.site_id = p_site_id
       and (case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end) <= v_rank
       and ii.status = 'open'
       and not exists (select 1 from alert_dispatches d
                        where d.incident_id = ii.id and d.channel = p_channel
                          and d.status in ('sent','claimed'))   -- 'failed' stays eligible => retry
     order by (case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end), ii.occurred_at
     limit p_limit
     for update skip locked
  ),
  ins as (
    insert into alert_dispatches (tenant_id, site_id, incident_id, channel, severity, status, detail_json)
    select c.tenant_id, c.site_id, c.id, p_channel, c.severity, 'claimed',
           jsonb_build_object('incident_type', c.incident_type, 'occurred_at', c.occurred_at,
                              'camera_id', c.camera_id, 'detail', c.detail_json)
      from cand c
    on conflict (incident_id, channel) do update
       set status = 'claimed', claimed_at = now(), severity = excluded.severity, detail_json = excluded.detail_json
       where alert_dispatches.status = 'failed'     -- only re-claim a previously FAILED send
    returning incident_id, severity, detail_json
  )
  select coalesce(jsonb_agg(jsonb_build_object('incident_id', incident_id, 'severity', severity, 'detail', detail_json)
           order by case severity when 'critical' then 0 when 'warning' then 1 else 2 end), '[]'::jsonb)
    into v_out from ins;
  return v_out;
end $$;
revoke all on function public.wl_claim_alerts(uuid,text,text,int) from public, anon, authenticated;
grant execute on function public.wl_claim_alerts(uuid,text,text,int) to service_role;

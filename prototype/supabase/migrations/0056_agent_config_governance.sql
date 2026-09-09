-- ===================================================================
-- 0056 — Ship the Operations-Intelligence GOVERNANCE layer to the agent.
--
-- 0049/0053 added the governance columns (confidence_min, cooldown_seconds,
-- occupancy_min, actions, evidence_json, sensitive, review_required) and the
-- server-side incident derivation (0054 bridge) reads them directly. But the
-- config the AGENT pulls (wl_agent_analytics_config, last defined in 0030) still
-- shipped only the pre-governance rule shape, so the packaged runtime never saw:
--   * actions            -> could not dispatch evidence actions (capture_still/request_footage)
--   * cooldown_seconds    -> could not floor its action de-dupe to the configured window
--   * sensitive / review_required -> could not treat a rule as an exception the way
--                            the 0054 bridge does (severity/promote OR sensitive/review)
--   * confidence_min      -> could not skip evidence capture for a firing the 0049
--                            emitter would gate out by confidence
--   * occupancy_min       -> the queue_wait primitive fell back to its default length
--
-- This migration ONLY re-shapes the config payload (a pure read function) — no data,
-- table or trigger change. It also surfaces sites.multi_agent_enabled on BOTH return
-- branches (even the unchanged-version early return) so the single-authority lease
-- feature toggle reaches the agent within one poll, without waiting for a config bump.
--
-- Nothing here enables multi-agent: the flag is reported, defaults false, and the
-- LeaseClient stays single-agent/authoritative until a site explicitly opts in.
-- ===================================================================

create or replace function public.wl_agent_analytics_config(
  p_agent_id uuid, p_agent_key text, p_known_version bigint default 0
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_agent    agents;
  v_version  bigint;
  v_multi    boolean;
  v_config   jsonb;
  v_requests jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  select analytics_config_version, coalesce(multi_agent_enabled, false)
    into v_version, v_multi
    from sites where id = v_agent.site_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'request_id', cr.id, 'camera_id', cr.camera_id, 'channel', c.channel)
           order by cr.requested_at), '[]'::jsonb)
    into v_requests
    from camera_snapshot_requests cr
    join cameras c on c.id = cr.camera_id
   where cr.site_id = v_agent.site_id and cr.completed_at is null;

  -- Unchanged config version: still report the lease flag + snapshot requests so a
  -- multi_agent toggle (safety-critical fencing) is honoured without a config bump.
  if coalesce(p_known_version, 0) = v_version then
    return jsonb_build_object('version', v_version, 'changed', false,
      'multi_agent_enabled', v_multi, 'snapshot_requests', v_requests);
  end if;

  select jsonb_build_object(
    'site_id', s.id, 'site_type', s.site_type, 'timezone', s.timezone,
    'version', s.analytics_config_version,
    'multi_agent_enabled', coalesce(s.multi_agent_enabled, false),
    'schedules', coalesce((select jsonb_agg(jsonb_build_object(
        'id', ms.id, 'name', ms.name, 'timezone', ms.timezone,
        'schedule', ms.schedule_json, 'enabled', ms.enabled))
        from monitoring_schedules ms where ms.site_id = s.id and ms.enabled), '[]'::jsonb),
    'cameras', coalesce((select jsonb_agg(jsonb_build_object(
        'id', c.id, 'channel', c.channel, 'name', c.name, 'purpose', c.purpose,
        'analytics_enabled', c.analytics_enabled,
        'rules', coalesce((select jsonb_agg(jsonb_build_object(
            'id', r.id, 'analytic_key', r.analytic_key, 'name', r.name,
            'rule_type', r.rule_type, 'object_classes', r.object_classes,
            'geometry', r.geometry_json, 'direction', r.direction_json,
            'schedule_id', r.schedule_id, 'dwell_seconds', r.dwell_seconds,
            'sample_seconds', r.sample_seconds, 'severity', r.severity,
            'promote_incident', r.promote_incident,
            -- governance layer (0049/0053) — now travels to the runtime
            'occupancy_min', r.occupancy_min,
            'occupancy_max', r.occupancy_max,
            'confidence_min', r.confidence_min,
            'cooldown_seconds', r.cooldown_seconds,
            'actions', coalesce(r.actions, '[]'::jsonb),
            'evidence_json', coalesce(r.evidence_json, '{}'::jsonb),
            'sensitive', coalesce(r.sensitive, false),
            'review_required', coalesce(r.review_required, false),
            'rule_version', r.rule_version))
          from monitoring_rules r
          where r.camera_id = c.id and r.enabled and r.rule_type <> 'health'), '[]'::jsonb))
        order by c.channel)
        from cameras c where c.site_id = s.id and c.analytics_enabled), '[]'::jsonb)
  ) into v_config from sites s where s.id = v_agent.site_id;

  return jsonb_build_object('version', v_version, 'changed', true,
    'multi_agent_enabled', v_multi, 'config', v_config, 'snapshot_requests', v_requests);
end $$;

-- signature unchanged (uuid, text, bigint) — the 0024 revoke/grant still applies.

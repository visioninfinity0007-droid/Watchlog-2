-- ===================================================================
-- 0055 — Agent-facing archive-execution RPCs (reconcile the agent with 0051).
--
-- 0051's engine entrypoints are service_role; the agent authenticates with its agent key.
-- These three RPCs let the agent CLAIM pending scans, RECORD recovered results, and UPDATE
-- scan status — each authenticating via wl_auth_agent and STRICTLY scoped to the agent's own
-- site. Recovered results always keep the fixed 0051 provenance label ("Recovered from
-- recorder archive"), so archive-reprocessed AI is never presented as live observation.
-- ===================================================================

-- Claim pending scans for this agent's site (mirrors the incident-clip claim pattern in 0041:
-- a data-modifying CTE at the TOP LEVEL of a SELECT ... INTO, atomic under FOR UPDATE SKIP LOCKED).
create or replace function public.wl_agent_claim_archive_scans(
  p_agent_id uuid, p_agent_key text, p_limit int default 1
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_agent public.agents;
  v_limit int := least(greatest(coalesce(p_limit, 1), 1), 4);
  v_result jsonb;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  with picked as (
    select s.id from public.archive_scans s
     where s.site_id = v_agent.site_id and s.status = 'requested'
     order by s.requested_at
     for update skip locked
     limit v_limit
  ), claimed as (
    update public.archive_scans s
       set status = 'analyzing', started_at = coalesce(s.started_at, now())
      from picked p where s.id = p.id
    returning s.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'scan_id', c.id, 'camera_ids', c.camera_ids, 'rule_ids', c.rule_ids,
           'from_ts', c.from_ts, 'to_ts', c.to_ts) order by c.requested_at), '[]'::jsonb)
    into v_result from claimed c;
  return v_result;
end $$;

create or replace function public.wl_agent_record_archive_result(
  p_agent_id uuid, p_agent_key text, p_scan_id uuid, p_camera_id uuid, p_result_type text,
  p_recovered_at timestamptz, p_confidence numeric default null,
  p_still jsonb default '{}'::jsonb, p_clip jsonb default '{}'::jsonb, p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_scan public.archive_scans; v_row public.archive_scan_results;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  select * into v_scan from public.archive_scans where id = p_scan_id and site_id = v_agent.site_id;
  if v_scan.id is null then
    raise exception 'archive scan not for this agent site' using errcode = '42501';
  end if;
  insert into public.archive_scan_results (
    scan_id, tenant_id, site_id, camera_id, result_type, recovered_at, confidence,
    still_evidence, clip_ref, detail)
  values (
    v_scan.id, v_scan.tenant_id, v_scan.site_id, p_camera_id, p_result_type, p_recovered_at,
    p_confidence, coalesce(p_still, '{}'::jsonb), coalesce(p_clip, '{}'::jsonb), coalesce(p_detail, '{}'::jsonb))
  returning * into v_row;                       -- provenance_label defaults to the fixed archive label
  return to_jsonb(v_row);
end $$;

create or replace function public.wl_agent_set_archive_scan_status(
  p_agent_id uuid, p_agent_key text, p_scan_id uuid, p_status text,
  p_error text default null, p_stats jsonb default null
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_agent public.agents; v_scan public.archive_scans;
begin
  v_agent := wl_auth_agent(p_agent_id, p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode = '28000';
  end if;
  if p_status not in ('requested','locating','retrieving','analyzing','complete','failed','cancelled') then
    raise exception 'invalid status %', p_status using errcode = '22023';
  end if;
  update public.archive_scans s
     set status = p_status,
         completed_at = case when p_status in ('complete','failed','cancelled') then now() else s.completed_at end,
         error_message = coalesce(p_error, s.error_message),
         stats = coalesce(p_stats, s.stats)
   where s.id = p_scan_id and s.site_id = v_agent.site_id
   returning * into v_scan;
  if v_scan.id is null then
    raise exception 'archive scan not for this agent site' using errcode = '42501';
  end if;
  return to_jsonb(v_scan);
end $$;

revoke all on function public.wl_agent_claim_archive_scans(uuid,text,int) from public;
grant execute on function public.wl_agent_claim_archive_scans(uuid,text,int) to anon, authenticated;
revoke all on function public.wl_agent_record_archive_result(uuid,text,uuid,uuid,text,timestamptz,numeric,jsonb,jsonb,jsonb) from public;
grant execute on function public.wl_agent_record_archive_result(uuid,text,uuid,uuid,text,timestamptz,numeric,jsonb,jsonb,jsonb) to anon, authenticated;
revoke all on function public.wl_agent_set_archive_scan_status(uuid,text,uuid,text,text,jsonb) from public;
grant execute on function public.wl_agent_set_archive_scan_status(uuid,text,uuid,text,text,jsonb) to anon, authenticated;

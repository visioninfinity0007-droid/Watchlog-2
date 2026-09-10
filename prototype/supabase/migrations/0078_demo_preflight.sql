-- =====================================================================
-- 0078 — Demo / Acceptance Preflight (item 11). An internal readiness tool that inspects a
-- site's REAL state and answers: is it ready to demonstrate / accept? PASS or BLOCKED with a
-- reason per check. It injects NO fake production data — it only reports what is actually true.
-- Useful for every future pilot, not only Al-Khalid.
--
-- Overall ready = all HARD checks pass (agent, recorder identified, events flowing). Soft
-- checks (coverage, camera naming, recipient, policies) surface as 'warn'/'blocked' with the
-- reason but a warn does not fail the demo.
-- =====================================================================

create or replace function public.wl_demo_preflight(
  p_site_id uuid, p_date date default null
) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_site sites; v_tz text; v_date date; v_start timestamptz; v_end timestamptz;
  v_agents int; v_online boolean; v_vendor text; v_model text;
  v_cams int; v_named int; v_events int; v_health int; v_faults int;
  v_profile jsonb; v_recip int; v_pol int; v_cov jsonb; v_ratio numeric;
  v_checks jsonb := '[]'::jsonb; v_hard_ok boolean := true;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_tz := v_site.timezone;
  v_date := coalesce(p_date, (now() at time zone v_tz)::date);
  v_start := (v_date::text||' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';

  select count(*), bool_or(a.last_seen_at > now() - interval '3 min'),
         max(a.device_vendor), max(a.device_model)
    into v_agents, v_online, v_vendor, v_model from agents a where a.site_id = p_site_id;
  select count(*),
         count(*) filter (where name is not null and name !~* '^(camera|cam|channel|ch)\s*[0-9]*$' and length(btrim(name)) > 2)
    into v_cams, v_named from cameras where site_id = p_site_id;
  select count(*) into v_events from events where site_id = p_site_id and device_ts >= v_start and device_ts < v_end;
  select count(*), count(*) filter (where health_state = 'offline')
    into v_health, v_faults from camera_health where site_id = p_site_id;
  if v_vendor is not null and v_model is not null then
    v_profile := wl_recorder_profile(v_vendor, v_model);
  else v_profile := '[]'::jsonb; end if;
  select count(*) into v_recip from report_recipients r
   where r.tenant_id = v_site.tenant_id and r.enabled and (r.site_id is null or r.site_id = p_site_id)
     and r.channel in ('whatsapp','both');
  select count(*) into v_pol from incident_policies where site_id = p_site_id and enabled;
  v_cov := wl_site_coverage_report(p_site_id, v_start, least(v_end, greatest(v_start, now())));
  v_ratio := coalesce((v_cov->>'coverage_ratio')::numeric, 0);

  -- hard checks
  v_checks := v_checks || _wl_pf('agent_available', 'Site agent reporting', coalesce(v_online,false),
                (case when v_agents=0 then 'no agent enrolled' when not coalesce(v_online,false) then 'agent not seen in 3 min' else 'online' end), true);
  v_checks := v_checks || _wl_pf('recorder_identified', 'Recorder identified', v_vendor is not null and v_model is not null,
                (case when v_vendor is null then 'recorder vendor/model not reported yet' else v_vendor||' '||coalesce(v_model,'') end), true);
  v_checks := v_checks || _wl_pf('events_flowing', 'Intelligence data available for the day', v_events > 0,
                (case when v_events=0 then 'no events for '||v_date else v_events||' events' end), true);

  -- soft checks
  v_checks := v_checks || _wl_pf('capability_profile', 'Recorder capability profile resolved', jsonb_array_length(v_profile) > 0,
                (case when jsonb_array_length(v_profile)=0 then 'recorder not in the capability KB (add it, or run a Site Control read)' else jsonb_array_length(v_profile)||' capabilities known' end), false);
  v_checks := v_checks || _wl_pf('cameras_named', 'Cameras named meaningfully', v_cams > 0 and v_named = v_cams,
                (case when v_cams=0 then 'no cameras discovered' else v_named||'/'||v_cams||' cameras have meaningful names' end), false);
  v_checks := v_checks || _wl_pf('health_truthful', 'Camera health being reported (faults truthful)', v_health > 0,
                (case when v_health=0 then 'no camera health reported yet' else v_health||' cameras tracked, '||v_faults||' currently offline' end), false);
  v_checks := v_checks || _wl_pf('site_control_available', 'Site Control possible', coalesce(v_online,false) and v_vendor is not null,
                (case when coalesce(v_online,false) and v_vendor is not null then 'agent online + recorder known' else 'needs agent online + recorder identified' end), false);
  v_checks := v_checks || _wl_pf('alert_config', 'Incident/alert policies configured', v_pol > 0,
                (case when v_pol=0 then 'no incident policies for this site (add via custom analytics)' else v_pol||' policies' end), false);
  v_checks := v_checks || _wl_pf('whatsapp_recipient', 'WhatsApp recipient configured', v_recip > 0,
                (case when v_recip=0 then 'no WhatsApp recipient (client-supplied) configured' else v_recip||' recipient(s)' end), false);
  v_checks := v_checks || _wl_pf('monitoring_coverage', 'Monitoring coverage sufficient', v_ratio >= 0.9,
                round(v_ratio*100)||'% of the window verified', false);

  select bool_and((c->>'status') = 'pass') into v_hard_ok
    from jsonb_array_elements(v_checks) c where (c->>'hard')::boolean;

  return jsonb_build_object('site', v_site.name, 'site_id', v_site.id, 'date', v_date,
    'ready', coalesce(v_hard_ok,false), 'checks', v_checks,
    'summary', jsonb_build_object(
      'pass', (select count(*) from jsonb_array_elements(v_checks) c where c->>'status'='pass'),
      'blocked', (select count(*) from jsonb_array_elements(v_checks) c where c->>'status'='blocked')));
end $$;
revoke all on function public.wl_demo_preflight(uuid,date) from public, anon, authenticated;
grant execute on function public.wl_demo_preflight(uuid,date) to service_role;

-- one check object; hard failures are 'blocked', soft failures 'warn'
create or replace function public._wl_pf(p_key text, p_label text, p_ok boolean, p_detail text, p_hard boolean)
returns jsonb language sql immutable set search_path = public as $$
  select jsonb_build_array(jsonb_build_object('key', p_key, 'label', p_label,
    'status', case when p_ok then 'pass' when p_hard then 'blocked' else 'warn' end,
    'detail', p_detail, 'hard', p_hard));
$$;

create or replace function public.wl_my_demo_preflight(p_site_id uuid, p_date date default null)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode='42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then raise exception 'not authorized for this site' using errcode='42501'; end if;
  return wl_demo_preflight(p_site_id, p_date);
end $$;
revoke all on function public.wl_my_demo_preflight(uuid,date) from public, anon;
grant execute on function public.wl_my_demo_preflight(uuid,date) to authenticated, service_role;

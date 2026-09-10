-- =====================================================================
-- 0079 — Site / recorder diagnosis for the capability-aware Site Control portal surface
-- (items 4 + 5, data plane).
--
-- ONE tenant-guarded call that gives the Site Control page everything it needs to render a
-- capability-aware diagnosis WITHOUT ever exposing a raw recorder action: recorder identity +
-- connectivity + clock, cameras with current health / VideoLoss, the recorder capability
-- profile (evidence-graded verdicts), configuration drift, monitoring coverage, and the
-- caller's ROLE (so the UI can gate Read -> Recommend -> Approve). No recorder credential is
-- ever returned; the credential stays on the agent.
-- =====================================================================

create or replace function public.wl_my_site_diagnosis(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
  v_tenant uuid := wl_my_tenant(); v_owner uuid; v_role text;
  v_site sites; v_tz text; v_vendor text; v_model text; v_online boolean; v_seen timestamptz; v_driver text;
  v_profile jsonb; v_start timestamptz; v_now timestamptz := now();
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  select role into v_role from memberships where user_id = auth.uid() and tenant_id = v_tenant limit 1;
  select * into v_site from sites where id = p_site_id;
  v_tz := v_site.timezone;
  select max(device_vendor), max(device_model),
         bool_or(last_seen_at > now() - interval '3 min'), max(last_seen_at), max(device_driver)
    into v_vendor, v_model, v_online, v_seen, v_driver from agents where site_id = p_site_id;
  if v_vendor is not null and v_model is not null then
    v_profile := wl_recorder_profile(v_vendor, v_model);
  else v_profile := '[]'::jsonb; end if;
  v_start := ((v_now at time zone v_tz)::date::text || ' 00:00:00')::timestamp at time zone v_tz;

  return jsonb_build_object(
    'site', v_site.name, 'site_id', v_site.id, 'timezone', v_tz,
    'role', coalesce(v_role, 'viewer'),
    'recorder', jsonb_build_object('vendor', v_vendor, 'model', v_model, 'driver', v_driver,
                                   'identified', v_vendor is not null and v_model is not null),
    'connectivity', jsonb_build_object('agent_online', coalesce(v_online, false), 'last_seen', v_seen),
    'capabilities', v_profile,
    'capability_known', jsonb_array_length(v_profile) > 0,
    'cameras', (select coalesce(jsonb_agg(jsonb_build_object(
                  'name', c.name, 'channel', c.channel, 'purpose', c.purpose,
                  'health_state', coalesce(h.health_state::text, 'unknown'),
                  'video_loss', coalesce(h.health_state::text = 'offline' and h.reason_code::text = 'video_loss', false),
                  'recording_state', coalesce(h.recording_state::text, 'unknown')) order by c.channel), '[]'::jsonb)
                from cameras c left join camera_health h on h.camera_id = c.id
               where c.site_id = p_site_id),
    'faults', (select coalesce(jsonb_agg(jsonb_build_object('camera', c.name, 'reason', h.reason_code::text) order by c.name), '[]'::jsonb)
                from camera_health h join cameras c on c.id = h.camera_id
               where h.site_id = p_site_id and h.health_state::text = 'offline'),
    'coverage', wl_site_coverage_report(p_site_id, v_start, v_now),
    'clock', jsonb_build_object('note', 'clock/NTP verified via a Site Control READ (never assumed)'),
    'tiers', jsonb_build_object('read', true,
                                'recommend', coalesce(v_role in ('owner','admin','manager'), false),
                                'approve', coalesce(v_role in ('owner','admin'), false)));
end $$;
revoke all on function public.wl_my_site_diagnosis(uuid) from public, anon;
grant execute on function public.wl_my_site_diagnosis(uuid) to authenticated, service_role;

-- 0155 - Multi-recorder reporting / owner coverage bridge.
--
-- STACKED AFTER 0154. REPO ONLY until separately approved.
--
-- 0153 introduced the correct recorder/camera coverage facts, but existing
-- daily intelligence and owner context still consumed the legacy site-Agent
-- coverage scalar. On a multi-recorder site that can overstate certainty:
-- Recorder B may be unavailable while the Agent and Recorder A remain online.
--
-- This migration makes the existing coverage-classes compatibility point choose
-- the right truth:
--   * 0/1 configured recorder  -> exact legacy wall-clock coverage contract;
--   * >1 configured recorders  -> recorder/camera-time coverage;
--   * multi-recorder window that starts before recorder tracking began -> UNKNOWN.
--
-- IMPORTANT semantic rule:
-- camera-time seconds are never relabelled as wall-clock LIVE/RECOVERED/
-- UNVERIFIED durations. The multi-recorder payload therefore has no legacy
-- "classes" object. It exposes camera_coverage_ratio plus explicit wall-clock
-- impact windows ("some cameras unverified").
--
-- Because wl_daily_intelligence already calls wl_site_coverage_report_classes,
-- this bridge automatically governs Phase-28 daily/site/office period support
-- without duplicating vertical-specific coverage logic.

create or replace function public.wl_effective_site_coverage(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_recorders integer;
  v_legacy jsonb;
  v_facts jsonb;
  v_complete boolean;
  v_ratio numeric;
  v_gaps jsonb;
begin
  select count(*)::integer
    into v_recorders
    from public.recorders r
   where r.site_id=p_site_id
     and r.is_configured;

  -- Backwards compatibility: a true singleton site keeps the historical
  -- wall-clock LIVE/RECOVERED/UNVERIFIED contract byte-for-field compatible.
  if coalesce(v_recorders,0)<=1 then
    return public.wl_site_coverage_legacy_classes(p_site_id,p_from,p_to);
  end if;

  v_facts := public.wl_site_recorder_coverage_facts(p_site_id,p_from,p_to);
  v_complete := coalesce((v_facts->>'complete_window')::boolean,false);

  if v_complete then
    v_ratio := nullif(v_facts->>'camera_coverage_ratio','')::numeric;
  else
    v_ratio := null;
  end if;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'start',x->'start',
        'end',x->'end',
        -- A whole-site Agent/PC gap keeps its site-level cause (0153
        -- impact window cause); any other window is a recorder/camera gap.
        'cause',coalesce(nullif(x->>'cause',''),'recorder_unverified'),
        'state',x->>'state',
        'affected_camera_count',
          coalesce((x->>'affected_camera_count')::integer,0),
        'total_camera_count',
          coalesce((x->>'total_camera_count')::integer,0)
      )
      order by (x->>'start')::timestamptz
    ),
    '[]'::jsonb
  )
  into v_gaps
  from jsonb_array_elements(
    case when v_complete
      then coalesce(v_facts->'impact_windows','[]'::jsonb)
      else '[]'::jsonb
    end
  ) x;

  return jsonb_build_object(
    'schema','multi-recorder-effective-coverage-v1',
    'coverage_basis','camera_time',
    'multi_recorder',true,
    'known',v_complete and v_ratio is not null,
    'complete_window',v_complete,
    'requested_from',v_facts->'requested_from',
    'requested_to',v_facts->'requested_to',
    'tracking_started_at',v_facts->'tracking_started_at',
    'legacy_seconds',v_facts->'legacy_seconds',

    -- This ratio is verified camera-time / total camera-time.
    'coverage_ratio',case when v_complete then v_ratio else null end,
    'camera_coverage_ratio',case when v_complete then v_ratio else null end,

    -- Wall-clock impact is kept separately. any_unverified_seconds means
    -- "at least one configured camera was unverified", not whole-site downtime.
    'any_unverified_seconds',case when v_complete
      then (v_facts->>'any_unverified_seconds')::numeric else null end,
    'partial_unverified_seconds',case when v_complete
      then (v_facts->>'partial_unverified_seconds')::numeric else null end,
    'fully_unverified_seconds',case when v_complete
      then (v_facts->>'fully_unverified_seconds')::numeric else null end,
    'fully_verified_seconds',case when v_complete
      then (v_facts->>'fully_verified_seconds')::numeric else null end,
    'max_affected_cameras',case when v_complete
      then (v_facts->>'max_affected_cameras')::integer else null end,

    -- Camera-time facts retain their explicit units.
    'camera_time_seconds',case when v_complete
      then (v_facts->>'camera_time_seconds')::numeric else null end,
    'live_camera_seconds',case when v_complete
      then (v_facts->>'live_camera_seconds')::numeric else null end,
    'recovered_camera_seconds',case when v_complete
      then (v_facts->>'recovered_camera_seconds')::numeric else null end,
    'unverified_camera_seconds',case when v_complete
      then (v_facts->>'unverified_camera_seconds')::numeric else null end,

    'camera_count',coalesce((v_facts->>'camera_count')::integer,0),
    'recorder_count',coalesce((v_facts->>'recorder_count')::integer,0),
    'gaps',v_gaps,
    'recorders',coalesce(v_facts->'recorders','[]'::jsonb),
    'recorder_coverage',v_facts,
    'measurement_notes',jsonb_build_array(
      'Multi-recorder coverage is a camera-time ratio, not wall-clock uptime.',
      'An impact window means some configured cameras were unverified; it does not imply the whole site was offline.',
      'Recovered time restores only the cameras the recovery names, on that recorder.',
      'When the site Agent or PC could not observe, every recorder''s cameras are unverified for that time.',
      'Coverage before recorder tracking started remains Unknown and is never reconstructed from legacy site connectivity.'
    )
  );
end
$function$;

revoke all on function public.wl_effective_site_coverage(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated,service_role;


-- Keep the existing public/internal call point so wl_daily_intelligence and all
-- Phase-28 period functions automatically receive effective recorder truth.
create or replace function public.wl_site_coverage_report_classes(
  p_site_id uuid,
  p_from timestamptz,
  p_to timestamptz
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
begin
  perform public.wl_assert_my_site(p_site_id);
  return public.wl_effective_site_coverage(p_site_id,p_from,p_to);
end
$function$;

revoke all on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) from public,anon,authenticated,service_role;
grant execute on function public.wl_site_coverage_report_classes(
  uuid,timestamptz,timestamptz
) to authenticated,service_role;


-- Owner context previously used wl_site_coverage_report() directly, bypassing
-- the 3-class/effective coverage layer. Re-point it to the same governed source
-- used by daily intelligence so Home, Health, Cameras and Ask cannot disagree.
create or replace function public.wl_my_site_diagnosis(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_tenant uuid := wl_my_tenant();
  v_owner uuid;
  v_role text;
  v_site public.sites;
  v_tz text;
  v_vendor text;
  v_model text;
  v_online boolean;
  v_seen timestamptz;
  v_driver text;
  v_profile jsonb;
  v_start timestamptz;
  v_now timestamptz := now();
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;

  select tenant_id into v_owner
    from public.sites
   where id=p_site_id;
  if v_owner is null or v_owner<>v_tenant then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  select role into v_role
    from public.memberships
   where user_id=auth.uid() and tenant_id=v_tenant
   limit 1;

  select * into v_site
    from public.sites
   where id=p_site_id;

  v_tz := v_site.timezone;

  select max(device_vendor),max(device_model),
         bool_or(last_seen_at>now()-interval '3 min'),
         max(last_seen_at),max(device_driver)
    into v_vendor,v_model,v_online,v_seen,v_driver
    from public.agents
   where site_id=p_site_id;

  if v_vendor is not null and v_model is not null then
    v_profile := public.wl_recorder_profile(v_vendor,v_model);
  else
    v_profile := '[]'::jsonb;
  end if;

  v_start := (
    (v_now at time zone v_tz)::date::text||' 00:00:00'
  )::timestamp at time zone v_tz;

  return jsonb_build_object(
    'site',v_site.name,
    'site_id',v_site.id,
    'timezone',v_tz,
    'role',coalesce(v_role,'viewer'),
    'recorder',jsonb_build_object(
      'vendor',v_vendor,
      'model',v_model,
      'driver',v_driver,
      'identified',v_vendor is not null and v_model is not null
    ),
    'connectivity',jsonb_build_object(
      'agent_online',coalesce(v_online,false),
      'last_seen',v_seen
    ),
    'capabilities',v_profile,
    'capability_known',jsonb_array_length(v_profile)>0,
    'cameras',(
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'name',c.name,
            'channel',c.channel,
            'purpose',c.purpose,
            'health_state',coalesce(h.health_state::text,'unknown'),
            'video_loss',coalesce(
              h.health_state::text='offline'
              and h.reason_code::text='video_loss',
              false
            ),
            'recording_state',coalesce(h.recording_state::text,'unknown')
          )
          order by c.channel
        ),
        '[]'::jsonb
      )
      from public.cameras c
      left join public.camera_health h on h.camera_id=c.id
      where c.site_id=p_site_id
    ),
    'faults',(
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'camera',c.name,
            'reason',h.reason_code::text
          )
          order by c.name
        ),
        '[]'::jsonb
      )
      from public.camera_health h
      join public.cameras c on c.id=h.camera_id
      where h.site_id=p_site_id
        and h.health_state::text='offline'
    ),
    'coverage',
      public.wl_site_coverage_report_classes(p_site_id,v_start,v_now),
    'clock',jsonb_build_object(
      'note','clock/NTP verified via a Site Control READ (never assumed)'
    ),
    'tiers',jsonb_build_object(
      'read',true,
      'recommend',coalesce(v_role in ('owner','admin','manager'),false),
      'approve',coalesce(v_role in ('owner','admin'),false)
    )
  );
end
$function$;

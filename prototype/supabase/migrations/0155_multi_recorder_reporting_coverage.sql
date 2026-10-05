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
-- without duplicating vertical-specific coverage logic. wl_daily_intelligence
-- is restated below only so an Unknown (null) ratio becomes an explicit
-- caveat instead of 100% (MNVR-046).

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


-- Daily intelligence: Unknown coverage is never 100% (MNVR-046).
--
-- Identical to the 0099 v4 body except the coverage honesty rules:
--   * a null coverage_ratio means coverage is UNKNOWN (a multi-recorder window
--     that starts before recorder tracking, or no configured camera-time).
--     0099 coalesced it to 1, which hid both the gap caveat and the fact that
--     the day could not be measured. It now emits an explicit caveat and the
--     payload keeps coverage_ratio null (report_snapshots.coverage_ratio too);
--   * the recovered-time caveat also reads the multi-recorder
--     recovered_camera_seconds, since that payload has no legacy classes.
-- Payload schema stays daily_intelligence.v4 (one added caveat string).
create or replace function public.wl_daily_intelligence(
  p_site_id uuid, p_date date default null, p_derive boolean default true
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare
  v_site sites; v_date date; v_start timestamptz; v_end timestamptz; v_to timestamptz; v_tz text; v_partial boolean;
  v_office jsonb; v_coverage jsonb; v_episodes jsonb; v_incidents jsonb; v_attn jsonb;
  v_people jsonb; v_bounds jsonb; v_restricted jsonb; v_restr text[]; v_ratio numeric; v_open time;
begin
  select * into v_site from sites where id = p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode = '22023'; end if;
  v_tz := v_site.timezone;
  v_date := coalesce(p_date, (now() at time zone v_tz)::date);
  v_start := (v_date::text||' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';
  v_partial := v_end > now();
  v_to := least(v_end, greatest(v_start, now()));
  select coalesce(restricted_purposes,'{armory,restricted,vault,strongroom,safe}'), open_time
    into v_restr, v_open from site_business_context where site_id = p_site_id;
  if v_restr is null then v_restr := '{armory,restricted,vault,strongroom,safe}'; end if;

  if p_derive then
    perform wl_derive_activities(p_site_id, v_start, v_end);
    perform wl_derive_episodes(p_site_id, v_start, v_end);
    perform wl_derive_journeys(p_site_id, v_start, v_end);
    perform wl_promote_incidents(p_site_id, v_start, v_end);
    perform wl_derive_entity_inferences(p_site_id, v_start - interval '13 days', v_end);
  end if;

  v_office := wl_office_brief(p_site_id, v_date);
  v_coverage := wl_site_coverage_report_classes(p_site_id, v_start, v_to);   -- 3-class / camera-time coverage
  v_ratio := (v_coverage->>'coverage_ratio')::numeric;                        -- null = UNKNOWN, never 1
  v_people := wl_site_entity_inferences(p_site_id, v_start, v_end);
  v_bounds := wl_site_day_state(p_site_id, v_date);

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose, 'type', ep.episode_type,
           'object_class', ep.object_class,
           'start', to_char(ep.started_at at time zone v_tz,'HH24:MI'),
           'end', to_char(ep.ended_at at time zone v_tz,'HH24:MI'),
           'dwell_seconds', ep.dwell_seconds, 'detections', ep.detection_count) order by ep.started_at), '[]'::jsonb)
    into v_episodes from episodes ep left join cameras c on c.id = ep.camera_id
   where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end;

  select coalesce(jsonb_agg(jsonb_build_object(
           'camera', coalesce(c.name,'unassigned'), 'purpose', c.purpose, 'episodes', cnt,
           'last', to_char(last_at at time zone v_tz, 'HH24:MI')) order by c.name), '[]'::jsonb)
    into v_restricted from (
      select ep.camera_id, count(*) cnt, max(ep.ended_at) last_at from episodes ep
        left join cameras c on c.id = ep.camera_id
       where ep.site_id = p_site_id and ep.started_at >= v_start and ep.started_at < v_end
         and exists (select 1 from unnest(v_restr) term
                      where coalesce(c.purpose,'') ilike '%'||term||'%' or coalesce(c.name,'') ilike '%'||term||'%')
       group by ep.camera_id) z left join cameras c on c.id = z.camera_id;

  select coalesce(jsonb_agg(jsonb_build_object(
           'type', ii.incident_type, 'severity', ii.severity, 'camera', coalesce(c.name,'unassigned'),
           'purpose', c.purpose, 'time', to_char(ii.occurred_at at time zone v_tz,'HH24:MI'),
           'status', ii.status, 'policy', ii.detail_json->>'policy', 'detail', ii.detail_json)
           order by case ii.severity when 'critical' then 0 when 'warning' then 1 else 2 end, ii.occurred_at), '[]'::jsonb)
    into v_incidents from intel_incidents ii left join cameras c on c.id = ii.camera_id
   where ii.site_id = p_site_id and ii.occurred_at >= v_start and ii.occurred_at < v_end;

  select jsonb_build_object('incidents_total', count(*),
           'critical', count(*) filter (where severity='critical'),
           'warning', count(*) filter (where severity='warning'),
           'info', count(*) filter (where severity='info'))
    into v_attn from intel_incidents where site_id = p_site_id and occurred_at >= v_start and occurred_at < v_end;

  return jsonb_build_object(
    'schema', 'daily_intelligence.v4',
    'meta', jsonb_build_object('site', v_site.name, 'site_id', v_site.id, 'site_type', v_site.site_type,
       'date', v_date, 'timezone', v_tz, 'generated_at', now(), 'partial_day', v_partial),
    'day_boundaries', v_bounds,
    'office', v_office,
    'coverage', v_coverage,
    'access_windows', v_episodes,
    'restricted', v_restricted,
    'after_hours', v_bounds->'after_hours',
    'incidents', v_incidents,
    'attention', v_attn,
    'people', v_people,
    'honesty', (select coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from (
        select 'Counts are camera detections, not a headcount of distinct people.'::text as x
        union all select case when v_partial then 'Partial day: figures cover midnight to report time only.' end
        union all select case when v_ratio is null then 'Monitoring coverage could not be confirmed for this window, so it is not counted as fully monitored; some activity may be unobserved.' end
        union all select case when v_ratio < 1 then 'Monitoring had gaps in this window; some activity may be unobserved.' end
        union all select case when coalesce((v_coverage#>>'{classes,recovered_seconds}')::numeric,
                                            (v_coverage->>'recovered_camera_seconds')::numeric,0) > 0
                    then 'Coverage separates LIVE monitored, RECOVERED from the recorder, and UNVERIFIED time — never blended.' end
        union all select case when v_open is null then 'Business hours are not configured, so after-hours is not asserted.' end
        union all select 'Visitor/staff labels are estimated behavioral classifications from movement patterns, not identities.'
      ) z where x is not null));
end $$;
revoke all on function public.wl_daily_intelligence(uuid,date,boolean) from public, anon, authenticated;
grant execute on function public.wl_daily_intelligence(uuid,date,boolean) to service_role;


-- Owner context previously used wl_site_coverage_report() directly, bypassing
-- the 3-class/effective coverage layer. Re-point it to the same governed source
-- used by daily intelligence so Home, Health, Cameras and Ask cannot disagree.
--
-- Site Control diagnosis is recorder-aware (MNVR-048). The portal Site Control
-- page calls this directly and codes against this shape:
--
--   site, site_id, timezone, role          unchanged
--   recorder_count    int    configured recorders of the site
--   multi_recorder    bool   recorder_count > 1
--   recorder          {vendor, model, driver, firmware, identified} | null
--                     Site-level identity only while recorder identity is
--                     singleton (0 or 1 configured recorder): the configured
--                     recorder row when it reports vendor and model, else the
--                     current site Agent's reported device, always both values
--                     from ONE row (never max() across historical Agent rows).
--                     null on a multi-recorder site.
--   capabilities      [wl_recorder_profile rows] | null
--                     The singleton recorder's model profile; [] when its
--                     identity is unknown; null on a multi-recorder site.
--   capability_known  bool   false on a multi-recorder site
--   recorders         [{recorder_id, display_name, is_primary, vendor, model,
--                       firmware, identified, capability_known, camera_count,
--                       cameras:[{camera_id, channel, name, purpose,
--                                 health_state, video_loss, recording_state}]}]
--                     One entry per configured recorder, primary first.
--                     capability_known = the capability knowledge base has rows
--                     for that recorder's reported vendor/model; it is not proof
--                     that any capability was verified on that recorder.
--   cameras           [{camera_id, recorder_id, recorder_name, channel, name,
--                       purpose, health_state, video_loss, recording_state}]
--                     Canonical configured cameras of configured recorders
--                     only (no hidden ONVIF profile / legacy rows); channel is
--                     the physical channel; raw profile names show as
--                     'Camera N'; ordered by recorder, then channel.
--   faults            [{camera_id, recorder_id, camera, reason}]  same scope
--   connectivity, coverage, clock, tiers   unchanged
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
  v_recorder_count integer := 0;
  v_single public.recorders;
  v_agent public.agents;
  v_vendor text;
  v_model text;
  v_driver text;
  v_firmware text;
  v_identified boolean := false;
  v_online boolean;
  v_seen timestamptz;
  v_profile jsonb;
  v_recorders jsonb;
  v_cameras jsonb;
  v_faults jsonb;
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

  select bool_or(last_seen_at>now()-interval '3 min'),
         max(last_seen_at)
    into v_online,v_seen
    from public.agents
   where site_id=p_site_id;

  select count(*)::integer
    into v_recorder_count
    from public.recorders r
   where r.site_id=p_site_id
     and r.tenant_id=v_tenant
     and r.is_configured;

  if v_recorder_count<=1 then
    select r.* into v_single
      from public.recorders r
     where r.site_id=p_site_id
       and r.tenant_id=v_tenant
       and r.is_configured
     limit 1;

    select a.* into v_agent
      from public.agents a
     where a.id=public.wl_current_site_agent(p_site_id)
       and a.site_id=p_site_id;

    if v_single.vendor is not null and v_single.model is not null then
      v_vendor := v_single.vendor;
      v_model := v_single.model;
      v_firmware := v_single.firmware;
      v_driver := coalesce(v_single.driver,v_agent.device_driver);
    elsif v_agent.device_vendor is not null and v_agent.device_model is not null then
      v_vendor := v_agent.device_vendor;
      v_model := v_agent.device_model;
      v_driver := coalesce(v_agent.device_driver,v_single.driver);
    elsif v_single.vendor is not null or v_single.model is not null then
      v_vendor := v_single.vendor;
      v_model := v_single.model;
      v_firmware := v_single.firmware;
      v_driver := coalesce(v_single.driver,v_agent.device_driver);
    else
      v_vendor := v_agent.device_vendor;
      v_model := v_agent.device_model;
      v_driver := coalesce(v_agent.device_driver,v_single.driver);
    end if;

    v_identified := v_vendor is not null and v_model is not null;
    if v_identified then
      v_profile := coalesce(public.wl_recorder_profile(v_vendor,v_model),'[]'::jsonb);
    else
      v_profile := '[]'::jsonb;
    end if;
  else
    -- Never project one recorder's identity or capability truth onto the
    -- whole site; per-recorder facts are in 'recorders'.
    v_profile := null;
  end if;

  with cam as (
    select
      c.id camera_id,
      c.recorder_id,
      r.display_name recorder_name,
      r.is_primary,
      r.created_at recorder_created_at,
      coalesce(c.physical_channel,c.channel) channel,
      case
        when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
          then 'Camera '||coalesce(c.physical_channel,c.channel)
        else c.name
      end camera_name,
      c.purpose,
      coalesce(h.health_state::text,'unknown') health_state,
      coalesce(
        h.health_state::text='offline'
        and h.reason_code::text='video_loss',
        false
      ) video_loss,
      coalesce(h.recording_state::text,'unknown') recording_state,
      h.health_state::text raw_health_state,
      h.reason_code::text reason_code
    from public.cameras c
    join public.recorders r
      on r.id=c.recorder_id
     and r.tenant_id=c.tenant_id
     and r.site_id=c.site_id
     and r.is_configured
    left join public.camera_health h on h.camera_id=c.id
    where c.site_id=p_site_id
      and c.tenant_id=v_tenant
      and c.is_configured
      and coalesce(c.is_canonical,true)
  ),
  ordered as (
    select
      cam.*,
      case when cam.channel~'^[0-9]+$' then cam.channel::int else 2147483647 end channel_num
    from cam
  )
  select
    coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'camera_id',o.camera_id,
          'recorder_id',o.recorder_id,
          'recorder_name',o.recorder_name,
          'channel',o.channel,
          'name',o.camera_name,
          'purpose',o.purpose,
          'health_state',o.health_state,
          'video_loss',o.video_loss,
          'recording_state',o.recording_state
        )
        order by o.is_primary desc,o.recorder_created_at,o.recorder_id,
                 o.channel_num,o.channel,o.camera_id
      )
      from ordered o
    ),'[]'::jsonb),
    coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'camera_id',o.camera_id,
          'recorder_id',o.recorder_id,
          'camera',o.camera_name,
          'reason',o.reason_code
        )
        order by o.camera_name,o.camera_id
      )
      from ordered o
      where o.raw_health_state='offline'
    ),'[]'::jsonb)
  into v_cameras,v_faults;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'recorder_id',r.id,
        'display_name',r.display_name,
        'is_primary',r.is_primary,
        'vendor',case when v_recorder_count=1 then v_vendor else r.vendor end,
        'model',case when v_recorder_count=1 then v_model else r.model end,
        'firmware',case when v_recorder_count=1 then v_firmware else r.firmware end,
        'identified',case
          when v_recorder_count=1 then v_identified
          else r.vendor is not null and r.model is not null
        end,
        'capability_known',case
          when v_recorder_count=1 then jsonb_array_length(v_profile)>0
          when r.vendor is not null and r.model is not null then jsonb_array_length(
            coalesce(public.wl_recorder_profile(r.vendor,r.model),'[]'::jsonb)
          )>0
          else false
        end,
        'camera_count',jsonb_array_length(rc.cameras),
        'cameras',rc.cameras
      )
      order by r.is_primary desc,r.created_at,r.id
    ),
    '[]'::jsonb
  )
  into v_recorders
  from public.recorders r
  cross join lateral (
    select coalesce(
      jsonb_agg(x-'recorder_id'-'recorder_name' order by e.ord),
      '[]'::jsonb
    ) cameras
    from jsonb_array_elements(v_cameras) with ordinality e(x,ord)
    where x->>'recorder_id'=r.id::text
  ) rc
  where r.site_id=p_site_id
    and r.tenant_id=v_tenant
    and r.is_configured;

  v_start := (
    (v_now at time zone v_tz)::date::text||' 00:00:00'
  )::timestamp at time zone v_tz;

  return jsonb_build_object(
    'site',v_site.name,
    'site_id',v_site.id,
    'timezone',v_tz,
    'role',coalesce(v_role,'viewer'),
    'recorder_count',v_recorder_count,
    'multi_recorder',v_recorder_count>1,
    'recorder',case
      when v_recorder_count>1 then null
      else jsonb_build_object(
        'vendor',v_vendor,
        'model',v_model,
        'driver',v_driver,
        'firmware',v_firmware,
        'identified',v_identified
      )
    end,
    'recorders',v_recorders,
    'connectivity',jsonb_build_object(
      'agent_online',coalesce(v_online,false),
      'last_seen',v_seen
    ),
    'capabilities',v_profile,
    'capability_known',coalesce(jsonb_array_length(v_profile)>0,false),
    'cameras',v_cameras,
    'faults',v_faults,
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

-- Restate the 0079 ACL: authenticated callers only, tenant-checked above.
revoke all on function public.wl_my_site_diagnosis(uuid) from public,anon;
grant execute on function public.wl_my_site_diagnosis(uuid) to authenticated,service_role;

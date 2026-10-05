-- 0157 - Owner capability truth is recorder-scoped on a single-recorder site.
--
-- STACKED AFTER 0156. REPO ONLY until separately approved.
--
-- Site Control (portal) offers a setting for change only when its evidence is
-- FIELD_VERIFIED *on this recorder* (evidence_scope = 'recorder', MNVR-049).
-- The single-recorder diagnosis (0155) served wl_recorder_profile(vendor,model),
-- the model-level profile, which has no evidence_scope at all. So no site could
-- reach "Configurable", while Watch AI, reading the same model-level rows through
-- wl_ai_context, still treated model-level FIELD_VERIFIED as a configurable
-- recorder capability. The two surfaces disagreed.
--
-- wl_my_site_diagnosis now serves wl_recorder_profile_for_recorder (0149) for the
-- configured recorder whenever the site identity is that recorder's own
-- vendor/model. Same row shape plus evidence_scope; a FIELD_VERIFIED proven only
-- on another unit of the model is downgraded there, exactly as the write path
-- (0150) requires. wl_ai_context reads capabilities from this diagnosis, so Watch
-- AI gets the same recorder-scoped truth. Everything else is the 0155 body.
--
-- wl_ai_context 'faults' (0152) carried only {camera: raw name, reason}, ordered
-- by name. Profile-named cameras are 'Camera '||channel, so two recorders that
-- both have channel 1 produced identical fault rows in either order, and System
-- Health could only guess which camera (and recorder) a fault was about. Each
-- fault now also carries camera_id and recorder_id, as the diagnosis faults do
-- (0155), and the order is deterministic. Everything else is the 0152 body; the
-- payload stays additive (facts_version v7).

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
--   capabilities      [wl_recorder_profile_for_recorder rows] | null
--                     The singleton recorder's own capability truth (0157):
--                     model rows with this recorder's evidence overlay and
--                     evidence_scope. FIELD_VERIFIED + evidence_scope
--                     'recorder' only when this recorder's identity has its
--                     own field evidence; a model-level FIELD_VERIFIED from
--                     another unit is downgraded (0149). When the identity
--                     comes only from the current site Agent (the recorder
--                     row reports no vendor/model) it is the model profile
--                     with model-only FIELD_VERIFIED capped
--                     (wl_recorder_profile_model_scoped, 0155, evidence_scope
--                     'model'), so no reader can present another unit's field
--                     evidence as verified on this recorder.
--                     [] when identity is unknown; null on a multi-recorder
--                     site.
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
    if v_identified
       and v_single.id is not null
       and v_single.vendor=v_vendor
       and v_single.model=v_model then
      -- Recorder-scoped truth (MNVR-049): evidence_scope says whether a
      -- FIELD_VERIFIED row was proven on THIS recorder.
      v_profile := coalesce(public.wl_recorder_profile_for_recorder(v_single.id),'[]'::jsonb);
    elsif v_identified then
      -- Identity known only from the Agent's report: the model profile with any
      -- model-only FIELD_VERIFIED capped exactly as the recorder-scoped helper caps
      -- it (0155 wl_recorder_profile_model_scoped, MNVR-049).
      v_profile := coalesce(public.wl_recorder_profile_model_scoped(v_vendor,v_model),'[]'::jsonb);
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
            coalesce(public.wl_recorder_profile_for_recorder(r.id),'[]'::jsonb)
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


-- Owner/AI context: every fault names its camera and recorder (additive).
create or replace function public.wl_ai_context(p_site_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $function$
declare
  v_tenant uuid := wl_assert_my_site(p_site_id);
  v_site public.sites;
  v_diag jsonb;
  v_ctx jsonb;
  v_onboarding jsonb;
  v_recent jsonb;
  v_camera_rows jsonb;
  v_recorders jsonb;
  v_recorder_count int := 0;
begin
  select * into v_site
    from public.sites
   where id=p_site_id
     and tenant_id=v_tenant;

  v_diag := public.wl_my_site_diagnosis(p_site_id);
  v_ctx := public.wl_my_site_context(p_site_id);
  v_onboarding := public.wl_onboarding_status(p_site_id);
  v_recorders := public.wl_my_site_recorders(p_site_id);
  v_recorder_count := coalesce(jsonb_array_length(v_recorders->'recorders'),0);

  select coalesce(
    jsonb_agg(to_jsonb(r) order by r.device_ts desc),
    '[]'::jsonb
  )
  into v_recent
  from (
    select
      e.id as event_id,
      e.camera_id,
      e.recorder_id,
      e.event_type,
      e.device_ts,
      e.received_at,
      case
        when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
          then 'Camera '||coalesce(c.physical_channel,c.channel)
        else c.name
      end as camera,
      coalesce(c.physical_channel,c.channel) as channel,
      coalesce(e.payload->>'source','live') as source,
      case lower(trim(coalesce(e.payload->>'recovered','false')))
        when 'true' then true
        when 't' then true
        when '1' then true
        when 'yes' then true
        else false
      end as recovered
    from public.events e
    left join public.cameras c on c.id=e.camera_id
    where e.site_id=p_site_id
      and e.tenant_id=v_tenant
    order by e.device_ts desc
    limit 20
  ) r;

  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id',c.id,
        'recorder_id',c.recorder_id,
        'channel',coalesce(c.physical_channel,c.channel),
        'name',case
          when c.name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
            then 'Camera '||coalesce(c.physical_channel,c.channel)
          else c.name
        end,
        'purpose',c.purpose,
        'monitor',c.is_configured,
        'analytics_enabled',coalesce(c.analytics_enabled,false),
        'health_state',coalesce(h.health_state::text,'unknown'),
        'recording_state',coalesce(h.recording_state::text,'unknown')
      )
      order by
        c.recorder_id,
        case
          when coalesce(c.physical_channel,c.channel) ~ '^[0-9]+$'
            then coalesce(c.physical_channel,c.channel)::int
          else 2147483647
        end,
        coalesce(c.physical_channel,c.channel)
    ),
    '[]'::jsonb
  )
  into v_camera_rows
  from public.cameras c
  join public.recorders cr
    on cr.id=c.recorder_id
   and cr.tenant_id=c.tenant_id
   and cr.site_id=c.site_id
   and cr.is_configured
  left join public.camera_health h on h.camera_id=c.id
  where c.site_id=p_site_id
    and c.tenant_id=v_tenant
    and coalesce(c.is_canonical,true);

  return jsonb_build_object(
    'facts_version','watchlog-ai-context-v7',
    'generated_at',now(),
    'site',jsonb_build_object(
      'id',v_site.id,
      'name',v_site.name,
      'timezone',v_site.timezone
    ),
    'business_context',v_ctx,
    'onboarding',v_onboarding,
    -- Legacy recorder/capability diagnosis is only valid while recorder
    -- identity is singleton. On a true multi-recorder site fail closed rather
    -- than projecting one recorder's capability truth onto the whole site.
    'recorder',case when v_recorder_count<=1 then v_diag->'recorder' else null end,
    'recorders',coalesce(v_recorders->'recorders','[]'::jsonb),
    'connectivity',v_diag->'connectivity',
    'capabilities',case
      when v_recorder_count<=1 then v_diag->'capabilities'
      else '{}'::jsonb
    end,
    'capability_known',case
      when v_recorder_count<=1 then coalesce((v_diag->>'capability_known')::boolean,false)
      else false
    end,
    'cameras',v_camera_rows,
    'faults',(
      select coalesce(
        jsonb_agg(
          jsonb_build_object(
            'camera_id',c.id,
            'recorder_id',c.recorder_id,
            'camera',c.name,
            'reason',coalesce(h.reason_code::text,'unknown')
          )
          order by c.name,c.id
        ),
        '[]'::jsonb
      )
      from public.camera_health h
      join public.cameras c on c.id=h.camera_id
      join public.recorders fr
        on fr.id=c.recorder_id
       and fr.tenant_id=c.tenant_id
       and fr.site_id=c.site_id
       and fr.is_configured
      where h.site_id=p_site_id
        and h.tenant_id=v_tenant
        and c.is_configured
        and coalesce(c.is_canonical,true)
        and h.health_state::text='offline'
    ),
    'coverage',v_diag->'coverage',
    'permissions',v_diag->'tiers',
    'recent_events',v_recent,
    'safety',jsonb_build_object(
      'recorder_credentials_leave_site',false,
      'recorder_writes_require_approval',true,
      'unknown_capability_must_not_be_assumed',true
    )
  );
end
$function$;

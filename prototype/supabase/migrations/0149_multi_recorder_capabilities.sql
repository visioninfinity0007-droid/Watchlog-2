-- 0149 - Recorder-scoped capability truth.
--
-- STACKED AFTER 0148. REPO ONLY until separately approved.
--
-- The legacy capability RPC remains the compatibility path for a true
-- single-recorder site. Multi-recorder Agents use the explicit recorder RPC.
-- Recorder capability sync never fabricates or unions a site-wide capability:
-- one recorder proving a feature does not prove every recorder/camera at the site.

create or replace function public.wl_multi_recorder_agent_contract(
  p_agent_id uuid,
  p_agent_key text
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  return jsonb_build_object(
    'ok',true,
    'version',1,
    'configured_recorders',(
      select count(*)
        from public.recorders r
       where r.tenant_id=v_agent.tenant_id
         and r.site_id=v_agent.site_id
         and r.is_configured
    ),
    'features',jsonb_build_array(
      'recorders',
      'recorder_cameras',
      'recorder_events',
      'recorder_health',
      'recorder_recovery',
      'recorder_reconciliation',
      'recorder_capabilities'
    ),
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_multi_recorder_agent_contract(uuid,text)
  from public,anon,authenticated,service_role;
grant execute on function public.wl_multi_recorder_agent_contract(uuid,text)
  to anon;

create or replace function public.wl_overlay_recorder_camera_truth(
  p_site_id uuid,
  p_recorder_id uuid,
  p_capabilities jsonb
) returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
  select case
    when p_capabilities is null then null
    when jsonb_typeof(p_capabilities) <> 'object' then p_capabilities
    when jsonb_typeof(p_capabilities->'channels') <> 'array' then p_capabilities
    else jsonb_set(
      p_capabilities,
      '{channels}',
      coalesce((
        select jsonb_agg(
          case
            when c.id is null then
              q.item || jsonb_build_object(
                'configuration_state','unknown',
                'configured',null
              )
            else
              q.item || jsonb_build_object(
                'camera_id',c.id,
                'name',c.name,
                'configured',c.is_configured,
                'configuration_state',
                  case when c.is_configured then 'configured'
                       else 'no_camera_configured' end
              )
          end
          order by q.ord
        )
        from jsonb_array_elements(p_capabilities->'channels')
             with ordinality as q(item,ord)
        left join public.cameras c
          on c.site_id=p_site_id
         and c.recorder_id=p_recorder_id
         and c.channel=q.item->>'channel'
      ),'[]'::jsonb),
      true
    )
  end
$function$;

revoke all on function public.wl_overlay_recorder_camera_truth(
  uuid,uuid,jsonb
) from public,anon,authenticated,service_role;

create or replace function public.wl_sync_recorder_capabilities(
  p_agent_id uuid,
  p_agent_key text,
  p_recorder_id uuid,
  p_capabilities jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_agent public.agents;
  v_effective jsonb;
begin
  v_agent := public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then
    raise exception 'agent not recognised' using errcode='28000';
  end if;

  if public.wl_current_site_agent(v_agent.site_id) is distinct from v_agent.id then
    raise exception 'agent is not current site authority' using errcode='42501';
  end if;

  if not exists (
    select 1
      from public.recorders r
     where r.id=p_recorder_id
       and r.tenant_id=v_agent.tenant_id
       and r.site_id=v_agent.site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this agent site'
      using errcode='42501';
  end if;

  v_effective := public.wl_overlay_recorder_camera_truth(
    v_agent.site_id,p_recorder_id,p_capabilities
  );

  update public.recorders
     set capabilities=v_effective,
         capabilities_at=now(),
         updated_at=now()
   where id=p_recorder_id
     and tenant_id=v_agent.tenant_id
     and site_id=v_agent.site_id;

  update public.agents
     set last_seen_at=now()
   where id=v_agent.id;

  return jsonb_build_object(
    'ok',true,
    'recorder_id',p_recorder_id,
    'channels',case
      when jsonb_typeof(v_effective->'channels')='array'
      then jsonb_array_length(v_effective->'channels')
      else 0
    end,
    'server_time',now()
  );
end
$function$;

revoke all on function public.wl_sync_recorder_capabilities(
  uuid,text,uuid,jsonb
) from public,anon,authenticated,service_role;
grant execute on function public.wl_sync_recorder_capabilities(
  uuid,text,uuid,jsonb
) to anon;

-- ---------------------------------------------------------------------
-- Recorder-scoped write eligibility (MNVR-049).
--
-- The 0061 knowledge base is keyed by vendor + model. Its FIELD_VERIFIED rows
-- come from one field test on one physical unit (FIELD-AKSS-001, "site-specific,
-- not manufacturer truth"), so they prove nothing about another recorder of the
-- same model, nor about the same recorder on other firmware. Field evidence now
-- names the recorder, identity fingerprint and firmware it was taken on, and
-- only evidence that matches all three makes a capability FIELD_VERIFIED for a
-- recorder. Model-only field evidence is capped below FIELD_VERIFIED.
-- ---------------------------------------------------------------------
alter table public.recorder_field_evidence
  add column if not exists recorder_id uuid
    references public.recorders(id) on delete set null;

alter table public.recorder_field_evidence
  add column if not exists identity_fingerprint text;

create index if not exists recorder_field_evidence_recorder_idx
  on public.recorder_field_evidence(recorder_id,capability)
  where recorder_id is not null;

-- Owner-only helper. Callers authenticate and authorize the recorder first.
-- Same shape as wl_recorder_capability, plus recorder scope:
--   evidence_scope       'recorder' when this recorder's own evidence is used,
--                        'model' when only vendor/model knowledge applies;
--   model_evidence_class the knowledge-base grade before recorder scoping;
--   field_write_verified this recorder's evidence covers a read-back-verified
--                        write of the capability.
create or replace function public.wl_recorder_capability_for_recorder(
  p_recorder_id uuid,
  p_capability text
) returns jsonb
language plpgsql
stable
set search_path = public
as $function$
declare
  v_recorder public.recorders;
  v_model jsonb;
  v_field boolean := false;
  v_field_write boolean := false;
  v_official boolean := false;
  v_class text;
  v_scope text := 'model';
begin
  select *
    into v_recorder
    from public.recorders r
   where r.id=p_recorder_id;

  if v_recorder.id is null
     or nullif(btrim(v_recorder.vendor),'') is null
     or nullif(btrim(v_recorder.model),'') is null
  then
    return jsonb_build_object(
      'recorder_id',p_recorder_id,
      'capability',p_capability,
      'verdict','unknown',
      'evidence_class','UNKNOWN',
      'model_evidence_class','UNKNOWN',
      'evidence_scope','none',
      'field_write_verified',false,
      'read',null,
      'write',null,
      'safety_class','na',
      'source_ids','[]'::jsonb,
      'notes','recorder identity unknown'
    );
  end if;

  v_model := public.wl_recorder_capability(
    v_recorder.vendor,v_recorder.model,p_capability,v_recorder.firmware
  );

  -- Evidence counts only for the unit, identity and firmware it was taken on.
  -- An unknown recorder identity or firmware never matches.
  select coalesce(bool_or(true),false),
         coalesce(bool_or(
           e.operation in ('write','read_write')
           and e.read_back_verified is true
         ),false)
    into v_field,v_field_write
    from public.recorder_field_evidence e
   where e.recorder_id=v_recorder.id
     and e.capability=p_capability
     and e.evidence_class='FIELD_VERIFIED'
     and e.vendor=v_recorder.vendor
     and e.model=v_recorder.model
     and nullif(btrim(v_recorder.identity_fingerprint),'') is not null
     and e.identity_fingerprint=v_recorder.identity_fingerprint
     and nullif(btrim(v_recorder.firmware),'') is not null
     and e.firmware=v_recorder.firmware;

  select exists (
    select 1
      from jsonb_array_elements_text(
             coalesce(v_model->'source_ids','[]'::jsonb)
           ) s(id)
      join public.recorder_capability_sources src
        on src.id=s.id
       and src.source_type='official'
  )
  into v_official;

  if v_field and v_model->>'verdict'='supported' then
    -- This recorder's own evidence confirms the documented/implemented support.
    v_class := 'FIELD_VERIFIED';
    v_scope := 'recorder';
  elsif v_model->>'evidence_class'='FIELD_VERIFIED' then
    -- Proven on another unit only: documented support at most.
    v_class := case when v_official then 'OFFICIAL_DOCUMENTED'
                    else 'IMPLEMENTED_UNVERIFIED' end;
  else
    v_class := v_model->>'evidence_class';
  end if;

  return v_model || jsonb_build_object(
    'recorder_id',v_recorder.id,
    'evidence_class',v_class,
    'model_evidence_class',v_model->>'evidence_class',
    'evidence_scope',v_scope,
    'field_write_verified',(v_scope='recorder' and v_field_write)
  );
end
$function$;

revoke all on function public.wl_recorder_capability_for_recorder(uuid,text)
  from public,anon,authenticated,service_role;

-- Owner-only, recorder-scoped counterpart of wl_recorder_profile (same row
-- shape plus evidence_scope) for read models that show one recorder's
-- capability truth. A same-model sibling's field evidence never shows here as
-- FIELD_VERIFIED.
create or replace function public.wl_recorder_profile_for_recorder(
  p_recorder_id uuid
) returns jsonb
language sql
stable
set search_path = public
as $function$
  select coalesce(jsonb_agg(jsonb_build_object(
           'capability',x.cap->>'capability',
           'verdict',x.cap->>'verdict',
           'evidence_class',x.cap->>'evidence_class',
           'evidence_scope',x.cap->>'evidence_scope',
           'ai_location',x.cap->'ai_location',
           'read',x.cap->'read',
           'write',x.cap->'write',
           'safety_class',x.cap->'safety_class',
           'constraints',x.cap->'constraints',
           'source_ids',x.cap->'source_ids'
         ) order by x.capability),'[]'::jsonb)
    from (
      select k.capability,
             public.wl_recorder_capability_for_recorder(r.id,k.capability) as cap
        from public.recorders r
        join (
          select distinct c.vendor,c.model,c.capability
            from public.recorder_capabilities c
        ) k
          on k.vendor=r.vendor
         and k.model=r.model
       where r.id=p_recorder_id
    ) x
$function$;

revoke all on function public.wl_recorder_profile_for_recorder(uuid)
  from public,anon,authenticated,service_role;

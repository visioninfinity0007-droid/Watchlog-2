-- ---------------------------------------------------------------------
-- Portal/customer read models for the canonical incident object.
-- ---------------------------------------------------------------------
create or replace function public.wl_operations_incidents_v2(
  p_site_id uuid,
  p_days int default 7,
  p_review_status text default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_owner uuid;
  v_days int := least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then
    raise exception 'not signed in' using errcode='42501';
  end if;

  select tenant_id into v_owner from public.sites where id=p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  return coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'id',i.id,
        'site_id',i.site_id,
        'camera_id',i.camera_id,
        'camera',c.name,
        'incident_type',i.incident_type,
        'object_class',i.object_class,
        'severity',i.severity,
        'review_status',i.status,
        'lifecycle_state',i.lifecycle_state,
        'review_required',i.review_required,
        'sensitive',i.sensitive,
        'started_at',i.incident_started_at,
        'last_activity_at',i.last_activity_at,
        'ended_at',i.incident_ended_at,
        'duration_seconds',
          case when i.incident_started_at is null then null
               else extract(epoch from (
                 coalesce(i.incident_ended_at,i.last_activity_at,now())
                 - i.incident_started_at
               )) end,
        'evidence_window_start',i.evidence_window_start,
        'evidence_window_end',i.evidence_window_end,
        'confidence',i.lifecycle_confidence,
        'summary',i.summary_json,
        'detail',i.detail,
        'opened_at',i.opened_at,
        'acknowledged_at',i.acknowledged_at,
        'resolved_at',i.resolved_at
      )
      order by i.incident_started_at desc nulls last, i.id desc
    )
      from public.operations_incidents i
      left join public.cameras c on c.id=i.camera_id
     where i.site_id=p_site_id
       and i.tenant_id=v_tenant
       and i.opened_at >= now()-make_interval(days=>v_days)
       and (p_review_status is null or i.status=p_review_status)
  ),'[]'::jsonb);
end $$;

revoke all on function public.wl_operations_incidents_v2(uuid,int,text)
  from public, anon;
grant execute on function public.wl_operations_incidents_v2(uuid,int,text)
  to authenticated, service_role;

create or replace function public.wl_operations_incident_detail(
  p_incident_id bigint
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_inc public.operations_incidents;
begin
  if v_tenant is null then
    raise exception 'not signed in' using errcode='42501';
  end if;

  select * into v_inc
    from public.operations_incidents
   where id=p_incident_id and tenant_id=v_tenant;

  if v_inc.id is null then
    raise exception 'incident not in your account' using errcode='42501';
  end if;

  return jsonb_build_object(
    'incident',to_jsonb(v_inc),
    'camera',(select jsonb_build_object(
       'id',c.id,'name',c.name,'channel',c.channel,'purpose',c.purpose)
       from public.cameras c where c.id=v_inc.camera_id),
    'lifecycle',coalesce((
      select jsonb_agg(jsonb_build_object(
        'event',e.event_type,
        'at',e.occurred_at,
        'detail',e.detail
      ) order by e.occurred_at,e.id)
      from public.operations_incident_lifecycle_events e
      where e.incident_id=v_inc.id
    ),'[]'::jsonb),
    'evidence',public.wl_operations_incident_evidence(v_inc.id)
  );
end $$;

revoke all on function public.wl_operations_incident_detail(bigint)
  from public, anon;
grant execute on function public.wl_operations_incident_detail(bigint)
  to authenticated, service_role;

-- p_site_id NULL means all sites in the signed-in tenant. This preserves the
-- Incidents page's All Sites UX without weakening tenant isolation.
create or replace function public.wl_operations_incidents_v2(
  p_site_id uuid,
  p_days int default 7,
  p_review_status text default null
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_days int := least(greatest(coalesce(p_days,7),1),90);
begin
  if v_tenant is null then
    raise exception 'not signed in' using errcode='42501';
  end if;

  if p_site_id is not null and not exists(
    select 1 from public.sites
     where id=p_site_id and tenant_id=v_tenant
  ) then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;

  return coalesce((
    select jsonb_agg(
      jsonb_build_object(
        'id',i.id,
        'site_id',i.site_id,
        'site',s.name,
        'camera_id',i.camera_id,
        'camera',c.name,
        'incident_type',i.incident_type,
        'object_class',i.object_class,
        'severity',i.severity,
        'review_status',i.status,
        'lifecycle_state',i.lifecycle_state,
        'review_required',i.review_required,
        'sensitive',i.sensitive,
        'started_at',i.incident_started_at,
        'last_activity_at',i.last_activity_at,
        'ended_at',i.incident_ended_at,
        'duration_seconds',
          case when i.incident_started_at is null then null
               else extract(epoch from (
                 coalesce(i.incident_ended_at,i.last_activity_at,now())
                 - i.incident_started_at
               )) end,
        'evidence_window_start',i.evidence_window_start,
        'evidence_window_end',i.evidence_window_end,
        'confidence',i.lifecycle_confidence,
        'summary',i.summary_json,
        'detail',i.detail,
        'opened_at',i.opened_at,
        'acknowledged_at',i.acknowledged_at,
        'resolved_at',i.resolved_at
      )
      order by i.incident_started_at desc nulls last, i.id desc
    )
      from public.operations_incidents i
      join public.sites s on s.id=i.site_id
      left join public.cameras c on c.id=i.camera_id
     where i.tenant_id=v_tenant
       and (p_site_id is null or i.site_id=p_site_id)
       and i.opened_at >= now()-make_interval(days=>v_days)
       and (p_review_status is null or i.status=p_review_status)
  ),'[]'::jsonb);
end $$;

revoke all on function public.wl_operations_incidents_v2(uuid,int,text)
  from public, anon;
grant execute on function public.wl_operations_incidents_v2(uuid,int,text)
  to authenticated, service_role;

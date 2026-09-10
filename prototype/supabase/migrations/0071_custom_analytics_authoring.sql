-- =====================================================================
-- 0071 — Custom analytics workflow: tenant-authored incident policies.
--
-- incident_policies (0065) is WHAT makes an activity an incident. This lets a tenant author
-- those rules themselves from the portal — "any person at the armory after 19:00 -> critical",
-- "dwell over 5 min at the loading bay -> warning" — which is exactly the software-defined
-- analytics that lets a recorder WITHOUT native IVS (e.g. the Cooper-I on the live site) still
-- produce restricted-area / after-hours / dwell incidents from plain person detections.
--
-- All three entry points are tenant-guarded (wl_my_tenant): a tenant can only see and edit
-- policies for its own sites, and can never target another tenant's site or policy id.
-- =====================================================================

create or replace function public.wl_my_incident_policies(p_site_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'id', p.id, 'name', p.name, 'match_object_class', p.match_object_class,
            'match_purpose_ilike', p.match_purpose_ilike, 'match_episode_type', p.match_episode_type,
            'after_hours_only', p.after_hours_only, 'min_dwell_seconds', p.min_dwell_seconds,
            'promote_to', p.promote_to, 'severity', p.severity, 'enabled', p.enabled)
            order by p.created_at), '[]'::jsonb)
          from incident_policies p where p.site_id = p_site_id);
end $$;
revoke all on function public.wl_my_incident_policies(uuid) from public, anon;
grant execute on function public.wl_my_incident_policies(uuid) to authenticated, service_role;

create or replace function public.wl_upsert_incident_policy(
  p_site_id uuid,
  p_name text,
  p_promote_to text,
  p_severity text default 'warning',
  p_match_object_class text default null,
  p_match_purpose_ilike text default null,
  p_match_episode_type text default null,
  p_after_hours_only boolean default false,
  p_min_dwell_seconds numeric default null,
  p_enabled boolean default true,
  p_id uuid default null
) returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid; v_id uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  select tenant_id into v_owner from sites where id = p_site_id;
  if v_owner is null or v_owner <> v_tenant then
    raise exception 'not authorized for this site' using errcode = '42501';
  end if;
  if coalesce(trim(p_name), '') = '' then raise exception 'name required' using errcode = '22023'; end if;
  if coalesce(trim(p_promote_to), '') = '' then raise exception 'promote_to required' using errcode = '22023'; end if;
  if p_severity not in ('info','warning','critical') then
    raise exception 'severity must be info|warning|critical' using errcode = '22023';
  end if;

  if p_id is null then
    insert into incident_policies (tenant_id, site_id, name, match_object_class, match_purpose_ilike,
           match_episode_type, after_hours_only, min_dwell_seconds, promote_to, severity, enabled)
    values (v_tenant, p_site_id, p_name, p_match_object_class, p_match_purpose_ilike,
           p_match_episode_type, coalesce(p_after_hours_only,false), p_min_dwell_seconds,
           p_promote_to, p_severity, coalesce(p_enabled,true))
    returning id into v_id;
  else
    -- Update guarded by BOTH id and tenant, so a caller can never edit another tenant's policy.
    update incident_policies set
        name = p_name, match_object_class = p_match_object_class,
        match_purpose_ilike = p_match_purpose_ilike, match_episode_type = p_match_episode_type,
        after_hours_only = coalesce(p_after_hours_only,false), min_dwell_seconds = p_min_dwell_seconds,
        promote_to = p_promote_to, severity = p_severity, enabled = coalesce(p_enabled,true)
      where id = p_id and tenant_id = v_tenant and site_id = p_site_id
      returning id into v_id;
    if v_id is null then raise exception 'policy not found for this tenant/site' using errcode = '42501'; end if;
  end if;
  return jsonb_build_object('id', v_id, 'ok', true);
end $$;
revoke all on function public.wl_upsert_incident_policy(uuid,text,text,text,text,text,text,boolean,numeric,boolean,uuid) from public, anon;
grant execute on function public.wl_upsert_incident_policy(uuid,text,text,text,text,text,text,boolean,numeric,boolean,uuid) to authenticated, service_role;

create or replace function public.wl_delete_incident_policy(p_id uuid)
returns jsonb
language plpgsql volatile security definer set search_path = public as $$
declare v_tenant uuid := wl_my_tenant(); v_del uuid;
begin
  if v_tenant is null then raise exception 'not authenticated' using errcode = '42501'; end if;
  delete from incident_policies where id = p_id and tenant_id = v_tenant returning id into v_del;
  if v_del is null then raise exception 'policy not found for this tenant' using errcode = '42501'; end if;
  return jsonb_build_object('id', v_del, 'deleted', true);
end $$;
revoke all on function public.wl_delete_incident_policy(uuid) from public, anon;
grant execute on function public.wl_delete_incident_policy(uuid) to authenticated, service_role;

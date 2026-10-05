
-- 0145: Camera preview performance and batched read contract.
-- Avoid sorting/encoding every candidate image before choosing the latest row.

create or replace function public.wl_camera_config_snapshot(p_camera_id uuid)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public'
as $function$
declare
  v_tenant uuid;
  v_cfg record;
  v_snap record;
  v_selected record;
begin
  select tenant_id into v_tenant
  from public.cameras
  where id=p_camera_id;

  if v_tenant is null or not public.wl_is_member(v_tenant) then
    return null;
  end if;

  -- camera_config_snapshots is one row per camera (PK camera_id).
  select
    cs.camera_id,
    cs.content_type,
    cs.bytes,
    cs.captured_at,
    cs.image
  into v_cfg
  from public.camera_config_snapshots cs
  where cs.camera_id=p_camera_id
  limit 1;

  -- snapshots_camera_captured_idx supports this direct latest-row lookup.
  select
    s.camera_id,
    s.content_type,
    s.bytes,
    s.captured_at,
    s.image
  into v_snap
  from public.snapshots s
  where s.camera_id=p_camera_id
  order by s.captured_at desc
  limit 1;

  if v_cfg.camera_id is null and v_snap.camera_id is null then
    return null;
  end if;

  if v_snap.camera_id is null
     or (v_cfg.camera_id is not null and v_cfg.captured_at >= v_snap.captured_at) then
    v_selected := v_cfg;
  else
    v_selected := v_snap;
  end if;

  return jsonb_build_object(
    'camera_id',v_selected.camera_id,
    'content_type',v_selected.content_type,
    'bytes',v_selected.bytes,
    'captured_at',v_selected.captured_at,
    -- Encode only the selected image.
    'image_b64',encode(v_selected.image,'base64')
  );
end
$function$;

create or replace function public.wl_camera_config_snapshots(p_camera_ids uuid[])
returns jsonb
language plpgsql
stable security definer
set search_path to 'public','pg_temp'
as $function$
declare
  v_tenant uuid := public.wl_my_tenant();
  v_count integer := coalesce(cardinality(p_camera_ids),0);
begin
  if v_tenant is null then
    raise exception 'not authenticated' using errcode='42501';
  end if;

  if v_count=0 then
    return '[]'::jsonb;
  end if;

  if v_count>64 then
    raise exception 'too many camera ids; maximum is 64' using errcode='22023';
  end if;

  return coalesce((
    with requested as (
      select u.camera_id,min(u.ord) as ord
      from unnest(p_camera_ids) with ordinality as u(camera_id,ord)
      group by u.camera_id
    ),
    allowed as (
      select r.camera_id,r.ord
      from requested r
      join public.cameras c
        on c.id=r.camera_id
       and c.tenant_id=v_tenant
    ),
    resolved as (
      select a.ord,a.camera_id,public.wl_camera_config_snapshot(a.camera_id) as snapshot
      from allowed a
    )
    select jsonb_agg(
      coalesce(
        snapshot,
        jsonb_build_object('camera_id',camera_id,'available',false)
      )
      order by ord
    )
    from resolved
  ),'[]'::jsonb);
end
$function$;

revoke all on function public.wl_camera_config_snapshots(uuid[]) from public, anon;
grant execute on function public.wl_camera_config_snapshots(uuid[]) to authenticated, service_role;

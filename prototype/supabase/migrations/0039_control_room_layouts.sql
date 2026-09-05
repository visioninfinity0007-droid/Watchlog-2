-- =====================================================================
-- 0039 - Control Room saved layouts
--
-- Control Room layouts are tenant configuration, not a video-streaming
-- feature. Layouts store only camera references and presentation order.
-- No recorder credentials, video URLs or image payloads are stored here.
--
-- Tables remain fail-closed behind RLS with no direct client grants. Portal
-- access goes through tenant-scoped SECURITY DEFINER RPCs. Any tenant member
-- can read layouts; only Owner/Admin can create, change or delete them.
-- =====================================================================

create table if not exists public.control_room_layouts (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  name        text not null,
  grid_size   smallint not null default 2,
  camera_ids  uuid[] not null default '{}'::uuid[],
  created_by  uuid not null,
  updated_by  uuid not null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint control_room_layouts_grid_size_check check (grid_size in (2,3,4)),
  constraint control_room_layouts_name_check check (length(btrim(name)) between 1 and 80),
  constraint control_room_layouts_capacity_check check (cardinality(camera_ids) <= grid_size * grid_size),
  constraint control_room_layouts_tenant_name_uniq unique (tenant_id, name)
);

create index if not exists control_room_layouts_tenant_updated_idx
  on public.control_room_layouts(tenant_id, updated_at desc);

alter table public.control_room_layouts enable row level security;
revoke all on table public.control_room_layouts from public, anon, authenticated;

-- Read helper. wl_my_tenant() already fails closed for administratively
-- suspended accounts after migration 0038.
create or replace function public.wl_control_room_layouts()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_my_tenant();
  v_role text := wl_my_role();
begin
  if v_tenant is null then
    raise exception 'no active account' using errcode='42501';
  end if;

  return jsonb_build_object(
    'role', v_role,
    'can_manage', v_role in ('owner','admin'),
    'items', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'id', l.id,
          'name', l.name,
          'grid_size', l.grid_size,
          'camera_ids', to_jsonb(l.camera_ids),
          'updated_at', l.updated_at
        ) order by l.updated_at desc, l.name
      )
      from public.control_room_layouts l
      where l.tenant_id = v_tenant
    ), '[]'::jsonb)
  );
end
$$;

revoke all on function public.wl_control_room_layouts() from public, anon;
grant execute on function public.wl_control_room_layouts() to authenticated;

-- Create/update one layout. Camera references are validated against the
-- caller's tenant so a crafted RPC cannot place another customer's camera in
-- a saved layout. Duplicate camera ids are rejected because one camera in two
-- slots is almost always an operator mistake.
create or replace function public.wl_save_control_room_layout(
  p_name text,
  p_grid_size int,
  p_camera_ids uuid[],
  p_layout_id uuid default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_user uuid := auth.uid();
  v_id uuid;
  v_name text := btrim(coalesce(p_name,''));
  v_camera_ids uuid[] := coalesce(p_camera_ids, '{}'::uuid[]);
  v_distinct_count int;
  v_owned_count int;
begin
  if length(v_name) < 1 or length(v_name) > 80 then
    raise exception 'layout name must be between 1 and 80 characters';
  end if;
  if p_grid_size not in (2,3,4) then
    raise exception 'layout grid must be 2, 3 or 4';
  end if;
  if cardinality(v_camera_ids) > p_grid_size * p_grid_size then
    raise exception 'too many cameras for selected layout grid';
  end if;
  if array_position(v_camera_ids, null) is not null then
    raise exception 'layout camera list cannot contain null values';
  end if;

  select count(distinct x) into v_distinct_count from unnest(v_camera_ids) as x;
  if v_distinct_count <> cardinality(v_camera_ids) then
    raise exception 'a camera can appear only once in a layout';
  end if;

  select count(*) into v_owned_count
    from public.cameras c
   where c.tenant_id = v_tenant
     and c.id = any(v_camera_ids);
  if v_owned_count <> cardinality(v_camera_ids) then
    raise exception 'one or more cameras are not in your account' using errcode='42501';
  end if;

  if p_layout_id is null then
    insert into public.control_room_layouts
      (tenant_id, name, grid_size, camera_ids, created_by, updated_by)
    values
      (v_tenant, v_name, p_grid_size, v_camera_ids, v_user, v_user)
    returning id into v_id;
  else
    update public.control_room_layouts
       set name=v_name,
           grid_size=p_grid_size,
           camera_ids=v_camera_ids,
           updated_by=v_user,
           updated_at=now()
     where id=p_layout_id
       and tenant_id=v_tenant
    returning id into v_id;

    if v_id is null then
      raise exception 'layout not found in your account' using errcode='42501';
    end if;
  end if;

  return (
    select jsonb_build_object(
      'id', l.id,
      'name', l.name,
      'grid_size', l.grid_size,
      'camera_ids', to_jsonb(l.camera_ids),
      'updated_at', l.updated_at
    )
    from public.control_room_layouts l
    where l.id=v_id and l.tenant_id=v_tenant
  );
end
$$;

revoke all on function public.wl_save_control_room_layout(text,int,uuid[],uuid) from public, anon;
grant execute on function public.wl_save_control_room_layout(text,int,uuid[],uuid) to authenticated;

create or replace function public.wl_delete_control_room_layout(p_layout_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_deleted uuid;
begin
  delete from public.control_room_layouts
   where id=p_layout_id and tenant_id=v_tenant
  returning id into v_deleted;

  if v_deleted is null then
    raise exception 'layout not found in your account' using errcode='42501';
  end if;

  return jsonb_build_object('deleted', true, 'id', v_deleted);
end
$$;

revoke all on function public.wl_delete_control_room_layout(uuid) from public, anon;
grant execute on function public.wl_delete_control_room_layout(uuid) to authenticated;

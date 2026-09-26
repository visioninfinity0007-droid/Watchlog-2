-- 0116_camera_preview_realtime.sql
-- Realtime invalidation for customer camera previews without publishing JPEG bytes.
--
-- WatchLog has two persisted still-image sources:
--   * camera_config_snapshots: an on-demand/latest camera preview
--   * snapshots: event/incident evidence captured by the agent
--
-- Both contain private bytea image data and remain behind a tenant-authorized RPC.
-- This migration publishes only minimal metadata so a browser can learn that a
-- newer camera image exists and then fetch it through wl_camera_config_snapshot.

create table if not exists public.camera_snapshot_signals (
  camera_id    uuid primary key references public.cameras(id) on delete cascade,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  site_id      uuid not null references public.sites(id) on delete cascade,
  captured_at  timestamptz not null,
  version      bigint not null default 1 check (version > 0),
  updated_at   timestamptz not null default now()
);

create index if not exists camera_snapshot_signals_site_idx
  on public.camera_snapshot_signals(site_id, captured_at desc);

-- The latest-preview RPC reads event snapshots by camera. The original
-- incident-snapshot schema indexes tenant/time, not camera/time.
create index if not exists snapshots_camera_captured_idx
  on public.snapshots(camera_id, captured_at desc)
  where camera_id is not null;

alter table public.camera_snapshot_signals enable row level security;

revoke all on public.camera_snapshot_signals from public, anon;
grant select on public.camera_snapshot_signals to authenticated;

drop policy if exists portal_read_camera_snapshot_signals
  on public.camera_snapshot_signals;
create policy portal_read_camera_snapshot_signals
  on public.camera_snapshot_signals
  for select
  to authenticated
  using (public.wl_is_member(tenant_id));

create or replace function public.wl_signal_camera_preview()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- Some legacy event snapshots may not be mapped to a camera. They cannot
  -- drive a camera tile, so they intentionally produce no preview signal.
  if new.camera_id is null then
    return new;
  end if;

  insert into public.camera_snapshot_signals(
    camera_id, tenant_id, site_id, captured_at, version, updated_at
  )
  values (
    new.camera_id, new.tenant_id, new.site_id, new.captured_at, 1, now()
  )
  on conflict (camera_id) do update
     set tenant_id = excluded.tenant_id,
         site_id = excluded.site_id,
         captured_at = greatest(
           public.camera_snapshot_signals.captured_at,
           excluded.captured_at
         ),
         version = public.camera_snapshot_signals.version + 1,
         updated_at = now();

  return new;
end
$$;

revoke all on function public.wl_signal_camera_preview()
  from public, anon, authenticated;

drop trigger if exists trg_camera_config_snapshot_realtime_signal
  on public.camera_config_snapshots;
create trigger trg_camera_config_snapshot_realtime_signal
after insert or update on public.camera_config_snapshots
for each row execute function public.wl_signal_camera_preview();

drop trigger if exists trg_event_snapshot_realtime_signal
  on public.snapshots;
create trigger trg_event_snapshot_realtime_signal
after insert on public.snapshots
for each row execute function public.wl_signal_camera_preview();

-- Keep the existing RPC signature used by the portal, but return the newest
-- authorized still from either persisted image source. Raw table access remains
-- unnecessary in the browser.
create or replace function public.wl_camera_config_snapshot(p_camera_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tenant uuid;
  v jsonb;
begin
  select tenant_id into v_tenant
    from public.cameras
   where id = p_camera_id;

  if v_tenant is null or not public.wl_is_member(v_tenant) then
    return null;
  end if;

  select jsonb_build_object(
           'camera_id', q.camera_id,
           'content_type', q.content_type,
           'bytes', q.bytes,
           'captured_at', q.captured_at,
           'image_b64', encode(q.image, 'base64')
         )
    into v
    from (
      select
        cs.camera_id,
        cs.content_type,
        cs.bytes,
        cs.captured_at,
        cs.image
      from public.camera_config_snapshots cs
      where cs.camera_id = p_camera_id

      union all

      select
        s.camera_id,
        s.content_type,
        s.bytes,
        s.captured_at,
        s.image
      from public.snapshots s
      where s.camera_id = p_camera_id
    ) q
   order by q.captured_at desc
   limit 1;

  return v;
end
$$;

revoke all on function public.wl_camera_config_snapshot(uuid) from public, anon;
grant execute on function public.wl_camera_config_snapshot(uuid) to authenticated;

-- Backfill the newest known timestamp per camera across both image sources so a
-- browser reconnect can reconcile immediately after this migration.
insert into public.camera_snapshot_signals(
  camera_id, tenant_id, site_id, captured_at, version, updated_at
)
select distinct on (x.camera_id)
  x.camera_id,
  x.tenant_id,
  x.site_id,
  x.captured_at,
  1,
  now()
from (
  select
    cs.camera_id,
    cs.tenant_id,
    cs.site_id,
    cs.captured_at
  from public.camera_config_snapshots cs

  union all

  select
    s.camera_id,
    s.tenant_id,
    s.site_id,
    s.captured_at
  from public.snapshots s
  where s.camera_id is not null
) x
order by x.camera_id, x.captured_at desc
on conflict (camera_id) do update
   set tenant_id = excluded.tenant_id,
       site_id = excluded.site_id,
       captured_at = greatest(
         public.camera_snapshot_signals.captured_at,
         excluded.captured_at
       ),
       updated_at = now();

-- Publish only metadata. Neither raw image table is added to Realtime.
do $$
begin
  if exists (
    select 1 from pg_publication where pubname = 'supabase_realtime'
  ) and not exists (
    select 1
      from pg_publication_tables
     where pubname = 'supabase_realtime'
       and schemaname = 'public'
       and tablename = 'camera_snapshot_signals'
  ) then
    execute 'alter publication supabase_realtime add table public.camera_snapshot_signals';
  end if;
end
$$;

-- 0158 - Camera sync is idempotent after an ONVIF-profile era (repo only until approved).
--
-- Field defect, Al-Khalid 2026-10-07 (5.1.1 Setup, Dahua DH-XVR1B08-I over dahua-cgi):
-- wl_sync_cameras failed with 23505 on cameras_recorder_channel_uniq. The site first ran
-- through the ONVIF fallback (2026-09-26), so it already has canonical rows '1'..'8'
-- (channel = physical_channel) AND the earlier pass's renamed rows 'legacy-profile-1',
-- '-3', '-5', ... sharing those physical channels. The native-numbering branch of
-- wl_sync_recorder_cameras_core (0117, restated in 0146) renamed every row that shares a
-- physical channel with another row, so canonical '1' became 'legacy-profile-1', which
-- already existed. Every site that once synced ONVIF profiles and later syncs native
-- channels hits this; 5.0.x Agents that stay on ONVIF never take this branch.
--
-- Change (wl_sync_recorder_cameras_core only; otherwise the 0146 body):
--   * a canonical row already in native numbering (channel = physical_channel, not a
--     MediaProfile name) is never renamed;
--   * a row that is already 'legacy-profile-*' does not count as a duplicate;
--   * a rename never reuses a taken legacy name (suffix with the row id instead);
--   * a canonical legacy row moves back to its physical channel only if that channel
--     is free.
-- Rows are only renamed, never deleted: events and evidence keep their camera ids.
-- wl_sync_cameras and wl_sync_recorder_cameras keep their 0146 bodies and ACLs.

create or replace function public.wl_sync_recorder_cameras_core(
  p_tenant_id uuid,
  p_site_id uuid,
  p_recorder_id uuid,
  p_cameras jsonb
) returns jsonb
language plpgsql
volatile
set search_path = public
as $function$
declare
  v_out jsonb := '{}'::jsonb;
  v_row record;
  v_legacy_profiles boolean := false;
begin
  if not exists (
    select 1 from public.recorders r
     where r.id = p_recorder_id
       and r.tenant_id = p_tenant_id
       and r.site_id = p_site_id
       and r.is_configured
  ) then
    raise exception 'recorder not configured for this site'
      using errcode='42501';
  end if;

  select exists(
    select 1
      from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
     where coalesce(c->>'name','') ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
  ) into v_legacy_profiles;

  if v_legacy_profiles then
    with parsed as (
      select c,
             c->>'channel' as transport_channel,
             coalesce(
               substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
               c->>'channel'
             ) as physical_channel,
             row_number() over (
               partition by coalesce(
                 substring(c->>'name' from '(?i)^MediaProfile_Channel([0-9]+)_'),
                 c->>'channel'
               )
               order by case when (c->>'channel') ~ '^[0-9]+$'
                             then (c->>'channel')::int else -1 end desc,
                        c->>'channel' desc
             ) as rn
        from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
       where coalesce(c->>'channel','') <> ''
    )
    insert into public.cameras(
      tenant_id,site_id,recorder_id,channel,physical_channel,name,
      is_configured,analytics_enabled,is_canonical
    )
    select
      p_tenant_id,
      p_site_id,
      p_recorder_id,
      transport_channel,
      physical_channel,
      case when rn=1 then 'Camera '||physical_channel
           else coalesce(nullif(c->>'name',''),'Profile '||transport_channel) end,
      coalesce((c->>'is_configured')::boolean,false),
      (rn=1),
      (rn=1)
      from parsed
    on conflict(recorder_id,channel) do update
      set physical_channel=excluded.physical_channel,
          name=case
            when cameras.is_canonical and cameras.is_configured then cameras.name
            else excluded.name
          end,
          is_canonical=excluded.is_canonical,
          analytics_enabled=case
            when excluded.is_canonical then cameras.analytics_enabled
            else false
          end,
          is_configured=case
            when excluded.is_canonical then cameras.is_configured
            else false
          end;

    for v_row in
      select channel,id
        from public.cameras
       where recorder_id=p_recorder_id
         and channel in (
           select c->>'channel'
             from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
         )
    loop
      v_out := v_out || jsonb_build_object(v_row.channel,v_row.id);
    end loop;
    return v_out;
  end if;

  update public.cameras
     set channel=case
           when exists (
             select 1 from public.cameras t
              where t.recorder_id=cameras.recorder_id
                and t.channel='legacy-profile-'||cameras.channel
           )
             then 'legacy-profile-'||channel||'-'||left(id::text,8)
           else 'legacy-profile-'||channel
         end
   where recorder_id=p_recorder_id
     and physical_channel is not null
     and channel !~ '^legacy-profile-'
     and not (
       is_canonical
       and channel=physical_channel
       and name !~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
     )
     and exists (
       select 1 from public.cameras x
        where x.id=cameras.id
          and (
            x.name ~* '^Camera [0-9]+$'
            or x.name ~* '^MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
            or exists (
              select 1 from public.cameras y
               where y.recorder_id=x.recorder_id
                 and y.physical_channel=x.physical_channel
                 and y.id<>x.id
                 and y.channel !~ '^legacy-profile-'
            )
          )
     );

  update public.cameras
     set channel=physical_channel,
         name=case
           when name ~* '^(Legacy )?MediaProfile_Channel[0-9]+_(MainStream|SubStream)'
             then 'Camera '||physical_channel
           else name
         end
   where recorder_id=p_recorder_id
     and is_canonical
     and physical_channel is not null
     and channel ~ '^legacy-profile-'
     and not exists (
       select 1 from public.cameras z
        where z.recorder_id=cameras.recorder_id
          and z.channel=cameras.physical_channel
     );

  update public.cameras
     set is_canonical=false,
         is_configured=false,
         analytics_enabled=false
   where recorder_id=p_recorder_id
     and channel ~ '^legacy-profile-';

  insert into public.cameras(
    tenant_id,site_id,recorder_id,channel,physical_channel,name,is_configured,is_canonical
  )
  select
    p_tenant_id,p_site_id,p_recorder_id,
    c->>'channel',c->>'channel',
    coalesce(nullif(c->>'name',''),'Camera '||(c->>'channel')),
    coalesce((c->>'is_configured')::boolean,false),true
  from jsonb_array_elements(coalesce(p_cameras,'[]'::jsonb)) c
  where coalesce(c->>'channel','')<>''
  on conflict(recorder_id,channel) do update
    set physical_channel=excluded.physical_channel,
        name=case when cameras.is_configured then cameras.name else excluded.name end,
        is_canonical=true;

  for v_row in
    select channel,id
      from public.cameras
     where recorder_id=p_recorder_id
       and coalesce(is_canonical,true)
  loop
    v_out := v_out || jsonb_build_object(v_row.channel,v_row.id);
  end loop;
  return v_out;
end
$function$;

revoke all on function public.wl_sync_recorder_cameras_core(uuid,uuid,uuid,jsonb)
  from public, anon, authenticated, service_role;

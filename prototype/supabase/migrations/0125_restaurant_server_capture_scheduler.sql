-- Server-driven restaurant capture using the deployed Analytics Config poll.
-- Only agents that explicitly advertise config_snapshot_requests are eligible.
-- Manual preview requests stay separate and are never promoted into business analytics.

alter table public.camera_snapshot_requests
  add column if not exists request_source text not null default 'manual';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname='camera_snapshot_requests_source_check'
      and conrelid='public.camera_snapshot_requests'::regclass
  ) then
    alter table public.camera_snapshot_requests
      add constraint camera_snapshot_requests_source_check
      check (request_source in ('manual','restaurant_analytics'));
  end if;
end $$;

create index if not exists camera_snapshot_requests_restaurant_due_idx
  on public.camera_snapshot_requests(camera_id,request_source,completed_at desc);

create or replace function public.wl_restaurant_schedule_snapshot_requests()
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare v_count integer:=0;
begin
  with candidate as (
    select
      p.camera_id,p.tenant_id,p.site_id,p.analytics_role,p.interval_seconds,
      last_done.last_completed,
      case p.analytics_role
        when 'dining_floor' then 1
        when 'kitchen' then 2
        when 'service_handoff' then 3
        when 'cash_counter' then 4
        else 9
      end as priority,
      case
        when coalesce(b.overnight,false)
          and b.open_time is not null and b.close_time is not null
          and b.close_time<=b.open_time
          and (now() at time zone s.timezone)::time < b.close_time
          then ((now() at time zone s.timezone)::date-1)
        else (now() at time zone s.timezone)::date
      end as service_date
    from public.restaurant_camera_profiles p
    join public.cameras c on c.id=p.camera_id and coalesce(c.is_canonical,true)
    join public.sites s on s.id=p.site_id
    join public.site_business_context b on b.site_id=p.site_id
    left join lateral (
      select max(q.completed_at) as last_completed
      from public.camera_snapshot_requests q
      where q.camera_id=p.camera_id
        and q.request_source='restaurant_analytics'
        and q.completed_at is not null
    ) last_done on true
    where p.enabled
      and p.sampling_mode in ('interval','hybrid')
      and p.interval_seconds is not null
      and p.interval_seconds>=15
      and exists (
        select 1
        from public.agents a
        where a.site_id=p.site_id
          and a.last_seen_at>now()-interval '5 minutes'
          and coalesce(a.capabilities,'[]'::jsonb) ? 'config_snapshot_requests'
      )
      and not exists (
        select 1 from public.camera_snapshot_requests open_q
        where open_q.camera_id=p.camera_id and open_q.completed_at is null
      )
      and (
        last_done.last_completed is null
        or last_done.last_completed <= now()-make_interval(secs=>p.interval_seconds)
      )
      and (
        case
          when coalesce(b.overnight,false)
            and b.open_time is not null and b.close_time is not null
            and b.close_time<=b.open_time
            then (
              (now() at time zone s.timezone)::time>=b.open_time
              or (now() at time zone s.timezone)::time<b.close_time
            )
          else (
            (b.open_time is null or (now() at time zone s.timezone)::time>=b.open_time)
            and (b.close_time is null or (now() at time zone s.timezone)::time<b.close_time)
          )
        end
      )
  ),
  eligible as (
    select c.*,
      row_number() over(
        partition by c.site_id
        order by c.priority,c.last_completed nulls first,c.camera_id
      ) as rn
    from candidate c
    join public.site_business_context b on b.site_id=c.site_id
    where coalesce(cardinality(b.working_days),0)=0
       or extract(isodow from c.service_date)::int=any(b.working_days)
  ),
  ins as (
    insert into public.camera_snapshot_requests(
      tenant_id,site_id,camera_id,requested_by,request_source
    )
    select tenant_id,site_id,camera_id,null,'restaurant_analytics'
    from eligible
    where rn<=2
    on conflict do nothing
    returning 1
  )
  select count(*) into v_count from ins;

  return jsonb_build_object('ok',true,'requested',v_count,'at',now());
end $$;

revoke all on function public.wl_restaurant_schedule_snapshot_requests() from public,anon,authenticated;
grant execute on function public.wl_restaurant_schedule_snapshot_requests() to service_role;

create or replace function public.wl_upload_config_snapshot(
  p_agent_id uuid,
  p_agent_key text,
  p_camera_id uuid,
  p_image_b64 text,
  p_content_type text default 'image/jpeg'
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare
  v_agent public.agents;
  v_cam public.cameras;
  v_img bytea;
  v_request_id uuid;
  v_request_source text;
  v_role text;
  v_ingest jsonb;
  v_now timestamptz:=now();
begin
  v_agent:=public.wl_auth_agent(p_agent_id,p_agent_key);
  if v_agent.id is null then raise exception 'agent not recognised' using errcode='28000'; end if;

  select * into v_cam
  from public.cameras
  where id=p_camera_id and site_id=v_agent.site_id;
  if v_cam.id is null then raise exception 'camera not in this site' using errcode='42501'; end if;

  select q.id,q.request_source
    into v_request_id,v_request_source
  from public.camera_snapshot_requests q
  where q.camera_id=v_cam.id and q.completed_at is null
  order by q.requested_at
  limit 1;

  v_img:=decode(p_image_b64,'base64');
  if octet_length(v_img) not between 1 and 3145728 then
    raise exception 'snapshot size invalid';
  end if;

  insert into public.camera_config_snapshots(
    camera_id,tenant_id,site_id,image,bytes,content_type,captured_at
  )
  values(
    v_cam.id,v_agent.tenant_id,v_agent.site_id,v_img,octet_length(v_img),
    coalesce(nullif(p_content_type,''),'image/jpeg'),v_now
  )
  on conflict(camera_id) do update set
    image=excluded.image,bytes=excluded.bytes,
    content_type=excluded.content_type,captured_at=excluded.captured_at;

  if v_request_source='restaurant_analytics' and v_request_id is not null then
    select analytics_role into v_role
    from public.restaurant_camera_profiles
    where camera_id=v_cam.id and site_id=v_agent.site_id and enabled
    limit 1;

    if v_role is not null then
      v_ingest:=public.wl_ingest_events(
        p_agent_id,p_agent_key,
        jsonb_build_array(jsonb_build_object(
          'channel',v_cam.channel,
          'event_type','visual_sample',
          'device_event_id','restaurant-request-'||v_request_id::text,
          'device_ts',v_now,
          'agent_ts',v_now,
          'payload',jsonb_build_object(
            'sample',true,
            'source','restaurant_requested_snapshot',
            'restaurant_role',v_role,
            'request_id',v_request_id
          ),
          'snapshot_b64',p_image_b64
        ))
      );
    end if;
  end if;

  update public.camera_snapshot_requests
     set completed_at=v_now
   where camera_id=v_cam.id and completed_at is null;

  return jsonb_build_object(
    'ok',true,
    'bytes',octet_length(v_img),
    'request_source',coalesce(v_request_source,'manual'),
    'ingested',coalesce(v_ingest,'{}'::jsonb)
  );
end $$;

create or replace function public.wl_vision_claim_snapshots_v2(
  p_limit integer default 2,
  p_worker_id text default null,
  p_provider_external boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare v_out jsonb;
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;

  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
      join public.snapshots s on s.event_id=r.event_id
      join public.events ev on ev.id=s.event_id
      join public.cameras c on c.id=s.camera_id
      left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id and rp.enabled
     where coalesce(c.is_canonical,true)
       and (
         (r.status='pending' and r.next_attempt_at<=now())
         or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
         or (r.status='processing' and r.lease_until<now() and r.attempts<5)
       )
       and (
         not coalesce(p_provider_external,true)
         or exists (
           select 1 from public.ai_site_egress_policy ep
            where ep.site_id=s.site_id and ep.external_egress_allowed=true
         )
       )
       and not (
         rp.sampling_mode='event'
         and coalesce(ev.payload->>'source','')='periodic_snapshot'
       )
     order by r.next_attempt_at,r.captured_at
     for update of r skip locked
     limit least(greatest(coalesce(p_limit,2),1),4)
  ),
  claimed as (
    update public.snapshot_visual_reviews r
       set status='processing',
           attempts=r.attempts+1,
           lease_until=now()+interval '4 minutes',
           worker_id=left(coalesce(p_worker_id,'edge-vision-worker'),120),
           last_error=null,
           updated_at=now()
      from picked p
     where r.event_id=p.event_id
     returning r.event_id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'event_id',s.event_id,
      'tenant_id',s.tenant_id,
      'site_id',s.site_id,
      'camera_id',s.camera_id,
      'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
      'channel',coalesce(c.physical_channel,c.channel),
      'camera_purpose',coalesce(c.purpose,'general'),
      'captured_at',s.captured_at,
      'timezone',coalesce(si.timezone,'Asia/Karachi'),
      'site_type',coalesce(b.site_type,si.site_type,'other'),
      'business_context',jsonb_build_object(
        'site_type',coalesce(b.site_type,si.site_type,'other'),
        'open_time',b.open_time,
        'close_time',b.close_time,
        'overnight',coalesce(b.overnight,false),
        'working_days',coalesce(to_jsonb(b.working_days),'[]'::jsonb),
        'camera_context',coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
        'owner_insight_priorities',coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
        'ai_context_note',coalesce(b.reporting_prefs->>'ai_context_note',''),
        'restaurant_analytics',case
          when coalesce(b.site_type,si.site_type,'other')='restaurant' and rp.enabled then
            jsonb_build_object(
              'enabled',true,
              'camera_role',rp.analytics_role,
              'sampling_mode',rp.sampling_mode,
              'interval_seconds',rp.interval_seconds,
              'config',rp.config,
              'tables',coalesce((
                select jsonb_agg(jsonb_build_object(
                  'table_key',rt.table_key,'label',rt.label,'capacity',rt.capacity,
                  'tracking_mode',rt.tracking_mode,'anchor',rt.anchor,'roi',rt.roi,
                  'can_combine',rt.can_combine
                ) order by rt.sort_order,rt.table_key)
                from public.restaurant_tables rt
                where rt.camera_id=s.camera_id and rt.site_id=s.site_id and rt.active
              ),'[]'::jsonb),
              'output_contract',jsonb_build_object(
                'top_level_key','restaurant',
                'schema_version','restaurant-vision-v1',
                'truth_rules',jsonb_build_array(
                  'Count only visible people; do not infer unique identity.',
                  'visible_customers means currently visible customers, not unique footfall.',
                  'food_present means visible food on a table; do not infer order correctness or food quality.',
                  'Do not infer sales, revenue, staff identity, health diagnosis, or customer demographics.',
                  'Use null when a requested field is not visually defensible.'
                ),
                'fields',jsonb_build_array(
                  'visible_customers','staff_count','occupied_tables','served_tables',
                  'kitchen_load','handoff_load','counter_active','confidence','tables'
                ),
                'table_fields',jsonb_build_array(
                  'table_key','occupied','customer_count','food_present','drinks_present',
                  'staff_present','clearing_state','combined_group','visibility_quality','confidence'
                )
              )
            )
          else null
        end
      ),
      'content_type',s.content_type,
      'bytes',s.bytes,
      'image_b64',encode(s.image,'base64')
    ) order by s.captured_at),'[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshot_visual_reviews r on r.event_id=q.event_id
    join public.snapshots s on s.event_id=q.event_id
    join public.sites si on si.id=s.site_id
    left join public.site_business_context b on b.site_id=s.site_id
    left join public.cameras c on c.id=s.camera_id
    left join public.restaurant_camera_profiles rp on rp.camera_id=s.camera_id
   where coalesce(c.is_canonical,true);

  return coalesce(v_out,'[]'::jsonb);
end $$;

revoke all on function public.wl_vision_claim_snapshots_v2(integer,text,boolean) from public,anon,authenticated;
grant execute on function public.wl_vision_claim_snapshots_v2(integer,text,boolean) to service_role;

do $$
begin
  if exists (select 1 from cron.job where jobname='watchlog-restaurant-snapshot-scheduler') then
    perform cron.unschedule('watchlog-restaurant-snapshot-scheduler');
  end if;
end $$;

select cron.schedule(
  'watchlog-restaurant-snapshot-scheduler',
  '30 seconds',
  $$select public.wl_restaurant_schedule_snapshot_requests();$$
);

-- Vision worker runtime + restaurant service-day hardening.
-- Adds a provider-egress-aware queue claim, Vault-backed worker auth,
-- pg_net support for scheduled Edge Function invocation, and service-role
-- compatibility for tenant-scoped restaurant report generation.

create extension if not exists pg_net with schema extensions;

do $$
begin
  if not exists (select 1 from vault.secrets where name='wl_vision_worker_cron_secret') then
    perform vault.create_secret(
      encode(gen_random_bytes(32),'hex'),
      'wl_vision_worker_cron_secret',
      'WatchLog scheduled vision worker authentication'
    );
  end if;
end $$;

create or replace function public.wl_vision_worker_expected_secret()
returns text
language plpgsql
security definer
set search_path=public,vault
as $$
declare v text;
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  select decrypted_secret into v
  from vault.decrypted_secrets
  where name='wl_vision_worker_cron_secret'
  limit 1;
  return v;
end $$;

revoke all on function public.wl_vision_worker_expected_secret() from public,anon,authenticated;
grant execute on function public.wl_vision_worker_expected_secret() to service_role;

create or replace function public.wl_assert_my_site(p_site_id uuid)
returns uuid
language plpgsql
stable
security definer
set search_path=public
as $$
declare v_tenant uuid := wl_my_tenant(); v_owner uuid;
begin
  select tenant_id into v_owner from sites where id=p_site_id;
  if auth.role()='service_role' then
    if v_owner is null then raise exception 'unknown site' using errcode='42704'; end if;
    return v_owner;
  end if;
  if v_tenant is null then raise exception 'not authenticated' using errcode='42501'; end if;
  if v_owner is null or v_owner<>v_tenant then
    raise exception 'not authorized for this site' using errcode='42501';
  end if;
  return v_tenant;
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
      join public.cameras c on c.id=s.camera_id
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

create or replace function public.wl_generate_daily_report(
  p_site_id uuid,
  p_date date default null,
  p_force boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path=public
as $$
declare
  v_site public.sites;
  v_ctx public.site_business_context;
  v_is_restaurant boolean;
  v_local_now timestamp;
  v_date date;
  v_existing uuid;
  v_payload jsonb;
  v_inf text;
  v_id uuid;
  v_rev int;
begin
  select * into v_site from public.sites where id=p_site_id;
  if v_site.id is null then raise exception 'no such site' using errcode='22023'; end if;
  select * into v_ctx from public.site_business_context where site_id=p_site_id;
  v_is_restaurant:=coalesce(v_ctx.site_type,v_site.site_type,'other')='restaurant';
  v_local_now:=now() at time zone v_site.timezone;

  if p_date is not null then
    v_date:=p_date;
  elsif v_is_restaurant
    and coalesce(v_ctx.overnight,false)
    and v_ctx.open_time is not null
    and v_ctx.close_time is not null
    and v_ctx.close_time<=v_ctx.open_time
    and v_local_now::time<v_ctx.close_time then
      v_date:=v_local_now::date-1;
  else
    v_date:=v_local_now::date;
  end if;

  select id into v_existing
    from public.report_snapshots
   where site_id=p_site_id and report_date=v_date;
  if v_existing is not null and not p_force then
    return (select jsonb_build_object(
      'report_id',id,'frozen',true,'revision',revision,
      'generated_at',generated_at,'payload',payload)
      from public.report_snapshots where id=v_existing);
  end if;

  v_payload:=public.wl_daily_intelligence(p_site_id,v_date,true);
  if v_is_restaurant then
    v_payload:=v_payload||jsonb_build_object(
      'restaurant',public.wl_restaurant_day(p_site_id,v_date)
    );
  end if;

  select version into v_inf
    from public.inference_config
   where site_id=p_site_id or site_id is null
   order by (site_id is not null) desc
   limit 1;

  insert into public.report_snapshots(
    tenant_id,site_id,report_date,payload,payload_schema,versions,coverage_ratio
  )
  values(
    v_site.tenant_id,p_site_id,v_date,v_payload,v_payload->>'schema',
    jsonb_build_object(
      'inference',coalesce(v_inf,'inference-v1'),
      'journeys','topology-v2',
      'day_state','state-machine-v1',
      'intelligence',v_payload->>'schema',
      'restaurant',case when v_is_restaurant then 'restaurant-day-v1' else null end
    ),
    (v_payload->'coverage'->>'coverage_ratio')::numeric
  )
  on conflict(site_id,report_date) do update
     set payload=excluded.payload,
         payload_schema=excluded.payload_schema,
         versions=excluded.versions,
         coverage_ratio=excluded.coverage_ratio,
         generated_at=now(),
         revision=public.report_snapshots.revision+1,
         delivery_status='pending',
         pdf_sha256=null,
         pdf_bytes=null
  returning id,revision into v_id,v_rev;

  return jsonb_build_object(
    'report_id',v_id,'frozen',false,'revision',v_rev,
    'generated_at',now(),'payload',v_payload
  );
end $$;

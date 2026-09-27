-- 0115_context_aware_visual_review.sql
-- Feed the private vision worker the tenant/site business context and camera role
-- that are already maintained by Guided Setup. Service-role only; no new public data path.

create or replace function public.wl_vision_claim_snapshots(
  p_limit int default 4,
  p_worker_id text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_out jsonb;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;

  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
     where (
       (r.status = 'pending' and r.next_attempt_at <= now())
       or (r.status = 'failed' and r.attempts < 5 and r.next_attempt_at <= now())
       or (r.status = 'processing' and r.lease_until < now() and r.attempts < 5)
     )
     order by r.next_attempt_at, r.captured_at
     for update skip locked
     limit least(greatest(coalesce(p_limit,4),1),16)
  ),
  claimed as (
    update public.snapshot_visual_reviews r
       set status = 'processing',
           attempts = r.attempts + 1,
           lease_until = now() + interval '12 minutes',
           worker_id = left(coalesce(p_worker_id,'vision-worker'),120),
           last_error = null,
           updated_at = now()
      from picked p
     where r.event_id = p.event_id
     returning r.event_id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'event_id', s.event_id,
           'tenant_id', s.tenant_id,
           'site_id', s.site_id,
           'camera_id', s.camera_id,
           'camera', coalesce(c.name, 'Camera ' || coalesce(c.channel,'?')),
           'channel', c.channel,
           'camera_purpose', coalesce(c.purpose,'general'),
           'captured_at', s.captured_at,
           'timezone', coalesce(si.timezone,'Asia/Karachi'),
           'site_type', coalesce(b.site_type, si.site_type, 'other'),
           'business_context', jsonb_build_object(
             'site_type', coalesce(b.site_type, si.site_type, 'other'),
             'open_time', b.open_time,
             'close_time', b.close_time,
             'overnight', coalesce(b.overnight,false),
             'working_days', coalesce(to_jsonb(b.working_days),'[]'::jsonb),
             'camera_context', coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
             'owner_insight_priorities', coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
             'ai_context_note', coalesce(b.reporting_prefs->>'ai_context_note','')
           ),
           'content_type', s.content_type,
           'bytes', s.bytes,
           'image_b64', encode(s.image,'base64'),
           'media_bucket', r.media_bucket,
           'media_key', r.media_key,
           'media_sha256', r.media_sha256,
           'media_bytes', r.media_bytes
         ) order by s.captured_at), '[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshot_visual_reviews r on r.event_id=q.event_id
    join public.snapshots s on s.event_id = q.event_id
    join public.sites si on si.id = s.site_id
    left join public.site_business_context b on b.site_id=s.site_id
    left join public.cameras c on c.id = s.camera_id;

  return coalesce(v_out, '[]'::jsonb);
end $$;

revoke all on function public.wl_vision_claim_snapshots(int,text) from public, anon, authenticated;
grant execute on function public.wl_vision_claim_snapshots(int,text) to service_role;


create or replace function public.wl_vision_day_for_worker(
  p_site_id uuid,
  p_date date
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_tz text;
  v_tenant uuid;
  v_start timestamptz;
  v_end timestamptz;
  v_context jsonb;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;

  select si.timezone, si.tenant_id,
         jsonb_build_object(
           'site_type', coalesce(b.site_type, si.site_type, 'other'),
           'open_time', b.open_time,
           'close_time', b.close_time,
           'overnight', coalesce(b.overnight,false),
           'working_days', coalesce(to_jsonb(b.working_days),'[]'::jsonb),
           'camera_context', coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
           'owner_insight_priorities', coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
           'ai_context_note', coalesce(b.reporting_prefs->>'ai_context_note','')
         )
    into v_tz, v_tenant, v_context
    from public.sites si
    left join public.site_business_context b on b.site_id=si.id
   where si.id=p_site_id;

  if v_tz is null then raise exception 'unknown site' using errcode='42704'; end if;

  v_start := (p_date::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';

  return jsonb_build_object(
    'site_id',p_site_id,
    'tenant_id',v_tenant,
    'date',p_date,
    'timezone',v_tz,
    'business_context',coalesce(v_context,'{}'::jsonb),
    'snapshots_total',(select count(*) from public.snapshot_visual_reviews r
                       where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end),
    'snapshots_analyzed',(select count(*) from public.snapshot_visual_reviews r
                          where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status='done'),
    'pending',(select count(*) from public.snapshot_visual_reviews r
               where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status<>'done'),
    'frames',coalesce((
      select jsonb_agg(jsonb_build_object(
        'event_id',r.event_id,
        'captured_at',r.captured_at,
        'camera_id',r.camera_id,
        'camera',coalesce(c.name,'Camera '||coalesce(c.channel,'?')),
        'channel',c.channel,
        'purpose',coalesce(c.purpose,'general'),
        'summary',r.analysis->>'summary',
        'people_count',coalesce((r.analysis->>'people_count')::int,0),
        'occupied',coalesce((r.analysis->>'occupied')::boolean,false),
        'activity',r.analysis->>'activity',
        'business',coalesce(r.analysis->'business','{}'::jsonb),
        'restricted_area',r.analysis->'restricted_area',
        'unusual',coalesce((r.analysis->>'unusual')::boolean,false),
        'unusual_reason',r.analysis->>'unusual_reason',
        'quality',r.analysis->>'quality',
        'people',r.analysis->'people'
      ) order by r.captured_at)
      from public.snapshot_visual_reviews r
      left join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id
        and r.captured_at>=v_start
        and r.captured_at<v_end
        and r.status='done'
    ),'[]'::jsonb)
  );
end $$;

revoke all on function public.wl_vision_day_for_worker(uuid,date) from public, anon, authenticated;
grant execute on function public.wl_vision_day_for_worker(uuid,date) to service_role;

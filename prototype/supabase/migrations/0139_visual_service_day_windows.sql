-- Make private visual-review day aggregation honor configured business/service-day windows.
-- Required for overnight sites such as Chai Wala (16:00 -> 04:00).

create or replace function public.wl_vision_day_for_worker(p_site_id uuid, p_date date)
returns jsonb
language plpgsql
stable security definer
set search_path to 'public'
as $function$
declare
  v_tz text;
  v_tenant uuid;
  v_start timestamptz;
  v_end timestamptz;
  v_context jsonb;
  v_open time;
  v_close time;
  v_overnight boolean;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;

  select si.timezone,si.tenant_id,
         coalesce(b.open_time,'00:00'::time),
         coalesce(b.close_time,'23:59:59'::time),
         coalesce(b.overnight,false) or coalesce(b.close_time,'23:59:59'::time) <= coalesce(b.open_time,'00:00'::time),
         jsonb_build_object(
           'site_type',coalesce(b.site_type,si.site_type,'other'),
           'open_time',b.open_time,
           'close_time',b.close_time,
           'overnight',coalesce(b.overnight,false),
           'working_days',coalesce(to_jsonb(b.working_days),'[]'::jsonb),
           'camera_context',coalesce(b.reporting_prefs->'camera_context','{}'::jsonb),
           'owner_insight_priorities',coalesce(b.reporting_prefs->'owner_insight_priorities','[]'::jsonb),
           'ai_context_note',coalesce(b.reporting_prefs->>'ai_context_note','')
         )
    into v_tz,v_tenant,v_open,v_close,v_overnight,v_context
    from public.sites si
    left join public.site_business_context b on b.site_id=si.id
   where si.id=p_site_id;

  if v_tz is null then raise exception 'unknown site' using errcode='42704'; end if;

  v_start := (p_date::timestamp + v_open) at time zone v_tz;
  v_end := (
    (case when v_overnight then (p_date+1)::timestamp else p_date::timestamp end)
    + v_close
  ) at time zone v_tz;

  return jsonb_build_object(
    'site_id',p_site_id,
    'tenant_id',v_tenant,
    'date',p_date,
    'timezone',v_tz,
    'from',v_start,
    'to',v_end,
    'business_context',coalesce(v_context,'{}'::jsonb),
    'snapshots_total',(
      select count(*)
      from public.snapshot_visual_reviews r
      join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end
        and coalesce(c.is_canonical,true)
    ),
    'snapshots_analyzed',(
      select count(*)
      from public.snapshot_visual_reviews r
      join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end
        and r.status='done' and coalesce(c.is_canonical,true)
    ),
    'pending',(
      select count(*)
      from public.snapshot_visual_reviews r
      join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end
        and r.status<>'done' and coalesce(c.is_canonical,true)
    ),
    'frames',coalesce((
      select jsonb_agg(jsonb_build_object(
        'event_id',r.event_id,
        'captured_at',r.captured_at,
        'camera_id',r.camera_id,
        'camera',coalesce(c.name,'Camera '||coalesce(c.physical_channel,c.channel,'?')),
        'channel',coalesce(c.physical_channel,c.channel),
        'purpose',coalesce(c.purpose,'general'),
        'summary',r.analysis->>'summary',
        'people_count',coalesce((r.analysis->>'people_count')::int,0),
        'occupied',coalesce((r.analysis->>'occupied')::boolean,false),
        'activity',r.analysis->>'activity',
        'business',coalesce(r.analysis->'business','{}'::jsonb),
        'restaurant',coalesce(r.analysis->'restaurant','{}'::jsonb),
        'restricted_area',r.analysis->'restricted_area',
        'unusual',coalesce((r.analysis->>'unusual')::boolean,false),
        'unusual_reason',r.analysis->>'unusual_reason',
        'quality',r.analysis->>'quality',
        'people',r.analysis->'people'
      ) order by r.captured_at)
      from public.snapshot_visual_reviews r
      join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id
        and r.captured_at>=v_start
        and r.captured_at<v_end
        and r.status='done'
        and coalesce(c.is_canonical,true)
    ),'[]'::jsonb)
  );
end
$function$;

create or replace function public.wl_vision_save_day_summary(
  p_site_id uuid,
  p_date date,
  p_model text,
  p_summary_version text,
  p_summary jsonb
)
returns jsonb
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_tenant uuid;
  v_tz text;
  v_open time;
  v_close time;
  v_overnight boolean;
  v_start timestamptz;
  v_end timestamptz;
  v_total int;
  v_done int;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;

  select s.tenant_id,s.timezone,
         coalesce(b.open_time,'00:00'::time),
         coalesce(b.close_time,'23:59:59'::time),
         coalesce(b.overnight,false) or coalesce(b.close_time,'23:59:59'::time) <= coalesce(b.open_time,'00:00'::time)
    into v_tenant,v_tz,v_open,v_close,v_overnight
    from public.sites s
    left join public.site_business_context b on b.site_id=s.id
   where s.id=p_site_id;

  if v_tenant is null then raise exception 'unknown site' using errcode='42704'; end if;

  v_start := (p_date::timestamp + v_open) at time zone v_tz;
  v_end := (
    (case when v_overnight then (p_date+1)::timestamp else p_date::timestamp end)
    + v_close
  ) at time zone v_tz;

  select count(*),count(*) filter(where r.status='done')
    into v_total,v_done
    from public.snapshot_visual_reviews r
    join public.cameras c on c.id=r.camera_id
   where r.site_id=p_site_id
     and r.captured_at>=v_start and r.captured_at<v_end
     and coalesce(c.is_canonical,true);

  insert into public.visual_day_summaries(
    site_id,tenant_id,report_date,timezone,status,snapshots_total,snapshots_analyzed,
    model,summary_version,summary,generated_at)
  values (
    p_site_id,v_tenant,p_date,v_tz,
    case when v_total>0 and v_done=v_total then 'complete' else 'partial' end,
    v_total,v_done,left(coalesce(p_model,'unknown'),160),
    left(coalesce(p_summary_version,'v1'),80),coalesce(p_summary,'{}'::jsonb),now())
  on conflict(site_id,report_date) do update
    set status=excluded.status,snapshots_total=excluded.snapshots_total,
        snapshots_analyzed=excluded.snapshots_analyzed,model=excluded.model,
        summary_version=excluded.summary_version,summary=excluded.summary,
        generated_at=excluded.generated_at;

  return jsonb_build_object(
    'ok',true,'site_id',p_site_id,'date',p_date,
    'from',v_start,'to',v_end,
    'snapshots_total',v_total,'snapshots_analyzed',v_done,
    'status',case when v_total>0 and v_done=v_total then 'complete' else 'partial' end
  );
end
$function$;

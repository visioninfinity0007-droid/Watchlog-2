-- 0111_visual_snapshot_pipeline.sql
-- Every persisted CCTV snapshot enters a service-role-only visual review queue.
-- A WatchLog-owned worker (Coolify) claims bounded batches, runs local/private
-- multimodal analysis, and writes structured findings back to the AI evidence
-- workspace. Customer/browser roles never receive raw queue image bytes.

create table if not exists public.snapshot_visual_reviews (
  event_id          bigint primary key references public.snapshots(event_id) on delete cascade,
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  site_id           uuid not null references public.sites(id) on delete cascade,
  camera_id         uuid references public.cameras(id) on delete set null,
  captured_at       timestamptz not null,
  status            text not null default 'pending'
                    check (status in ('pending','processing','done','failed')),
  attempts          int not null default 0 check (attempts >= 0),
  next_attempt_at   timestamptz not null default now(),
  lease_until       timestamptz,
  worker_id         text,
  model             text,
  analysis_version  text,
  analysis          jsonb,
  last_error        text,
  analyzed_at       timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists snapshot_visual_reviews_queue_idx
  on public.snapshot_visual_reviews(status, next_attempt_at, captured_at);
create index if not exists snapshot_visual_reviews_site_day_idx
  on public.snapshot_visual_reviews(site_id, captured_at desc);
alter table public.snapshot_visual_reviews enable row level security;
revoke all on public.snapshot_visual_reviews from public, anon, authenticated;

create table if not exists public.visual_day_summaries (
  site_id           uuid not null references public.sites(id) on delete cascade,
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  report_date       date not null,
  timezone          text not null,
  status            text not null default 'partial'
                    check (status in ('partial','complete')),
  snapshots_total   int not null default 0,
  snapshots_analyzed int not null default 0,
  model             text,
  summary_version   text,
  summary            jsonb not null default '{}'::jsonb,
  generated_at       timestamptz not null default now(),
  primary key (site_id, report_date)
);
create index if not exists visual_day_summaries_tenant_date_idx
  on public.visual_day_summaries(tenant_id, report_date desc);
alter table public.visual_day_summaries enable row level security;
revoke all on public.visual_day_summaries from public, anon;

create or replace function public.wl_queue_snapshot_visual_review()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.snapshot_visual_reviews(
    event_id, tenant_id, site_id, camera_id, captured_at, status, next_attempt_at)
  values (new.event_id, new.tenant_id, new.site_id, new.camera_id, new.captured_at,
          'pending', now())
  on conflict (event_id) do nothing;
  return new;
end $$;
revoke all on function public.wl_queue_snapshot_visual_review() from public, anon, authenticated;

drop trigger if exists trg_snapshot_visual_review_queue on public.snapshots;
create trigger trg_snapshot_visual_review_queue
after insert on public.snapshots
for each row execute function public.wl_queue_snapshot_visual_review();

-- Backfill retained snapshots. This is intentionally idempotent.
insert into public.snapshot_visual_reviews(
  event_id, tenant_id, site_id, camera_id, captured_at, status, next_attempt_at)
select s.event_id, s.tenant_id, s.site_id, s.camera_id, s.captured_at, 'pending', now()
from public.snapshots s
where s.captured_at >= now() - interval '30 days'
on conflict (event_id) do nothing;

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
     order by r.captured_at
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
           'captured_at', s.captured_at,
           'timezone', coalesce(si.timezone,'Asia/Karachi'),
           'content_type', s.content_type,
           'bytes', s.bytes,
           'image_b64', encode(s.image,'base64')
         ) order by s.captured_at), '[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshots s on s.event_id = q.event_id
    join public.sites si on si.id = s.site_id
    left join public.cameras c on c.id = s.camera_id;

  return coalesce(v_out, '[]'::jsonb);
end $$;
revoke all on function public.wl_vision_claim_snapshots(int,text) from public, anon, authenticated;
grant execute on function public.wl_vision_claim_snapshots(int,text) to service_role;

create or replace function public.wl_vision_complete_snapshot(
  p_event_id bigint,
  p_model text,
  p_analysis_version text,
  p_analysis jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_r public.snapshot_visual_reviews; v_bytes int;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;
  if p_analysis is null or jsonb_typeof(p_analysis) <> 'object' then
    raise exception 'analysis object required' using errcode = '22023';
  end if;

  update public.snapshot_visual_reviews
     set status='done', lease_until=null, worker_id=null,
         model=left(coalesce(p_model,'unknown'),160),
         analysis_version=left(coalesce(p_analysis_version,'v1'),80),
         analysis=p_analysis, analyzed_at=now(), last_error=null, updated_at=now()
   where event_id=p_event_id
   returning * into v_r;
  if v_r.event_id is null then
    raise exception 'snapshot review not found' using errcode = '42704';
  end if;

  select bytes into v_bytes from public.snapshots where event_id=p_event_id;

  -- Replace this worker's previous evidence row for the event, if any.
  delete from public.ai_evidence e
   where e.site_id=v_r.site_id
     and e.event_ref='snapshot:' || p_event_id::text
     and e.meta->>'source'='snapshot_visual_review';

  insert into public.ai_evidence(
    tenant_id, site_id, camera_id, event_ref, evidence_class, captured_at,
    content_type, payload_enc, byte_size, meta, expires_at)
  values (
    v_r.tenant_id, v_r.site_id, v_r.camera_id,
    'snapshot:' || p_event_id::text,
    'operational_snapshot', v_r.captured_at,
    'application/json', null, v_bytes,
    jsonb_build_object(
      'source','snapshot_visual_review',
      'snapshot_event_id',p_event_id,
      'summary',coalesce(p_analysis->'summary', to_jsonb('Visual review completed.'::text)),
      'analysis',p_analysis,
      'model',left(coalesce(p_model,'unknown'),160),
      'analysis_version',left(coalesce(p_analysis_version,'v1'),80),
      'provenance',jsonb_build_object('kind','watchlog_private_vision','captured_at',v_r.captured_at)
    ),
    v_r.captured_at + public.wl_ai_evidence_ttl('operational_snapshot')
  );

  return jsonb_build_object('ok',true,'event_id',p_event_id,'status','done');
end $$;
revoke all on function public.wl_vision_complete_snapshot(bigint,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.wl_vision_complete_snapshot(bigint,text,text,jsonb) to service_role;

create or replace function public.wl_vision_fail_snapshot(
  p_event_id bigint,
  p_error text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_attempts int;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;
  update public.snapshot_visual_reviews
     set status='failed', lease_until=null, worker_id=null,
         last_error=left(coalesce(p_error,'visual analysis failed'),500),
         next_attempt_at=now() + make_interval(mins => least(60, greatest(2, attempts*3))),
         updated_at=now()
   where event_id=p_event_id
   returning attempts into v_attempts;
  return jsonb_build_object('ok',true,'event_id',p_event_id,'attempts',coalesce(v_attempts,0));
end $$;
revoke all on function public.wl_vision_fail_snapshot(bigint,text) from public, anon, authenticated;
grant execute on function public.wl_vision_fail_snapshot(bigint,text) to service_role;

create or replace function public.wl_vision_day_for_worker(
  p_site_id uuid,
  p_date date
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tz text; v_tenant uuid; v_start timestamptz; v_end timestamptz;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;
  select timezone, tenant_id into v_tz, v_tenant from public.sites where id=p_site_id;
  if v_tz is null then raise exception 'unknown site' using errcode='42704'; end if;
  v_start := (p_date::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';

  return jsonb_build_object(
    'site_id',p_site_id,'tenant_id',v_tenant,'date',p_date,'timezone',v_tz,
    'snapshots_total',(select count(*) from public.snapshot_visual_reviews r
                       where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end),
    'snapshots_analyzed',(select count(*) from public.snapshot_visual_reviews r
                          where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status='done'),
    'pending',(select count(*) from public.snapshot_visual_reviews r
               where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status<>'done'),
    'frames',coalesce((
      select jsonb_agg(jsonb_build_object(
        'event_id',r.event_id,'captured_at',r.captured_at,
        'camera_id',r.camera_id,'camera',coalesce(c.name,'Camera '||coalesce(c.channel,'?')),
        'channel',c.channel,
        'summary',r.analysis->>'summary',
        'people_count',coalesce((r.analysis->>'people_count')::int,0),
        'occupied',coalesce((r.analysis->>'occupied')::boolean,false),
        'activity',r.analysis->>'activity',
        'restricted_area',r.analysis->'restricted_area',
        'unusual',coalesce((r.analysis->>'unusual')::boolean,false),
        'unusual_reason',r.analysis->>'unusual_reason',
        'quality',r.analysis->>'quality',
        'people',r.analysis->'people'
      ) order by r.captured_at)
      from public.snapshot_visual_reviews r
      left join public.cameras c on c.id=r.camera_id
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status='done'
    ),'[]'::jsonb)
  );
end $$;
revoke all on function public.wl_vision_day_for_worker(uuid,date) from public, anon, authenticated;
grant execute on function public.wl_vision_day_for_worker(uuid,date) to service_role;

create or replace function public.wl_vision_save_day_summary(
  p_site_id uuid,
  p_date date,
  p_model text,
  p_summary_version text,
  p_summary jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_tenant uuid; v_tz text; v_total int; v_done int;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;
  select tenant_id, timezone into v_tenant, v_tz from public.sites where id=p_site_id;
  if v_tenant is null then raise exception 'unknown site' using errcode='42704'; end if;

  select count(*),count(*) filter(where status='done')
    into v_total,v_done
    from public.snapshot_visual_reviews
   where site_id=p_site_id
     and (captured_at at time zone v_tz)::date=p_date;

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

  return jsonb_build_object('ok',true,'site_id',p_site_id,'date',p_date,
                            'snapshots_total',v_total,'snapshots_analyzed',v_done,
                            'status',case when v_total>0 and v_done=v_total then 'complete' else 'partial' end);
end $$;
revoke all on function public.wl_vision_save_day_summary(uuid,date,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.wl_vision_save_day_summary(uuid,date,text,text,jsonb) to service_role;

create or replace function public.wl_my_visual_day(
  p_site_id uuid,
  p_date date
) returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := public.wl_assert_my_site(p_site_id); v_tz text; v_start timestamptz; v_end timestamptz; v_s public.visual_day_summaries;
begin
  select timezone into v_tz from public.sites where id=p_site_id;
  v_start := (p_date::text || ' 00:00:00')::timestamp at time zone v_tz;
  v_end := v_start + interval '1 day';
  select * into v_s from public.visual_day_summaries
   where site_id=p_site_id and tenant_id=v_tenant and report_date=p_date;

  return jsonb_build_object(
    'date',p_date,'timezone',v_tz,
    'status',coalesce(v_s.status,'pending'),
    'snapshots_total',coalesce(v_s.snapshots_total,(
      select count(*) from public.snapshot_visual_reviews r
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end)),
    'snapshots_analyzed',coalesce(v_s.snapshots_analyzed,(
      select count(*) from public.snapshot_visual_reviews r
      where r.site_id=p_site_id and r.captured_at>=v_start and r.captured_at<v_end and r.status='done')),
    'summary',coalesce(v_s.summary,'{}'::jsonb),
    'generated_at',v_s.generated_at
  );
end $$;
revoke all on function public.wl_my_visual_day(uuid,date) from public, anon;
grant execute on function public.wl_my_visual_day(uuid,date) to authenticated, service_role;

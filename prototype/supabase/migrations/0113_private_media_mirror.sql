-- 0113_private_media_mirror.sql
-- Coolify private media mirror for CCTV snapshots. The vision worker writes
-- snapshots to private S3-compatible storage (MinIO) and records only the
-- opaque object key here. No public URL is stored or exposed to customers.

alter table public.snapshot_visual_reviews
  add column if not exists media_bucket text,
  add column if not exists media_key text,
  add column if not exists media_sha256 text,
  add column if not exists media_bytes int,
  add column if not exists media_mirrored_at timestamptz;

create index if not exists snapshot_visual_reviews_media_idx
  on public.snapshot_visual_reviews(site_id, media_mirrored_at desc)
  where media_key is not null;

create or replace function public.wl_vision_mark_media(
  p_event_id bigint,
  p_bucket text,
  p_key text,
  p_sha256 text,
  p_bytes int
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  if coalesce(length(p_bucket),0)=0 or coalesce(length(p_key),0)=0 then
    raise exception 'media location required' using errcode='22023';
  end if;
  update public.snapshot_visual_reviews
     set media_bucket=left(p_bucket,120),
         media_key=left(p_key,800),
         media_sha256=left(coalesce(p_sha256,''),64),
         media_bytes=greatest(coalesce(p_bytes,0),0),
         media_mirrored_at=now(),
         updated_at=now()
   where event_id=p_event_id;
  if not found then raise exception 'snapshot review not found' using errcode='42704'; end if;
  return jsonb_build_object('ok',true,'event_id',p_event_id);
end $$;
revoke all on function public.wl_vision_mark_media(bigint,text,text,text,int) from public,anon,authenticated;
grant execute on function public.wl_vision_mark_media(bigint,text,text,text,int) to service_role;

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
    raise exception 'service role required' using errcode='42501';
  end if;

  with picked as (
    select r.event_id
      from public.snapshot_visual_reviews r
     where (
       (r.status='pending' and r.next_attempt_at<=now())
       or (r.status='failed' and r.attempts<5 and r.next_attempt_at<=now())
       or (r.status='processing' and r.lease_until<now() and r.attempts<5)
     )
     order by r.next_attempt_at,r.captured_at
     for update skip locked
     limit least(greatest(coalesce(p_limit,4),1),16)
  ),
  claimed as (
    update public.snapshot_visual_reviews r
       set status='processing',attempts=r.attempts+1,
           lease_until=now()+interval '12 minutes',
           worker_id=left(coalesce(p_worker_id,'vision-worker'),120),
           last_error=null,updated_at=now()
      from picked p
     where r.event_id=p.event_id
     returning r.event_id,r.media_bucket,r.media_key,r.media_sha256,r.media_bytes
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'event_id',s.event_id,
           'tenant_id',s.tenant_id,
           'site_id',s.site_id,
           'camera_id',s.camera_id,
           'camera',coalesce(c.name,'Camera '||coalesce(c.channel,'?')),
           'channel',c.channel,
           'captured_at',s.captured_at,
           'timezone',coalesce(si.timezone,'Asia/Karachi'),
           'content_type',s.content_type,
           'bytes',s.bytes,
           'media_bucket',q.media_bucket,
           'media_key',q.media_key,
           'media_sha256',q.media_sha256,
           'media_bytes',q.media_bytes,
           -- First processing pass receives DB bytes so it can mirror them.
           -- Retries read from private Coolify media and avoid retransmitting the JPEG.
           'image_b64',case when q.media_key is null then encode(s.image,'base64') else null end
         ) order by s.captured_at),'[]'::jsonb)
    into v_out
    from claimed q
    join public.snapshots s on s.event_id=q.event_id
    join public.sites si on si.id=s.site_id
    left join public.cameras c on c.id=s.camera_id;

  return coalesce(v_out,'[]'::jsonb);
end $$;
revoke all on function public.wl_vision_claim_snapshots(int,text) from public,anon,authenticated;
grant execute on function public.wl_vision_claim_snapshots(int,text) to service_role;

-- Enrich the evidence write with the private-media provenance, without a public URL.
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
    raise exception 'service role required' using errcode='42501';
  end if;
  if p_analysis is null or jsonb_typeof(p_analysis)<>'object' then
    raise exception 'analysis object required' using errcode='22023';
  end if;

  update public.snapshot_visual_reviews
     set status='done',lease_until=null,worker_id=null,
         model=left(coalesce(p_model,'unknown'),160),
         analysis_version=left(coalesce(p_analysis_version,'v1'),80),
         analysis=p_analysis,analyzed_at=now(),last_error=null,updated_at=now()
   where event_id=p_event_id
   returning * into v_r;
  if v_r.event_id is null then raise exception 'snapshot review not found' using errcode='42704'; end if;

  select bytes into v_bytes from public.snapshots where event_id=p_event_id;

  delete from public.ai_evidence e
   where e.site_id=v_r.site_id
     and e.event_ref='snapshot:'||p_event_id::text
     and e.meta->>'source'='snapshot_visual_review';

  insert into public.ai_evidence(
    tenant_id,site_id,camera_id,event_ref,evidence_class,captured_at,
    content_type,payload_enc,byte_size,meta,expires_at)
  values(
    v_r.tenant_id,v_r.site_id,v_r.camera_id,
    'snapshot:'||p_event_id::text,'operational_snapshot',v_r.captured_at,
    'application/json',null,v_bytes,
    jsonb_build_object(
      'source','snapshot_visual_review',
      'snapshot_event_id',p_event_id,
      'summary',coalesce(p_analysis->'summary',to_jsonb('Visual review completed.'::text)),
      'analysis',p_analysis,
      'model',left(coalesce(p_model,'unknown'),160),
      'analysis_version',left(coalesce(p_analysis_version,'v1'),80),
      'media',case when v_r.media_key is not null then jsonb_build_object(
          'storage','coolify_private_object_store',
          'bucket',v_r.media_bucket,
          'key',v_r.media_key,
          'sha256',v_r.media_sha256,
          'bytes',v_r.media_bytes
        ) else null end,
      'provenance',jsonb_build_object('kind','watchlog_private_vision','captured_at',v_r.captured_at)
    ),
    v_r.captured_at+public.wl_ai_evidence_ttl('operational_snapshot')
  );
  return jsonb_build_object('ok',true,'event_id',p_event_id,'status','done');
end $$;
revoke all on function public.wl_vision_complete_snapshot(bigint,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.wl_vision_complete_snapshot(bigint,text,text,jsonb) to service_role;

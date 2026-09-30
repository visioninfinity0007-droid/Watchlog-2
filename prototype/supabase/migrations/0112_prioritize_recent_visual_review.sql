-- 0112_prioritize_recent_visual_review.sql
-- Queue ordering is based on next_attempt_at so urgent/recent backfills can be
-- prioritized without changing capture timestamps or the evidence itself.

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

-- One-time priority for the current Al-Khalid review request: 18 September 2026.
update public.snapshot_visual_reviews
   set next_attempt_at = timestamptz '2000-01-01 00:00:00+00'
 where site_id='588cb40a-3325-4d0f-8ca7-5eee7eb6e443'::uuid
   and (captured_at at time zone 'Asia/Karachi')::date=date '2026-09-18'
   and status in ('pending','failed');

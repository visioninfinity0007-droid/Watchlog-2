-- 0114_vision_worker_heartbeat.sql
create table if not exists public.vision_worker_status (
  worker_id text primary key,
  state text not null,
  model text,
  media_backend text,
  last_seen_at timestamptz not null default now(),
  last_success_at timestamptz,
  processed bigint not null default 0,
  failed bigint not null default 0,
  detail jsonb not null default '{}'::jsonb
);
alter table public.vision_worker_status enable row level security;
revoke all on public.vision_worker_status from public,anon,authenticated;

create or replace function public.wl_vision_worker_heartbeat(
  p_worker_id text,
  p_state text,
  p_model text default null,
  p_media_backend text default null,
  p_processed bigint default 0,
  p_failed bigint default 0,
  p_last_success_at timestamptz default null,
  p_detail jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path=public
as $$
begin
  if auth.role()<>'service_role' then
    raise exception 'service role required' using errcode='42501';
  end if;
  insert into public.vision_worker_status(
    worker_id,state,model,media_backend,last_seen_at,last_success_at,processed,failed,detail)
  values(
    left(coalesce(p_worker_id,'vision-worker'),120),
    left(coalesce(p_state,'unknown'),40),
    left(p_model,160),
    left(p_media_backend,80),
    now(),p_last_success_at,greatest(coalesce(p_processed,0),0),
    greatest(coalesce(p_failed,0),0),coalesce(p_detail,'{}'::jsonb))
  on conflict(worker_id) do update
    set state=excluded.state,model=excluded.model,media_backend=excluded.media_backend,
        last_seen_at=now(),last_success_at=excluded.last_success_at,
        processed=excluded.processed,failed=excluded.failed,detail=excluded.detail;
  return jsonb_build_object('ok',true);
end $$;
revoke all on function public.wl_vision_worker_heartbeat(text,text,text,text,bigint,bigint,timestamptz,jsonb) from public,anon,authenticated;
grant execute on function public.wl_vision_worker_heartbeat(text,text,text,text,bigint,bigint,timestamptz,jsonb) to service_role;
